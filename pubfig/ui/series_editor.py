"""Series selection and visual style editing."""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QListWidgetItem, QTableWidgetItem

from ..plot_config import (
    RECOMMENDED_PALETTES,
    SeriesColorRecipe,
    SeriesConfig,
    default_series,
)
from ..sheet_data import NAME_ROW
from ..style_colors import sample_series_color_recipe, series_color_recipe_matches

if TYPE_CHECKING:
    from .main_window import GraphDrawerWindow


class SeriesController(QObject):
    """Series selection and visual style editing."""

    def __init__(self, window: GraphDrawerWindow) -> None:
        super().__init__(window)
        self.window = window
        self.session = window.session

    def handle_plot_y_changed(self, *_args) -> None:
        if not self.session.loading_project_figure:
            self.window.workspace._reset_undo_coalescing()
            self.window.workspace.push_undo_baseline()
        self.window.table_editor.refresh_series_configs()
        self._discard_invalid_series_color_recipe()
        self.update_style_targets()
        self.refresh_cmap_column_list()
        self.window.figure_editor.update_axis_control_states()
        if not self.session.loading_project_figure:
            self.window.workspace.update_undo_baseline()

    def handle_plot_y_clicked(self, item: QListWidgetItem) -> None:
        if item is None:
            return
        QTimer.singleShot(
            0, lambda column=item.text(): self.select_series_for_y_column(column)
        )

    def select_series_for_y_column(self, y_column: str) -> None:
        if not y_column or y_column not in self.window.table_editor.checked_y_columns():
            self.window.set_status("Check a Y column to plot and edit its series.")
            return
        if y_column not in self.session.series_by_y:
            x_column = self.window.table_editor._nearest_left_x(y_column)
            if not x_column:
                return
            self.session.series_by_y[y_column] = default_series(
                x_column, y_column, len(self.session.series_by_y)
            )
        target_changed = (
            self.window.series_settings.target_combo.currentText() != y_column
        )
        self.window.series_settings.target_combo.setCurrentText(y_column)
        if not target_changed:
            self._load_series_into_widgets(self.session.series_by_y[y_column])
        self.window.set_status(f"Editing series: {y_column}")

    def update_style_targets(self) -> None:
        current = self.window.series_settings.target_combo.currentText()
        plotted = self.window.table_editor.checked_y_columns()
        target = self.window.series_settings.set_targets(plotted, current)
        if target and target in self.session.series_by_y:
            self._load_series_into_widgets(self.session.series_by_y[target])

    def handle_style_target_changed(self) -> None:
        target = self.window.series_settings.target_combo.currentText()
        if target and target in self.session.series_by_y:
            self._load_series_into_widgets(self.session.series_by_y[target])

    def _load_series_into_widgets(self, series: SeriesConfig) -> None:
        self.window.series_settings.load_series(
            series,
            error_columns=map(str, self.session.df.columns),
        )
        self.window.series_settings.set_linear_fit_result(
            self.window.preview.linear_fit_results_by_y.get(series.y),
            enabled=series.linear_fit_enabled,
        )

    def _refresh_error_column_combo(self) -> None:
        self.window.series_settings.set_error_columns(map(str, self.session.df.columns))

    def apply_series_widget_state(self) -> None:
        target = self.window.series_settings.target_combo.currentText()
        if not target or target not in self.session.series_by_y:
            return
        self.window.table.blockSignals(True)
        series = self.session.series_by_y[target]
        self.window.series_settings.update_series(series)
        self._discard_invalid_series_color_recipe()
        label = series.label
        col = list(map(str, self.session.df.columns)).index(target)
        name_item = self.window.table.item(NAME_ROW, col)
        if name_item is None:
            name_item = QTableWidgetItem("")
            self.window.table.setItem(NAME_ROW, col, name_item)
        name_item.setText(label)
        self.session.df.iat[NAME_ROW, col] = label
        if self.session.active_sheet_id in self.session.sheets:
            self.session.sheets[self.session.active_sheet_id].df = self.session.df
            self.window.table_editor._sync_sheet_series_labels(
                self.session.active_sheet_id, {target}
            )
        self.window.table.blockSignals(False)
        self.window.figure_editor.update_axis_control_states()
        if (
            series.linear_fit_enabled
            and not self.window.series_settings.linear_fit_bounds_are_valid()
        ):
            self.window.preview.render_timer.stop()
            self.window.preview.update_linear_fit_result_display()
            self.window.set_status(
                "Linear-fit X bounds must be finite numbers or blank."
            )
            return
        self.window.preview.schedule_render()

    def choose_series_color(self) -> None:
        target = self.window.series_settings.target_combo.currentText()
        if not target or target not in self.session.series_by_y:
            return
        current = self.session.series_by_y[target].color
        color = QColorDialog.getColor(
            QColor(current), self.window, "Choose series color"
        )
        if not color.isValid():
            return
        self.window.workspace.push_current_undo_state()
        color_name = color.name()
        self.session.series_by_y[target].color = color_name
        self.session.series_by_y[target].force_opaque = False
        self.session.plot_config.series_color_recipe = None
        self._set_color_button(color_name)
        self.window.preview.schedule_render()
        self.window.workspace.update_undo_baseline()

    def apply_colormap_to_plotted_series(self) -> None:
        selected = self.selected_cmap_columns()
        if not selected:
            self.window.set_status("Select at least one series in the column list.")
            return
        self.window.workspace.push_current_undo_state()
        count = len(selected)
        target = self.window.series_settings.target_combo.currentText()
        if self.window.series_settings.cmap_alpha_only_check.isChecked():
            self.session.plot_config.series_color_recipe = None
            base_color = self.window.series_settings.cmap_base_color_button.text()
            a_start = self.window.series_settings.cmap_alpha_start_spin.value()
            a_end = self.window.series_settings.cmap_alpha_end_spin.value()
            dist = (
                self.window.series_settings.cmap_alpha_distribution_combo.currentText()
            )
            for idx, y_column in enumerate(selected):
                t = 0.5 if count == 1 else idx / (count - 1)
                alpha_val = self._alpha_dist(t, a_start, a_end, dist)
                series = self.session.series_by_y.get(y_column)
                if series is None:
                    x_column = self.window.table_editor._nearest_left_x(y_column)
                    series = default_series(x_column, y_column, idx)
                    self.session.series_by_y[y_column] = series
                series.color = base_color
                series.alpha = round(alpha_val, 4)
                series.force_opaque = False
            if target in self.session.series_by_y:
                self._load_series_into_widgets(self.session.series_by_y[target])
            self.window.set_status(
                f"Applied alpha ({dist}, {a_start:.2f}–{a_end:.2f}) to {count} series."
            )
        else:
            cmap_name = self.window.series_settings.cmap_combo.currentText()
            start = self.window.series_settings.cmap_start_spin.value()
            end = self.window.series_settings.cmap_end_spin.value()
            recipe = SeriesColorRecipe(
                name=cmap_name,
                start=start,
                end=end,
                series_count=count,
            )
            colors = sample_series_color_recipe(recipe, count)
            for idx, (y_column, color) in enumerate(zip(selected, colors)):
                series = self.session.series_by_y.get(y_column)
                if series is None:
                    x_column = self.window.table_editor._nearest_left_x(y_column)
                    series = default_series(x_column, y_column, idx)
                    self.session.series_by_y[y_column] = series
                series.color = color
                series.alpha = 1.0
                series.force_opaque = True
            self.session.plot_config.series_color_recipe = (
                recipe
                if selected == self.window.table_editor.checked_y_columns()
                else None
            )
            if target in self.session.series_by_y:
                self._load_series_into_widgets(self.session.series_by_y[target])
            self.window.set_status(f"Applied {cmap_name} colormap to {count} series.")
        self.window.preview.schedule_render()
        self.window.workspace.update_undo_baseline()

    def apply_custom_gradient_to_series(self) -> None:
        selected = self.selected_cmap_columns()
        if not selected:
            self.window.set_status("Select at least one series in the column list.")
            return
        self.window.workspace.push_current_undo_state()
        self.session.plot_config.series_color_recipe = None
        count = len(selected)
        for idx, y_column in enumerate(selected):
            t = 0.5 if count == 1 else idx / (count - 1)
            color, alpha = self.window.series_settings.gradient_editor.sample(t)
            series = self.session.series_by_y.get(y_column)
            if series is None:
                x_column = self.window.table_editor._nearest_left_x(y_column)
                series = default_series(x_column, y_column, idx)
                self.session.series_by_y[y_column] = series
            series.color = color
            series.alpha = round(alpha, 4)
            series.force_opaque = False
        target = self.window.series_settings.target_combo.currentText()
        if target in self.session.series_by_y:
            self._load_series_into_widgets(self.session.series_by_y[target])
        self.window.preview.schedule_render()
        self.window.workspace.update_undo_baseline()
        self.window.set_status(
            f"Applied custom gradient ({len(self.window.series_settings.gradient_editor.stops)} stops) to {count} series."
        )

    def _save_custom_gradient(self) -> None:
        self.window.settings.setValue(
            "custom_gradient",
            json.dumps(self.window.series_settings.gradient_editor.stops_payload()),
        )

    def apply_recommended_palette_to_series(self) -> None:
        selected = self.selected_cmap_columns()
        if not selected:
            self.window.set_status("Select at least one series in the column list.")
            return
        palette_name = self.window.series_settings.palette_combo.currentText()
        colors = self.recommended_colors(palette_name, len(selected))
        if not colors:
            self.window.set_status("Choose a recommended palette first.")
            return
        self.window.workspace.push_current_undo_state()
        self.session.plot_config.series_color_recipe = None
        for idx, y_column in enumerate(selected):
            series = self.session.series_by_y.get(y_column)
            if series is None:
                x_column = self.window.table_editor._nearest_left_x(y_column)
                series = default_series(x_column, y_column, idx)
                self.session.series_by_y[y_column] = series
            series.color = colors[idx]
            series.alpha = 1.0
            series.force_opaque = True
        target = self.window.series_settings.target_combo.currentText()
        if target in self.session.series_by_y:
            self._load_series_into_widgets(self.session.series_by_y[target])
        self.window.preview.schedule_render()
        self.window.workspace.update_undo_baseline()
        self.window.set_status(
            f"Applied {palette_name} palette to {len(selected)} series."
        )

    def selected_cmap_columns(self) -> list[str]:
        return self.window.series_settings.selected_cmap_columns()

    def recommended_colors(self, palette_name: str, count: int) -> list[str]:
        palette = RECOMMENDED_PALETTES.get(palette_name, [])
        if count <= 0 or not palette:
            return []
        if count <= len(palette):
            return palette[:count]
        return [palette[idx % len(palette)] for idx in range(count)]

    @staticmethod
    def _alpha_dist(t: float, a_start: float, a_end: float, dist: str) -> float:
        """Map t∈[0,1] to an alpha value using the chosen distribution."""
        if dist == "Sqrt":
            # faster start, slower end — bigger gaps near a_start
            mapped = math.sqrt(t)
        elif dist == "Power²":
            # slower start, faster end — bigger gaps near a_end
            mapped = t**2
        elif dist == "Log":
            # logarithmic: rapid climb, then plateaus — similar to sqrt but more extreme
            mapped = math.log(1 + t * 9) / math.log(10)
        elif dist == "Geometric":
            # equal ratio between consecutive values — perceptually most uniform
            # avoids the "0.8 and 1.0 look the same" problem
            if a_start <= 0 or a_end <= 0:
                mapped = t
            else:
                mapped = (a_end / a_start) ** t
                return float(a_start * mapped)
        else:
            mapped = t  # Linear
        return float(a_start + (a_end - a_start) * mapped)

    def refresh_cmap_column_list(self) -> None:
        self.window.series_settings.set_cmap_columns(
            self.window.table_editor.checked_y_columns()
        )

    def _discard_invalid_series_color_recipe(self) -> None:
        recipe = self.session.plot_config.series_color_recipe
        if recipe is not None and not series_color_recipe_matches(
            recipe,
            [
                self.session.series_by_y[y_column]
                for y_column in self.window.table_editor.checked_y_columns()
                if y_column in self.session.series_by_y
            ],
        ):
            self.session.plot_config.series_color_recipe = None

    def choose_cmap_base_color(self) -> None:
        current = self.window.series_settings.cmap_base_color_button.text()
        color = QColorDialog.getColor(QColor(current), self.window, "Choose base color")
        if not color.isValid():
            return
        self._set_cmap_base_color_button(color.name())

    def _set_cmap_base_color_button(self, color: str) -> None:
        self.window.series_settings.set_cmap_base_color(color)

    def _set_color_button(self, color: str) -> None:
        self.window.series_settings.set_color(color)
