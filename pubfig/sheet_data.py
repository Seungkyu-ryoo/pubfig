"""Helpers for pubfig's two metadata rows and column identifiers.

The first two rows of every sheet are metadata rather than plotted values:
row 0 stores ``X``/``Y`` roles, row 1 stores display names, and data begins at
row 2.  Functions here use positional access so even a malformed frame with
duplicate labels can be normalized safely at the application boundary.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pandas as pd


ROLE_ROW = 0
NAME_ROW = 1
DATA_START_ROW = 2


def normalize_column_names(columns: Iterable[Any]) -> list[str]:
    """Return deterministic, non-empty, unique string column identifiers.

    Policy:

    * values are converted to strings without otherwise changing them;
    * an empty name becomes ``"Col N"`` using its one-based position; and
    * later collisions receive ``_2``, ``_3``, ... using the first available
      suffix (the first occurrence always keeps its original spelling).

    Examples: ``["X", "Y", "Y"] -> ["X", "Y", "Y_2"]`` and
    ``["A", "A_2", "A"] -> ["A", "A_2", "A_3"]``.
    """

    result: list[str] = []
    used: set[str] = set()
    for index, value in enumerate(columns):
        base = str(value)
        if not base:
            base = f"Col {index + 1}"
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate)
        result.append(candidate)
    return result


def next_column_name(columns: Iterable[Any], prefix: str = "Col") -> str:
    """Return the first unused one-based column identifier."""

    existing = {str(column) for column in columns}
    index = 1
    while f"{prefix} {index}" in existing:
        index += 1
    return f"{prefix} {index}"


def normalize_dataframe_columns(
    df: pd.DataFrame, *, copy: bool = True
) -> pd.DataFrame:
    """Apply :func:`normalize_column_names` to a DataFrame.

    ``copy=True`` is the safe default for project loading and leaves the input
    untouched.  Pass ``copy=False`` only when the caller owns the frame.
    """

    normalized = df.copy(deep=True) if copy else df
    normalized.columns = normalize_column_names(normalized.columns)
    return normalized


def blank_dataframe(rows: int = 20, columns: int = 4) -> pd.DataFrame:
    """Create an empty sheet including the role/name metadata rows."""

    if rows < 0 or columns < 0:
        raise ValueError("rows and columns must be non-negative")
    labels = [f"Col {index + 1}" for index in range(columns)]
    df = pd.DataFrame(
        "", index=range(rows + DATA_START_ROW), columns=labels, dtype=object
    )
    if columns:
        df.iat[ROLE_ROW, 0] = "X"
    if columns > 1:
        df.iat[ROLE_ROW, 1] = "Y"
    return df


def with_metadata_rows(data_df: pd.DataFrame) -> pd.DataFrame:
    """Prepend default role and display-name rows to imported data."""

    data = normalize_dataframe_columns(data_df)
    columns = list(data.columns)
    roles = ["X" if index == 0 else "Y" for index in range(len(columns))]
    metadata = pd.DataFrame([roles, columns], columns=columns, dtype=object)
    return pd.concat([metadata, data.astype(str)], ignore_index=True)


def ensure_metadata_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with at least the two metadata rows present."""

    result = normalize_dataframe_columns(df)
    missing = max(DATA_START_ROW - len(result), 0)
    if missing:
        padding = pd.DataFrame(
            "", index=range(missing), columns=result.columns, dtype=object
        )
        result = pd.concat([result, padding], ignore_index=True)
    return result


def plot_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Return only plotted rows, with a fresh positional index."""

    if len(df) <= DATA_START_ROW:
        return pd.DataFrame(columns=df.columns)
    return df.iloc[DATA_START_ROW:].reset_index(drop=True)


def _column_position(df: pd.DataFrame, column: str) -> int | None:
    target = str(column)
    for index, name in enumerate(map(str, df.columns)):
        if name == target:
            return index
    return None


def normalize_role(value: Any) -> str:
    """Normalize a role cell to ``X``, ``Y``, or the empty role."""

    if value is None or pd.isna(value):
        return ""
    text = str(value).strip().upper()
    if text.startswith("X"):
        return "X"
    if text.startswith("Y"):
        return "Y"
    return ""


def column_role(df: pd.DataFrame, column: str) -> str:
    """Return the normalized role for a column identifier."""

    position = _column_position(df, column)
    if position is None or len(df) <= ROLE_ROW:
        return ""
    return normalize_role(df.iat[ROLE_ROW, position])


def column_name(df: pd.DataFrame, column: str) -> str:
    """Return a column's display name, falling back to its identifier."""

    identifier = str(column)
    position = _column_position(df, identifier)
    if position is None or len(df) <= NAME_ROW:
        return identifier
    value = df.iat[NAME_ROW, position]
    if value is None or pd.isna(value):
        return identifier
    text = str(value).strip()
    return text or identifier


def nearest_left_x(df: pd.DataFrame, y_column: str) -> str:
    """Return the closest X-role column to the left of ``y_column``."""

    columns = list(map(str, df.columns))
    try:
        y_index = columns.index(str(y_column))
    except ValueError:
        return ""
    for index in range(y_index - 1, -1, -1):
        column = columns[index]
        if column_role(df, column) == "X":
            return column
    return ""


def x_columns(df: pd.DataFrame) -> list[str]:
    """Return all columns marked with the X role."""

    return [
        column
        for column in map(str, df.columns)
        if column_role(df, column) == "X"
    ]


def y_columns(df: pd.DataFrame, *, require_left_x: bool = False) -> list[str]:
    """Return Y-role columns, optionally requiring a preceding X column."""

    result = [
        column
        for column in map(str, df.columns)
        if column_role(df, column) == "Y"
    ]
    if require_left_x:
        result = [column for column in result if nearest_left_x(df, column)]
    return result


# Small vocabulary aliases for callers migrating the old methods.
project_plot_dataframe = plot_dataframe
project_column_role = column_role
project_column_name = column_name
project_nearest_left_x = nearest_left_x
unique_column_names = normalize_column_names


__all__ = [
    "DATA_START_ROW",
    "NAME_ROW",
    "ROLE_ROW",
    "blank_dataframe",
    "column_name",
    "column_role",
    "ensure_metadata_rows",
    "nearest_left_x",
    "next_column_name",
    "normalize_column_names",
    "normalize_dataframe_columns",
    "normalize_role",
    "plot_dataframe",
    "project_column_name",
    "project_column_role",
    "project_nearest_left_x",
    "project_plot_dataframe",
    "unique_column_names",
    "with_metadata_rows",
    "x_columns",
    "y_columns",
]
