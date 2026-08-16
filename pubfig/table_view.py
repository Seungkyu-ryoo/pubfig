"""Virtualized editable DataFrame table used by the pubfig GUI.

QTableWidget creates one C++ item per cell, which makes project switching
painfully slow for scientific datasets with tens of thousands of rows.
QTableView asks this model only for visible cells while the compatibility
helpers keep the rest of the application API small and familiar.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from pandas.api.types import is_object_dtype
from PySide6.QtCore import (
    QAbstractTableModel,
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
    Qt,
    Signal,
)
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QTableView

from .sheet_data import next_column_name


ROLE_ROW = 0
NAME_ROW = 1


class DataFrameTableModel(QAbstractTableModel):
    """Editable, virtual Qt model backed directly by a pandas DataFrame."""

    cell_edited = Signal(int, int, str)

    def __init__(self) -> None:
        super().__init__()
        self._df = pd.DataFrame()
        self._column_names: list[str] = []
        self._display_headers: list[str] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._df)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._column_names)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or role not in (Qt.DisplayRole, Qt.EditRole):
            return None
        value = self._df.iat[index.row(), index.column()]
        return "" if pd.isna(value) else str(value)

    def setData(self, index: QModelIndex, value, role: int = Qt.EditRole) -> bool:
        if not index.isValid() or role not in (Qt.EditRole, Qt.DisplayRole):
            return False
        text = "" if value is None else str(value)
        current = self.data(index, Qt.EditRole)
        if current == text:
            return True
        column = index.column()
        self._ensure_object_columns(column, column)
        self._df.iat[index.row(), column] = text
        self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])
        self.cell_edited.emit(index.row(), index.column(), text)
        return True

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            if 0 <= section < len(self._display_headers):
                return self._display_headers[section]
            return ""
        if section == ROLE_ROW:
            return "Role"
        if section == NAME_ROW:
            return "Name"
        return str(section - NAME_ROW)

    def set_dataframe(self, df: pd.DataFrame) -> None:
        self.beginResetModel()
        self._df = df
        self._column_names = list(map(str, df.columns))
        # pubfig treats column identifiers as UI labels. Keep the backing frame
        # aligned with those canonical strings so row concatenation cannot
        # create extra columns when an imported DataFrame used numeric headers.
        self._df.columns = self._column_names
        self._display_headers = self._column_names.copy()
        self.endResetModel()

    def dataframe_copy(self) -> pd.DataFrame:
        copied = self._df.copy(deep=True)
        copied.columns = self._column_names.copy()
        return copied

    def dataframe(self) -> pd.DataFrame:
        """Return the live backing frame.

        Structural model operations replace the backing DataFrame, so the
        application uses this after those operations to keep its own reference
        in sync without resetting the view (and losing the current selection).
        """
        self._df.columns = self._column_names
        return self._df

    def text(self, row: int, column: int) -> str:
        if not self.valid_cell(row, column):
            return ""
        return self.data(self.index(row, column), Qt.DisplayRole)

    def set_text(self, row: int, column: int, text: str) -> None:
        if self.valid_cell(row, column):
            self.setData(self.index(row, column), text, Qt.EditRole)

    def set_block(self, start_row: int, start_column: int, grid: list[list[str]]) -> None:
        """Write a clipboard grid and notify the view only once.

        Ragged input rows leave the unmatched cells in the rectangular block
        unchanged, matching the previous per-cell paste behavior.
        """
        if (
            not grid
            or start_row < 0
            or start_column < 0
            or start_row >= self.rowCount()
            or start_column >= self.columnCount()
        ):
            return
        height = min(len(grid), self.rowCount() - start_row)
        max_width = max((len(row) for row in grid[:height]), default=0)
        width = min(max_width, self.columnCount() - start_column)
        if height <= 0 or width <= 0:
            return

        row_slice = slice(start_row, start_row + height)
        column_slice = slice(start_column, start_column + width)
        block = self._df.iloc[row_slice, column_slice].to_numpy(dtype=object, copy=True)
        for row_offset, values in enumerate(grid[:height]):
            usable = min(len(values), width)
            if usable:
                block[row_offset, :usable] = [
                    "" if value is None else str(value)
                    for value in values[:usable]
                ]

        self._ensure_object_columns(start_column, start_column + width - 1)
        self._df.iloc[row_slice, column_slice] = block
        top_left = self.index(start_row, start_column)
        bottom_right = self.index(start_row + height - 1, start_column + width - 1)
        self.dataChanged.emit(top_left, bottom_right, [Qt.DisplayRole, Qt.EditRole])

    def fill_range(
        self,
        top: int,
        left: int,
        bottom: int,
        right: int,
        text: str,
    ) -> None:
        if self.rowCount() <= 0 or self.columnCount() <= 0:
            return
        top = max(0, int(top))
        left = max(0, int(left))
        bottom = min(int(bottom), self.rowCount() - 1)
        right = min(int(right), self.columnCount() - 1)
        if bottom < top or right < left:
            return
        self._ensure_object_columns(left, right)
        self._df.iloc[top : bottom + 1, left : right + 1] = str(text)
        self.dataChanged.emit(
            self.index(top, left),
            self.index(bottom, right),
            [Qt.DisplayRole, Qt.EditRole],
        )

    def _ensure_object_columns(self, first: int, last: int) -> None:
        # Spreadsheet edits are text. Convert only columns that are actually
        # edited so numeric frames round-trip losslessly until then.
        for column in range(first, last + 1):
            if not is_object_dtype(self._df.dtypes.iloc[column]):
                self._df.isetitem(column, self._df.iloc[:, column].astype(object))

    def valid_cell(self, row: int, column: int) -> bool:
        return 0 <= row < self.rowCount() and 0 <= column < self.columnCount()

    def column_name(self, column: int) -> str:
        if 0 <= column < len(self._column_names):
            return self._column_names[column]
        return ""

    def set_display_header(self, column: int, label: str) -> None:
        if not 0 <= column < len(self._display_headers):
            return
        self._display_headers[column] = label
        self.headerDataChanged.emit(Qt.Horizontal, column, column)

    def set_column_names(self, labels: list[str]) -> None:
        labels = [str(label) for label in labels]
        if len(labels) != self.columnCount():
            return
        self._column_names = labels
        self._display_headers = labels.copy()
        self._df.columns = labels
        if labels:
            self.headerDataChanged.emit(Qt.Horizontal, 0, len(labels) - 1)

    def rename_column(self, column: int, label: str) -> None:
        if not 0 <= column < self.columnCount():
            return
        self._column_names[column] = label
        self._display_headers[column] = label
        self._df.columns = self._column_names
        self.headerDataChanged.emit(Qt.Horizontal, column, column)

    def set_row_count(self, count: int) -> None:
        count = max(0, int(count))
        current = self.rowCount()
        if count == current:
            return
        if count < current:
            self.beginRemoveRows(QModelIndex(), count, current - 1)
            self._df = self._df.iloc[:count].reset_index(drop=True)
            self.endRemoveRows()
        else:
            self.beginInsertRows(QModelIndex(), current, count - 1)
            extra = pd.DataFrame(
                "",
                index=range(count - current),
                columns=self._column_names,
            )
            self._df = pd.concat([self._df, extra], ignore_index=True)
            self.endInsertRows()

    def set_column_count(self, count: int) -> None:
        count = max(0, int(count))
        current = self.columnCount()
        if count == current:
            return
        if count < current:
            self.beginRemoveColumns(QModelIndex(), count, current - 1)
            self._column_names = self._column_names[:count]
            self._display_headers = self._display_headers[:count]
            self._df = self._df.iloc[:, :count].copy()
            self._df.columns = self._column_names
            self.endRemoveColumns()
        else:
            self.beginInsertColumns(QModelIndex(), current, count - 1)
            for _ in range(count - current):
                name = self._next_column_name()
                self._df[name] = ""
                self._column_names.append(name)
                self._display_headers.append(name)
            self.endInsertColumns()

    def insert_row(self, row: int) -> None:
        row = max(0, min(int(row), self.rowCount()))
        self.beginInsertRows(QModelIndex(), row, row)
        upper = self._df.iloc[:row]
        lower = self._df.iloc[row:]
        blank = pd.DataFrame([[""] * self.columnCount()], columns=self._column_names)
        self._df = pd.concat([upper, blank, lower], ignore_index=True)
        self.endInsertRows()

    def remove_row(self, row: int) -> None:
        self.remove_rows(row, 1)

    def remove_rows(self, row: int, count: int) -> None:
        row = int(row)
        count = min(max(0, int(count)), self.rowCount() - row)
        if row < 0 or count <= 0:
            return
        last = row + count - 1
        self.beginRemoveRows(QModelIndex(), row, last)
        # Remove by position; imported frames may have duplicate index labels.
        self._df = pd.concat(
            [self._df.iloc[:row], self._df.iloc[last + 1 :]],
            ignore_index=True,
        )
        self.endRemoveRows()

    def insert_column(self, column: int, name: str | None = None) -> None:
        column = max(0, min(int(column), self.columnCount()))
        name = str(name) if name else self._next_column_name()
        self.beginInsertColumns(QModelIndex(), column, column)
        self._df.insert(column, name, "", allow_duplicates=True)
        self._column_names.insert(column, name)
        self._display_headers.insert(column, name)
        self.endInsertColumns()

    def remove_column(self, column: int) -> None:
        self.remove_columns(column, 1)

    def remove_columns(self, column: int, count: int) -> None:
        column = int(column)
        count = min(max(0, int(count)), self.columnCount() - column)
        if column < 0 or count <= 0:
            return
        last = column + count - 1
        self.beginRemoveColumns(QModelIndex(), column, last)
        del self._column_names[column : last + 1]
        del self._display_headers[column : last + 1]
        # Drop by position. DataFrames loaded from external payloads can have
        # duplicate labels, and drop(columns=[label]) would remove all of them
        # while Qt was notified that only the selected columns disappeared.
        self._df = pd.concat(
            [self._df.iloc[:, :column], self._df.iloc[:, last + 1 :]],
            axis=1,
        )
        self._df.columns = self._column_names
        self.endRemoveColumns()

    def _next_column_name(self) -> str:
        return next_column_name(self._column_names)


@dataclass(frozen=True)
class CellAdapter:
    table: "SpreadsheetTableWidget"
    _row: int
    _column: int

    def row(self) -> int:
        return self._row

    def column(self) -> int:
        return self._column

    def text(self) -> str:
        return self.table.table_model.text(self._row, self._column)

    def setText(self, text: str) -> None:
        self.table.table_model.set_text(self._row, self._column, text)


@dataclass(frozen=True)
class HeaderAdapter:
    table: "SpreadsheetTableWidget"
    _column: int

    def text(self) -> str:
        return self.table.table_model.column_name(self._column)


@dataclass(frozen=True)
class TableSelectionRange:
    top: int
    left: int
    bottom: int
    right: int

    def topRow(self) -> int:
        return self.top

    def leftColumn(self) -> int:
        return self.left

    def bottomRow(self) -> int:
        return self.bottom

    def rightColumn(self) -> int:
        return self.right


class SpreadsheetTableWidget(QTableView):
    """QTableWidget-compatible surface backed by a virtual DataFrame model."""

    pasted = Signal(int, int, str)
    copied = Signal(str)
    delete_requested = Signal()
    itemChanged = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.table_model = DataFrameTableModel()
        self.setModel(self.table_model)
        self.table_model.cell_edited.connect(self._emit_item_changed)

    def _emit_item_changed(self, row: int, column: int, _text: str) -> None:
        if not self.signalsBlocked():
            self.itemChanged.emit(CellAdapter(self, row, column))

    def set_dataframe(self, df: pd.DataFrame) -> None:
        self.table_model.set_dataframe(df)

    def dataframe_copy(self) -> pd.DataFrame:
        return self.table_model.dataframe_copy()

    def dataframe(self) -> pd.DataFrame:
        return self.table_model.dataframe()

    def rowCount(self) -> int:
        return self.table_model.rowCount()

    def columnCount(self) -> int:
        return self.table_model.columnCount()

    def item(self, row: int, column: int) -> CellAdapter | None:
        if not self.table_model.valid_cell(row, column):
            return None
        return CellAdapter(self, row, column)

    def setItem(self, row: int, column: int, item) -> None:
        text = item.text() if item is not None and hasattr(item, "text") else ""
        self.table_model.set_text(row, column, text)

    def set_block(self, start_row: int, start_column: int, grid: list[list[str]]) -> None:
        self.table_model.set_block(start_row, start_column, grid)

    def fill_range(self, selected: TableSelectionRange, text: str) -> None:
        self.table_model.fill_range(
            selected.topRow(),
            selected.leftColumn(),
            selected.bottomRow(),
            selected.rightColumn(),
            text,
        )

    def currentItem(self) -> CellAdapter | None:
        index = self.currentIndex()
        return self.item(index.row(), index.column()) if index.isValid() else None

    def currentRow(self) -> int:
        return self.currentIndex().row()

    def currentColumn(self) -> int:
        return self.currentIndex().column()

    def selectedItems(self) -> list[CellAdapter]:
        return [
            CellAdapter(self, index.row(), index.column())
            for index in sorted(
                self.selectionModel().selectedIndexes(),
                key=lambda value: (value.row(), value.column()),
            )
        ]

    def selectedRanges(self) -> list[TableSelectionRange]:
        return [
            TableSelectionRange(
                selected.top(),
                selected.left(),
                selected.bottom(),
                selected.right(),
            )
            for selected in self.selectionModel().selection()
        ]

    def setCurrentCell(
        self,
        row: int,
        column: int,
        command: QItemSelectionModel.SelectionFlag = QItemSelectionModel.ClearAndSelect,
    ) -> None:
        index = self.table_model.index(row, column)
        if index.isValid():
            self.selectionModel().setCurrentIndex(index, command)

    def setRangeSelected(self, selected: TableSelectionRange, select: bool) -> None:
        top_left = self.table_model.index(selected.topRow(), selected.leftColumn())
        bottom_right = self.table_model.index(selected.bottomRow(), selected.rightColumn())
        if not top_left.isValid() or not bottom_right.isValid():
            return
        selection = QItemSelection(top_left, bottom_right)
        command = QItemSelectionModel.Select if select else QItemSelectionModel.Deselect
        self.selectionModel().select(selection, command)

    def scrollToItem(self, item: CellAdapter) -> None:
        self.scrollTo(self.table_model.index(item.row(), item.column()))

    def horizontalHeaderItem(self, column: int) -> HeaderAdapter | None:
        if not 0 <= column < self.columnCount():
            return None
        return HeaderAdapter(self, column)

    def setHorizontalHeaderItem(self, column: int, item) -> None:
        text = item.text() if item is not None and hasattr(item, "text") else ""
        self.table_model.rename_column(column, text)

    def setHorizontalHeaderLabels(self, labels: list[str]) -> None:
        self.table_model.set_column_names(labels)

    def set_display_header(self, column: int, label: str) -> None:
        self.table_model.set_display_header(column, label)

    def setVerticalHeaderLabels(self, _labels: list[str]) -> None:
        if self.rowCount():
            self.table_model.headerDataChanged.emit(Qt.Vertical, 0, self.rowCount() - 1)

    def clear(self) -> None:
        self.set_dataframe(pd.DataFrame())

    def setRowCount(self, count: int) -> None:
        self.table_model.set_row_count(count)

    def setColumnCount(self, count: int) -> None:
        self.table_model.set_column_count(count)

    def insertRow(self, row: int) -> None:
        self.table_model.insert_row(row)

    def removeRow(self, row: int) -> None:
        self.table_model.remove_row(row)

    def removeRows(self, row: int, count: int) -> None:
        self.table_model.remove_rows(row, count)

    def insertColumn(self, column: int, name: str | None = None) -> None:
        self.table_model.insert_column(column, name)

    def removeColumn(self, column: int) -> None:
        self.table_model.remove_column(column)

    def removeColumns(self, column: int, count: int) -> None:
        self.table_model.remove_columns(column, count)

    def keyPressEvent(self, event):
        if event.modifiers() & Qt.ControlModifier and event.key() in (
            Qt.Key_Left,
            Qt.Key_Right,
            Qt.Key_Up,
            Qt.Key_Down,
        ):
            if event.modifiers() & Qt.ShiftModifier:
                self.extend_selection_to_data_edge(event.key())
            else:
                self.move_to_data_edge(event.key())
            return
        if event.matches(QKeySequence.Paste):
            text = QApplication.clipboard().text()
            if text:
                self.pasted.emit(max(self.currentRow(), 0), max(self.currentColumn(), 0), text)
            return
        if event.matches(QKeySequence.Copy):
            QApplication.clipboard().setText(self.selected_text())
            self.copied.emit("Copied selected cells.")
            return
        if event.matches(QKeySequence.Cut):
            QApplication.clipboard().setText(self.selected_text())
            self.delete_requested.emit()
            self.copied.emit("Cut selected cells.")
            return
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_requested.emit()
            return
        super().keyPressEvent(event)

    def selected_text(self) -> str:
        ranges = self.selectedRanges()
        if not ranges:
            return ""
        selected = ranges[0]
        rows: list[str] = []
        for row in range(selected.topRow(), selected.bottomRow() + 1):
            cells = [
                self.table_model.text(row, column)
                for column in range(selected.leftColumn(), selected.rightColumn() + 1)
            ]
            rows.append("\t".join(cells))
        return "\n".join(rows)

    def move_to_data_edge(self, key: int) -> None:
        if self.rowCount() <= 0 or self.columnCount() <= 0:
            return
        row = max(0, self.currentRow())
        column = max(0, self.currentColumn())
        directions = {
            Qt.Key_Left: (0, -1),
            Qt.Key_Right: (0, 1),
            Qt.Key_Up: (-1, 0),
            Qt.Key_Down: (1, 0),
        }
        target_row, target_column = self._data_edge_cell(row, column, *directions[key])
        self.setCurrentCell(target_row, target_column)
        self.scrollTo(self.table_model.index(target_row, target_column))

    def extend_selection_to_data_edge(self, key: int) -> None:
        if self.rowCount() <= 0 or self.columnCount() <= 0:
            return
        current_row = max(0, self.currentRow())
        current_column = max(0, self.currentColumn())
        anchor_row, anchor_column = self._selection_anchor(current_row, current_column)
        directions = {
            Qt.Key_Left: (0, -1),
            Qt.Key_Right: (0, 1),
            Qt.Key_Up: (-1, 0),
            Qt.Key_Down: (1, 0),
        }
        target_row, target_column = self._data_edge_cell(
            current_row,
            current_column,
            *directions[key],
        )
        selected = TableSelectionRange(
            min(anchor_row, target_row),
            min(anchor_column, target_column),
            max(anchor_row, target_row),
            max(anchor_column, target_column),
        )
        self.clearSelection()
        self.setRangeSelected(selected, True)
        self.setCurrentCell(target_row, target_column, QItemSelectionModel.NoUpdate)
        self.scrollTo(self.table_model.index(target_row, target_column))

    def _selection_anchor(self, current_row: int, current_column: int) -> tuple[int, int]:
        ranges = self.selectedRanges()
        if not ranges:
            return current_row, current_column
        block = ranges[0]
        anchor_row = block.topRow() if current_row >= block.bottomRow() else block.bottomRow()
        anchor_column = (
            block.leftColumn() if current_column >= block.rightColumn() else block.rightColumn()
        )
        return anchor_row, anchor_column

    def _data_edge_cell(self, row: int, column: int, row_step: int, column_step: int) -> tuple[int, int]:
        last_row = self.rowCount() - 1
        last_column = self.columnCount() - 1

        def in_bounds(target_row: int, target_column: int) -> bool:
            return 0 <= target_row <= last_row and 0 <= target_column <= last_column

        def filled(target_row: int, target_column: int) -> bool:
            return bool(self.table_model.text(target_row, target_column).strip())

        next_row = row + row_step
        next_column = column + column_step
        if not in_bounds(next_row, next_column):
            return row, column
        if filled(row, column) and filled(next_row, next_column):
            while in_bounds(next_row + row_step, next_column + column_step) and filled(
                next_row + row_step,
                next_column + column_step,
            ):
                next_row += row_step
                next_column += column_step
            return next_row, next_column

        edge_row, edge_column = next_row, next_column
        while in_bounds(next_row, next_column) and not filled(next_row, next_column):
            edge_row, edge_column = next_row, next_column
            next_row += row_step
            next_column += column_step
        return (next_row, next_column) if in_bounds(next_row, next_column) else (edge_row, edge_column)
