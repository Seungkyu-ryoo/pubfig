"""Figure settings panel with one authoritative widget/config binding list."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import replace
from sys import float_info
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..plot_config import PRESETS, TICK_NOTATIONS, PlotConfig, axis_tick_notation
from ..theme import style_color_button
from .binding import BindingRegistry, FieldBinding, change_signal, optional_float


PLOT_RATIO_PRESETS: dict[str, float | None] = {
    "Current": None,
    "1:1": 1.0,
    "4:3": 4 / 3,
    "3:4": 3 / 4,
    "3:2": 3 / 2,
    "2:3": 2 / 3,
    "16:9": 16 / 9,
    "9:16": 9 / 16,
    "2:1": 2.0,
    "1:2": 0.5,
    "Golden 1.618:1": 1.618,
    "Golden 1:1.618": 1 / 1.618,
    "A-series sqrt2:1": math.sqrt(2),
    "A-series 1:sqrt2": 1 / math.sqrt(2),
}


_optional_float = optional_float


def _positive_float(edit: QLineEdit) -> float:
    value = _optional_float(edit)
    if value is not None and value > 0:
        edit.setProperty("last_valid_divisor", value)
        return value
    # Incomplete input such as '1e' must not reset an existing scale to 1.
    return float(edit.property("last_valid_divisor") or 1.0)


def _set_divisor(edit: QLineEdit, value: float) -> None:
    # Reset the fallback when switching graphs or restoring undo history.
    edit.setProperty("last_valid_divisor", value if math.isfinite(value) and value > 0 else 1.0)
    edit.setText(str(value))


def _optional_text(value: float | None) -> str:
    return "" if value is None else str(value)


class FigureSettingsPanel(QGroupBox):
    """Own all figure controls and synchronize them through one registry."""

    changed = Signal(object)

    def __init__(self, config: PlotConfig, parent: QWidget | None = None) -> None:
        super().__init__("Figure", parent)
        self._build_controls(config)
        self._build_layout()
        self.bindings = self._build_bindings()
        self.widget_map = {
            name: value
            for name, value in vars(self).items()
            if name.endswith(("_spin", "_edit", "_check", "_combo", "_btn"))
        }
        self._numeric_edits = tuple(
            binding.widget
            for binding in self.bindings
            if isinstance(binding.widget, QLineEdit)
            and binding.field not in {"title", "x_label", "y_label", "y2_label"}
        )
        for edit in self._numeric_edits:
            edit.textChanged.connect(
                lambda _text, target=edit: self._refresh_numeric_edit_validity(target)
            )
        # ``config`` is the complete initial state. Several controls (notably
        # labels, checkboxes, and optional numeric edits) cannot be initialized
        # by their constructors, so finish construction through the same
        # authoritative load path used when switching graphs.
        self.load(config)
        self._connect_changed_sources()

    @staticmethod
    def _double(value: float, minimum: float, maximum: float, decimals: int) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setValue(value)
        return spin

    @staticmethod
    def _integer(value: int, minimum: int, maximum: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

    @staticmethod
    def _combo(items: Iterable[str], current: str) -> QComboBox:
        combo = QComboBox()
        combo.addItems(list(items))
        combo.setCurrentText(current)
        return combo

    def _build_controls(self, c: PlotConfig) -> None:
        self.preset_combo = self._combo([*PRESETS, "Custom"], c.preset)
        self.title_edit = QLineEdit()
        self.x_label_edit = QLineEdit()
        self.y_label_edit = QLineEdit()
        self.y2_label_edit = QLineEdit()

        # Allow poster-sized figures without an arbitrary physical-size cap.
        self.width_spin = self._double(c.width_mm, 20.0, float_info.max, 1)
        self.height_spin = self._double(c.height_mm, 20.0, float_info.max, 1)
        self.plot_width_spin = self._double(c.plot_width_mm, 1.0, float_info.max, 1)
        self.plot_height_spin = self._double(c.plot_height_mm, 1.0, float_info.max, 1)
        self.plot_ratio_lock_check = QCheckBox("Lock plot ratio")
        self.plot_ratio_preset_combo = self._combo(PLOT_RATIO_PRESETS, c.plot_ratio_preset)
        self.dpi_spin = self._integer(c.dpi, 72, 1200)

        self.title_size_spin = self._integer(c.title_size, 4, 40)
        self.axis_size_spin = self._integer(c.axis_size, 4, 40)
        self.axis_line_width_spin = self._double(c.axis_line_width, 0.1, 10.0, 2)
        self.tick_size_spin = self._integer(c.tick_size, 4, 40)
        self.legend_size_spin = self._integer(c.legend_size, 1, 40)
        self.x_label_offset_spin = self._double(c.x_label_offset_mm, 0.0, 50.0, 1)
        self.y_label_offset_spin = self._double(c.y_label_offset_mm, 0.0, 50.0, 1)
        self.x_tick_pad_spin = self._double(c.x_tick_pad, 0.0, 40.0, 1)
        self.y_tick_pad_spin = self._double(c.y_tick_pad, 0.0, 40.0, 1)
        self.y2_tick_pad_spin = self._double(c.y2_tick_pad, 0.0, 40.0, 1)

        self.y_axis_color_btn = QPushButton(c.y_axis_color)
        self.y2_axis_color_btn = QPushButton(c.y2_axis_color)
        self._set_color(self.y_axis_color_btn, c.y_axis_color)
        self._set_color(self.y2_axis_color_btn, c.y2_axis_color)

        self.pad_left_spin = self._double(c.pad_left_mm, 0.0, 100.0, 1)
        self.pad_right_spin = self._double(c.pad_right_mm, 0.0, 100.0, 1)
        self.pad_top_spin = self._double(c.pad_top_mm, 0.0, 100.0, 1)
        self.pad_bottom_spin = self._double(c.pad_bottom_mm, 0.0, 100.0, 1)
        self.fixed_plot_area_check = QCheckBox("Lock plot box size")
        self.plot_margin_left_spin = self._double(c.plot_margin_left_mm, 0.0, 200.0, 1)
        self.plot_margin_right_spin = self._double(c.plot_margin_right_mm, 0.0, 200.0, 1)
        self.plot_margin_top_spin = self._double(c.plot_margin_top_mm, 0.0, 200.0, 1)
        self.plot_margin_bottom_spin = self._double(c.plot_margin_bottom_mm, 0.0, 200.0, 1)
        self.center_plot_box_btn = QPushButton("Center plot box")
        self.center_content_btn = QPushButton("Center content")
        self.fit_canvas_btn = QPushButton("Fit canvas to content")

        self.x_scale_combo = self._combo(["linear", "log"], c.x_scale)
        self.y_scale_combo = self._combo(["linear", "log"], c.y_scale)
        self.y2_scale_combo = self._combo(["linear", "log"], c.y2_scale)
        self.x_scale_divisor_edit = QLineEdit(str(c.x_scale_divisor))
        self.y_scale_divisor_edit = QLineEdit(str(c.y_scale_divisor))
        self.y2_scale_divisor_edit = QLineEdit(str(c.y2_scale_divisor))

        optional_fields = (
            "x_min", "x_max", "y_min", "y_max", "y2_min", "y2_max",
            "x_tick_interval", "y_tick_interval", "y2_tick_interval",
            "x_break_left_min", "x_break_left_max", "x_break_right_min",
            "x_break_right_max", "y_break_lower_min", "y_break_lower_max",
            "y_break_upper_min", "y_break_upper_max",
        )
        for field in optional_fields:
            edit = QLineEdit()
            edit.setPlaceholderText("auto")
            setattr(self, f"{field}_edit", edit)
        for edit in (self.x_tick_interval_edit, self.y_tick_interval_edit, self.y2_tick_interval_edit):
            edit.setPlaceholderText("auto; log: decades")
            edit.setToolTip(
                "Linear scale: original data-unit interval, unaffected by divisor. "
                "Log scale: decade interval."
            )
        for edit in (self.x_scale_divisor_edit, self.y_scale_divisor_edit, self.y2_scale_divisor_edit):
            edit.setPlaceholderText("1")
            edit.setProperty("positive_only", True)
            edit.setToolTip(
                "Tick label = original axis value / divisor. Curves and axis limits stay unchanged. "
                "Enter a finite positive number "
                "such as 1000000 or 1e6 (not 10^6). Use 0.001 to convert seconds to ms. "
                "Axis limits and tick intervals remain in original data units. "
                "Invalid or incomplete input keeps the last valid divisor; enter 1 to reset."
            )
        for axis in ("x", "y", "y2"):
            for bound in ("min", "max"):
                getattr(self, f"{axis}_{bound}_edit").setToolTip(
                    "Axis limit in original data units, unaffected by divisor. Blank uses automatic limits."
                )

        self.x_minor_divisions_spin = self._integer(c.x_minor_divisions, 0, 20)
        self.y_minor_divisions_spin = self._integer(c.y_minor_divisions, 0, 20)
        self.y2_minor_divisions_spin = self._integer(c.y2_minor_divisions, 0, 20)
        for axis in ("x", "y", "y2"):
            spin = self._integer(-1, -1, 12)
            spin.setSpecialValueText("Auto")
            spin.setToolTip(
                "Decimal places in numeric tick labels (0–12). "
                "Auto uses automatic precision; 2 displays 1.00. "
                "In scientific formats this controls the coefficient's decimals. "
                "Only labels change, not the data or tick spacing."
            )
            setattr(self, f"{axis}_tick_decimals_spin", spin)
            combo = QComboBox()
            for mode, label in TICK_NOTATIONS.items():
                combo.addItem(label, mode)
            combo.setToolTip(
                "Plain: 1000000. Each tick: 1×10⁶, 2×10⁶. "
                "Shared exponent: ticks 1, 2 with ×10⁶ at the axis edge. "
                "Auto chooses notation from the axis scale and value range. "
                "Formatting applies after the divisor."
            )
            setattr(self, f"{axis}_tick_notation_combo", combo)
        self.x_break_check = QCheckBox("X broken axis")
        self.y_break_check = QCheckBox("Y broken axis")
        self.x_break_gap_spin = self._double(c.x_break_gap, 0.01, 0.5, 3)
        self.y_break_gap_spin = self._double(c.y_break_gap, 0.01, 0.5, 3)

        self.show_x_tick_labels_check = QCheckBox("Show X tick labels")
        self.show_y_tick_labels_check = QCheckBox("Show Y tick labels")
        self.show_y2_tick_labels_check = QCheckBox("Show Y2 tick labels")
        self.show_tick_marks_check = QCheckBox("Show tick marks")
        self.show_axis_arrows_check = QCheckBox("Show axis arrows")
        self.show_top_axis_check = QCheckBox("Show top axis")
        self.show_bottom_axis_check = QCheckBox("Show bottom axis")
        self.show_left_axis_check = QCheckBox("Show left axis")
        self.show_right_axis_check = QCheckBox("Show right axis")
        self.grid_check = QCheckBox("Grid")
        self.legend_check = QCheckBox("Legend")
        self.edit_legend_btn = QPushButton("Edit legend entries…")
        self.y_offset_spin = self._double(c.y_offset_step, -1e9, 1e9, 4)

    @staticmethod
    def _add_tab(tabs: QTabWidget, title: str, rows: Iterable[tuple[str | None, QWidget]]) -> None:
        tab = QWidget()
        form = QFormLayout(tab)
        for label, widget in rows:
            if label is None:
                form.addRow(widget)
            else:
                form.addRow(label, widget)
        tabs.addTab(tab, title)

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        self._add_tab(tabs, "Size", (
            ("Preset", self.preset_combo), ("Canvas width (mm)", self.width_spin),
            ("Canvas height (mm)", self.height_spin), ("Plot width (mm)", self.plot_width_spin),
            ("Plot height (mm)", self.plot_height_spin), (None, self.plot_ratio_lock_check),
            ("Plot ratio preset", self.plot_ratio_preset_combo), ("DPI", self.dpi_spin),
        ))
        self._add_tab(tabs, "Padding", (
            ("Left padding (mm)", self.pad_left_spin), ("Right padding (mm)", self.pad_right_spin),
            ("Top padding (mm)", self.pad_top_spin), ("Bottom padding (mm)", self.pad_bottom_spin),
        ))
        self._add_tab(tabs, "Layout", (
            (None, self.fixed_plot_area_check), ("Plot left margin (mm)", self.plot_margin_left_spin),
            ("Plot right margin (mm)", self.plot_margin_right_spin),
            ("Plot top margin (mm)", self.plot_margin_top_spin),
            ("Plot bottom margin (mm)", self.plot_margin_bottom_spin),
            (None, self.center_plot_box_btn), (None, self.center_content_btn), (None, self.fit_canvas_btn),
        ))
        self._add_tab(tabs, "Labels", (
            ("Title", self.title_edit), ("X label", self.x_label_edit), ("Y label", self.y_label_edit),
            ("Y2 label", self.y2_label_edit), ("X label gap (mm)", self.x_label_offset_spin),
            ("Y label gap (mm)", self.y_label_offset_spin), ("X tick gap (pt)", self.x_tick_pad_spin),
            ("Y tick gap (pt)", self.y_tick_pad_spin), ("Y2 tick gap (pt)", self.y2_tick_pad_spin),
        ))
        axes_rows: list[tuple[str | None, QWidget]] = [
            ("X scale", self.x_scale_combo), ("Y scale", self.y_scale_combo),
            ("Y2 scale", self.y2_scale_combo), ("X divisor", self.x_scale_divisor_edit),
            ("Y divisor", self.y_scale_divisor_edit), ("Y2 divisor", self.y2_scale_divisor_edit),
            ("X min", self.x_min_edit), ("X max", self.x_max_edit),
            ("Y min", self.y_min_edit), ("Y max", self.y_max_edit),
            ("Y2 min", self.y2_min_edit), ("Y2 max", self.y2_max_edit),
            ("X tick interval", self.x_tick_interval_edit),
            ("X tick decimals", self.x_tick_decimals_spin),
            ("X number format", self.x_tick_notation_combo),
            ("X minor divisions", self.x_minor_divisions_spin),
            ("Y tick interval", self.y_tick_interval_edit),
            ("Y tick decimals", self.y_tick_decimals_spin),
            ("Y number format", self.y_tick_notation_combo),
            ("Y minor divisions", self.y_minor_divisions_spin),
            ("Y2 tick interval", self.y2_tick_interval_edit),
            ("Y2 tick decimals", self.y2_tick_decimals_spin),
            ("Y2 number format", self.y2_tick_notation_combo),
            ("Y2 minor divisions", self.y2_minor_divisions_spin),
            (None, self.x_break_check), ("X break left min", self.x_break_left_min_edit),
            ("X break left max", self.x_break_left_max_edit),
            ("X break right min", self.x_break_right_min_edit),
            ("X break right max", self.x_break_right_max_edit), ("X break gap", self.x_break_gap_spin),
            (None, self.y_break_check), ("Y break lower min", self.y_break_lower_min_edit),
            ("Y break lower max", self.y_break_lower_max_edit),
            ("Y break upper min", self.y_break_upper_min_edit),
            ("Y break upper max", self.y_break_upper_max_edit), ("Y break gap", self.y_break_gap_spin),
        ]
        axes_rows.extend((None, widget) for widget in (
            self.show_top_axis_check, self.show_bottom_axis_check, self.show_left_axis_check,
            self.show_right_axis_check, self.show_tick_marks_check, self.show_axis_arrows_check,
            self.show_x_tick_labels_check, self.show_y_tick_labels_check,
            self.show_y2_tick_labels_check,
        ))
        self._add_tab(tabs, "Axes", axes_rows)
        self._add_tab(tabs, "Style", (
            ("Title size", self.title_size_spin), ("Axis size", self.axis_size_spin),
            ("Axis line width (pt)", self.axis_line_width_spin), ("Tick size", self.tick_size_spin),
            ("Legend size", self.legend_size_spin), ("Y1 axis color", self.y_axis_color_btn),
            ("Y2 axis color", self.y2_axis_color_btn), ("Y offset", self.y_offset_spin),
            (None, self.grid_check), (None, self.legend_check), (None, self.edit_legend_btn),
        ))

    _signal = staticmethod(change_signal)

    _set_color = staticmethod(style_color_button)

    @staticmethod
    def _set_combo_with_fallback(
        combo: QComboBox,
        value: object,
        allowed: Iterable[str],
        fallback: str,
    ) -> None:
        text = str(value)
        combo.setCurrentText(text if text in allowed else fallback)

    def _standard_binding(self, field: str, widget: QWidget) -> FieldBinding[Any]:
        if isinstance(widget, QLineEdit):
            read, write = widget.text, widget.setText
        elif isinstance(widget, QComboBox):
            read = widget.currentText
            if field == "preset":
                write = lambda value, target=widget: self._set_combo_with_fallback(
                    target, value, (*PRESETS, "Custom"), "Custom"
                )
            elif field == "plot_ratio_preset":
                write = lambda value, target=widget: self._set_combo_with_fallback(
                    target, value, PLOT_RATIO_PRESETS, "Current"
                )
            else:
                write = widget.setCurrentText
        elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            read, write = widget.value, widget.setValue
        elif isinstance(widget, QCheckBox):
            read, write = widget.isChecked, widget.setChecked
        elif isinstance(widget, QPushButton):
            read = widget.text
            write = lambda value, target=widget: self._set_color(target, str(value))
        else:
            raise TypeError(f"Unsupported binding widget: {type(widget).__name__}")
        return FieldBinding(field, widget, read, write, self._signal(widget))

    def _build_bindings(self) -> BindingRegistry:
        names = {
            "preset": self.preset_combo, "title": self.title_edit,
            "x_label": self.x_label_edit, "y_label": self.y_label_edit,
            "y2_label": self.y2_label_edit, "width_mm": self.width_spin,
            "height_mm": self.height_spin, "plot_width_mm": self.plot_width_spin,
            "plot_height_mm": self.plot_height_spin,
            "plot_ratio_locked": self.plot_ratio_lock_check,
            "plot_ratio_preset": self.plot_ratio_preset_combo, "dpi": self.dpi_spin,
            "title_size": self.title_size_spin, "axis_size": self.axis_size_spin,
            "axis_line_width": self.axis_line_width_spin, "tick_size": self.tick_size_spin,
            "legend_size": self.legend_size_spin, "x_label_offset_mm": self.x_label_offset_spin,
            "y_label_offset_mm": self.y_label_offset_spin, "x_tick_pad": self.x_tick_pad_spin,
            "y_tick_pad": self.y_tick_pad_spin, "y2_tick_pad": self.y2_tick_pad_spin,
            "y_axis_color": self.y_axis_color_btn, "y2_axis_color": self.y2_axis_color_btn,
            "pad_left_mm": self.pad_left_spin, "pad_right_mm": self.pad_right_spin,
            "pad_top_mm": self.pad_top_spin, "pad_bottom_mm": self.pad_bottom_spin,
            "fixed_plot_area": self.fixed_plot_area_check,
            "plot_margin_left_mm": self.plot_margin_left_spin,
            "plot_margin_right_mm": self.plot_margin_right_spin,
            "plot_margin_top_mm": self.plot_margin_top_spin,
            "plot_margin_bottom_mm": self.plot_margin_bottom_spin,
            "x_scale": self.x_scale_combo, "y_scale": self.y_scale_combo,
            "y2_scale": self.y2_scale_combo, "x_minor_divisions": self.x_minor_divisions_spin,
            "y_minor_divisions": self.y_minor_divisions_spin,
            "y2_minor_divisions": self.y2_minor_divisions_spin,
            "x_break_enabled": self.x_break_check, "x_break_gap": self.x_break_gap_spin,
            "y_break_enabled": self.y_break_check, "y_break_gap": self.y_break_gap_spin,
            "y_offset_step": self.y_offset_spin,
            "show_x_tick_labels": self.show_x_tick_labels_check,
            "show_y_tick_labels": self.show_y_tick_labels_check,
            "show_y2_tick_labels": self.show_y2_tick_labels_check,
            "show_tick_marks": self.show_tick_marks_check,
            "show_axis_arrows": self.show_axis_arrows_check,
            "show_top_axis": self.show_top_axis_check,
            "show_bottom_axis": self.show_bottom_axis_check,
            "show_left_axis": self.show_left_axis_check,
            "show_right_axis": self.show_right_axis_check,
            "grid": self.grid_check, "legend": self.legend_check,
        }
        bindings = [self._standard_binding(field, widget) for field, widget in names.items()]
        for axis in ("x", "y", "y2"):
            combo = getattr(self, f"{axis}_tick_notation_combo")
            bindings.append(FieldBinding(
                f"{axis}_tick_notation", combo, combo.currentData,
                lambda value, target=combo: target.setCurrentIndex(
                    max(0, target.findData(value))
                ), combo.currentIndexChanged,
            ))
            field = f"{axis}_tick_decimals"
            spin = getattr(self, f"{field}_spin")
            bindings.append(FieldBinding(
                field, spin,
                lambda target=spin: None if target.value() < 0 else target.value(),
                lambda value, target=spin: target.setValue(-1 if value is None else value),
                spin.valueChanged,
            ))
        for field in ("x_scale_divisor", "y_scale_divisor", "y2_scale_divisor"):
            edit = getattr(self, f"{field}_edit")
            bindings.append(FieldBinding(
                field, edit, lambda target=edit: _positive_float(target),
                lambda value, target=edit: _set_divisor(target, value), edit.textChanged,
            ))
        optional_fields = (
            "x_min", "x_max", "y_min", "y_max", "y2_min", "y2_max",
            "x_tick_interval", "y_tick_interval", "y2_tick_interval",
            "x_break_left_min", "x_break_left_max", "x_break_right_min",
            "x_break_right_max", "y_break_lower_min", "y_break_lower_max",
            "y_break_upper_min", "y_break_upper_max",
        )
        for field in optional_fields:
            edit = getattr(self, f"{field}_edit")
            bindings.append(FieldBinding(
                field, edit, lambda target=edit: _optional_float(target),
                lambda value, target=edit: target.setText(_optional_text(value)),
                edit.textChanged,
            ))
        return BindingRegistry(bindings)

    def install_compatibility_aliases(self, target: object) -> None:
        """Expose controls on the old window surface during the migration."""

        for name, widget in self.widget_map.items():
            setattr(target, name, widget)
        setattr(target, "_numeric_edits", self._numeric_edits)

    def load(self, config: PlotConfig) -> None:
        self.bindings.load(replace(config, **{
            f"{axis}_tick_notation": axis_tick_notation(config, axis)
            for axis in ("x", "y", "y2")
        }))
        for edit in self._numeric_edits:
            self._refresh_numeric_edit_validity(edit)

    def update(self, config: PlotConfig) -> PlotConfig:
        self.bindings.update(config)
        for axis in ("x", "y", "y2"):
            setattr(config, f"{axis}_scientific_notation",
                    getattr(config, f"{axis}_tick_notation") != "plain")
        return config

    def connect_changed(self, callback: Callable[[QWidget], None]) -> None:
        """Call ``callback`` with the widget that originated each change."""

        self.changed.connect(callback)

    def _connect_changed_sources(self) -> None:
        for binding in self.bindings:
            for signal in binding.signals():
                signal.connect(
                    lambda *_args, source=binding.widget: self.changed.emit(source)
                )

    @staticmethod
    def _refresh_numeric_edit_validity(edit: QLineEdit) -> None:
        text = edit.text().strip()
        positive_only = bool(edit.property("positive_only"))
        valid = not text and not positive_only
        if text:
            try:
                value = float(text)
                valid = math.isfinite(value) and (not positive_only or value > 0)
            except ValueError:
                valid = False
        edit.setProperty("invalid", not valid)
        edit.style().unpolish(edit)
        edit.style().polish(edit)


__all__ = ["FigureSettingsPanel", "PLOT_RATIO_PRESETS"]
