"""Sheet editing, column roles, and graph-reference updates."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
from PySide6.QtCore import QItemSelectionModel, QObject, Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QListWidgetItem,
    QMessageBox,
    QTableWidgetItem,
)

from ..data_parser import ParsedTable, parse_clipboard_grid, parse_table_text
from ..graph_editing import (
    delete_graph_column_references,
    remove_legend_sources,
    rename_graph_column_reference,
)
from ..plot_config import SeriesConfig, default_series
from ..sheet_data import (
    DATA_START_ROW,
    NAME_ROW,
    ROLE_ROW,
    column_name,
    column_role,
    nearest_left_x,
    next_column_name,
    normalize_role,
    plot_dataframe,
    with_metadata_rows,
)
from ..table_view import CellAdapter, TableSelectionRange
from .binding import signals_blocked

if TYPE_CHECKING:
    from .main_window import GraphDrawerWindow


class TableEditor(QObject):
    """Sheet editing, column roles, and graph-reference updates."""

    def __init__(self, window: GraphDrawerWindow) -> None:
        super().__init__(window)
        self.window = window
        self.session = window.session

    _remove_legend_sources = staticmethod(remove_legend_sources)
    _rename_graph_column_reference = staticmethod(rename_graph_column_reference)
    _delete_graph_column_references = staticmethod(delete_graph_column_references)

    def handle_table_paste(self, row: int, col: int, text: str) -> None:
        if self._table_is_blank() and row == 0 and col == 0:
            self.replace_table_from_text(text)
            return
        grid = parse_clipboard_grid(text)
        if not grid:
            return
        self.window.workspace.push_current_undo_state()
        self._paste_grid(row, col, grid)
        self.sync_dataframe_after_paste(row, col, grid)
        self.window.workspace.update_undo_baseline()
        self.window.set_status(
            f"Pasted {len(grid)} rows x {max(len(values) for values in grid)} cols."
        )

    def replace_table_from_text(self, text: str) -> None:
        try:
            parsed = parse_table_text(text)
        except Exception as exc:
            QMessageBox.warning(self.window, "Paste data", str(exc))
            self.window.set_status(f"Paste failed: {exc}")
            return
        self.window.workspace.push_current_undo_state()
        self.set_dataframe(parsed)
        self.window.workspace.update_undo_baseline()

    def set_dataframe(self, parsed: ParsedTable) -> None:
        self.session.df = with_metadata_rows(parsed.dataframe)
        self.window.preview.preview_numeric_cache.clear()
        self.session.series_by_y.clear()
        self.window.preview.linear_fit_results_by_y.clear()
        self.populate_table()
        self.populate_columns()
        self.window.set_status(
            f"Loaded {len(parsed.dataframe)} data rows x {len(self.session.df.columns)} cols; delimiter={parsed.delimiter}; header={parsed.has_header}"
        )
        self.window.preview.schedule_render()

    def import_data_file(self) -> None:
        filters = "Data files (*.csv *.txt *.tsv *.dat);;All files (*)"
        path, _ = QFileDialog.getOpenFileName(
            self.window, "Import data file", str(self.window.files.last_folder), filters
        )
        if not path:
            return
        self.import_data_path(Path(path))

    def import_data_path(self, path: Path) -> None:
        try:
            text = self._read_text_file(path)
        except Exception as exc:
            QMessageBox.warning(
                self.window, "Import data", f"Could not read {path}:\n{exc}"
            )
            return
        if not self._table_is_blank():
            reply = QMessageBox.question(
                self.window,
                "Import data",
                "Replace the current figure's data with the file contents?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply != QMessageBox.Yes:
                return
        self.window.files.last_folder = path.parent
        self.replace_table_from_text(text)

    def _read_text_file(self, path: Path) -> str:
        raw = path.read_bytes()
        for encoding in ("utf-8-sig", "utf-8", "cp949"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    def populate_table(self) -> None:
        """Attach the DataFrame to the virtual table without creating cells."""
        self.window.table.blockSignals(True)
        try:
            self.window.table.clearSelection()
            self.window.table.set_dataframe(self.session.df)
            self._update_column_headers()
            if len(self.session.df) * max(len(self.session.df.columns), 1) <= 2000:
                self.window.table.resizeColumnsToContents()
            self.window.data_info.setText(
                f"{max(len(self.session.df) - DATA_START_ROW, 0)} data rows, {len(self.session.df.columns)} columns"
            )
        finally:
            self.window.table.blockSignals(False)

    def populate_columns(self, preferred_y: list[str] | None = None) -> None:
        columns = list(map(str, self.session.df.columns))
        previous_y = (
            self.checked_y_columns() if preferred_y is None else list(preferred_y)
        )
        self.session.series_by_y = {
            key: value
            for key, value in self.session.series_by_y.items()
            if key in columns
        }
        self.window.series_editor._refresh_error_column_combo()

        self.window.y_list.blockSignals(True)
        self.window.y_list.clear()

        y_columns = [
            column
            for column in columns
            if self._column_role(column) == "Y" and self._nearest_left_x(column)
        ]
        for column in y_columns:
            item = QListWidgetItem(column)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.window.y_list.addItem(item)

        kept_y = [column for column in previous_y if column in y_columns]
        if kept_y or preferred_y is not None:
            self._set_checked_y_columns(kept_y)
        else:
            self._set_checked_y_columns(y_columns)

        self.window.y_list.blockSignals(False)
        self.refresh_series_configs()
        self.window.series_editor.update_style_targets()

    def handle_table_item_changed(self, item: CellAdapter) -> None:
        row, col = item.row(), item.column()
        if row < 0 or not 0 <= col < self.window.table.columnCount():
            return
        self.window.workspace._reset_undo_coalescing()
        self.window.workspace.push_undo_baseline()
        # The table model has already committed this cell before emitting.
        if row == ROLE_ROW:
            with signals_blocked(self.window.table):
                self._normalize_role_cell(item)
        touched = (
            {str(self.window.table.dataframe().columns[col])}
            if row == NAME_ROW
            else set()
        )
        self.window.table_actions.sync_after_edit(
            refresh_columns=row in (ROLE_ROW, NAME_ROW),
            label_columns=touched,
        )
        self.window.workspace.update_undo_baseline()

    def sync_dataframe_from_table(self, preferred_y: list[str] | None = None) -> None:
        self.window.table_actions.sync_after_edit(preferred_y)

    def _select_table_context_target(self, position) -> None:
        """Make context actions target the cell that was actually clicked."""

        index = self.window.table.indexAt(position)
        if not index.isValid():
            return
        selection = self.window.table.selectionModel()
        command = (
            QItemSelectionModel.NoUpdate
            if selection.isSelected(index)
            else QItemSelectionModel.ClearAndSelect
        )
        selection.setCurrentIndex(index, command)

    def show_table_menu(self, position) -> None:
        self.window.table_actions.show_cell_menu(position)

    def add_row(self, *, position: str = "below") -> None:
        self.window.table_actions.insert_row(position)

    def add_column(self, *, position: str = "right") -> None:
        self.window.table_actions.insert_column(position)

    def _rename_sheet_column_references(
        self,
        sheet_id: str | None,
        old_name: str,
        new_name: str,
    ) -> None:
        if sheet_id is None:
            return
        for graph in self.session.graphs.values():
            if graph.sheet_id == sheet_id:
                self._rename_graph_column_reference(
                    graph,
                    old_name,
                    new_name,
                )

    def _sync_sheet_series_labels(
        self,
        sheet_id: str | None,
        columns: set[str] | None = None,
    ) -> None:
        """Propagate shared Name-row labels to every graph on a sheet."""

        if sheet_id is None or sheet_id not in self.session.sheets:
            return
        dataframe = self.session.sheets[sheet_id].df
        available = set(map(str, dataframe.columns))
        targets = (
            available
            if columns is None
            else available & {str(value) for value in columns}
        )
        labels = {column: column_name(dataframe, column) for column in targets}

        def update(series_by_y: dict[str, SeriesConfig]) -> None:
            for key, series in series_by_y.items():
                column = series.y if series.y in labels else key
                if column in labels:
                    series.label = labels[column]

        for graph in self.session.graphs.values():
            if graph.sheet_id == sheet_id:
                update(graph.series_by_y)
        if self.session.active_sheet_id == sheet_id:
            update(self.session.series_by_y)

    def _delete_sheet_column_references(
        self,
        sheet_id: str | None,
        removed_names: set[str],
    ) -> None:
        if sheet_id is None:
            return
        for graph in self.session.graphs.values():
            if graph.sheet_id == sheet_id:
                self._delete_graph_column_references(
                    graph,
                    removed_names,
                )

    def _restore_active_graph_column_state(self) -> list[str] | None:
        if self.session.active_graph_id is None:
            return None
        graph = self.session.graphs.get(self.session.active_graph_id)
        if graph is None:
            return None
        self.session.series_by_y = deepcopy(graph.series_by_y)
        self.session.plot_config.legend_entries = deepcopy(
            graph.plot_config.legend_entries
        )
        self.session.plot_config.legend_row_lengths = list(
            graph.plot_config.legend_row_lengths
        )
        return list(graph.checked_y)

    def rename_current_column(self) -> None:
        self.window.table_actions.rename_column(
            max(0, self.window.table.currentColumn())
        )

    def set_selected_column_roles(self, role: str) -> None:
        cols = self._selected_columns()
        if not cols and self.window.table.currentColumn() >= 0:
            cols = [self.window.table.currentColumn()]
        self.window.workspace.push_current_undo_state()
        self._ensure_metadata_rows()
        self.window.table.blockSignals(True)
        for col in cols:
            item = self.window.table.item(ROLE_ROW, col)
            if item is None:
                item = QTableWidgetItem("")
                self.window.table.setItem(ROLE_ROW, col, item)
            item.setText(role)
        self.window.table.blockSignals(False)
        self.sync_dataframe_from_table()
        self.window.workspace.update_undo_baseline()
        self.window.set_status(f"Set {len(cols)} column role(s) to {role or 'blank'}.")

    def clear_selected_cells(self) -> None:
        ranges = self.window.table.selectedRanges()
        if (
            not ranges
            and self.window.table.currentRow() >= 0
            and self.window.table.currentColumn() >= 0
        ):
            ranges = [
                TableSelectionRange(
                    self.window.table.currentRow(),
                    self.window.table.currentColumn(),
                    self.window.table.currentRow(),
                    self.window.table.currentColumn(),
                )
            ]
        if not ranges:
            self.window.set_status("Select one or more cells to clear.")
            return
        self.window.workspace.push_current_undo_state()
        self.window.table.blockSignals(True)
        for selected_range in ranges:
            self.window.table.fill_range(selected_range, "")
        self.window.table.blockSignals(False)
        self.sync_dataframe_from_table()
        self.window.workspace.update_undo_baseline()
        self.window.set_status("Cleared selected cells.")

    def delete_selected_rows(self) -> None:
        intervals = [
            (max(selected.topRow(), DATA_START_ROW), selected.bottomRow())
            for selected in self.window.table.selectedRanges()
            if selected.bottomRow() >= DATA_START_ROW
        ]
        if not intervals and self.window.table.currentRow() >= DATA_START_ROW:
            intervals = [
                (self.window.table.currentRow(), self.window.table.currentRow())
            ]
        intervals = self._merge_intervals(intervals)
        if not intervals:
            self.window.set_status("Role/Name rows are kept.")
            return
        removed_count = sum(last - first + 1 for first, last in intervals)
        self.window.workspace.push_current_undo_state()
        self.window.table.blockSignals(True)
        for first, last in reversed(intervals):
            self.window.table.removeRows(first, last - first + 1)
        if self.window.table.rowCount() == 0:
            self.window.table.setRowCount(1)
        self.window.table.blockSignals(False)
        self.sync_dataframe_from_table()
        self.window.workspace.update_undo_baseline()
        self.window.set_status(f"Deleted {removed_count} row(s).")

    def delete_selected_columns(self) -> None:
        intervals = [
            (selected.leftColumn(), selected.rightColumn())
            for selected in self.window.table.selectedRanges()
        ]
        if not intervals and self.window.table.currentColumn() >= 0:
            intervals = [
                (self.window.table.currentColumn(), self.window.table.currentColumn())
            ]
        intervals = self._merge_intervals(intervals)
        if not intervals:
            self.window.set_status("Select one or more columns to delete.")
            return
        removed_names = [
            self._header_text(col)
            for first, last in intervals
            for col in range(first, last + 1)
        ]
        removed_count = sum(last - first + 1 for first, last in intervals)
        self.window.workspace.push_current_undo_state()
        self.window.table.blockSignals(True)
        for first, last in reversed(intervals):
            self.window.table.removeColumns(first, last - first + 1)
        if self.window.table.columnCount() == 0:
            self.window.table.setColumnCount(1)
            self.window.table.setHorizontalHeaderItem(0, QTableWidgetItem("Col 1"))
        self.window.table.blockSignals(False)
        self._delete_sheet_column_references(
            self.session.active_sheet_id,
            set(removed_names),
        )
        preferred_y = self._restore_active_graph_column_state()
        self.sync_dataframe_from_table(preferred_y)
        self.window.workspace.update_undo_baseline()
        self.window.set_status(f"Deleted {removed_count} column(s).")

    def _selected_columns(self) -> list[int]:
        cols: list[int] = []
        for selected_range in self.window.table.selectedRanges():
            cols.extend(
                range(selected_range.leftColumn(), selected_range.rightColumn() + 1)
            )
        return sorted(set(cols))

    @staticmethod
    def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
        merged: list[list[int]] = []
        for first, last in sorted(
            (min(first, last), max(first, last)) for first, last in intervals
        ):
            if not merged or first > merged[-1][1] + 1:
                merged.append([first, last])
            else:
                merged[-1][1] = max(merged[-1][1], last)
        return [(first, last) for first, last in merged]

    def _paste_grid(
        self, start_row: int, start_col: int, grid: list[list[str]]
    ) -> None:
        row_count = start_row + len(grid)
        col_count = start_col + max(len(row) for row in grid)
        self.window.table.setUpdatesEnabled(False)
        self.window.table.blockSignals(True)
        try:
            self._ensure_table_size(row_count, col_count)

            normalized_grid = [
                [
                    self._normalized_role_text(value)
                    if start_row + row_offset == ROLE_ROW
                    else value
                    for value in row_values
                ]
                for row_offset, row_values in enumerate(grid)
            ]
            self.window.table.set_block(start_row, start_col, normalized_grid)
        finally:
            self.window.table.blockSignals(False)
            self.window.table.setUpdatesEnabled(True)

    def _ensure_table_size(self, rows: int, columns: int) -> None:
        if self.window.table.rowCount() < rows:
            self.window.table.setRowCount(rows)
        if self.window.table.columnCount() < columns:
            self.window.table.setColumnCount(columns)
        self._ensure_metadata_rows()

    def sync_dataframe_after_paste(
        self, start_row: int, start_col: int, grid: list[list[str]]
    ) -> None:
        self.window.table_actions.sync_after_paste(start_row, start_col, grid)

    def _table_is_blank(self) -> bool:
        dataframe = self.window.table.dataframe()
        if dataframe.empty or dataframe.shape[1] == 0:
            return True
        visible_values = dataframe.iloc[NAME_ROW:].fillna("").astype(str)
        return not visible_values.apply(
            lambda column: column.str.strip().ne("").any()
        ).any()

    def _header_text(self, col: int) -> str:
        item = self.window.table.horizontalHeaderItem(col)
        text = (
            item.text()
            if item is not None and item.text().strip()
            else f"Col {col + 1}"
        )
        # HeaderAdapter already exposes the stable internal identifier rather
        # than the decorated "name (Role)" shown by the view.  Parenthesized
        # scientific identifiers such as "Voltage (V)" must remain intact.
        return text

    def _next_column_name(self) -> str:
        return next_column_name(
            self._header_text(column)
            for column in range(self.window.table.columnCount())
        )

    def _ensure_metadata_rows(self) -> None:
        while self.window.table.rowCount() < DATA_START_ROW:
            self.window.table.insertRow(self.window.table.rowCount())
        self.window.table.refresh_row_headers()

    def _column_role(self, column: str) -> str:
        return column_role(self.session.df, column)

    def _nearest_left_x(self, y_column: str) -> str:
        return nearest_left_x(self.session.df, y_column)

    def _column_name(self, column: str) -> str:
        return column_name(self.session.df, column)

    def _plot_dataframe(self) -> pd.DataFrame:
        return plot_dataframe(self.session.df)

    def _normalize_role_cell(self, item: CellAdapter) -> None:
        item.setText(self._normalized_role_text(item.text()))

    def _normalized_role_text(self, value: str) -> str:
        return normalize_role(value, preserve_unknown=True)

    def _update_column_headers(self) -> None:
        for col in range(self.window.table.columnCount()):
            name = self._header_text(col)
            role = ""
            if col < len(self.session.df.columns):
                role = self._column_role(str(self.session.df.columns[col]))
            label = f"{name} ({role})" if role else name
            self.window.table.set_display_header(col, label)

    def _set_checked_y_columns(self, columns: list[str]) -> None:
        column_set = set(columns)
        for idx in range(self.window.y_list.count()):
            item = self.window.y_list.item(idx)
            item.setCheckState(
                Qt.Checked if item.text() in column_set else Qt.Unchecked
            )

    def checked_y_columns(self) -> list[str]:
        columns: list[str] = []
        for idx in range(self.window.y_list.count()):
            item = self.window.y_list.item(idx)
            if item.checkState() == Qt.Checked:
                columns.append(item.text())
        return columns

    def set_all_plot_y_checked(self, checked: bool) -> None:
        """Check or clear every available Plot Y item with one refresh."""

        target_state = Qt.Checked if checked else Qt.Unchecked
        changed = False
        with signals_blocked(self.window.y_list):
            for idx in range(self.window.y_list.count()):
                item = self.window.y_list.item(idx)
                if item.checkState() != target_state:
                    item.setCheckState(target_state)
                    changed = True

        if not changed:
            return
        self.window.series_editor.handle_plot_y_changed()
        self.window.set_status(
            "Selected all Plot Y columns." if checked else "Cleared all Plot Y columns."
        )

    def refresh_series_configs(self) -> None:
        for y_column in self.checked_y_columns():
            x_column = self._nearest_left_x(y_column)
            if not x_column:
                continue
            if y_column not in self.session.series_by_y:
                self.session.series_by_y[y_column] = default_series(
                    x_column, y_column, len(self.session.series_by_y)
                )
            self.session.series_by_y[y_column].x = x_column
            self.session.series_by_y[y_column].label = self._column_name(y_column)
        self.window.preview.schedule_render()
