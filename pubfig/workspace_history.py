"""Lightweight project snapshots for undo/redo.

The persisted project model mixes two very different kinds of state:

* comparatively small, mutable graph/tree configuration; and
* potentially hundreds of megabytes of tabular data.

Copying both for every UI signal makes an otherwise inexpensive style change
scale with the size of the whole workbook.  The helpers here make a structural
snapshot instead: ordinary model objects are deep-copied, while DataFrames use
pandas Copy-on-Write views.  A later table edit copies only the affected pandas
storage block and leaves historical states isolated.

The resource map is deliberately based on backing array identity.  It lets the
generic undo manager charge its memory budget only for buffers retained solely
by history, rather than charging the same live workbook once per snapshot.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import fields, is_dataclass
import re
import sys
from typing import Hashable, Mapping

import numpy as np
import pandas as pd

from .model import ProjectDocument


# Object arrays hold pointers to Python values.  The pointers themselves are
# reported by ``nbytes``; this multiplier is a conservative, constant-time
# allowance for values which may become reachable only through an old frame.
_OBJECT_STORAGE_MULTIPLIER = 8
_DATAFRAME_WRAPPER_BYTES = 1024
_COLUMN_WRAPPER_BYTES = 128
_MASKED_ARRAY_TYPES = tuple(
    array_type
    for array_type in (
        getattr(pd.arrays, "IntegerArray", None),
        getattr(pd.arrays, "FloatingArray", None),
        getattr(pd.arrays, "BooleanArray", None),
    )
    if array_type is not None
)
_SPARSE_ARRAY_TYPE = getattr(pd.arrays, "SparseArray", None)
_INTERVAL_ARRAY_TYPE = getattr(pd.arrays, "IntervalArray", None)


def enable_dataframe_copy_on_write() -> None:
    """Enable pandas 2.x Copy-on-Write before creating shared snapshots.

    Copy-on-Write is always enabled in pandas 3.  Assigning its deprecated
    option there emits a warning, so only pandas 2 needs an explicit setting.
    """

    match = re.match(r"\s*(\d+)", pd.__version__)
    major = int(match.group(1)) if match is not None else 3
    if major < 3:
        pd.options.mode.copy_on_write = True


def clone_project_document(document: ProjectDocument) -> ProjectDocument:
    """Clone project metadata while structurally sharing DataFrame buffers."""

    enable_dataframe_copy_on_write()
    memo: dict[int, object] = {}
    for sheet in document.sheets.values():
        dataframe = sheet.df
        # Preserve intentional aliases between sheets while giving every
        # snapshot its own DataFrame/index/column wrapper.
        if id(dataframe) not in memo:
            memo[id(dataframe)] = dataframe.copy(deep=False)
    return deepcopy(document, memo)


def project_snapshot_resources(
    document: ProjectDocument,
) -> Mapping[Hashable, int]:
    """Return shared backing-buffer tokens and constant-time size estimates."""

    resources: dict[Hashable, int] = {}
    seen_frames: set[int] = set()
    for sheet in document.sheets.values():
        dataframe = sheet.df
        marker = id(dataframe)
        if marker in seen_frames:
            continue
        seen_frames.add(marker)
        for token, size in _dataframe_resources(dataframe).items():
            resources[token] = max(resources.get(token, 0), size)
    return resources


def project_snapshot_metadata_weight(document: ProjectDocument) -> int:
    """Estimate non-tabular snapshot memory without inspecting cell values."""

    return _metadata_size(document, set())


def _metadata_size(value: object, seen: set[int]) -> int:
    if isinstance(value, pd.DataFrame):
        # Do not call DataFrame.__sizeof__: it performs a deep cell-memory
        # traversal for object columns.  Wrapper overhead is shape-independent
        # apart from the small per-column manager/index structures.
        return _DATAFRAME_WRAPPER_BYTES + len(value.columns) * _COLUMN_WRAPPER_BYTES

    marker = id(value)
    if marker in seen:
        return 0
    seen.add(marker)

    size = sys.getsizeof(value)
    if is_dataclass(value) and not isinstance(value, type):
        return size + sum(
            _metadata_size(getattr(value, field.name), seen)
            for field in fields(value)
        )
    if isinstance(value, dict):
        return size + sum(
            _metadata_size(key, seen) + _metadata_size(item, seen)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return size + sum(_metadata_size(item, seen) for item in value)
    return size


def _dataframe_resources(dataframe: pd.DataFrame) -> dict[Hashable, int]:
    resources: dict[Hashable, int] = {}
    manager = getattr(dataframe, "_mgr", None)
    blocks = getattr(manager, "blocks", None)
    if blocks is not None:
        for block in blocks:
            _add_array_resource(resources, getattr(block, "values", None))
        return resources

    # ArrayManager and third-party DataFrame implementations do not expose
    # blocks.  Column arrays are still a bounded-by-column fallback and never
    # inspect individual cells.
    for column in dataframe.columns:
        _add_array_resource(resources, dataframe[column].array)
    return resources


def _add_array_resource(
    resources: dict[Hashable, int],
    array: object,
    seen_arrays: set[int] | None = None,
) -> None:
    if array is None:
        return

    if seen_arrays is None:
        seen_arrays = set()
    marker = id(array)
    if marker in seen_arrays:
        return
    seen_arrays.add(marker)

    if _MASKED_ARRAY_TYPES and isinstance(array, _MASKED_ARRAY_TYPES):
        # Nullable Int64/Float64/boolean arrays use distinct EA wrappers in a
        # shallow DataFrame copy, but their data and validity-mask ndarrays are
        # CoW views over the same roots.
        _add_array_resource(resources, array._data, seen_arrays)
        _add_array_resource(resources, array._mask, seen_arrays)
        return

    if isinstance(array, pd.Categorical):
        _add_array_resource(resources, array._codes, seen_arrays)
        category_values = getattr(array.categories, "_values", None)
        _add_array_resource(resources, category_values, seen_arrays)
        return

    if _SPARSE_ARRAY_TYPE is not None and isinstance(array, _SPARSE_ARRAY_TYPE):
        _add_array_resource(resources, array.sp_values, seen_arrays)
        sparse_index = array.sp_index
        for attribute in ("indices", "blocs", "blengths"):
            _add_array_resource(
                resources,
                getattr(sparse_index, attribute, None),
                seen_arrays,
            )
        return

    if _INTERVAL_ARRAY_TYPE is not None and isinstance(
        array, _INTERVAL_ARRAY_TYPE
    ):
        for endpoint in (array.left, array.right):
            _add_array_resource(
                resources,
                getattr(endpoint, "_values", endpoint),
                seen_arrays,
            )
        return

    candidate = getattr(array, "_ndarray", array)
    if isinstance(candidate, np.ndarray):
        root = candidate
        visited: set[int] = set()
        while isinstance(root.base, np.ndarray) and id(root.base) not in visited:
            visited.add(id(root))
            root = root.base
        multiplier = _OBJECT_STORAGE_MULTIPLIER if root.dtype.hasobject else 1
        token: Hashable = ("numpy", id(root))
        resources[token] = max(
            resources.get(token, 0),
            max(int(root.nbytes) * multiplier, 1),
        )
        return

    # pandas Arrow extension arrays create a new lightweight wrapper for a
    # shallow copy.  Wrapper identity therefore cannot describe ownership,
    # while Arrow's immutable buffers retain stable addresses across those
    # wrappers and get new addresses when a value is edited.
    arrow_array = getattr(candidate, "_pa_array", None)
    if arrow_array is not None and _add_arrow_resources(resources, arrow_array):
        return

    # Most extension arrays are shared as an object by a shallow DataFrame
    # copy.  Their public ``nbytes`` remains a cheap, non-deep estimate.
    nbytes = getattr(candidate, "nbytes", 0)
    try:
        size = max(int(nbytes), 1)
    except (TypeError, ValueError):
        size = 1
    token = ("extension", id(candidate))
    resources[token] = max(resources.get(token, 0), size)


def _add_arrow_resources(
    resources: dict[Hashable, int],
    arrow_array: object,
    seen_arrays: set[int] | None = None,
) -> bool:
    """Add physical Arrow buffers without converting or visiting cell values."""

    if seen_arrays is None:
        seen_arrays = set()
    marker = id(arrow_array)
    if marker in seen_arrays:
        return False
    seen_arrays.add(marker)

    found = False
    chunks = getattr(arrow_array, "chunks", None)
    if chunks is not None:
        for chunk in chunks:
            found = _add_arrow_resources(resources, chunk, seen_arrays) or found
        return found

    buffers_method = getattr(arrow_array, "buffers", None)
    if callable(buffers_method):
        for buffer in buffers_method():
            if buffer is None:
                continue
            try:
                address = int(buffer.address)
                size = int(buffer.size)
            except (AttributeError, TypeError, ValueError):
                continue
            if size <= 0:
                continue
            token: Hashable = ("arrow", address, size)
            resources[token] = max(resources.get(token, 0), size)
            found = True

    # Dictionary and nested list-like arrays may own child storage not exposed
    # by their top-level validity/offset buffers.  These properties are Arrow
    # arrays, never materialized Python cell sequences.
    for attribute in ("dictionary", "values"):
        child = getattr(arrow_array, attribute, None)
        if child is None or callable(child):
            continue
        child_module = type(child).__module__
        if child_module.startswith("pyarrow"):
            found = _add_arrow_resources(resources, child, seen_arrays) or found
    return found


__all__ = [
    "clone_project_document",
    "enable_dataframe_copy_on_write",
    "project_snapshot_metadata_weight",
    "project_snapshot_resources",
]
