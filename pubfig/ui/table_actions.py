"""Table menus and edit commands shared by cells and row/column headers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal
import unicodedata

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QInputDialog, QMenu, QMessageBox

from ..sheet_data import DATA_START_ROW, NAME_ROW, ROLE_ROW
from .binding import signals_blocked

if TYPE_CHECKING:
    from .main_window import GraphDrawerWindow


class TableActions:
    """Coordinate table edits with the window's document and undo history."""

    def __init__(self, window: GraphDrawerWindow) -> None:
        self.window = window
        self.table = window.table
        horizontal = self.table.horizontalHeader()
        vertical = self.table.verticalHeader()
        horizontal.setContextMenuPolicy(Qt.CustomContextMenu)
        vertical.setContextMenuPolicy(Qt.CustomContextMenu)
        horizontal.customContextMenuRequested.connect(self.show_column_menu)
        vertical.customContextMenuRequested.connect(self.show_row_menu)
        horizontal.sectionDoubleClicked.connect(self.rename_column)
        horizontal.setToolTip("Double-click to rename; right-click for column actions.")
        vertical.setToolTip("Right-click to insert a row above or below.")

    def create_menu(self, context: Literal["cell", "column", "row"]) -> QMenu:
        window = self.window
        menu = QMenu(window)

        def action(label, callback, *, enabled=True):
            item = menu.addAction(label)
            item.setEnabled(enabled)
            item.triggered.connect(lambda _checked=False: callback())

        if context != "column":
            action("Insert row above", lambda: window.table_editor.add_row(position="above"))
            action("Insert row below", lambda: window.table_editor.add_row(position="below"))
        if context != "row":
            action("Insert column left", lambda: window.table_editor.add_column(position="left"))
            action("Insert column right", lambda: window.table_editor.add_column(position="right"))
            action("Rename column", window.table_editor.rename_current_column,
                   enabled=self.table.currentColumn() >= 0)
            menu.addSeparator()
            action("Set selected columns as X", lambda: window.table_editor.set_selected_column_roles("X"))
            action("Set selected columns as Y", lambda: window.table_editor.set_selected_column_roles("Y"))
            action("Clear selected column roles", lambda: window.table_editor.set_selected_column_roles(""))
        menu.addSeparator()
        action("Clear selected cells", window.table_editor.clear_selected_cells)
        if context != "column":
            action("Delete selected rows", window.table_editor.delete_selected_rows,
                   enabled=any(r.bottomRow() >= DATA_START_ROW for r in self.table.selectedRanges()))
        if context != "row":
            action("Delete selected columns", window.table_editor.delete_selected_columns)
        return menu

    def _exec_menu(self, context, global_position) -> None:
        menu = self.create_menu(context)
        try:
            menu.exec(global_position)
        finally:
            menu.deleteLater()

    def show_cell_menu(self, position) -> None:
        self.window._select_table_context_target(position)
        self._exec_menu("cell", self.table.viewport().mapToGlobal(position))

    def _select_header(self, section: int, *, column: bool) -> None:
        row = max(0, self.table.currentRow()) if column else section
        col = section if column else max(0, self.table.currentColumn())
        index = self.table.table_model.index(row, col)
        if not index.isValid():
            return
        selection = self.table.selectionModel()
        selected = (selection.isColumnSelected(section) if column
                    else selection.isRowSelected(section))
        command = (QItemSelectionModel.NoUpdate if selected else
                   QItemSelectionModel.ClearAndSelect |
                   (QItemSelectionModel.Columns if column else QItemSelectionModel.Rows))
        selection.setCurrentIndex(index, command)

    def show_column_menu(self, position) -> None:
        header = self.table.horizontalHeader()
        column = header.logicalIndexAt(position)
        if column < 0:
            return
        self._select_header(column, column=True)
        self._exec_menu("column", header.viewport().mapToGlobal(position))

    def show_row_menu(self, position) -> None:
        header = self.table.verticalHeader()
        row = header.logicalIndexAt(position)
        if row < 0:
            return
        self._select_header(row, column=False)
        self._exec_menu("row", header.viewport().mapToGlobal(position))

    def insert_row(self, position: Literal["above", "below"] = "below") -> None:
        if position not in ("above", "below"):
            raise ValueError("Row position must be above or below")
        window = self.window
        row = self.table.currentRow()
        insert_at = (self.table.rowCount() if row < 0 else
                     max(DATA_START_ROW, row + (position == "below")))
        window.workspace.push_current_undo_state()
        with signals_blocked(self.table):
            self.table.insertRow(insert_at)
        window.table_editor.sync_dataframe_from_table()
        self.table.setCurrentCell(insert_at, max(0, self.table.currentColumn()))
        window.workspace.update_undo_baseline()
        window.set_status(f"Inserted data row {insert_at - DATA_START_ROW + 1}.")

    def insert_column(self, position: Literal["left", "right"] = "right") -> None:
        if position not in ("left", "right"):
            raise ValueError("Column position must be left or right")
        window = self.window
        column = self.table.currentColumn()
        insert_at = (self.table.columnCount() if column < 0 else
                     column + (position == "right"))
        name = window.table_editor._next_column_name()
        window.workspace.push_current_undo_state()
        with signals_blocked(self.table):
            self.table.insertColumn(insert_at, name)
        window.table_editor.sync_dataframe_from_table()
        self.table.setCurrentCell(max(0, self.table.currentRow()), insert_at)
        window.workspace.update_undo_baseline()
        window.set_status(f"Inserted column {name}.")

    def rename_column(self, column: int) -> None:
        if not 0 <= column < self.table.columnCount():
            return
        window = self.window
        old_name = window.table_editor._header_text(column)
        new_name, ok = QInputDialog.getText(
            window, "Rename column", "Column name", text=old_name,
        )
        new_name = unicodedata.normalize("NFC", str(new_name).strip())
        if not ok or not new_name:
            return
        if new_name == old_name:
            window.set_status("Column name unchanged.")
            return
        existing = {
            unicodedata.normalize("NFC", window.table_editor._header_text(index))
            for index in range(self.table.columnCount()) if index != column
        }
        if new_name in existing:
            QMessageBox.warning(window, "Rename column", f"Column already exists: {new_name}")
            return
        window.workspace.push_current_undo_state()
        name_item = self.table.item(NAME_ROW, column)
        displayed_name = name_item.text().strip() if name_item is not None else ""
        with signals_blocked(self.table):
            self.table.rename_column(column, new_name)
            if name_item is not None and displayed_name == old_name:
                name_item.setText(new_name)
        window.table_editor._rename_sheet_column_references(window.session.active_sheet_id, old_name, new_name)
        if old_name in window.preview.linear_fit_results_by_y:
            window.preview.linear_fit_results_by_y[new_name] = window.preview.linear_fit_results_by_y.pop(old_name)
        preferred_y = window.table_editor._restore_active_graph_column_state()
        window.table_editor.sync_dataframe_from_table(preferred_y)
        window.workspace.update_undo_baseline()
        window.set_status(f"Renamed column {old_name} to {new_name}.")

    def sync_after_edit(
        self,
        preferred_y: list[str] | None = None,
        *,
        refresh_columns: bool = True,
        label_columns: set[str] | None = None,
    ) -> None:
        """Publish the table buffer, invalidate preview data, then update the UI.

        None updates all display labels; an empty set leaves labels alone.
        Structural edits can replace the model's DataFrame, so the table is
        always the source of the live sheet buffer here.
        """
        window = self.window
        window.session.df = self.table.dataframe()
        window.preview.preview_numeric_cache.clear()
        if window.session.active_sheet_id in window.session.sheets:
            if label_columns is None or label_columns:
                window.table_editor._sync_sheet_series_labels(window.session.active_sheet_id, label_columns)
        self.table.refresh_row_headers()
        if refresh_columns:
            window.table_editor.populate_columns(preferred_y)
            window.series_editor._discard_invalid_series_color_recipe()
        window.table_editor._update_column_headers()
        window.data_info.setText(
            f"{max(len(window.session.df) - DATA_START_ROW, 0)} data rows, {len(window.session.df.columns)} columns"
        )
        window.preview.schedule_render()

    def sync_after_paste(self, start_row: int, start_col: int, grid: list[list[str]]) -> None:
        name_touched = start_row <= NAME_ROW < start_row + len(grid)
        role_touched = start_row <= ROLE_ROW < start_row + len(grid)
        touched: set[str] = set()
        if name_touched:
            columns = self.table.dataframe().columns
            end_col = min(start_col + len(grid[NAME_ROW - start_row]), len(columns))
            touched = {str(columns[column]) for column in range(start_col, end_col)}
        self.sync_after_edit(refresh_columns=name_touched or role_touched, label_columns=touched)
