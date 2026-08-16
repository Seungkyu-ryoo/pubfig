"""Clipboard/table text parsing for Graph_drawer."""

from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
import re

import pandas as pd


@dataclass(frozen=True)
class ParsedTable:
    dataframe: pd.DataFrame
    delimiter: str
    has_header: bool


def parse_table_text(text: str) -> ParsedTable:
    """Parse copied spreadsheet/CSV text into a DataFrame.

    The parser favors common clipboard formats first:
    tab, comma, semicolon, then whitespace. The preview DataFrame keeps text
    values, while plotting code can coerce numeric columns later.
    """
    cleaned = _normalize_text(text)
    if not cleaned:
        raise ValueError("No clipboard text found.")

    delimiter_name, sep = _detect_separator(cleaned)
    rows = _read_rows(cleaned, sep)
    if not rows:
        raise ValueError("No table rows found.")

    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    has_header = _looks_like_header(rows[0], rows[1:])

    if has_header:
        columns = _dedupe_headers([value.strip() or f"Col {idx + 1}" for idx, value in enumerate(rows[0])])
        data_rows = rows[1:]
    else:
        columns = [f"Col {idx + 1}" for idx in range(width)]
        data_rows = rows

    df = pd.DataFrame(data_rows, columns=columns)
    df = df.apply(lambda column: column.map(lambda value: value.strip() if isinstance(value, str) else value))
    return ParsedTable(df, delimiter_name, has_header)


def numeric_columns(df: pd.DataFrame) -> list[str]:
    """Return columns that contain at least one numeric value."""
    columns: list[str] = []
    for column in df.columns:
        numeric = pd.to_numeric(df[column], errors="coerce")
        if numeric.notna().any():
            columns.append(str(column))
    return columns


def coerce_numeric(series: pd.Series) -> pd.Series:
    """Convert a column to numeric values for plotting."""
    return pd.to_numeric(series, errors="coerce")


def parse_clipboard_grid(text: str) -> list[list[str]]:
    """Parse clipboard text as plain cells without header inference."""
    cleaned = _normalize_text(text)
    if not cleaned:
        return []
    _, sep = _detect_separator(cleaned)
    return _read_rows(cleaned, sep)


def _normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _detect_separator(text: str) -> tuple[str, str]:
    sample = "\n".join(text.splitlines()[:8])
    candidates = [("tab", "\t"), ("comma", ","), ("semicolon", ";")]
    best_name = "whitespace"
    best_sep = r"\s+"
    best_score = 0

    for name, sep in candidates:
        counts = [line.count(sep) for line in sample.splitlines() if line.strip()]
        if not counts:
            continue
        nonzero = [count for count in counts if count > 0]
        score = len(nonzero) * 10 + (min(nonzero) if nonzero else 0)
        if score > best_score:
            best_name = name
            best_sep = sep
            best_score = score

    if best_score == 0 and any(len(re.split(r"\s+", line.strip())) > 1 for line in sample.splitlines()):
        return "whitespace", r"\s+"
    return best_name, best_sep


def _read_rows(text: str, sep: str) -> list[list[str]]:
    try:
        df = pd.read_csv(
            StringIO(text),
            sep=sep,
            header=None,
            dtype=str,
            engine="python",
            keep_default_na=False,
        )
        return df.fillna("").astype(str).values.tolist()
    except Exception:
        rows: list[list[str]] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            if sep == r"\s+":
                rows.append(re.split(r"\s+", line.strip()))
            else:
                rows.append([cell.strip() for cell in line.split(sep)])
        return rows


def _looks_like_header(first_row: list[str], data_rows: list[list[str]]) -> bool:
    if not data_rows:
        return any(_numeric_or_blank(value) is False for value in first_row)

    first_numeric = sum(_numeric_or_blank(value) is True for value in first_row)
    first_text = sum(_numeric_or_blank(value) is False for value in first_row)
    sample = data_rows[:5]
    data_numeric = 0
    data_cells = 0
    for row in sample:
        for value in row:
            if value.strip() == "":
                continue
            data_cells += 1
            data_numeric += int(_numeric_or_blank(value) is True)

    data_ratio = data_numeric / data_cells if data_cells else 0.0
    return first_text > first_numeric and data_ratio >= 0.5


def _numeric_or_blank(value: str) -> bool | None:
    text = value.strip()
    if not text:
        return None
    try:
        float(text)
        return True
    except ValueError:
        return False


def _dedupe_headers(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result: list[str] = []
    for header in headers:
        base = header or "Column"
        count = seen.get(base, 0)
        seen[base] = count + 1
        result.append(base if count == 0 else f"{base}_{count + 1}")
    return result
