"""Standalone Qt widgets used by the pubfig user interface.

These widgets deliberately contain no application-window state.  Keeping them
here makes them independently testable and lets the main window focus on
coordinating the project model and the rendering pipeline.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDrag,
    QFont,
    QFontDatabase,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from matplotlib.markers import MarkerStyle
from matplotlib.path import Path as MatplotlibPath

from ..legend_layout import (
    legend_entries_to_text,
    legend_line_cells,
    legend_text_to_entries,
)
from ..plot_config import (
    FILLABLE_MARKERS,
    MARKER_CHOICES,
    MARKER_FILL_STYLES,
    PARTIAL_MARKER_FILL_STYLES,
    LegendEntryConfig,
)
from ..theme import color_swatch_style


class ProjectTreeWidget(QTreeWidget):
    """Project tree that asks its host to perform drag-and-drop moves.

    The widget never reparents items itself, so the project model remains the
    single source of truth.  ``node_dropped`` emits ``source_id``,
    ``target_id`` and one of ``on``, ``above``, ``below`` or ``root``.
    """

    node_dropped = Signal(str, str, str)
    rename_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._drag_source_id: str | None = None
        self._pending_drop: tuple[str, str, str] | None = None

    def startDrag(self, supported_actions) -> None:
        item = self.currentItem()
        if item is None:
            return
        mime_data = self.mimeData([item])
        if mime_data is None:
            return
        drag = QDrag(self)
        drag.setMimeData(mime_data)
        rect = self.visualItemRect(item)
        if not rect.isEmpty():
            drag.setPixmap(self.viewport().grab(rect))
        self._drag_source_id = str(item.data(0, Qt.UserRole) or "")
        self._pending_drop = None
        try:
            # Own the drag so QAbstractItemView cannot delete source rows after
            # a move. The controller alone changes the project and its view.
            drag.exec(supported_actions, Qt.MoveAction)
        finally:
            pending = self._pending_drop
            self._pending_drop = None
            self._drag_source_id = None
            self.setState(QAbstractItemView.NoState)
            self.viewport().update()
        # Rebuild only after the native drag has unwound, without a zero timer
        # that can wait for another event (or run inside the drag event loop).
        if pending is not None:
            self.node_dropped.emit(*pending)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_F2:
            item = self.currentItem()
            node_id = item.data(0, Qt.UserRole) if item is not None else ""
            if node_id:
                self.rename_requested.emit(str(node_id))
                event.accept()
                return
        super().keyPressEvent(event)

    def dropEvent(self, event) -> None:
        source_item = self.currentItem()
        target_item = self.itemAt(event.position().toPoint())
        if source_item is None:
            event.ignore()
            return
        source_id = self._drag_source_id or source_item.data(0, Qt.UserRole)
        target_id = target_item.data(0, Qt.UserRole) if target_item is not None else ""
        indicator = self.dropIndicatorPosition()
        if indicator == QAbstractItemView.OnItem:
            position = "on"
        elif indicator == QAbstractItemView.AboveItem:
            position = "above"
        elif indicator == QAbstractItemView.BelowItem:
            position = "below"
        else:
            position = "root"

        event.acceptProposedAction()
        move = (source_id or "", target_id or "", position)
        if self._drag_source_id is not None:
            self._pending_drop = move
        else:
            self.node_dropped.emit(*move)


class NoWheelComboBox(QComboBox):
    """Combo box whose value cannot accidentally change while scrolling."""

    def wheelEvent(self, event) -> None:
        event.ignore()


def _qt_marker_path(
    marker: str,
    fill_style: str = "full",
    *,
    alternate: bool = False,
) -> QPainterPath:
    """Convert Matplotlib's canonical marker geometry to a Qt painter path."""

    style = MarkerStyle(marker, fillstyle=fill_style)
    source_path = style.get_alt_path() if alternate else style.get_path()
    if source_path is None:
        return QPainterPath()
    transform = style.get_alt_transform() if alternate else style.get_transform()
    source = source_path.transformed(transform)
    target = QPainterPath()
    vertices = source.vertices
    codes = source.codes
    if not len(vertices):
        return target
    if codes is None:
        target.moveTo(float(vertices[0][0]), float(vertices[0][1]))
        for x, y in vertices[1:]:
            target.lineTo(float(x), float(y))
        return target

    index = 0
    while index < len(vertices):
        code = int(codes[index])
        x, y = map(float, vertices[index])
        if code == MatplotlibPath.MOVETO:
            target.moveTo(x, y)
            index += 1
        elif code == MatplotlibPath.LINETO:
            target.lineTo(x, y)
            index += 1
        elif code == MatplotlibPath.CURVE3 and index + 1 < len(vertices):
            end_x, end_y = map(float, vertices[index + 1])
            target.quadTo(x, y, end_x, end_y)
            index += 2
        elif code == MatplotlibPath.CURVE4 and index + 2 < len(vertices):
            control_2_x, control_2_y = map(float, vertices[index + 1])
            end_x, end_y = map(float, vertices[index + 2])
            target.cubicTo(
                x,
                y,
                control_2_x,
                control_2_y,
                end_x,
                end_y,
            )
            index += 3
        elif code == MatplotlibPath.CLOSEPOLY:
            target.closeSubpath()
            index += 1
        elif code == MatplotlibPath.STOP:
            break
        else:
            target.lineTo(x, y)
            index += 1
    return target


def marker_preview_icon(
    marker: str,
    fill_style: str = "full",
    *,
    size: int = 26,
) -> QIcon:
    """Render the actual Matplotlib marker as a compact, theme-safe icon."""

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = QColor("#4477AA")
        pen = QPen(color, 1.7)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        try:
            path = _qt_marker_path(marker, fill_style)
            alternate_path = _qt_marker_path(
                marker,
                fill_style,
                alternate=True,
            )
        except (TypeError, ValueError):
            path = QPainterPath()
            alternate_path = QPainterPath()
        complete_path = QPainterPath(path)
        complete_path.addPath(alternate_path)
        bounds = complete_path.boundingRect()
        if path.isEmpty():
            # A crossed circle makes the intentional "No marker" entry
            # distinguishable from an icon that failed to render.
            margin = 6.0
            diameter = size - margin * 2.0
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QPointF(size / 2.0, size / 2.0), diameter / 2.0, diameter / 2.0)
            painter.drawLine(
                QPointF(margin + 1.0, size - margin - 1.0),
                QPointF(size - margin - 1.0, margin + 1.0),
            )
            return QIcon(pixmap)

        available = float(size - 8)
        # Matplotlib's standard markers live in a shared one-unit box.  Using
        # that canonical extent preserves intentional relative sizes such as
        # the half-sized point marker instead of enlarging every path equally.
        extent = max(float(bounds.width()), float(bounds.height()), 1.0)
        scale = available / extent
        if marker == ",":
            # Matplotlib's pixel marker is rasterized as a single pixel even
            # though its canonical path has square-sized bounds.  Keep it
            # visibly tiny, but large enough to find in a toolbar icon.
            scale = min(scale, 2.0)
        center = bounds.center()
        painter.translate(size / 2.0, size / 2.0)
        painter.scale(scale, -scale)
        painter.translate(-center.x(), -center.y())
        painter.setBrush(
            Qt.NoBrush if fill_style == "none" else QBrush(color)
        )
        painter.drawPath(path)
        if not alternate_path.isEmpty():
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(alternate_path)
    finally:
        painter.end()
    return QIcon(pixmap)


class MarkerGridPicker(QToolButton):
    """A 12-column palette with filled, open, and partial markers."""

    choiceChanged = Signal(object)
    COLUMNS = 12

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._marker = "o"
        self._fill_style = "full"
        self._buttons: dict[tuple[str, str], QToolButton] = {}
        self._labels = dict(MARKER_CHOICES)
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setPopupMode(QToolButton.InstantPopup)
        self.setIconSize(QSize(24, 24))
        self.setMinimumWidth(150)
        self.setAccessibleName("Marker")

        self._menu = QMenu(self)
        container = QWidget(self._menu)
        grid = QGridLayout(container)
        grid.setContentsMargins(5, 5, 5, 5)
        grid.setHorizontalSpacing(2)
        grid.setVerticalSpacing(2)

        choices: list[tuple[str, str, str]] = []
        for marker, label in MARKER_CHOICES:
            choices.append((marker, "full", label))
            if marker in FILLABLE_MARKERS:
                choices.append((marker, "none", f"Open {label.lower()}"))
                for fill_style in PARTIAL_MARKER_FILL_STYLES:
                    choices.append(
                        (
                            marker,
                            fill_style,
                            f"{fill_style.capitalize()}-half {label.lower()}",
                        )
                    )

        for index, (marker, fill_style, label) in enumerate(choices):
            button = QToolButton(container)
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setFixedSize(34, 34)
            button.setIconSize(QSize(24, 24))
            button.setIcon(marker_preview_icon(marker, fill_style))
            button.setToolTip(label)
            button.setAccessibleName(label)
            button.clicked.connect(
                lambda _checked=False, code=marker, fill=fill_style: self.set_choice(
                    code,
                    fill,
                )
            )
            grid.addWidget(button, index // self.COLUMNS, index % self.COLUMNS)
            self._buttons[(marker, fill_style)] = button

        action = QWidgetAction(self._menu)
        action.setDefaultWidget(container)
        self._menu.addAction(action)
        self.setMenu(self._menu)
        self.set_choice("o", "full", emit=False)

    @property
    def option_count(self) -> int:
        return len(self._buttons)

    @property
    def grid_shape(self) -> tuple[int, int]:
        rows = (self.option_count + self.COLUMNS - 1) // self.COLUMNS
        return rows, self.COLUMNS

    def marker_code(self) -> str:
        return self._marker

    def fill_style(self) -> str:
        return self._fill_style

    def set_marker_code(self, marker: object) -> None:
        self.set_choice(str(marker), self._fill_style, emit=False)

    def set_fill_style(self, fill_style: object) -> None:
        self.set_choice(self._marker, str(fill_style), emit=False)

    def set_choice(
        self,
        marker: str,
        fill_style: str,
        *,
        emit: bool = True,
    ) -> None:
        marker = str(marker)
        fill_style = (
            fill_style if fill_style in MARKER_FILL_STYLES else "full"
        )
        if (
            fill_style in PARTIAL_MARKER_FILL_STYLES
            and marker not in FILLABLE_MARKERS
        ) or (marker in self._labels and marker not in FILLABLE_MARKERS):
            fill_style = "full"
        changed = (marker, fill_style) != (self._marker, self._fill_style)
        self._marker = marker
        self._fill_style = fill_style
        for choice, button in self._buttons.items():
            button.setChecked(choice == (marker, fill_style))

        label = self._labels.get(marker, f"Custom ({marker})")
        if fill_style == "none":
            label = f"Open {label.lower()}"
        elif fill_style in PARTIAL_MARKER_FILL_STYLES:
            label = f"{fill_style.capitalize()}-half {label.lower()}"
        self.setText(label)
        self.setIcon(marker_preview_icon(marker, fill_style))
        self.setToolTip(f"{label} — click to choose from all markers")
        if self._menu.isVisible():
            self._menu.close()
        if emit and changed:
            self.choiceChanged.emit((marker, fill_style))


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    """Double spin box whose value cannot accidentally change while scrolling."""

    def wheelEvent(self, event) -> None:
        event.ignore()


class NoWheelSpinBox(QSpinBox):
    """Spin box whose value cannot accidentally change while scrolling."""

    def wheelEvent(self, event) -> None:
        event.ignore()


class AnnotationTextEdit(QPlainTextEdit):
    """Compact annotation editor with the text-widget compatibility API."""

    def __init__(self) -> None:
        super().__init__()
        self.setMaximumHeight(70)
        self.setTabChangesFocus(True)

    def text(self) -> str:
        return self.toPlainText()

    def setText(self, value: str) -> None:
        self.setPlainText(value)

    def insert(self, value: str) -> None:
        self.insertPlainText(value)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ShiftModifier:
            self.insertPlainText("\n")
            return
        super().keyPressEvent(event)


class LegendEditorDialog(QDialog):
    """Edit a legend as free text with Origin-style series substitutions."""

    def __init__(
        self,
        candidates: list[tuple[str, str, bool]],
        entries: list[LegendEntryConfig],
        row_lengths: list[int],
        *,
        automatic: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit legend content")
        self.resize(900, 500)
        self.candidates = list(candidates)
        self._automatic = automatic
        self._loading_text_format = False
        self._current_text_color = ""

        layout = QVBoxLayout(self)
        help_label = QLabel(
            "Edit the legend as text. \\L(1) inserts series 1's marker/line "
            "sample and %(1) inserts its current label. Type any other text "
            "directly. Use a new line for a new row. Use Tab, or type another "
            "\\L(n) after a space, for another entry on the same row."
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText(
            "Type legend text, or select a series below and insert a sample."
        )
        self.text_edit.setTabChangesFocus(False)
        # Clear aliases make the editor discoverable to host code and tests
        # without reviving the old table-oriented API.
        self.editor = self.text_edit
        self.legend_text_edit = self.text_edit
        layout.addWidget(self.text_edit, 1)

        source_controls = QHBoxLayout()
        source_controls.addWidget(QLabel("Series"))
        self.candidate_combo = NoWheelComboBox()
        self.source_combo = self.candidate_combo
        for index, (source_y, candidate_label, visible) in enumerate(
            self.candidates,
            start=1,
        ):
            suffix = "" if visible else " — not shown in automatic legend"
            self.candidate_combo.addItem(
                f"{index}. {candidate_label}  [{source_y}]{suffix}",
                source_y,
            )
        self.candidate_combo.setEnabled(bool(self.candidates))
        source_controls.addWidget(self.candidate_combo, 1)
        layout.addLayout(source_controls)

        self.text_format_box = QGroupBox("Text format — entry at cursor")
        text_format_layout = QHBoxLayout(self.text_format_box)
        text_format_layout.addWidget(QLabel("Font"))
        self.text_font_family_combo = NoWheelComboBox()
        self.text_font_family_combo.addItem("Default", "")
        for family in QFontDatabase.families():
            self.text_font_family_combo.addItem(family, family)
        self.text_font_family_combo.setMinimumWidth(170)
        text_format_layout.addWidget(self.text_font_family_combo, 1)

        text_format_layout.addWidget(QLabel("Size"))
        self.text_font_size_spin = NoWheelDoubleSpinBox()
        self.text_font_size_spin.setRange(0.0, 100.0)
        self.text_font_size_spin.setDecimals(1)
        self.text_font_size_spin.setSingleStep(0.5)
        self.text_font_size_spin.setSpecialValueText("Default")
        self.text_font_size_spin.setMinimumWidth(90)
        text_format_layout.addWidget(self.text_font_size_spin)

        self.text_bold_btn = QPushButton("B")
        self.text_bold_btn.setCheckable(True)
        self.text_bold_btn.setToolTip("Bold the current legend entry")
        self.text_bold_btn.setMaximumWidth(38)
        self.text_italic_btn = QPushButton("I")
        self.text_italic_btn.setCheckable(True)
        self.text_italic_btn.setToolTip("Italicize the current legend entry")
        self.text_italic_btn.setMaximumWidth(38)
        text_format_layout.addWidget(self.text_bold_btn)
        text_format_layout.addWidget(self.text_italic_btn)

        self.text_color_btn = QPushButton("Default color")
        self.text_color_btn.setToolTip("Choose a color for the current legend entry")
        self.reset_text_format_btn = QPushButton("Reset format")
        text_format_layout.addWidget(self.text_color_btn)
        text_format_layout.addWidget(self.reset_text_format_btn)
        layout.addWidget(self.text_format_box)

        controls = QHBoxLayout()
        self.insert_sample_btn = QPushButton("Insert sample")
        self.insert_label_btn = QPushButton("Insert label")
        self.insert_both_btn = QPushButton("Insert sample + label")
        self.reset_btn = QPushButton("Reset to automatic")
        self.insert_sample_button = self.insert_sample_btn
        self.insert_label_button = self.insert_label_btn
        self.insert_both_button = self.insert_both_btn
        for button in (
            self.insert_sample_btn,
            self.insert_label_btn,
            self.insert_both_btn,
        ):
            button.setEnabled(bool(self.candidates))
        controls.addWidget(self.insert_sample_btn)
        controls.addWidget(self.insert_label_btn)
        controls.addWidget(self.insert_both_btn)
        controls.addStretch(1)
        controls.addWidget(self.reset_btn)
        layout.addLayout(controls)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.insert_sample_btn.clicked.connect(self.insert_sample)
        self.insert_label_btn.clicked.connect(self.insert_label)
        self.insert_both_btn.clicked.connect(self.insert_sample_and_label)
        self.reset_btn.clicked.connect(self.reset_automatic)
        self.text_color_btn.clicked.connect(self.choose_text_color)
        self.reset_text_format_btn.clicked.connect(self.reset_current_text_format)

        initial_text = (
            self._automatic_text()
            if automatic
            else legend_entries_to_text(
                entries,
                row_lengths,
                self._source_order(),
            )
        )
        self._set_text(initial_text)
        if not automatic:
            self._apply_saved_text_formats(entries)

        self.text_edit.textChanged.connect(self._mark_explicit)
        self.text_edit.cursorPositionChanged.connect(self._load_current_text_format)
        self.text_font_family_combo.currentIndexChanged.connect(
            self._apply_current_text_format
        )
        self.text_font_size_spin.valueChanged.connect(self._apply_current_text_format)
        self.text_bold_btn.toggled.connect(self._apply_current_text_format)
        self.text_italic_btn.toggled.connect(self._apply_current_text_format)
        self._load_current_text_format()

    def _source_order(self) -> list[str]:
        return [str(source_y) for source_y, _label, _visible in self.candidates]

    def _automatic_text(self) -> str:
        lines = [
            f"\\L({index}) %({index})"
            for index, (_source_y, _label, visible) in enumerate(
                self.candidates,
                start=1,
            )
            if visible
        ]
        return "\n".join(lines)

    def _set_text(self, text: str) -> None:
        was_blocked = self.text_edit.blockSignals(True)
        try:
            self.text_edit.setPlainText(text)
            self.text_edit.moveCursor(QTextCursor.End)
        finally:
            self.text_edit.blockSignals(was_blocked)

    @staticmethod
    def _utf16_length(text: str) -> int:
        return len(text.encode("utf-16-le")) // 2

    def _legend_cell_ranges(self) -> list[tuple[int, int, int, int]]:
        """Return QTextDocument ranges as ``start, end, row, column``."""

        if self.text_edit.toPlainText() == "":
            return []
        ranges: list[tuple[int, int, int, int]] = []
        block = self.text_edit.document().firstBlock()
        source_order = self._source_order()
        row = 0
        while block.isValid():
            block_text = block.text()
            for column, (_cell, cell_start, cell_end) in enumerate(
                legend_line_cells(block_text, source_order)
            ):
                start = block.position() + self._utf16_length(
                    block_text[:cell_start]
                )
                end = block.position() + self._utf16_length(
                    block_text[:cell_end]
                )
                ranges.append((start, end, row, column))
            block = block.next()
            row += 1
        return ranges

    def _current_legend_cell_index(self) -> int | None:
        position = self.text_edit.textCursor().position()
        ranges = self._legend_cell_ranges()
        for index, (start, end, _row, _column) in enumerate(ranges):
            if start <= position <= end:
                return index
        return len(ranges) - 1 if ranges else None

    def _text_format_for_range(self, start: int, end: int) -> QTextCharFormat:
        if end <= start:
            return QTextCharFormat()
        cursor = QTextCursor(self.text_edit.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        return cursor.charFormat()

    @staticmethod
    def _entry_text_format(entry: LegendEntryConfig) -> QTextCharFormat:
        text_format = QTextCharFormat()
        family = str(getattr(entry, "font_family", "") or "").strip()
        if family:
            text_format.setFontFamilies([family])
        try:
            point_size = float(getattr(entry, "font_size", 0.0) or 0.0)
        except (TypeError, ValueError):
            point_size = 0.0
        if point_size > 0:
            text_format.setFontPointSize(point_size)
        if bool(getattr(entry, "font_bold", False)):
            text_format.setFontWeight(QFont.Bold)
        if bool(getattr(entry, "font_italic", False)):
            text_format.setFontItalic(True)
        color = QColor(str(getattr(entry, "text_color", "") or ""))
        if color.isValid():
            text_format.setForeground(color)
        return text_format

    def _apply_saved_text_formats(self, entries: list[LegendEntryConfig]) -> None:
        original_cursor = self.text_edit.textCursor()
        for entry, (start, end, _row, _column) in zip(
            entries,
            self._legend_cell_ranges(),
        ):
            if end <= start:
                continue
            cursor = QTextCursor(self.text_edit.document())
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            cursor.setCharFormat(self._entry_text_format(entry))
        self.text_edit.setTextCursor(original_cursor)

    @staticmethod
    def _style_values_from_format(
        text_format: QTextCharFormat,
    ) -> tuple[str, float | None, bool, bool, str]:
        families = text_format.fontFamilies() or []
        family = str(families[0]) if families else ""
        raw_size = float(text_format.fontPointSize())
        point_size = raw_size if raw_size > 0 else None
        bold = text_format.fontWeight() >= QFont.Bold
        italic = bool(text_format.fontItalic())
        brush = text_format.foreground()
        color = brush.color().name() if brush.style() != Qt.NoBrush else ""
        return family, point_size, bold, italic, color

    def _load_current_text_format(self, *_args) -> None:
        if self._loading_text_format:
            return
        ranges = self._legend_cell_ranges()
        index = self._current_legend_cell_index()
        enabled = index is not None and index < len(ranges)
        self.text_format_box.setEnabled(enabled)
        if not enabled:
            self.text_format_box.setTitle("Text format — no entry")
            return

        start, end, row, column = ranges[index]
        text_format = self._text_format_for_range(start, end)
        family, point_size, bold, italic, color = self._style_values_from_format(
            text_format
        )
        self._loading_text_format = True
        try:
            family_index = self.text_font_family_combo.findData(family)
            if family and family_index < 0:
                self.text_font_family_combo.addItem(family, family)
                family_index = self.text_font_family_combo.count() - 1
            self.text_font_family_combo.setCurrentIndex(max(family_index, 0))
            self.text_font_size_spin.setValue(point_size or 0.0)
            self.text_bold_btn.setChecked(bold)
            self.text_italic_btn.setChecked(italic)
            self._current_text_color = color
            self._update_text_color_button()
        finally:
            self._loading_text_format = False
        self.text_format_box.setTitle(
            f"Text format — row {row + 1}, entry {column + 1}"
        )

    def _update_text_color_button(self) -> None:
        if self._current_text_color:
            self.text_color_btn.setText(self._current_text_color)
            self.text_color_btn.setStyleSheet(
                color_swatch_style(self._current_text_color)
            )
        else:
            self.text_color_btn.setText("Default color")
            self.text_color_btn.setStyleSheet("")

    def _format_from_controls(self) -> QTextCharFormat:
        text_format = QTextCharFormat()
        family = str(self.text_font_family_combo.currentData() or "")
        if family:
            text_format.setFontFamilies([family])
        point_size = self.text_font_size_spin.value()
        if point_size > 0:
            text_format.setFontPointSize(point_size)
        if self.text_bold_btn.isChecked():
            text_format.setFontWeight(QFont.Bold)
        if self.text_italic_btn.isChecked():
            text_format.setFontItalic(True)
        color = QColor(self._current_text_color)
        if color.isValid():
            text_format.setForeground(color)
        return text_format

    def _apply_current_text_format(self, *_args) -> None:
        if self._loading_text_format:
            return
        ranges = self._legend_cell_ranges()
        index = self._current_legend_cell_index()
        if index is None or index >= len(ranges):
            return
        start, end, _row, _column = ranges[index]
        text_format = self._format_from_controls()
        original_cursor = self.text_edit.textCursor()
        if end > start:
            cursor = QTextCursor(self.text_edit.document())
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            cursor.setCharFormat(text_format)
        else:
            self.text_edit.setCurrentCharFormat(text_format)
        self.text_edit.setTextCursor(original_cursor)
        self._automatic = False

    def choose_text_color(self) -> None:
        initial = QColor(self._current_text_color or "#000000")
        chosen = QColorDialog.getColor(initial, self, "Choose legend text color")
        if chosen.isValid():
            self.set_current_text_color(chosen.name())

    def set_current_text_color(self, color: str) -> None:
        chosen = QColor(str(color))
        if not chosen.isValid():
            return
        self._current_text_color = chosen.name()
        self._update_text_color_button()
        self._apply_current_text_format()

    def reset_current_text_format(self) -> None:
        if self._current_legend_cell_index() is None:
            return
        self._loading_text_format = True
        try:
            self.text_font_family_combo.setCurrentIndex(0)
            self.text_font_size_spin.setValue(0.0)
            self.text_bold_btn.setChecked(False)
            self.text_italic_btn.setChecked(False)
            self._current_text_color = ""
            self._update_text_color_button()
        finally:
            self._loading_text_format = False
        self._apply_current_text_format()

    def _mark_explicit(self, *_args) -> None:
        self._automatic = False

    def _selected_series_number(self) -> int | None:
        index = self.candidate_combo.currentIndex()
        if index < 0 or index >= len(self.candidates):
            return None
        return index + 1

    def _insert_text(self, text: str) -> None:
        if not text:
            return
        cursor = self.text_edit.textCursor()
        current_text = self.text_edit.toPlainText()
        if (
            not cursor.hasSelection()
            and cursor.atEnd()
            and current_text
            and not current_text.endswith(("\n", "\t", " "))
        ):
            text = "\n" + text
        self.text_edit.insertPlainText(text)
        self.text_edit.setFocus()

    def insert_sample(self) -> None:
        number = self._selected_series_number()
        if number is None:
            return
        self._insert_text(f"\\L({number})")

    def insert_label(self) -> None:
        number = self._selected_series_number()
        if number is None:
            return
        self._insert_text(f"%({number})")

    def insert_sample_and_label(self) -> None:
        number = self._selected_series_number()
        if number is None:
            return
        self._insert_text(f"\\L({number}) %({number})")

    def reset_automatic(self) -> None:
        self._set_text(self._automatic_text())
        self._automatic = True
        self._load_current_text_format()

    def result_config(
        self,
    ) -> tuple[list[LegendEntryConfig] | None, list[int]]:
        if self._automatic:
            return None, []
        entries, row_lengths = legend_text_to_entries(
            self.text_edit.toPlainText(),
            self._source_order(),
        )
        for entry, (start, end, _row, _column) in zip(
            entries,
            self._legend_cell_ranges(),
        ):
            (
                entry.font_family,
                entry.font_size,
                entry.font_bold,
                entry.font_italic,
                entry.text_color,
            ) = self._style_values_from_format(
                self._text_format_for_range(start, end)
            )
        return entries, row_lengths


class GradientEditorWidget(QWidget):
    """Editable multi-stop color gradient.

    Each stop is ``[position 0-1, "#rrggbb", alpha 0-1]``. Stops can be
    dragged, recolored, added by double-clicking the bar, and removed with a
    right-click while at least two stops remain.
    """

    changed = Signal()

    BAR_TOP = 4
    BAR_HEIGHT = 26
    HANDLE_SIZE = 12

    def __init__(self) -> None:
        super().__init__()
        self.stops: list[list] = [[0.0, "#0072B2", 1.0], [1.0, "#D55E00", 1.0]]
        self.selected_index = 0
        self._drag_index: int | None = None
        self.setMinimumHeight(self.BAR_TOP + self.BAR_HEIGHT + self.HANDLE_SIZE + 10)
        self.setMinimumWidth(180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip(
            "Double-click the bar to add a color stop.\n"
            "Drag a stop to move where the color changes.\n"
            "Double-click a stop to pick its color and opacity.\n"
            "Right-click a stop to remove it (at least two stops remain)."
        )

    def stops_payload(self) -> list[list]:
        return [[float(pos), str(color), float(alpha)] for pos, color, alpha in self.stops]

    def set_stops(self, payload) -> None:
        stops: list[list] = []
        for item in payload or []:
            try:
                pos, color, alpha = item
                pos = min(max(float(pos), 0.0), 1.0)
                alpha = min(max(float(alpha), 0.0), 1.0)
                if not QColor(str(color)).isValid():
                    continue
                stops.append([pos, str(color), alpha])
            except (TypeError, ValueError):
                continue
        if len(stops) >= 2:
            self.stops = stops
            self.selected_index = 0
            self.update()

    def sorted_stops(self) -> list[list]:
        return sorted(self.stops, key=lambda stop: stop[0])

    def sample(self, t: float) -> tuple[str, float]:
        """Return the interpolated ``(hex color, alpha)`` at ``t``."""

        stops = self.sorted_stops()
        t = min(max(t, 0.0), 1.0)
        if t <= stops[0][0]:
            return stops[0][1], stops[0][2]
        if t >= stops[-1][0]:
            return stops[-1][1], stops[-1][2]
        for (pos0, color0, alpha0), (pos1, color1, alpha1) in zip(stops, stops[1:]):
            if pos0 <= t <= pos1:
                span = max(pos1 - pos0, 1e-9)
                fraction = (t - pos0) / span
                start = QColor(color0)
                end = QColor(color1)
                red = round(start.red() + (end.red() - start.red()) * fraction)
                green = round(start.green() + (end.green() - start.green()) * fraction)
                blue = round(start.blue() + (end.blue() - start.blue()) * fraction)
                alpha = alpha0 + (alpha1 - alpha0) * fraction
                return QColor(red, green, blue).name(), alpha
        return stops[-1][1], stops[-1][2]

    def _bar_left(self) -> float:
        return self.HANDLE_SIZE / 2 + 1

    def _bar_right(self) -> float:
        return self.width() - self.HANDLE_SIZE / 2 - 1

    def _pos_to_x(self, pos: float) -> float:
        return self._bar_left() + pos * (self._bar_right() - self._bar_left())

    def _x_to_pos(self, x: float) -> float:
        span = max(self._bar_right() - self._bar_left(), 1e-9)
        return min(max((x - self._bar_left()) / span, 0.0), 1.0)

    def _stop_at(self, point: QPointF) -> int | None:
        best_idx: int | None = None
        best_distance = self.HANDLE_SIZE / 2 + 3
        for idx, (pos, _color, _alpha) in enumerate(self.stops):
            distance = abs(point.x() - self._pos_to_x(pos))
            if distance <= best_distance:
                best_distance = distance
                best_idx = idx
        return best_idx

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        bar_left = self._bar_left()
        bar_width = self._bar_right() - bar_left

        cell = 6
        painter.save()
        painter.setClipRect(int(bar_left), self.BAR_TOP, int(bar_width), self.BAR_HEIGHT)
        for row in range(self.BAR_HEIGHT // cell + 1):
            for col in range(int(bar_width) // cell + 1):
                shade = "#e3e6ea" if (row + col) % 2 == 0 else "#ffffff"
                painter.fillRect(
                    int(bar_left + col * cell),
                    self.BAR_TOP + row * cell,
                    cell,
                    cell,
                    QColor(shade),
                )
        gradient = QLinearGradient(bar_left, 0.0, self._bar_right(), 0.0)
        for pos, color, alpha in self.sorted_stops():
            stop_color = QColor(color)
            stop_color.setAlphaF(min(max(alpha, 0.0), 1.0))
            gradient.setColorAt(min(max(pos, 0.0), 1.0), stop_color)
        painter.fillRect(int(bar_left), self.BAR_TOP, int(bar_width), self.BAR_HEIGHT, QBrush(gradient))
        painter.restore()
        painter.setPen(QPen(QColor("#b9c0ca")))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(int(bar_left), self.BAR_TOP, int(bar_width), self.BAR_HEIGHT)

        handle_top = self.BAR_TOP + self.BAR_HEIGHT + 2
        for idx, (pos, color, _alpha) in enumerate(self.stops):
            x = self._pos_to_x(pos)
            triangle = QPolygonF(
                [
                    QPointF(x, handle_top),
                    QPointF(x - self.HANDLE_SIZE / 2, handle_top + self.HANDLE_SIZE),
                    QPointF(x + self.HANDLE_SIZE / 2, handle_top + self.HANDLE_SIZE),
                ]
            )
            painter.setBrush(QBrush(QColor(color)))
            outline = QColor("#2f6fed") if idx == self.selected_index else QColor("#444444")
            painter.setPen(QPen(outline, 1.6 if idx == self.selected_index else 1.0))
            painter.drawPolygon(triangle)

    def mousePressEvent(self, event) -> None:
        idx = self._stop_at(event.position())
        if event.button() == Qt.RightButton:
            if idx is not None and len(self.stops) > 2:
                del self.stops[idx]
                self.selected_index = min(self.selected_index, len(self.stops) - 1)
                self.update()
                self.changed.emit()
            return
        if idx is not None:
            self.selected_index = idx
            self._drag_index = idx
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_index is None:
            return
        self.stops[self._drag_index][0] = self._x_to_pos(event.position().x())
        self.update()
        self.changed.emit()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_index = None

    def mouseDoubleClickEvent(self, event) -> None:
        idx = self._stop_at(event.position())
        if idx is not None:
            self.edit_stop(idx)
            return
        position = self._x_to_pos(event.position().x())
        color, alpha = self.sample(position)
        self.stops.append([position, color, alpha])
        self.selected_index = len(self.stops) - 1
        self.update()
        self.changed.emit()

    def edit_stop(self, idx: int) -> None:
        _pos, color, alpha = self.stops[idx]
        initial = QColor(color)
        initial.setAlphaF(min(max(alpha, 0.0), 1.0))
        chosen = QColorDialog.getColor(initial, self, "Gradient stop color", QColorDialog.ShowAlphaChannel)
        if not chosen.isValid():
            return
        self.stops[idx][1] = chosen.name()
        self.stops[idx][2] = chosen.alphaF()
        self.selected_index = idx
        self.update()
        self.changed.emit()


__all__ = [
    "AnnotationTextEdit",
    "GradientEditorWidget",
    "LegendEditorDialog",
    "MarkerGridPicker",
    "NoWheelComboBox",
    "NoWheelDoubleSpinBox",
    "NoWheelSpinBox",
    "ProjectTreeWidget",
    "marker_preview_icon",
]


def integer_spin(value: int, minimum: int, maximum: int) -> NoWheelSpinBox:
    """Build an aligned integer control using the editor's common sizing."""
    spin = NoWheelSpinBox()
    spin.setRange(minimum, maximum)
    spin.setValue(value)
    spin.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    spin.setMinimumWidth(86)
    return spin
