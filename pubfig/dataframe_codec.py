"""JSON table encoding with consistent missing-value and dtype handling."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from .sheet_data import DATA_START_ROW, trim_trailing_empty_rows


def _json_compatible_cell(value: Any) -> Any:
    """Return one JSON-native scalar while preserving the null policy."""

    # Project tables are normally string-backed.  Keep native JSON scalars on
    # a very small fast path before consulting pandas/NumPy type machinery for
    # uncommon extension values.
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        return value if math.isfinite(value) else None

    if isinstance(
        value,
        (
            np.bytes_,
            np.complexfloating,
            np.datetime64,
            np.timedelta64,
            np.void,
        ),
    ):
        raise TypeError(
            f"Unsupported DataFrame value for JSON serialization: "
            f"{type(value).__name__}"
        )
    if isinstance(value, np.generic):
        value = value.item()
        # ``longdouble.item()`` intentionally remains a NumPy scalar because
        # converting it to Python's double-width float can lose information.
        if isinstance(value, np.generic):
            raise TypeError(
                f"Unsupported NumPy precision for JSON serialization: "
                f"{type(value).__name__}"
            )
        return _json_compatible_cell(value)

    if pd.isna(value):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(
        f"Unsupported DataFrame value for JSON serialization: "
        f"{type(value).__name__}"
    )


def _validate_json_dtypes(df: pd.DataFrame) -> None:
    """Reject ndarray dtypes whose scalar identity is lost by ``frompyfunc``.

    NumPy presents homogeneous datetime and timedelta arrays to object ufuncs
    as integer epoch counts.  Accepting those integers would silently change a
    project that the legacy writer rejected.  Complex, byte-string, and void
    arrays are likewise outside pubfig's JSON scalar schema.
    """

    unsupported_kinds = {"M", "m", "c", "S", "V"}
    invalid = [
        f"{column!r} ({dtype})"
        for column, dtype in zip(df.columns, df.dtypes)
        if getattr(dtype, "kind", None) in unsupported_kinds
    ]
    if invalid:
        details = ", ".join(invalid)
        raise TypeError(
            "Unsupported DataFrame dtype for JSON serialization: "
            f"{details}. Convert these columns to text or ordinary numeric "
            "values first."
        )


# ``frompyfunc`` walks the contiguous ndarray directly and avoids constructing
# a pandas Series for every row.  The scalar helper deliberately keeps the
# established missing/non-finite semantics, including errors for unsupported
# non-scalar cell objects.
_json_compatible_values = np.frompyfunc(_json_compatible_cell, 1, 1)


def dataframe_to_payload(df: pd.DataFrame) -> dict[str, Any]:
    _validate_json_dtypes(df)
    trimmed = trim_trailing_empty_rows(
        df,
        minimum_rows=DATA_START_ROW,
        copy=False,
    )
    values = trimmed.to_numpy(copy=False)
    rows = _json_compatible_values(values).tolist()
    return {"columns": list(map(str, trimmed.columns)), "rows": rows}


def dataframe_from_payload(payload: dict[str, Any]) -> pd.DataFrame:
    frame = pd.DataFrame(payload.get("rows", []), columns=payload.get("columns", []))
    return trim_trailing_empty_rows(
        frame,
        minimum_rows=DATA_START_ROW,
        copy=True,
    )
