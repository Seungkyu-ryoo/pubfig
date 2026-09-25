"""Pure helpers shared by legend editing and rendering."""

from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Any
from urllib.parse import quote, unquote

from .plot_config import LegendEntryConfig


# ``LegendEntryConfig.label == ""`` historically means "use the source
# series' default label".  The editor needs a distinct representation for an
# intentional sample with no visible text, so it uses this zero-width value in
# the typed model and translates it back to an empty cell in the text editor.
_SAMPLE_ONLY_LABEL = "\u200b"
_SAMPLE_TOKEN_RE = re.compile(
    r"\\[lL](?:\(\s*(?P<numeric>\d+)\s*\)|\{(?P<stable>[^{}]*)\})"
)


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


def _entry_values(entry: LegendEntryConfig | dict[str, Any]) -> tuple[str, str]:
    if isinstance(entry, dict):
        source = entry.get("source_y", "")
        label = entry.get("label", "")
    else:
        source = getattr(entry, "source_y", "")
        label = getattr(entry, "label", "")
    return (
        "" if source is None else str(source),
        "" if label is None else str(label),
    )


def legend_entries_to_text(
    entries: Iterable[LegendEntryConfig | dict[str, Any]],
    row_lengths: Iterable[int],
    source_order: Iterable[str],
) -> str:
    """Serialize typed legend cells to the Origin-style editor notation.

    Numeric ``\\L(n)`` tokens are only an editing representation.  The typed
    model remains bound to stable ``source_y`` identifiers so column renames,
    project IO, and undo do not depend on a plot's current numeric position.
    A URL-escaped ``\\L{source}`` token preserves a stale legacy source until
    that source exists again.
    """

    configured_entries = list(entries)
    if not configured_entries:
        return ""
    sources = [str(source) for source in source_order]
    source_indexes = {
        source: index
        for index, source in enumerate(sources, start=1)
    }
    row_ids = legend_row_ids(len(configured_entries), row_lengths)
    rows: list[list[str]] = []
    previous_row_id: int | None = None

    for row_id, entry in zip(row_ids, configured_entries):
        if row_id != previous_row_id:
            rows.append([])
            previous_row_id = row_id
        source_y, stored_label = _entry_values(entry)
        sample_only = stored_label == _SAMPLE_ONLY_LABEL
        label = "" if sample_only else stored_label
        if not source_y:
            cell = label
        else:
            source_index = source_indexes.get(source_y)
            if source_index is None:
                sample_token = f"\\L{{{quote(source_y, safe='')}}}"
            else:
                sample_token = f"\\L({source_index})"

            if sample_only:
                cell = sample_token
            elif label:
                cell = f"{sample_token} {label}"
            elif source_index is not None:
                # Preserve the legacy empty-label meaning: use the current
                # default name for this sample.
                cell = f"{sample_token} %({source_index})"
            else:
                cell = sample_token
        rows[-1].append(cell)

    return "\n".join("\t".join(row) for row in rows)


def _remove_sample_token(cell: str, match: re.Match[str]) -> str:
    """Remove one sample token plus its single formatting delimiter."""

    label = cell[: match.start()] + cell[match.end() :]
    delimiter_index = match.start()
    if delimiter_index < len(label) and label[delimiter_index] == " ":
        label = label[:delimiter_index] + label[delimiter_index + 1 :]
    return label


def _resolved_sample_token(
    match: re.Match[str],
    sources: list[str],
) -> tuple[str, int | None] | None:
    """Resolve one sample token, leaving invalid numeric tokens as text."""

    numeric = match.group("numeric")
    if numeric is not None:
        source_index = int(numeric) - 1
        if 0 <= source_index < len(sources):
            return sources[source_index], source_index
        return None

    source_y = unquote(match.group("stable") or "")
    return (source_y, None) if source_y else None


def legend_line_cells(
    line: str,
    source_order: Iterable[str],
) -> list[tuple[str, int, int]]:
    """Return ``(text, start, end)`` cells for one physical editor line.

    A literal Tab is the canonical separator.  For forgiving pasted or typed
    input, a second resolvable ``\\L`` token preceded by spaces also starts a
    new cell.  Ordinary spaces inside a label remain untouched when the line
    contains only one sample token.
    """

    value = "" if line is None else str(line)
    sources = [str(source) for source in source_order]
    output: list[tuple[str, int, int]] = []
    explicit_start = 0

    while True:
        tab_index = value.find("\t", explicit_start)
        explicit_end = len(value) if tab_index < 0 else tab_index
        explicit_cell = value[explicit_start:explicit_end]
        valid_matches = [
            match
            for match in _SAMPLE_TOKEN_RE.finditer(explicit_cell)
            if _resolved_sample_token(match, sources) is not None
        ]
        split_starts = (
            [
                match.start()
                for match in valid_matches[1:]
                if match.start() > 0
                and explicit_cell[match.start() - 1] == " "
            ]
            if valid_matches and valid_matches[0].start() == 0
            else []
        )

        segment_start = 0
        for split_start in split_starts:
            raw_text = explicit_cell[segment_start:split_start]
            output.append(
                (
                    raw_text.rstrip(" "),
                    explicit_start + segment_start,
                    explicit_start + split_start,
                )
            )
            segment_start = split_start
        output.append(
            (
                explicit_cell[segment_start:],
                explicit_start + segment_start,
                explicit_end,
            )
        )

        if tab_index < 0:
            break
        explicit_start = tab_index + 1

    return output


def legend_text_to_entries(
    text: str,
    source_order: Iterable[str],
) -> tuple[list[LegendEntryConfig], list[int]]:
    """Compile free-form legend text into stable typed cells and row sizes.

    A physical newline starts a legend row and a literal Tab separates cells
    on that row.  A subsequent valid ``\\L`` token after spaces is accepted as
    an implicit separator as well.  Text without a valid sample token becomes
    a text-only item; this also keeps invalid numeric tokens visible instead
    of silently losing user-authored content.  An entirely empty editor means
    an explicit empty legend, while empty cells among other content are
    retained as spacers.
    """

    value = "" if text is None else str(text)
    if value == "":
        return [], []

    sources = [str(source) for source in source_order]
    entries: list[LegendEntryConfig] = []
    row_lengths: list[int] = []
    for line in value.split("\n"):
        cells = legend_line_cells(line, sources)
        row_lengths.append(len(cells))
        for cell, _start, _end in cells:
            source_y = ""
            label = cell
            uses_default_label = False

            resolved_match = next(
                (
                    (match, resolved)
                    for match in _SAMPLE_TOKEN_RE.finditer(cell)
                    if (resolved := _resolved_sample_token(match, sources))
                    is not None
                ),
                None,
            )
            if resolved_match is not None:
                sample_match, (source_y, source_index) = resolved_match
                label = _remove_sample_token(cell, sample_match)
                if source_index is not None:
                    default_token = f"%({source_index + 1})"
                    if label == default_token:
                        label = ""
                        uses_default_label = True

            if source_y and label == "" and not uses_default_label:
                label = _SAMPLE_ONLY_LABEL
            entries.append(LegendEntryConfig(source_y=source_y, label=label))

    if all(length == 1 for length in row_lengths):
        row_lengths = []
    return entries, row_lengths


__all__ = [
    "legend_entries_to_text",
    "legend_line_cells",
    "legend_row_ids",
    "legend_text_to_entries",
    "new_row_flags",
    "normalize_row_lengths",
]
