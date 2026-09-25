"""Qt-independent updates to graph references after sheet column edits."""

from __future__ import annotations

from .legend_layout import legend_row_ids
from .model import Graph
from .plot_config import LegendEntryConfig, PlotConfig, SeriesConfig


def remove_legend_sources(
    config: PlotConfig,
    removed_sources: set[str],
) -> None:
    if config.legend_entries is None:
        return
    row_ids = legend_row_ids(
        len(config.legend_entries),
        config.legend_row_lengths,
    )
    kept: list[tuple[int, LegendEntryConfig]] = [
        (row_id, entry)
        for row_id, entry in zip(row_ids, config.legend_entries)
        if entry.source_y not in removed_sources
    ]
    config.legend_entries = [entry for _row_id, entry in kept]

    compact_lengths: list[int] = []
    previous_row: int | None = None
    for row_id, _entry in kept:
        if row_id != previous_row:
            compact_lengths.append(1)
            previous_row = row_id
        else:
            compact_lengths[-1] += 1
    config.legend_row_lengths = (
        []
        if all(length == 1 for length in compact_lengths)
        else compact_lengths
    )


def rename_graph_column_reference(
    graph: Graph,
    old_name: str,
    new_name: str,
) -> None:
    graph.checked_y = [
        new_name if column == old_name else column
        for column in graph.checked_y
    ]
    remapped_series: dict[str, SeriesConfig] = {}
    for key, series in graph.series_by_y.items():
        source_was_renamed = key == old_name or series.y == old_name
        if series.x == old_name:
            series.x = new_name
        if series.error_column == old_name:
            series.error_column = new_name
        if source_was_renamed:
            series.y = new_name
        remapped_series[new_name if source_was_renamed else key] = series
    graph.series_by_y = remapped_series
    if graph.plot_config.legend_entries is not None:
        for entry in graph.plot_config.legend_entries:
            if entry.source_y == old_name:
                entry.source_y = new_name


def delete_graph_column_references(
    graph: Graph,
    removed_names: set[str],
) -> None:
    recipe_was_affected = any(
        column in removed_names for column in graph.checked_y
    ) or any(
        series.x in removed_names
        for key, series in graph.series_by_y.items()
        if key in graph.checked_y or series.y in graph.checked_y
    )
    graph.checked_y = [
        column
        for column in graph.checked_y
        if column not in removed_names
    ]
    kept_series: dict[str, SeriesConfig] = {}
    for key, series in graph.series_by_y.items():
        if key in removed_names or series.y in removed_names:
            continue
        if series.x in removed_names:
            series.x = ""
        if series.error_column in removed_names:
            series.error_column = ""
        kept_series[key] = series
    graph.series_by_y = kept_series
    remove_legend_sources(graph.plot_config, removed_names)
    if recipe_was_affected:
        graph.plot_config.series_color_recipe = None
