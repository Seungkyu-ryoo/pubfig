"""Pure helpers shared by legend editing and rendering."""

from __future__ import annotations

from collections.abc import Iterable


def normalize_row_lengths(row_lengths: Iterable[int]) -> list[int]:
    """Return non-negative integer row sizes in stable order."""

    return [max(0, int(length)) for length in row_lengths]


def legend_row_ids(entry_count: int, row_lengths: Iterable[int]) -> list[int]:
    """Map each legend entry to its logical row."""

    if entry_count <= 0:
        return []
    lengths = normalize_row_lengths(row_lengths)
    if not lengths:
        return list(range(entry_count))
    row_ids: list[int] = []
    row_index = 0
    for length in lengths:
        row_ids.extend([row_index] * min(length, entry_count - len(row_ids)))
        row_index += 1
        if len(row_ids) >= entry_count:
            break
    while len(row_ids) < entry_count:
        row_ids.append(row_index)
        row_index += 1
    return row_ids


def new_row_flags(entry_count: int, row_lengths: Iterable[int]) -> list[bool]:
    """Return editor flags marking the first entry in each logical row."""

    if entry_count <= 0:
        return []
    row_ids = legend_row_ids(entry_count, row_lengths)
    return [index == 0 or row_ids[index] != row_ids[index - 1] for index in range(entry_count)]


__all__ = ["legend_row_ids", "new_row_flags", "normalize_row_lengths"]
