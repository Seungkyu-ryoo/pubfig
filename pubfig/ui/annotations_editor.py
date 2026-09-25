"""Annotation editing, clipboard, and special-character insertion."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QColorDialog, QLineEdit, QWidget

from ..annotation_geometry import annotation_display_geometry
from ..plot_config import AnnotationConfig, annotation_config_from_payload
from ..sheet_data import NAME_ROW

if TYPE_CHECKING:
    from .main_window import GraphDrawerWindow


class AnnotationController(QObject):
    """Annotation editing, clipboard, and special-character insertion."""

    def __init__(self, window: GraphDrawerWindow) -> None:
        super().__init__(window)
        self.window = window
        self.session = window.session
        self.annotation_clipboard: dict | None = None

        self.special_text_target: QWidget | None = None

    def delete_selected_annotation_from_key(self) -> bool:
        if self.window.focus_widget_uses_text_shortcuts():
            return False
        if not self.selected_annotation_indices():
            return False
        self.remove_selected_annotation()
        return True

    def insert_special_character(self, value: str) -> None:
        focus = QApplication.focusWidget()
        if focus in self.window.special_text_fields():
            target = focus
        else:
            if self.insert_special_character_into_table_name(value, focus):
                return
            target = self.special_text_target
        if target is None:
            self.window.set_status(
                "Select an annotation, label, title, series text field, or column Name cell first."
            )
            return
        target.insert(value)
        target.setFocus()
        self.special_text_target = target

    def insert_special_character_into_table_name(
        self, value: str, focus: QWidget | None
    ) -> bool:
        if isinstance(focus, QLineEdit) and self.widget_has_ancestor(
            focus, self.window.table
        ):
            focus.insert(value)
            return True
        item = self.window.table.currentItem()
        if item is not None and item.row() == NAME_ROW:
            item.setText(item.text() + value)
            self.window.table.setFocus()
            return True
        return False

    def widget_has_ancestor(self, widget: QWidget | None, ancestor: QWidget) -> bool:
        while widget is not None:
            if widget is ancestor:
                return True
            widget = widget.parentWidget()
        return False

    def choose_annotation_color(self) -> None:
        current = self.window.annotation_settings.color_button.text()
        color = QColorDialog.getColor(
            QColor(current), self.window, "Choose annotation color"
        )
        if not color.isValid():
            return
        self.window.workspace.push_current_undo_state()
        self._set_annotation_color_button(color.name())
        self.update_selected_annotation()
        self.window.workspace.update_undo_baseline()

    def add_annotation(self) -> None:
        self.window.preview.render_timer.stop()
        self.window.workspace.push_current_undo_state()
        x, y, x2, y2, width, height = self.default_annotation_geometry()
        annotation = AnnotationConfig(
            kind=self.window.annotation_settings.kind_combo.currentText(),
            text=self.window.annotation_settings.text_edit.text(),
            x=x,
            y=y,
            x2=x2,
            y2=y2,
            width=width,
            height=height,
            angle=self.window.annotation_settings.angle_spin.value(),
            color=self.window.annotation_settings.color_button.text(),
            line_style=self.window.annotation_settings.line_style_combo.currentText(),
            alpha=self.window.annotation_settings.alpha_spin.value(),
            fill=self.window.annotation_settings.fill_check.isChecked(),
            font_size=self.window.annotation_settings.font_spin.value(),
            arrow_head_size=self.window.annotation_settings.arrow_head_spin.value(),
        )
        self.session.annotations.append(annotation)
        self.refresh_annotation_list()
        self.window.annotation_settings.list_widget.setCurrentRow(
            len(self.session.annotations) - 1
        )
        self.window.preview.render_plot()
        self.window.workspace.update_undo_baseline()
        self.window.set_status(f"Added {annotation.kind} annotation.")

    def default_annotation_geometry(
        self,
    ) -> tuple[float, float, float, float, float, float]:
        values = (
            self.window.annotation_settings.x_spin.value(),
            self.window.annotation_settings.y_spin.value(),
            self.window.annotation_settings.x2_spin.value(),
            self.window.annotation_settings.y2_spin.value(),
            self.window.annotation_settings.width_spin.value(),
            self.window.annotation_settings.height_spin.value(),
        )
        if any(abs(value) > 1e-12 for value in values[:2]):
            x, y, _, _, width, height = values
            return x, y, x + width, y + height, width, height

        kind = self.window.annotation_settings.kind_combo.currentText()
        if kind in {"line", "arrow"}:
            x, y, width, height = 0.35, 0.72, 0.18, -0.18
        elif kind in {"box", "circle"}:
            x, y, width, height = 0.38, 0.56, 0.16, 0.16
        else:
            x, y = 0.42, 0.68
            width = self.window.annotation_settings.width_spin.value()
            height = self.window.annotation_settings.height_spin.value()
        return x, y, x + width, y + height, width, height

    def _annotation_display_geometry(
        self, annotation: AnnotationConfig
    ) -> tuple[float, float, float, float]:
        ax = self.annotation_axes()
        if ax is None:
            return 0.0, 0.0, 0.0, 0.0
        return annotation_display_geometry(ax, annotation)

    def annotation_axes(self):
        if (
            self.window.preview.current_figure is None
            or not self.window.preview.current_figure.axes
        ):
            return None
        if self.current_broken_y_active():
            return self.window.preview.current_figure.axes[1]
        return self.window.preview.current_figure.axes[0]

    def current_broken_y_active(self) -> bool:
        if (
            self.window.preview.current_figure is None
            or len(self.window.preview.current_figure.axes) < 2
        ):
            return False
        if (
            not self.session.plot_config.y_break_enabled
            or self.session.plot_config.y_scale != "linear"
        ):
            return False
        if any(
            series.y_axis == "right"
            for series in self.window.figure_editor.selected_series_configs()
        ):
            return False
        values = (
            self.session.plot_config.y_break_lower_min,
            self.session.plot_config.y_break_lower_max,
            self.session.plot_config.y_break_upper_min,
            self.session.plot_config.y_break_upper_max,
        )
        if any(value is None for value in values):
            return False
        lower_min, lower_max, upper_min, upper_max = values
        return lower_min < lower_max < upper_min < upper_max

    def legend_axes(self):
        if (
            self.window.preview.current_figure is None
            or not self.window.preview.current_figure.axes
        ):
            return None
        return self.window.preview.current_figure.axes[0]

    def remove_selected_annotation(self) -> None:
        rows = self.selected_annotation_indices()
        if not rows:
            return
        self.window.workspace.push_current_undo_state()
        removed_count = len(rows)
        for row in sorted(rows, reverse=True):
            self.session.annotations.pop(row)
        self.refresh_annotation_list()
        if self.session.annotations:
            self.window.annotation_settings.list_widget.setCurrentRow(
                min(rows[0], len(self.session.annotations) - 1)
            )
        self.window.preview.render_plot()
        self.window.workspace.update_undo_baseline()
        self.window.set_status(f"Removed {removed_count} annotation(s).")

    def copy_selected_annotation(self) -> bool:
        row = self.window.annotation_settings.list_widget.currentRow()
        if row < 0 or row >= len(self.session.annotations):
            return False
        self.annotation_clipboard = asdict(self.session.annotations[row])
        QApplication.clipboard().setText(
            "GRAPH_DRAWER_ANNOTATION\t" + json.dumps(self.annotation_clipboard)
        )
        self.window.set_status("Copied annotation.")
        return True

    def paste_annotation(self) -> bool:
        payload = self.annotation_clipboard
        text = QApplication.clipboard().text()
        if text.startswith("GRAPH_DRAWER_ANNOTATION\t"):
            try:
                payload = json.loads(text.split("\t", 1)[1])
            except json.JSONDecodeError:
                payload = self.annotation_clipboard
        if not payload:
            return False
        self.window.workspace.push_current_undo_state()
        annotation = annotation_config_from_payload(payload)
        annotation.x += 0.03
        annotation.y -= 0.03
        annotation.x2 = annotation.x + annotation.width
        annotation.y2 = annotation.y + annotation.height
        self.session.annotations.append(annotation)
        self.refresh_annotation_list()
        self.window.annotation_settings.list_widget.setCurrentRow(
            len(self.session.annotations) - 1
        )
        self.window.preview.render_plot()
        self.window.workspace.update_undo_baseline()
        self.window.set_status("Pasted annotation.")
        return True

    def refresh_annotation_list(self) -> None:
        self.window.annotation_settings.set_annotations(self.session.annotations)

    def update_annotation_list_item(self, row: int) -> None:
        self.window.annotation_settings.update_annotation_list_item(row)

    def selected_annotation_indices(self) -> list[int]:
        return self.window.annotation_settings.selected_annotation_indices()

    def update_selected_annotation(self) -> None:
        if self.window.annotation_settings.loading_annotation_inputs:
            return
        row = self.window.annotation_settings.list_widget.currentRow()
        if row < 0 or row >= len(self.session.annotations):
            return
        annotation = self.session.annotations[row]
        self.window.annotation_settings.update_annotation(annotation)
        self.window.preview.schedule_render()

    def _set_annotation_color_button(self, color: str) -> None:
        self.window.annotation_settings.set_color(color)

    def update_annotation_inputs(self, index: int) -> None:
        if index < 0 or index >= len(self.session.annotations):
            return
        self.window.annotation_settings.load_annotation(self.session.annotations[index])
        self.window.interaction.draw_annotation_handles()
