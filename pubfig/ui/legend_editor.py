"""Legend editor workflow and layout updates."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QDialog

from ..plot_config import LegendEntryConfig, SeriesConfig
from .widgets import LegendEditorDialog

if TYPE_CHECKING:
    from .main_window import GraphDrawerWindow


class LegendController(QObject):
    """Legend editor workflow and layout updates."""

    def __init__(self, window: GraphDrawerWindow) -> None:
        super().__init__(window)
        self.window = window
        self.session = window.session

    def edit_legend_text(self) -> None:
        series_configs = self.window.figure_editor.selected_series_configs()
        candidates = self.legend_editor_candidates(series_configs)
        entries = self.legend_editor_entries(candidates)
        dialog = LegendEditorDialog(
            candidates,
            entries,
            self.session.plot_config.legend_row_lengths,
            automatic=self.session.plot_config.legend_entries is None,
            parent=self.window,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        new_entries, row_lengths = dialog.result_config()
        changed = self.apply_legend_entry_config(
            new_entries,
            row_lengths,
            series_configs,
        )
        if not changed:
            self.window.set_status("Legend was not changed.")
            return
        self.window.workspace.update_undo_baseline()
        self.window.preview.render_plot()
        self.window.set_status("Updated free-form legend content.")

    def legend_editor_candidates(
        self,
        series_configs: list[SeriesConfig],
    ) -> list[tuple[str, str, bool]]:
        left = [series for series in series_configs if series.y_axis != "right"]
        right = [series for series in series_configs if series.y_axis == "right"]
        return [
            (
                series.y,
                series.label or self.window.table_editor._column_name(series.y),
                series.show_in_legend,
            )
            for series in left + right
        ]

    def legend_editor_entries(
        self,
        candidates: list[tuple[str, str, bool]],
    ) -> list[LegendEntryConfig]:
        if self.session.plot_config.legend_entries is None:
            return [
                LegendEntryConfig(source_y=source_y, label=label)
                for source_y, label, visible in candidates
                if visible
            ]
        return deepcopy(self.session.plot_config.legend_entries)

    def apply_legend_entry_config(
        self,
        entries: list[LegendEntryConfig] | None,
        row_lengths: list[int],
        series_configs: list[SeriesConfig],
    ) -> bool:
        normalized_entries = (
            None
            if entries is None
            else [
                replace(
                    entry,
                    source_y=str(entry.source_y),
                    label=str(entry.label),
                )
                for entry in entries
            ]
        )
        clean_row_lengths = [max(0, int(length)) for length in row_lengths]
        if clean_row_lengths and all(length == 1 for length in clean_row_lengths):
            clean_row_lengths = []

        if (
            normalized_entries == self.session.plot_config.legend_entries
            and clean_row_lengths == self.session.plot_config.legend_row_lengths
        ):
            return False

        self.window.workspace.push_current_undo_state()
        self.session.plot_config.legend_entries = normalized_entries
        self.session.plot_config.legend_row_lengths = clean_row_lengths
        self.window.series_editor.update_style_targets()
        return True
