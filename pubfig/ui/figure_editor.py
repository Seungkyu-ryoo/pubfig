"""Figure settings, physical dimensions, and aspect-ratio controls."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QWidget

from ..plot_config import PlotConfig, SeriesConfig, apply_preset, default_series
from ..style_colors import sample_series_color_recipe
from ..theme import style_color_button
from .binding import signals_blocked
from .figure_settings import PLOT_RATIO_PRESETS

if TYPE_CHECKING:
    from .main_window import GraphDrawerWindow


class FigureController(QObject):
    """Figure settings, physical dimensions, and aspect-ratio controls."""

    def __init__(self, window: GraphDrawerWindow) -> None:
        super().__init__(window)
        self.window = window
        self.session = window.session
        self.updating_plot_ratio = False

    def handle_figure_setting_changed(self, source: QWidget) -> None:
        """Apply one figure edit, including any dependent widget changes.

        Qt emits after the source widget has changed. The undo manager keeps a
        pre-signal baseline, so every dependent update must finish inside the
        same change callback before the new baseline is captured.
        """

        def apply_change() -> None:
            if source is self.window.figure_settings.preset_combo:
                self.handle_preset_changed(
                    self.window.figure_settings.preset_combo.currentText()
                )
            elif source is self.window.figure_settings.plot_width_spin:
                self.handle_plot_dimension_changed("width")
            elif source is self.window.figure_settings.plot_height_spin:
                self.handle_plot_dimension_changed("height")
            elif source is self.window.figure_settings.plot_ratio_lock_check:
                self.handle_plot_ratio_lock_toggled(
                    self.window.figure_settings.plot_ratio_lock_check.isChecked()
                )
            elif source is self.window.figure_settings.plot_ratio_preset_combo:
                self.handle_plot_ratio_preset_changed(
                    self.window.figure_settings.plot_ratio_preset_combo.currentText()
                )
            self.window.preview.schedule_render()

        self.window.workspace.handle_undoable_widget_change(apply_change, source)

    def update_axis_control_states(self, *args) -> None:
        right_axis_needed = any(
            self.session.series_by_y.get(column) is not None
            and self.session.series_by_y[column].y_axis == "right"
            for column in self.window.table_editor.checked_y_columns()
        )
        for widget in (
            self.window.figure_settings.y2_label_edit,
            self.window.figure_settings.y2_tick_pad_spin,
            self.window.figure_settings.y2_axis_color_btn,
            self.window.figure_settings.y2_scale_combo,
            self.window.figure_settings.y2_scale_divisor_edit,
            self.window.figure_settings.y2_min_edit,
            self.window.figure_settings.y2_max_edit,
            self.window.figure_settings.y2_tick_interval_edit,
            self.window.figure_settings.y2_tick_decimals_spin,
            self.window.figure_settings.y2_tick_notation_combo,
            self.window.figure_settings.y2_minor_divisions_spin,
            self.window.figure_settings.show_y2_tick_labels_check,
        ):
            widget.setEnabled(right_axis_needed)

        x_break_compatible = (
            self.window.figure_settings.x_scale_combo.currentText() == "linear"
            and not right_axis_needed
            and not self.window.figure_settings.y_break_check.isChecked()
        )
        y_break_compatible = (
            self.window.figure_settings.y_scale_combo.currentText() == "linear"
            and not right_axis_needed
            and not self.window.figure_settings.x_break_check.isChecked()
        )
        for widget in (
            self.window.figure_settings.x_break_left_min_edit,
            self.window.figure_settings.x_break_left_max_edit,
            self.window.figure_settings.x_break_right_min_edit,
            self.window.figure_settings.x_break_right_max_edit,
            self.window.figure_settings.x_break_gap_spin,
        ):
            widget.setEnabled(
                self.window.figure_settings.x_break_check.isChecked()
                and x_break_compatible
            )
        for widget in (
            self.window.figure_settings.y_break_lower_min_edit,
            self.window.figure_settings.y_break_lower_max_edit,
            self.window.figure_settings.y_break_upper_min_edit,
            self.window.figure_settings.y_break_upper_max_edit,
            self.window.figure_settings.y_break_gap_spin,
        ):
            widget.setEnabled(
                self.window.figure_settings.y_break_check.isChecked()
                and y_break_compatible
            )

        self.window.figure_settings.x_break_check.setToolTip(
            ""
            if x_break_compatible
            else "Broken X requires linear X, no Y2 series, and no broken Y axis."
        )
        self.window.figure_settings.y_break_check.setToolTip(
            ""
            if y_break_compatible
            else "Broken Y requires linear Y, no Y2 series, and no broken X axis."
        )

    def choose_y2_axis_color(self) -> None:
        current = self.window.figure_settings.y2_axis_color_btn.text()
        color = QColorDialog.getColor(
            QColor(current), self.window, "Choose Y2 axis color"
        )
        if not color.isValid():
            return
        self.window.workspace.push_current_undo_state()
        self._set_y2_axis_color_button(color.name())
        self.window.preview.schedule_render()
        self.window.workspace.update_undo_baseline()

    def choose_y_axis_color(self) -> None:
        current = self.window.figure_settings.y_axis_color_btn.text()
        color = QColorDialog.getColor(
            QColor(current), self.window, "Choose Y1 axis color"
        )
        if not color.isValid():
            return
        self.window.workspace.push_current_undo_state()
        self._set_y_axis_color_button(color.name())
        self.window.preview.schedule_render()
        self.window.workspace.update_undo_baseline()

    def _set_y2_axis_color_button(self, color: str) -> None:
        self.session.plot_config.y2_axis_color = color
        style_color_button(self.window.figure_settings.y2_axis_color_btn, color)

    def _set_y_axis_color_button(self, color: str) -> None:
        self.session.plot_config.y_axis_color = color
        style_color_button(self.window.figure_settings.y_axis_color_btn, color)

    def handle_preset_changed(self, preset_name: str) -> None:
        if preset_name == "Custom":
            return
        apply_preset(self.session.plot_config, preset_name)
        widgets = (
            self.window.figure_settings.width_spin,
            self.window.figure_settings.height_spin,
            self.window.figure_settings.plot_width_spin,
            self.window.figure_settings.plot_height_spin,
            self.window.figure_settings.plot_ratio_lock_check,
            self.window.figure_settings.plot_ratio_preset_combo,
            self.window.figure_settings.dpi_spin,
            self.window.figure_settings.title_size_spin,
            self.window.figure_settings.axis_size_spin,
            self.window.figure_settings.tick_size_spin,
            self.window.figure_settings.legend_size_spin,
            self.window.figure_settings.x_label_offset_spin,
            self.window.figure_settings.y_label_offset_spin,
            self.window.figure_settings.x_tick_pad_spin,
            self.window.figure_settings.y_tick_pad_spin,
            self.window.figure_settings.y2_tick_pad_spin,
        )
        with signals_blocked(*widgets):
            self.window.figure_settings.width_spin.setValue(
                self.session.plot_config.width_mm
            )
            self.window.figure_settings.height_spin.setValue(
                self.session.plot_config.height_mm
            )
            self.window.figure_settings.plot_width_spin.setValue(
                self.session.plot_config.plot_width_mm
            )
            self.window.figure_settings.plot_height_spin.setValue(
                self.session.plot_config.plot_height_mm
            )
            self.window.figure_settings.plot_ratio_lock_check.setChecked(
                self.session.plot_config.plot_ratio_locked
            )
            self.window.figure_settings.plot_ratio_preset_combo.setCurrentText(
                self.session.plot_config.plot_ratio_preset
                if self.session.plot_config.plot_ratio_preset in PLOT_RATIO_PRESETS
                else "Current"
            )
            self.window.figure_settings.dpi_spin.setValue(self.session.plot_config.dpi)
            self.window.figure_settings.title_size_spin.setValue(
                self.session.plot_config.title_size
            )
            self.window.figure_settings.axis_size_spin.setValue(
                self.session.plot_config.axis_size
            )
            self.window.figure_settings.tick_size_spin.setValue(
                self.session.plot_config.tick_size
            )
            self.window.figure_settings.legend_size_spin.setValue(
                self.session.plot_config.legend_size
            )
            self.window.figure_settings.x_label_offset_spin.setValue(
                self.session.plot_config.x_label_offset_mm
            )
            self.window.figure_settings.y_label_offset_spin.setValue(
                self.session.plot_config.y_label_offset_mm
            )
            self.window.figure_settings.x_tick_pad_spin.setValue(
                self.session.plot_config.x_tick_pad
            )
            self.window.figure_settings.y_tick_pad_spin.setValue(
                self.session.plot_config.y_tick_pad
            )
            self.window.figure_settings.y2_tick_pad_spin.setValue(
                self.session.plot_config.y2_tick_pad
            )

    def handle_plot_ratio_lock_toggled(self, checked: bool) -> None:
        if checked:
            self.session.plot_config.plot_aspect_ratio = self.current_plot_ratio()

    def handle_plot_ratio_preset_changed(self, preset_name: str) -> None:
        ratio = self.plot_ratio_from_preset(preset_name)
        if ratio is None:
            self.session.plot_config.plot_aspect_ratio = self.current_plot_ratio()
            return
        self.session.plot_config.plot_aspect_ratio = ratio
        self.apply_plot_ratio_from_width(ratio)

    def handle_plot_dimension_changed(self, changed: str) -> None:
        if (
            self.updating_plot_ratio
            or not self.window.figure_settings.plot_ratio_lock_check.isChecked()
        ):
            return
        ratio = self.active_plot_ratio()
        if ratio <= 0:
            return
        if changed == "width":
            self.apply_plot_ratio_from_width(ratio)
        else:
            self.apply_plot_ratio_from_height(ratio)

    def active_plot_ratio(self) -> float:
        ratio = self.plot_ratio_from_preset(
            self.window.figure_settings.plot_ratio_preset_combo.currentText()
        )
        if ratio is None:
            ratio = (
                self.session.plot_config.plot_aspect_ratio or self.current_plot_ratio()
            )
        return max(ratio, 1e-9)

    def current_plot_ratio(self) -> float:
        height = max(self.window.figure_settings.plot_height_spin.value(), 1e-9)
        return max(self.window.figure_settings.plot_width_spin.value() / height, 1e-9)

    def plot_ratio_from_preset(self, preset_name: str) -> float | None:
        return PLOT_RATIO_PRESETS.get(preset_name)

    def apply_plot_ratio_from_width(self, ratio: float) -> None:
        previous = self.updating_plot_ratio
        self.updating_plot_ratio = True
        try:
            with signals_blocked(self.window.figure_settings.plot_height_spin):
                self.window.figure_settings.plot_height_spin.setValue(
                    max(
                        self.window.figure_settings.plot_height_spin.minimum(),
                        min(
                            self.window.figure_settings.plot_height_spin.maximum(),
                            self.window.figure_settings.plot_width_spin.value() / ratio,
                        ),
                    )
                )
        finally:
            self.updating_plot_ratio = previous

    def apply_plot_ratio_from_height(self, ratio: float) -> None:
        previous = self.updating_plot_ratio
        self.updating_plot_ratio = True
        try:
            with signals_blocked(self.window.figure_settings.plot_width_spin):
                self.window.figure_settings.plot_width_spin.setValue(
                    max(
                        self.window.figure_settings.plot_width_spin.minimum(),
                        min(
                            self.window.figure_settings.plot_width_spin.maximum(),
                            self.window.figure_settings.plot_height_spin.value()
                            * ratio,
                        ),
                    )
                )
        finally:
            self.updating_plot_ratio = previous

    def collect_plot_config(self) -> PlotConfig:
        self.window.figure_settings.update(self.session.plot_config)
        self.session.plot_config.plot_aspect_ratio = (
            self.active_plot_ratio()
            if self.session.plot_config.plot_ratio_locked
            else self.current_plot_ratio()
        )
        self.session.plot_config.trim_whitespace = self.window.trim_check.isChecked()
        self.session.plot_config.transparent = self.window.transparent_check.isChecked()
        return self.session.plot_config

    def selected_series_configs(self) -> list[SeriesConfig]:
        series_configs: list[SeriesConfig] = []
        for idx, y_column in enumerate(self.window.table_editor.checked_y_columns()):
            x_column = self.window.table_editor._nearest_left_x(y_column)
            if not x_column:
                continue
            series = self.session.series_by_y.get(y_column) or default_series(
                x_column, y_column, idx
            )
            series.x = x_column
            series.y = y_column
            series.label = self.window.table_editor._column_name(y_column)
            series_configs.append(series)
        return series_configs

    def _load_config_into_widgets(self) -> None:
        self.window.figure_settings.load(self.session.plot_config)
        with signals_blocked(self.window.trim_check, self.window.transparent_check):
            self.window.trim_check.setChecked(self.session.plot_config.trim_whitespace)
            self.window.transparent_check.setChecked(
                self.session.plot_config.transparent
            )
        recipe = self.session.plot_config.series_color_recipe
        if (
            recipe is not None
            and recipe.kind == "matplotlib_colormap"
            and sample_series_color_recipe(recipe, 1)
        ):
            index = self.window.series_settings.cmap_combo.findText(recipe.name)
            if index >= 0:
                with signals_blocked(
                    self.window.series_settings.cmap_combo,
                    self.window.series_settings.cmap_start_spin,
                    self.window.series_settings.cmap_end_spin,
                ):
                    self.window.series_settings.cmap_combo.setCurrentIndex(index)
                    self.window.series_settings.cmap_start_spin.setValue(
                        float(recipe.start)
                    )
                    self.window.series_settings.cmap_end_spin.setValue(
                        float(recipe.end)
                    )
        self.update_axis_control_states()
