"""Versioned pubfig project JSON codec.

The writer always emits the existing v3 on-disk shape.  The reader migrates
the legacy v1 single-figure and v2 figure-list shapes into ``ProjectDocument``
while rejecting missing, non-integral, mismatched, or future schema versions.
No Qt classes are imported here.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from .model import (
    Graph,
    ProjectDocument,
    Sheet,
    TreeNode,
    new_id,
    project_root,
    repair_document,
)
from .plot_config import (
    AnnotationConfig,
    PlotConfig,
    SeriesConfig,
    annotation_config_from_payload,
    dataframe_from_payload as _dataframe_from_payload,
    dataframe_to_payload as _dataframe_to_payload,
    plot_config_from_payload,
    series_config_from_payload,
)
from .sheet_data import blank_dataframe, normalize_dataframe_columns


PROJECT_SCHEMA_VERSION = 3
SUPPORTED_SCHEMA_VERSIONS: tuple[int, ...] = (1, 2, 3)


class ProjectFormatError(ValueError):
    """The JSON value is not a valid project payload."""


class UnsupportedProjectVersion(ProjectFormatError):
    """The payload uses a schema version this build cannot read."""


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProjectFormatError(f"{label} must be a JSON object")
    return value


def project_schema_version(payload: Mapping[str, Any]) -> int:
    """Validate and return the payload's exact schema version."""

    version = payload.get("schema_version")
    # bool is an int subclass, but is never a meaningful schema version.
    if type(version) is not int or version not in SUPPORTED_SCHEMA_VERSIONS:
        raise UnsupportedProjectVersion(
            f"Unsupported project schema_version: {version!r}; "
            f"supported versions are {SUPPORTED_SCHEMA_VERSIONS}"
        )

    has_figures = "figures" in payload
    has_sheets = "sheets" in payload
    if version == 1 and ("data" not in payload or has_figures or has_sheets):
        raise ProjectFormatError("schema_version 1 requires the single-figure shape")
    if version == 2 and (not has_figures or has_sheets):
        raise ProjectFormatError("schema_version 2 requires the figures shape")
    if version == 3 and (not has_sheets or has_figures):
        raise ProjectFormatError("schema_version 3 requires the sheets/graphs shape")
    return version


def dataframe_to_payload(df: pd.DataFrame) -> dict[str, Any]:
    """Encode a DataFrame after enforcing canonical unique columns."""

    return _dataframe_to_payload(normalize_dataframe_columns(df))


def dataframe_from_payload(payload: Mapping[str, Any] | None) -> pd.DataFrame:
    """Decode a DataFrame and normalize old duplicate/non-string columns."""

    if payload is None:
        payload = {}
    mapping = _mapping(payload, "data")
    return normalize_dataframe_columns(_dataframe_from_payload(dict(mapping)), copy=False)


def sheet_to_payload(sheet: Sheet) -> dict[str, Any]:
    return {
        "id": sheet.id,
        "name": sheet.name,
        "data": dataframe_to_payload(sheet.df),
    }


def graph_to_payload(graph: Graph) -> dict[str, Any]:
    """Encode a graph using the established v3 annotation placement."""

    config = deepcopy(graph.plot_config)
    config.annotations = deepcopy(graph.annotations)
    return {
        "id": graph.id,
        "name": graph.name,
        "sheet_id": graph.sheet_id,
        "plot_config": asdict(config),
        "series_config": [
            asdict(series) for series in graph.series_by_y.values()
        ],
        "checked_y": list(graph.checked_y),
    }


def tree_to_payload(node: TreeNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "type": node.type,
        "name": node.name,
        "ref_id": node.ref_id,
        "expanded": node.expanded,
        "children": [tree_to_payload(child) for child in node.children],
    }


def document_to_payload(document: ProjectDocument) -> dict[str, Any]:
    """Encode a document in the current v3 JSON shape."""

    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "active_node_id": document.active_node_id,
        "sheets": [sheet_to_payload(sheet) for sheet in document.sheets.values()],
        "graphs": [graph_to_payload(graph) for graph in document.graphs.values()],
        "tree": tree_to_payload(document.tree_root),
    }


def _text(value: Any, fallback: str) -> str:
    if value is None:
        return fallback
    text = str(value)
    return text or fallback


def tree_from_payload(data: Mapping[str, Any]) -> TreeNode:
    mapping = _mapping(data, "tree node")
    raw_children = mapping.get("children", [])
    if raw_children is None:
        raw_children = []
    if not isinstance(raw_children, list):
        raise ProjectFormatError("tree node children must be an array")
    node_type = _text(mapping.get("type"), "folder")
    return TreeNode(
        id=_text(mapping.get("id"), new_id("nd")),
        type=node_type,  # repair_document validates/re-homes invalid types
        name=_text(mapping.get("name"), ""),
        ref_id=(
            None
            if mapping.get("ref_id") is None
            else str(mapping.get("ref_id"))
        ),
        children=[tree_from_payload(_mapping(child, "tree child")) for child in raw_children],
        expanded=bool(mapping.get("expanded", True)),
    )


def _annotations_and_config(
    payload: Mapping[str, Any] | None,
) -> tuple[list[AnnotationConfig], PlotConfig]:
    plot_payload = dict(_mapping(payload or {}, "plot_config"))
    raw_annotations = plot_payload.pop("annotations", None) or []
    if not isinstance(raw_annotations, list):
        raise ProjectFormatError("plot_config.annotations must be an array")
    annotations = [
        annotation_config_from_payload(dict(_mapping(item, "annotation")))
        for item in raw_annotations
    ]
    config = plot_config_from_payload(plot_payload)
    config.annotations = annotations
    return annotations, config


def _series_from_payload(value: Any) -> list[SeriesConfig]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ProjectFormatError("series_config must be an array")
    return [
        series_config_from_payload(dict(_mapping(item, "series")))
        for item in value
    ]


def _checked_y(item: Mapping[str, Any], series: list[SeriesConfig]) -> list[str]:
    # An explicitly empty list means no selected series.  Only a missing/null
    # legacy field falls back to all serialized series.
    raw = item.get("checked_y")
    if raw is None:
        return [entry.y for entry in series]
    if not isinstance(raw, list):
        raise ProjectFormatError("checked_y must be an array")
    return [str(value) for value in raw]


def _graph_from_item(
    item: Mapping[str, Any], *, sheet_id: str, index: int, with_id: bool
) -> Graph:
    annotations, config = _annotations_and_config(item.get("plot_config"))
    series = _series_from_payload(item.get("series_config", []))
    graph_id = (
        _text(item.get("id"), new_id("gr")) if with_id else new_id("gr")
    )
    return Graph(
        id=graph_id,
        name=_text(item.get("name"), f"Figure {index + 1}" if not with_id else "Graph"),
        sheet_id=sheet_id,
        plot_config=config,
        series_by_y={entry.y: entry for entry in series},
        checked_y=_checked_y(item, series),
        annotations=annotations,
    )


def _add_legacy_figure(
    document: ProjectDocument,
    item: Mapping[str, Any],
    *,
    index: int,
    name: str,
) -> TreeNode:
    sheet = Sheet(
        id=new_id("sh"),
        name=name,
        df=dataframe_from_payload(item.get("data", {})),
    )
    graph = _graph_from_item(item, sheet_id=sheet.id, index=index, with_id=False)
    graph.name = name
    document.sheets[sheet.id] = sheet
    document.graphs[graph.id] = graph
    graph_node = TreeNode(
        id=new_id("nd"), type="graph", name=graph.name, ref_id=graph.id
    )
    sheet_node = TreeNode(
        id=new_id("nd"),
        type="sheet",
        name=sheet.name,
        ref_id=sheet.id,
        children=[graph_node],
    )
    document.tree_root.children.append(sheet_node)
    return graph_node


def _document_from_v1(
    payload: Mapping[str, Any], fallback_name: str
) -> ProjectDocument:
    name = fallback_name or "Figure 1"
    document = ProjectDocument(tree_root=project_root())
    graph_node = _add_legacy_figure(
        document, payload, index=0, name=name
    )
    document.active_node_id = graph_node.id
    return document


def _document_from_v2(payload: Mapping[str, Any]) -> ProjectDocument:
    raw_figures = payload.get("figures", [])
    if not isinstance(raw_figures, list):
        raise ProjectFormatError("figures must be an array")

    document = ProjectDocument(tree_root=project_root())
    graph_node_ids: list[str] = []
    for index, raw_item in enumerate(raw_figures):
        item = _mapping(raw_item, f"figures[{index}]")
        name = _text(item.get("name"), f"Figure {index + 1}")
        graph_node = _add_legacy_figure(
            document, item, index=index, name=name
        )
        graph_node_ids.append(graph_node.id)

    if not raw_figures:
        sheet = Sheet(id=new_id("sh"), name="Sheet 1", df=blank_dataframe())
        document.sheets[sheet.id] = sheet
        node = TreeNode(
            id=new_id("nd"), type="sheet", name=sheet.name, ref_id=sheet.id
        )
        document.tree_root.children.append(node)
        document.active_node_id = node.id
        return document

    try:
        active_index = int(payload.get("active_figure_index", 0))
    except (TypeError, ValueError) as exc:
        raise ProjectFormatError("active_figure_index must be an integer") from exc
    if 0 <= active_index < len(graph_node_ids):
        document.active_node_id = graph_node_ids[active_index]
    else:
        document.active_node_id = graph_node_ids[0]
    return document


def _document_from_v3(payload: Mapping[str, Any]) -> ProjectDocument:
    raw_sheets = payload.get("sheets", [])
    raw_graphs = payload.get("graphs", [])
    if not isinstance(raw_sheets, list):
        raise ProjectFormatError("sheets must be an array")
    if not isinstance(raw_graphs, list):
        raise ProjectFormatError("graphs must be an array")

    sheets: dict[str, Sheet] = {}
    for index, raw_item in enumerate(raw_sheets):
        item = _mapping(raw_item, f"sheets[{index}]")
        sheet_id = _text(item.get("id"), new_id("sh"))
        if sheet_id in sheets:
            raise ProjectFormatError(f"duplicate sheet id: {sheet_id}")
        sheets[sheet_id] = Sheet(
            id=sheet_id,
            name=_text(item.get("name"), "Sheet"),
            df=dataframe_from_payload(item.get("data", {})),
        )

    graphs: dict[str, Graph] = {}
    for index, raw_item in enumerate(raw_graphs):
        item = _mapping(raw_item, f"graphs[{index}]")
        sheet_id = _text(item.get("sheet_id"), "")
        graph = _graph_from_item(
            item, sheet_id=sheet_id, index=index, with_id=True
        )
        if graph.id in graphs:
            raise ProjectFormatError(f"duplicate graph id: {graph.id}")
        graphs[graph.id] = graph

    raw_tree = payload.get("tree")
    tree = (
        tree_from_payload(_mapping(raw_tree, "tree"))
        if raw_tree is not None
        else project_root()
    )
    active_node_id = payload.get("active_node_id")
    document = ProjectDocument(
        sheets=sheets,
        graphs=graphs,
        tree_root=tree,
        active_node_id=(None if active_node_id is None else str(active_node_id)),
    )
    repair_document(document)
    return document


def document_from_payload(
    payload: Mapping[str, Any], fallback_name: str = "Figure 1"
) -> ProjectDocument:
    """Decode/migrate a v1, v2, or v3 payload into the current model."""

    mapping = _mapping(payload, "project")
    version = project_schema_version(mapping)
    if version == 1:
        return _document_from_v1(mapping, fallback_name)
    if version == 2:
        return _document_from_v2(mapping)
    return _document_from_v3(mapping)


def load_payload_into_model(
    payload: Mapping[str, Any], fallback_name: str = "Figure 1"
) -> tuple[dict[str, Sheet], dict[str, Graph], TreeNode, str | None]:
    """Compatibility adapter for the pre-``ProjectDocument`` UI API."""

    document = document_from_payload(payload, fallback_name)
    return (
        document.sheets,
        document.graphs,
        document.tree_root,
        document.active_node_id,
    )


def read_project(path: str | os.PathLike[str]) -> ProjectDocument:
    """Read and decode a project file; the filename stem names v1 projects."""

    project_path = Path(path)
    with project_path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    return document_from_payload(_mapping(payload, "project"), project_path.stem)


def write_json_atomic(
    path: str | os.PathLike[str],
    payload: Mapping[str, Any],
    *,
    indent: int | None = None,
) -> Path:
    """Atomically replace ``path`` with UTF-8 JSON and return the path.

    The temporary file is created beside the target so ``replace`` remains an
    atomic same-filesystem operation.  A failed encode/write leaves an existing
    target untouched and removes the temporary file.
    """

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as file:
            json.dump(
                payload,
                file,
                indent=indent,
                ensure_ascii=False,
                allow_nan=False,
            )
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(target)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return target


def write_project(
    path: str | os.PathLike[str],
    document: ProjectDocument,
    *,
    indent: int | None = 2,
) -> Path:
    """Encode and atomically save a project, adding ``.json`` when needed."""

    target = Path(path)
    if target.suffix.lower() != ".json":
        target = target.with_suffix(".json")
    return write_json_atomic(target, document_to_payload(document), indent=indent)


# Migration-friendly names used by the old window and by project builders.
project_payload = document_to_payload
project_to_payload = document_to_payload
project_from_payload = document_from_payload
write_document = write_project
read_document = read_project
_write_json_atomic = write_json_atomic


__all__ = [
    "PROJECT_SCHEMA_VERSION",
    "ProjectFormatError",
    "SUPPORTED_SCHEMA_VERSIONS",
    "UnsupportedProjectVersion",
    "dataframe_from_payload",
    "dataframe_to_payload",
    "document_from_payload",
    "document_to_payload",
    "graph_to_payload",
    "load_payload_into_model",
    "project_from_payload",
    "project_payload",
    "project_schema_version",
    "project_to_payload",
    "read_document",
    "read_project",
    "sheet_to_payload",
    "tree_from_payload",
    "tree_to_payload",
    "write_document",
    "write_json_atomic",
    "write_project",
]
