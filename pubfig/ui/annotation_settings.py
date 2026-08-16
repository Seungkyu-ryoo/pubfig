"""Self-contained annotation list and editor controls.

The panel owns annotation widgets and their config synchronization.  Rendering,
canvas hit-testing and dragging, and undo policy deliberately remain the
responsibility of the main-window coordinator.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..plot_config import ANNOTATION_LINE_STYLES, AnnotationConfig
from ..theme import color_swatch_style
from .binding import BindingRegistry, FieldBinding, signals_blocked
from .widgets import (
    AnnotationTextEdit,
    NoWheelComboBox,
    NoWheelDoubleSpinBox,
    NoWheelSpinBox,
)


ANNOTATION_KINDS = ("text", "line", "arrow", "box", "circle", "textbox")


# Old GraphDrawerWindow attribute -> AnnotationSettingsPanel attribute.  The
# explicit map keeps the migration surface searchable and removable.
ANNOTATION_WIDGET_ALIASES: dict[str, str] = {
    "annotation_list": "list_widget",
    "annotation_kind_combo": "kind_combo",
    "annotation_text_edit": "text_edit",
    "annotation_x_spin": "x_spin",
    "annotation_y_spin": "y_spin",
    "annotation_x2_spin": "x2_spin",
    "annotation_y2_spin": "y2_spin",
    "annotation_w_spin": "width_spin",
    "annotation_h_spin": "height_spin",
    "annotation_angle_spin": "angle_spin",
    "annotation_font_spin": "font_spin",
    "annotation_arrow_head_spin": "arrow_head_spin",
    "annotation_line_style_combo": "line_style_combo",
    "annotation_alpha_spin": "alpha_spin",
    "annotation_fill_check": "fill_check",
    "annotation_color_btn": "color_button",
    "add_annotation_btn": "add_button",
    "remove_annotation_btn": "remove_button",
}


class AnnotationSettingsPanel(QGroupBox):
    """Own the annotation list and edit one :class:`AnnotationConfig`.

    ``changed`` reports only a user-edit source.  It does not mutate the
    selected config automatically, which lets the coordinator capture undo
    state before calling :meth:`update_current`.  The three request signals
    similarly leave add/remove/color-dialog policy to the coordinator.
    """

    changed = Signal(object)
    selection_changed = Signal(int)
    add_requested = Signal()
    remove_requested = Signal()
    color_requested = Signal()

    def __init__(
        self,
        annotations: Iterable[AnnotationConfig] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Annotations", parent)
        self._annotations: list[AnnotationConfig] = []
        self.loading_annotation_inputs = False
        self._build_controls()
        self._build_layout()
        self.bindings = self._build_bindings()
        self._connect_local_behaviour()
        self.set_annotations(annotations, preserve_selection=False)

    @staticmethod
    def _double(
        value: float,
        minimum: float,
        maximum: float,
        decimals: int,
    ) -> NoWheelDoubleSpinBox:
        spin = NoWheelDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setSingleStep(0.1 if decimals else 1.0)
        spin.setValue(value)
        spin.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        spin.setMinimumWidth(86)
        return spin

    @staticmethod
    def _integer(value: int, minimum: int, maximum: int) -> NoWheelSpinBox:
        spin = NoWheelSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        spin.setMinimumWidth(86)
        return spin

    @staticmethod
    def _set_color(button: QPushButton, color: str) -> None:
        button.setText(str(color))
        button.setStyleSheet(color_swatch_style(str(color)))

    def _build_controls(self) -> None:
        self.list_widget = QListWidget()
        self.list_widget.setMinimumHeight(120)
        self.list_widget.setMaximumHeight(180)
        self.list_widget.setSelectionMode(QAbstractItemView.ExtendedSelection)

        self.kind_combo = NoWheelComboBox()
        self.kind_combo.addItems(ANNOTATION_KINDS)
        self.text_edit = AnnotationTextEdit()
        self.x_spin = self._double(0.0, -1e12, 1e12, 4)
        self.y_spin = self._double(0.0, -1e12, 1e12, 4)
        # x2/y2 are retained for project compatibility.  Width/height are the
        # editable representation and update() keeps the endpoints derived.
        self.x2_spin = self._double(0.0, -1e12, 1e12, 4)
        self.y2_spin = self._double(0.0, -1e12, 1e12, 4)
        self.x2_spin.setParent(self)
        self.y2_spin.setParent(self)
        self.x2_spin.hide()
        self.y2_spin.hide()
        self.width_spin = self._double(0.15, -10.0, 10.0, 4)
        self.height_spin = self._double(0.15, -10.0, 10.0, 4)
        self.angle_spin = self._double(0.0, -360.0, 360.0, 1)
        self.font_spin = self._integer(8, 4, 60)
        self.arrow_head_spin = self._double(12.0, 1.0, 80.0, 1)
        self.line_style_combo = NoWheelComboBox()
        self.line_style_combo.addItems(ANNOTATION_LINE_STYLES)
        self.alpha_spin = self._double(1.0, 0.0, 1.0, 2)
        self.fill_check = QCheckBox("Fill shape")
        self.color_button = QPushButton()
        self._set_color(self.color_button, "#000000")
        self.add_button = QPushButton("Add annotation")
        self.remove_button = QPushButton("Remove selected")

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(self.list_widget)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        text_tab = QWidget()
        text_form = QFormLayout(text_tab)
        text_form.addRow("Type", self.kind_combo)
        text_form.addRow("Text", self.text_edit)
        text_form.addRow(self.add_button)
        text_form.addRow(self.remove_button)
        self.tabs.addTab(text_tab, "Text")

        position_tab = QWidget()
        position_form = QFormLayout(position_tab)
        position_form.addRow("X (axes 0-1)", self.x_spin)
        position_form.addRow("Y (axes 0-1)", self.y_spin)
        position_form.addRow("Width", self.width_spin)
        position_form.addRow("Height", self.height_spin)
        position_form.addRow("Angle", self.angle_spin)
        self.tabs.addTab(position_tab, "Position")

        style_tab = QWidget()
        style_form = QFormLayout(style_tab)
        style_form.addRow("Font size", self.font_spin)
        style_form.addRow("Arrow head size", self.arrow_head_spin)
        style_form.addRow("Line style", self.line_style_combo)
        style_form.addRow("Alpha", self.alpha_spin)
        style_form.addRow(self.fill_check)
        style_form.addRow("Color", self.color_button)
        self.tabs.addTab(style_tab, "Style")

    @staticmethod
    def _signal(widget: QWidget):
        for name in ("textChanged", "currentTextChanged", "valueChanged", "toggled"):
            signal = getattr(widget, name, None)
            if signal is not None:
                return signal
        return None

    @staticmethod
    def _write_known_combo(
        combo: NoWheelComboBox,
        value: object,
        choices: Iterable[str],
        fallback: str,
    ) -> None:
        text = str(value)
        combo.setCurrentText(text if text in choices else fallback)

    def _build_bindings(self) -> BindingRegistry:
        standard: dict[str, QWidget] = {
            "kind": self.kind_combo,
            "text": self.text_edit,
            "x": self.x_spin,
            "y": self.y_spin,
            "x2": self.x2_spin,
            "y2": self.y2_spin,
            "width": self.width_spin,
            "height": self.height_spin,
            "angle": self.angle_spin,
            "color": self.color_button,
            "line_style": self.line_style_combo,
            "alpha": self.alpha_spin,
            "fill": self.fill_check,
            "font_size": self.font_spin,
            "arrow_head_size": self.arrow_head_spin,
        }
        bindings: list[FieldBinding[Any]] = []
        for field, widget in standard.items():
            if isinstance(widget, AnnotationTextEdit):
                read, write = widget.text, widget.setText
            elif isinstance(widget, NoWheelComboBox):
                read = widget.currentText
                if field == "kind":
                    write = lambda value, target=widget: self._write_known_combo(
                        target, value, ANNOTATION_KINDS, "text"
                    )
                else:
                    write = lambda value, target=widget: self._write_known_combo(
                        target, value, ANNOTATION_LINE_STYLES, "solid"
                    )
            elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                read, write = widget.value, widget.setValue
            elif isinstance(widget, QCheckBox):
                read, write = widget.isChecked, widget.setChecked
            elif isinstance(widget, QPushButton):
                read = widget.text
                write = lambda value, target=widget: self._set_color(target, str(value))
            else:
                raise TypeError(f"Unsupported annotation widget: {type(widget).__name__}")
            signal = None if widget is self.color_button else self._signal(widget)
            bindings.append(FieldBinding(field, widget, read, write, signal))
        return BindingRegistry(bindings)

    def _connect_local_behaviour(self) -> None:
        self.list_widget.currentRowChanged.connect(self._handle_current_row_changed)
        self.add_button.clicked.connect(lambda _checked=False: self.add_requested.emit())
        self.remove_button.clicked.connect(lambda _checked=False: self.remove_requested.emit())
        self.color_button.clicked.connect(lambda _checked=False: self.color_requested.emit())
        for binding in self.bindings:
            for signal in binding.signals():
                signal.connect(
                    lambda *_args, source=binding.widget: self._emit_changed(source)
                )

    def _emit_changed(self, source: QWidget) -> None:
        if not self.loading_annotation_inputs:
            self.changed.emit(source)

    @property
    def annotations(self) -> list[AnnotationConfig]:
        """The mutable config list currently represented by the list widget."""

        return self._annotations

    def set_annotations(
        self,
        annotations: Iterable[AnnotationConfig] | None,
        *,
        preserve_selection: bool = True,
    ) -> None:
        """Represent ``annotations`` and refresh the list without user signals.

        A list is retained by reference so the coordinator and panel can share
        the graph's authoritative annotation collection. Other iterables are
        materialized once.
        """

        if annotations is None:
            items: list[AnnotationConfig] = []
        else:
            items = annotations if isinstance(annotations, list) else list(annotations)
        if any(not isinstance(annotation, AnnotationConfig) for annotation in items):
            raise TypeError("set_annotations expects AnnotationConfig items")
        self._annotations = items
        self.refresh_annotation_list(preserve_selection=preserve_selection)

    def refresh_annotation_list(self, *, preserve_selection: bool = True) -> None:
        """Rebuild labels/tooltips while preserving valid index selections."""

        current = self.list_widget.currentRow() if preserve_selection else -1
        selected = set(self.selected_annotation_indices()) if preserve_selection else set()
        with signals_blocked(self.list_widget):
            self.list_widget.clear()
            for index, annotation in enumerate(self._annotations):
                item = QListWidgetItem(self.annotation_list_label(annotation, index))
                item.setToolTip(self.annotation_list_tooltip(annotation, index))
                self.list_widget.addItem(item)
            if self._annotations:
                current = min(max(current, 0), len(self._annotations) - 1)
                self.list_widget.setCurrentRow(current)
                for index in selected:
                    if 0 <= index < self.list_widget.count():
                        self.list_widget.item(index).setSelected(True)
            else:
                current = -1
        if current >= 0:
            self.update_annotation_inputs(current)

    @staticmethod
    def annotation_list_label(
        annotation: AnnotationConfig,
        index: int | None = None,
    ) -> str:
        prefix = f"{index + 1}. " if index is not None else ""
        text = " ".join(annotation.text.strip().split()) or annotation.kind
        if len(text) > 28:
            text = text[:25] + "..."
        return f"{prefix}{annotation.kind} | {text} | x={annotation.x:.3f}, y={annotation.y:.3f}"

    @staticmethod
    def annotation_list_tooltip(
        annotation: AnnotationConfig,
        index: int | None = None,
    ) -> str:
        prefix = f"Annotation {index + 1}\n" if index is not None else ""
        text = annotation.text.strip() or "(no text)"
        return (
            f"{prefix}"
            f"Type: {annotation.kind}\n"
            f"Text: {text}\n"
            f"Position: x={annotation.x:.4f}, y={annotation.y:.4f}\n"
            f"Size: width={annotation.width:.4f}, height={annotation.height:.4f}"
        )

    def update_annotation_list_item(self, row: int) -> None:
        if 0 <= row < len(self._annotations) and row < self.list_widget.count():
            annotation = self._annotations[row]
            item = self.list_widget.item(row)
            item.setText(self.annotation_list_label(annotation, row))
            item.setToolTip(self.annotation_list_tooltip(annotation, row))

    def selected_annotation_indices(self) -> list[int]:
        rows = sorted({self.list_widget.row(item) for item in self.list_widget.selectedItems()})
        if rows:
            return [row for row in rows if 0 <= row < len(self._annotations)]
        row = self.list_widget.currentRow()
        return [row] if 0 <= row < len(self._annotations) else []

    # Shorter synonym for callers that do not need migration-era naming.
    def selected_indices(self) -> list[int]:
        return self.selected_annotation_indices()

    def current_annotation(self) -> AnnotationConfig | None:
        row = self.list_widget.currentRow()
        return self._annotations[row] if 0 <= row < len(self._annotations) else None

    def set_selection(
        self,
        indices: Iterable[int],
        *,
        current: int | None = None,
        emit: bool = True,
    ) -> int:
        """Select valid indices, load the current item, and return its index."""

        valid = sorted({
            int(index)
            for index in indices
            if 0 <= int(index) < len(self._annotations)
        })
        if current is None or current not in valid:
            current = valid[0] if valid else -1
        with signals_blocked(self.list_widget):
            self.list_widget.clearSelection()
            self.list_widget.setCurrentRow(current)
            for index in valid:
                self.list_widget.item(index).setSelected(True)
        if current >= 0:
            self.update_annotation_inputs(current)
        if emit:
            self.selection_changed.emit(current)
        return current

    def set_current_index(self, index: int, *, emit: bool = True) -> int:
        valid = index if 0 <= index < len(self._annotations) else -1
        return self.set_selection(() if valid < 0 else (valid,), current=valid, emit=emit)

    # Existing canvas code uses this semantic name.
    def select_single_annotation(self, index: int) -> None:
        self.set_current_index(index)

    def _handle_current_row_changed(self, index: int) -> None:
        if 0 <= index < len(self._annotations):
            self.update_annotation_inputs(index)
        self.selection_changed.emit(index)

    def load_annotation(self, annotation: AnnotationConfig) -> None:
        """Load one config into every editor without emitting ``changed``."""

        if not isinstance(annotation, AnnotationConfig):
            raise TypeError("load_annotation expects AnnotationConfig")
        self.loading_annotation_inputs = True
        try:
            self.bindings.load(annotation)
        finally:
            self.loading_annotation_inputs = False

    def load(self, annotation: AnnotationConfig) -> None:
        self.load_annotation(annotation)

    def update_annotation_inputs(self, index: int) -> None:
        if 0 <= index < len(self._annotations):
            self.load_annotation(self._annotations[index])

    def update_annotation(
        self,
        annotation: AnnotationConfig,
    ) -> AnnotationConfig:
        """Write editor state to one config and normalize derived endpoints."""

        if not isinstance(annotation, AnnotationConfig):
            raise TypeError("update_annotation expects AnnotationConfig")
        self.bindings.update(annotation)
        annotation.x2 = annotation.x + annotation.width
        annotation.y2 = annotation.y + annotation.height
        with signals_blocked(self.x2_spin, self.y2_spin):
            self.x2_spin.setValue(annotation.x2)
            self.y2_spin.setValue(annotation.y2)
        row = next(
            (
                index
                for index, candidate in enumerate(self._annotations)
                if candidate is annotation
            ),
            -1,
        )
        if row >= 0:
            self.update_annotation_list_item(row)
        return annotation

    def update(self, annotation: AnnotationConfig) -> AnnotationConfig:
        return self.update_annotation(annotation)

    def update_current(self) -> AnnotationConfig | None:
        annotation = self.current_annotation()
        return self.update_annotation(annotation) if annotation is not None else None

    # Existing main-window naming, without render/undo side effects.
    def update_selected_annotation(self) -> AnnotationConfig | None:
        return self.update_current()

    def set_color(self, color: str) -> None:
        """Update the color control; the coordinator decides when to commit."""

        self._set_color(self.color_button, color)

    def connect_changed(self, callback: Callable[[QWidget], None]) -> None:
        self.changed.connect(callback)

    @property
    def compatibility_widgets(self) -> dict[str, QWidget]:
        return {
            old_name: getattr(self, panel_name)
            for old_name, panel_name in ANNOTATION_WIDGET_ALIASES.items()
        }

    def install_compatibility_aliases(self, target: object) -> None:
        """Expose old window widget attributes while callers migrate."""

        for name, widget in self.compatibility_widgets.items():
            setattr(target, name, widget)


__all__ = [
    "ANNOTATION_KINDS",
    "ANNOTATION_WIDGET_ALIASES",
    "AnnotationSettingsPanel",
]
