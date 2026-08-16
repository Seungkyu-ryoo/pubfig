"""Reusable controls for one series and multi-series colour operations."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import json
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..plot_config import LINE_STYLES, MARKERS, PLOT_TYPES, RECOMMENDED_PALETTES, SeriesConfig
from ..theme import color_swatch_style
from .binding import BindingRegistry, FieldBinding, signals_blocked
from .widgets import GradientEditorWidget, NoWheelComboBox, NoWheelDoubleSpinBox


PLOT_TYPE_HELP = {
    "line": "Continuous line for ordered X-Y data.",
    "scatter": "Markers only; useful for point distributions.",
    "line+marker": "Line with markers; common for publication curves.",
    "bar": "Bars for discrete or categorical comparisons.",
    "step": "Stair-step trace for binned or piecewise-constant data.",
    "area": "Filled area under a curve; useful for cumulative or contribution plots.",
    "stem": "Vertical sticks from baseline; useful for peaks or impulse-like data.",
}


# Old GraphDrawerWindow attribute -> SeriesSettingsPanel attribute. Keeping this
# table explicit makes compatibility temporary, searchable, and easy to remove.
SERIES_WIDGET_ALIASES: dict[str, str] = {
    "style_target_combo": "target_combo",
    "series_label_edit": "label_edit",
    "y_axis_combo": "axis_combo",
    "plot_type_combo": "type_combo",
    "plot_type_help": "type_help",
    "marker_combo": "marker_combo",
    "line_style_combo": "line_style_combo",
    "line_width_spin": "line_width_spin",
    "marker_size_spin": "marker_size_spin",
    "series_y_offset_spin": "y_offset_spin",
    "color_btn": "color_button",
    "series_alpha_spin": "alpha_spin",
    "show_in_legend_check": "legend_check",
    "error_column_combo": "error_combo",
    "error_cap_spin": "error_cap_spin",
    "cmap_column_list": "cmap_column_list",
    "cmap_select_all_btn": "cmap_select_all_button",
    "cmap_select_none_btn": "cmap_select_none_button",
    "cmap_alpha_only_check": "cmap_alpha_only_check",
    "cmap_alpha_section": "cmap_alpha_section",
    "cmap_base_color_btn": "cmap_base_color_button",
    "cmap_alpha_start_spin": "cmap_alpha_start_spin",
    "cmap_alpha_end_spin": "cmap_alpha_end_spin",
    "cmap_alpha_dist_combo": "cmap_alpha_distribution_combo",
    "cmap_color_section": "cmap_color_section",
    "cmap_combo": "cmap_combo",
    "cmap_start_spin": "cmap_start_spin",
    "cmap_end_spin": "cmap_end_spin",
    "apply_cmap_btn": "apply_cmap_button",
    "palette_combo": "palette_combo",
    "apply_palette_btn": "apply_palette_button",
    "gradient_editor": "gradient_editor",
    "apply_gradient_btn": "apply_gradient_button",
}


class SeriesSettingsPanel(QGroupBox):
    """Own series widgets and synchronize the Style tab with ``SeriesConfig``.

    The panel deliberately does not own the application's series dictionary,
    undo stack, table names, or rendering. Its ``load_series`` / ``update_series``
    methods handle one config; action buttons let a coordinator apply palettes
    and gradients across whichever series the application has selected.
    """

    changed = Signal(object)

    def __init__(
        self,
        *,
        gradient_stops: object | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Series style", parent)
        self._build_controls()
        self._build_layout()
        self.bindings = self._build_bindings()
        self._connect_local_behaviour()
        self.set_gradient_stops(gradient_stops)

    @staticmethod
    def _double(value: float, minimum: float, maximum: float, decimals: int) -> QDoubleSpinBox:
        spin = NoWheelDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setValue(value)
        return spin

    @staticmethod
    def _combo(items: Iterable[str], current: str = "") -> NoWheelComboBox:
        combo = NoWheelComboBox()
        combo.addItems(list(items))
        if current:
            combo.setCurrentText(current)
        return combo

    @staticmethod
    def _set_color(button: QPushButton, color: str) -> None:
        button.setText(color)
        button.setStyleSheet(color_swatch_style(color))

    def _build_controls(self) -> None:
        self.target_combo = NoWheelComboBox()
        self.label_edit = QLineEdit()
        self.axis_combo = self._combo(["left", "right"], "left")
        self.type_combo = self._combo(PLOT_TYPES, "line")
        self.type_help = QLabel(PLOT_TYPE_HELP["line"])
        self.type_help.setWordWrap(True)
        self.marker_combo = self._combo(MARKERS, "o")
        self.line_style_combo = self._combo(LINE_STYLES, "solid")
        self.line_width_spin = self._double(1.0, 0.1, 10.0, 2)
        self.marker_size_spin = self._double(4.0, 0.0, 30.0, 1)
        self.y_offset_spin = self._double(0.0, -1e9, 1e9, 4)
        self.color_button = QPushButton()
        self._set_color(self.color_button, "#4477AA")
        self.alpha_spin = self._double(1.0, 0.0, 1.0, 2)
        self.legend_check = QCheckBox("Show in legend")
        self.legend_check.setChecked(True)
        self.error_combo = NoWheelComboBox()
        self.error_combo.addItem("(none)")
        self.error_combo.setToolTip(
            "Column holding the ± error magnitude for this series; drawn as Y error bars."
        )
        self.error_cap_spin = self._double(2.0, 0.0, 20.0, 1)

        self.cmap_column_list = QListWidget()
        self.cmap_column_list.setMaximumHeight(80)
        self.cmap_select_all_button = QPushButton("All")
        self.cmap_select_none_button = QPushButton("None")
        self.cmap_alpha_only_check = QCheckBox("Alpha only (same color, vary alpha)")

        self.cmap_alpha_section = QWidget()
        self.cmap_base_color_button = QPushButton()
        self._set_color(self.cmap_base_color_button, "#4477AA")
        self.cmap_alpha_start_spin = self._double(0.2, 0.0, 1.0, 2)
        self.cmap_alpha_end_spin = self._double(1.0, 0.0, 1.0, 2)
        self.cmap_alpha_distribution_combo = self._combo(
            ["Linear", "Geometric", "Sqrt", "Power²", "Log"],
            "Geometric",
        )

        self.cmap_color_section = QWidget()
        self.cmap_combo = self._combo(
            [
                "viridis",
                "plasma",
                "inferno",
                "magma",
                "cividis",
                "turbo",
                "coolwarm",
                "Spectral",
                "rainbow",
            ]
        )
        self.cmap_start_spin = self._double(0.05, 0.0, 1.0, 3)
        self.cmap_end_spin = self._double(0.95, 0.0, 1.0, 3)
        self.apply_cmap_button = QPushButton("Apply colormap to plotted series")
        self.palette_combo = self._combo(RECOMMENDED_PALETTES)
        self.apply_palette_button = QPushButton("Apply recommended palette")
        self.gradient_editor = GradientEditorWidget()
        self.apply_gradient_button = QPushButton("Apply custom gradient")

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        style_tab = QWidget()
        style_form = QFormLayout(style_tab)
        for label, widget in (
            ("Edit series", self.target_combo),
            ("Label", self.label_edit),
            ("Y axis", self.axis_combo),
            ("Type", self.type_combo),
            (None, self.type_help),
            ("Marker", self.marker_combo),
            ("Line style", self.line_style_combo),
            ("Line width", self.line_width_spin),
            ("Marker size", self.marker_size_spin),
            ("Y offset", self.y_offset_spin),
            ("Color", self.color_button),
            ("Alpha", self.alpha_spin),
            ("Error column", self.error_combo),
            ("Error cap size", self.error_cap_spin),
            (None, self.legend_check),
        ):
            if label is None:
                style_form.addRow(widget)
            else:
                style_form.addRow(label, widget)
        self.tabs.addTab(style_tab, "Style")

        cmap_tab = QWidget()
        cmap_layout = QVBoxLayout(cmap_tab)
        cmap_layout.setContentsMargins(4, 4, 4, 4)
        selection_row = QHBoxLayout()
        selection_row.addWidget(self.cmap_select_all_button)
        selection_row.addWidget(self.cmap_select_none_button)

        alpha_form = QFormLayout(self.cmap_alpha_section)
        alpha_form.setContentsMargins(0, 0, 0, 0)
        alpha_form.addRow("Base color", self.cmap_base_color_button)
        alpha_form.addRow("Alpha start", self.cmap_alpha_start_spin)
        alpha_form.addRow("Alpha end", self.cmap_alpha_end_spin)
        alpha_form.addRow("Distribution", self.cmap_alpha_distribution_combo)

        color_form = QFormLayout(self.cmap_color_section)
        color_form.setContentsMargins(0, 0, 0, 0)
        color_form.addRow("Colormap", self.cmap_combo)
        color_form.addRow("Stretch start", self.cmap_start_spin)
        color_form.addRow("Stretch end", self.cmap_end_spin)

        cmap_layout.addWidget(QLabel("Apply to:"))
        cmap_layout.addWidget(self.cmap_column_list)
        cmap_layout.addLayout(selection_row)
        cmap_layout.addWidget(self.cmap_alpha_only_check)
        cmap_layout.addWidget(self.cmap_alpha_section)
        cmap_layout.addWidget(self.cmap_color_section)
        cmap_layout.addWidget(self.apply_cmap_button)
        palette_form = QFormLayout()
        palette_form.setContentsMargins(0, 0, 0, 0)
        palette_form.addRow("Recommended palette", self.palette_combo)
        cmap_layout.addLayout(palette_form)
        cmap_layout.addWidget(self.apply_palette_button)
        cmap_layout.addWidget(QLabel("Custom gradient:"))
        cmap_layout.addWidget(self.gradient_editor)
        hint = QLabel(
            "Double-click bar: add stop · drag stop: move transition · "
            "double-click stop: color/opacity · right-click stop: remove"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #5b6573; font-size: 11px;")
        cmap_layout.addWidget(hint)
        cmap_layout.addWidget(self.apply_gradient_button)
        self.tabs.addTab(cmap_tab, "Colormap")

    @staticmethod
    def _signal(widget: QWidget):
        for name in ("textChanged", "currentTextChanged", "valueChanged", "toggled"):
            signal = getattr(widget, name, None)
            if signal is not None:
                return signal
        return None

    def _build_bindings(self) -> BindingRegistry:
        standard = {
            "label": self.label_edit,
            "y_axis": self.axis_combo,
            "plot_type": self.type_combo,
            "marker": self.marker_combo,
            "line_style": self.line_style_combo,
            "line_width": self.line_width_spin,
            "marker_size": self.marker_size_spin,
            "y_offset": self.y_offset_spin,
            "color": self.color_button,
            "alpha": self.alpha_spin,
            "show_in_legend": self.legend_check,
            "error_cap_size": self.error_cap_spin,
        }
        bindings: list[FieldBinding[Any]] = []
        for field, widget in standard.items():
            if isinstance(widget, QLineEdit):
                read, write = widget.text, widget.setText
            elif isinstance(widget, NoWheelComboBox):
                read, write = widget.currentText, widget.setCurrentText
            elif isinstance(widget, QDoubleSpinBox):
                read, write = widget.value, widget.setValue
            elif isinstance(widget, QCheckBox):
                read, write = widget.isChecked, widget.setChecked
            elif isinstance(widget, QPushButton):
                read = widget.text
                write = lambda value, target=widget: self._set_color(target, str(value))
            else:
                raise TypeError(f"Unsupported series widget: {type(widget).__name__}")
            signal = None if widget is self.color_button else self._signal(widget)
            bindings.append(FieldBinding(field, widget, read, write, signal))

        bindings.append(
            FieldBinding(
                "error_column",
                self.error_combo,
                lambda: "" if self.error_combo.currentText() in ("", "(none)") else self.error_combo.currentText(),
                self._write_error_column,
                self.error_combo.currentTextChanged,
            )
        )
        return BindingRegistry(bindings)

    def _connect_local_behaviour(self) -> None:
        self.type_combo.currentTextChanged.connect(self.update_plot_type_help)
        self.cmap_alpha_only_check.toggled.connect(self.set_cmap_mode)
        self.cmap_select_all_button.clicked.connect(self.select_all_cmap_columns)
        self.cmap_select_none_button.clicked.connect(self.clear_cmap_columns)
        for binding in self.bindings:
            for signal in binding.signals():
                signal.connect(
                    lambda *_args, source=binding.widget: self.changed.emit(source)
                )
        self.set_cmap_mode(False)

    def _write_error_column(self, value: str) -> None:
        text = str(value or "")
        self.error_combo.setCurrentText(
            text if text and self.error_combo.findText(text) >= 0 else "(none)"
        )

    def set_error_columns(self, columns: Iterable[str]) -> None:
        """Replace error-column choices without emitting a series change."""
        current = self.error_combo.currentText()
        choices = list(dict.fromkeys(str(column) for column in columns))
        choices = [choice for choice in choices if choice and choice != "(none)"]
        with signals_blocked(self.error_combo):
            self.error_combo.clear()
            self.error_combo.addItem("(none)")
            self.error_combo.addItems(choices)
            self.error_combo.setCurrentText(current if current in choices else "(none)")

    def load_series(
        self,
        series: SeriesConfig,
        *,
        error_columns: Iterable[str] | None = None,
    ) -> None:
        """Load one config into the Style tab without emitting ``changed``."""
        if not isinstance(series, SeriesConfig):
            raise TypeError("load_series expects SeriesConfig")
        if error_columns is not None:
            self.set_error_columns(error_columns)
        self.bindings.load(series)

    def update_series(self, series: SeriesConfig) -> SeriesConfig:
        """Update and return one config while preserving non-widget fields."""
        if not isinstance(series, SeriesConfig):
            raise TypeError("update_series expects SeriesConfig")
        self.bindings.update(series)
        series.label = series.label.strip() or series.y
        return series

    # Short names parallel FigureSettingsPanel and ease generic panel handling.
    def load(self, series: SeriesConfig, *, error_columns: Iterable[str] | None = None) -> None:
        self.load_series(series, error_columns=error_columns)

    def update(self, series: SeriesConfig) -> SeriesConfig:
        return self.update_series(series)

    def connect_changed(self, callback: Callable[[QWidget], None]) -> None:
        self.changed.connect(callback)

    def set_targets(self, targets: Iterable[str], preferred: str | None = None) -> str:
        """Replace selectable series names, preserving a valid target."""
        names = list(dict.fromkeys(str(target) for target in targets if str(target)))
        current = preferred if preferred in names else self.target_combo.currentText()
        with signals_blocked(self.target_combo):
            self.target_combo.clear()
            self.target_combo.addItems(names)
            if current in names:
                self.target_combo.setCurrentText(current)
        self.set_editor_enabled(bool(names))
        return self.target_combo.currentText()

    def current_target(self) -> str:
        return self.target_combo.currentText()

    def set_editor_enabled(self, enabled: bool) -> None:
        for widget in self.bindings.widgets:
            widget.setEnabled(enabled)

    def update_plot_type_help(self, plot_type: str) -> None:
        self.type_help.setText(PLOT_TYPE_HELP.get(plot_type, ""))

    def set_color(self, color: str) -> None:
        self._set_color(self.color_button, color)

    def set_cmap_base_color(self, color: str) -> None:
        self._set_color(self.cmap_base_color_button, color)

    def set_cmap_mode(self, alpha_only: bool) -> None:
        self.cmap_alpha_section.setVisible(alpha_only)
        self.cmap_color_section.setVisible(not alpha_only)

    def set_cmap_columns(self, columns: Iterable[str], *, preserve_selection: bool = True) -> None:
        previous = set(self.selected_cmap_columns()) if preserve_selection else set()
        names = list(dict.fromkeys(str(column) for column in columns if str(column)))
        with signals_blocked(self.cmap_column_list):
            self.cmap_column_list.clear()
            for name in names:
                item = QListWidgetItem(name)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                checked = not previous or name in previous
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
                self.cmap_column_list.addItem(item)

    def selected_cmap_columns(self) -> list[str]:
        return [
            self.cmap_column_list.item(index).text()
            for index in range(self.cmap_column_list.count())
            if self.cmap_column_list.item(index).checkState() == Qt.Checked
        ]

    def select_all_cmap_columns(self) -> None:
        for index in range(self.cmap_column_list.count()):
            self.cmap_column_list.item(index).setCheckState(Qt.Checked)

    def clear_cmap_columns(self) -> None:
        for index in range(self.cmap_column_list.count()):
            self.cmap_column_list.item(index).setCheckState(Qt.Unchecked)

    def set_gradient_stops(self, payload: object | None) -> None:
        if payload in (None, ""):
            return
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                return
        try:
            self.gradient_editor.set_stops(payload)
        except (TypeError, ValueError):
            return

    @property
    def compatibility_widgets(self) -> dict[str, QWidget]:
        return {
            old_name: getattr(self, panel_name)
            for old_name, panel_name in SERIES_WIDGET_ALIASES.items()
        }

    def install_compatibility_aliases(self, target: object) -> None:
        """Expose old window attributes while callers migrate to this panel."""
        for name, widget in self.compatibility_widgets.items():
            setattr(target, name, widget)


__all__ = ["PLOT_TYPE_HELP", "SERIES_WIDGET_ALIASES", "SeriesSettingsPanel"]
