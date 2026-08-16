"""Standalone Qt widgets used by the pubfig user interface.

These widgets deliberately contain no application-window state.  Keeping them
here makes them independently testable and lets the main window focus on
coordinating the project model and the rendering pipeline.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from ..legend_layout import new_row_flags
from ..plot_config import LegendEntryConfig


class ProjectTreeWidget(QTreeWidget):
    """Project tree that asks its host to perform drag-and-drop moves.

    The widget never reparents items itself, so the project model remains the
    single source of truth.  ``node_dropped`` emits ``source_id``,
    ``target_id`` and one of ``on``, ``above``, ``below`` or ``root``.
    """

    node_dropped = Signal(str, str, str)

    def dropEvent(self, event) -> None:
        source_item = self.currentItem()
        target_item = self.itemAt(event.position().toPoint())
        if source_item is None:
            event.ignore()
            return
        source_id = source_item.data(0, Qt.UserRole)
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

        # The host rebuilds the widget from its model after handling the move.
        event.acceptProposedAction()
        QTimer.singleShot(
            0,
            lambda s=source_id or "", t=target_id or "", p=position: self.node_dropped.emit(s, t, p),
        )


class NoWheelComboBox(QComboBox):
    """Combo box whose value cannot accidentally change while scrolling."""

    def wheelEvent(self, event) -> None:
        event.ignore()


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
    """Edit legend handle sources independently from displayed names."""

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
        self.setWindowTitle("Edit legend entries")
        self.resize(720, 420)
        self.candidates = candidates
        self._automatic = automatic

        layout = QVBoxLayout(self)
        help_label = QLabel(
            "Choose the series that supplies each marker/line, then enter its "
            "independent legend name. Uncheck “New row” to place an entry on "
            "the same legend row as the previous entry."
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(
            ["Marker / line source", "Legend name", "New row"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.itemChanged.connect(self._mark_explicit)
        layout.addWidget(self.table)

        controls = QHBoxLayout()
        self.add_btn = QPushButton("Add entry")
        self.remove_btn = QPushButton("Remove")
        self.up_btn = QPushButton("Move up")
        self.down_btn = QPushButton("Move down")
        self.reset_btn = QPushButton("Reset to automatic")
        controls.addWidget(self.add_btn)
        controls.addWidget(self.remove_btn)
        controls.addWidget(self.up_btn)
        controls.addWidget(self.down_btn)
        controls.addStretch(1)
        controls.addWidget(self.reset_btn)
        layout.addLayout(controls)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.add_btn.clicked.connect(self.add_entry)
        self.remove_btn.clicked.connect(self.remove_selected_entry)
        self.up_btn.clicked.connect(lambda: self.move_selected_entry(-1))
        self.down_btn.clicked.connect(lambda: self.move_selected_entry(1))
        self.reset_btn.clicked.connect(self.reset_automatic)

        new_row_flags = self._new_row_flags(len(entries), row_lengths)
        self._set_rows(
            [
                (entry.source_y, entry.label, new_row_flags[index])
                for index, entry in enumerate(entries)
            ],
            mark_explicit=False,
        )

    @staticmethod
    def _new_row_flags(entry_count: int, row_lengths: list[int]) -> list[bool]:
        return new_row_flags(entry_count, row_lengths)

    def _set_rows(
        self,
        rows: list[tuple[str, str, bool]],
        *,
        mark_explicit: bool,
    ) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for source_y, label, new_row in rows:
            self._append_row(source_y, label, new_row)
        self.table.blockSignals(False)
        self._enforce_first_row()
        if rows:
            self.table.selectRow(0)
        if mark_explicit:
            self._mark_explicit()

    def _append_row(self, source_y: str, label: str, new_row: bool) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)

        source_combo = NoWheelComboBox()
        for candidate_y, candidate_label, visible in self.candidates:
            suffix = "" if visible else " — hidden by Show in legend"
            source_combo.addItem(
                f"{candidate_label}  [{candidate_y}]{suffix}",
                candidate_y,
            )
        source_index = source_combo.findData(source_y)
        if source_index < 0 and source_y:
            source_combo.addItem(f"Missing series  [{source_y}]", source_y)
            source_index = source_combo.count() - 1
        if source_index >= 0:
            source_combo.setCurrentIndex(source_index)
        source_combo.currentIndexChanged.connect(self._mark_explicit)
        self.table.setCellWidget(row, 0, source_combo)

        self.table.setItem(row, 1, QTableWidgetItem(label))

        new_row_check = QCheckBox()
        new_row_check.setChecked(bool(new_row))
        new_row_check.setToolTip(
            "Checked: start a new legend row. Unchecked: continue the previous row."
        )
        new_row_check.toggled.connect(self._mark_explicit)
        holder = QWidget()
        holder_layout = QHBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.addStretch(1)
        holder_layout.addWidget(new_row_check)
        holder_layout.addStretch(1)
        self.table.setCellWidget(row, 2, holder)

    def _source_combo(self, row: int) -> QComboBox | None:
        widget = self.table.cellWidget(row, 0)
        return widget if isinstance(widget, QComboBox) else None

    def _new_row_check(self, row: int) -> QCheckBox | None:
        holder = self.table.cellWidget(row, 2)
        return holder.findChild(QCheckBox) if holder is not None else None

    def _mark_explicit(self, *_args) -> None:
        self._automatic = False

    def _enforce_first_row(self) -> None:
        for row in range(self.table.rowCount()):
            checkbox = self._new_row_check(row)
            if checkbox is None:
                continue
            if row == 0:
                checkbox.blockSignals(True)
                checkbox.setChecked(True)
                checkbox.setEnabled(False)
                checkbox.blockSignals(False)
            else:
                checkbox.setEnabled(True)

    def rows_payload(self) -> list[tuple[str, str, bool]]:
        rows: list[tuple[str, str, bool]] = []
        for row in range(self.table.rowCount()):
            source_combo = self._source_combo(row)
            source_y = str(source_combo.currentData() or "") if source_combo else ""
            label_item = self.table.item(row, 1)
            label = label_item.text().strip() if label_item is not None else ""
            checkbox = self._new_row_check(row)
            rows.append((source_y, label, bool(checkbox and checkbox.isChecked())))
        return rows

    def add_entry(self) -> None:
        if not self.candidates:
            return
        used = {source_y for source_y, _label, _new_row in self.rows_payload()}
        source_y, label, _visible = next(
            (
                candidate
                for candidate in self.candidates
                if candidate[0] not in used
            ),
            self.candidates[0],
        )
        self.table.blockSignals(True)
        self._append_row(source_y, label, True)
        self.table.blockSignals(False)
        self._enforce_first_row()
        self.table.selectRow(self.table.rowCount() - 1)
        self._mark_explicit()

    def remove_selected_entry(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        rows = self.rows_payload()
        removed_started_row = rows[row][2]
        del rows[row]
        if removed_started_row and row < len(rows):
            source_y, label, _new_row = rows[row]
            rows[row] = (source_y, label, True)
        self._set_rows(rows, mark_explicit=True)
        if rows:
            self.table.selectRow(min(row, len(rows) - 1))

    def move_selected_entry(self, offset: int) -> None:
        row = self.table.currentRow()
        target = row + offset
        if row < 0 or target < 0 or target >= self.table.rowCount():
            return
        rows = self.rows_payload()
        source_y, label, _new_row = rows[row]
        target_y, target_label, _target_new_row = rows[target]
        rows[row] = (target_y, target_label, rows[row][2])
        rows[target] = (source_y, label, rows[target][2])
        self._set_rows(rows, mark_explicit=True)
        self.table.selectRow(target)

    def reset_automatic(self) -> None:
        rows = [
            (source_y, label, True)
            for source_y, label, visible in self.candidates
            if visible
        ]
        self._set_rows(rows, mark_explicit=False)
        self._automatic = True

    def result_config(
        self,
    ) -> tuple[list[LegendEntryConfig] | None, list[int]]:
        if self._automatic:
            return None, []
        rows = self.rows_payload()
        entries = [
            LegendEntryConfig(source_y=source_y, label=label)
            for source_y, label, _new_row in rows
            if source_y
        ]
        if not entries:
            return [], []

        row_lengths: list[int] = []
        current_length = 0
        for index, (_source_y, _label, new_row) in enumerate(rows):
            if index > 0 and new_row:
                row_lengths.append(current_length)
                current_length = 0
            current_length += 1
        if current_length:
            row_lengths.append(current_length)
        if all(length == 1 for length in row_lengths):
            row_lengths = []
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
    "NoWheelComboBox",
    "NoWheelDoubleSpinBox",
    "NoWheelSpinBox",
    "ProjectTreeWidget",
]
