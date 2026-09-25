from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
from pandas.testing import assert_frame_equal
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app import GraphDrawerWindow
from plot_config import LegendEntryConfig
from pubfig.project_io import read_project, write_project
from pubfig.sheet_data import DATA_START_ROW, NAME_ROW
from theme import apply_theme


class RenameWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        apply_theme(cls.app)

    def setUp(self) -> None:
        self.temp_directory = TemporaryDirectory()
        original_restore = GraphDrawerWindow.maybe_restore_autosave
        GraphDrawerWindow.maybe_restore_autosave = lambda _window: None
        try:
            self.window = GraphDrawerWindow()
        finally:
            GraphDrawerWindow.maybe_restore_autosave = original_restore
        self.window.autosave_timer.stop()
        self.window.autosave_path = Path(self.temp_directory.name) / "autosave.json"
        self.app.processEvents()

    def tearDown(self) -> None:
        self.window._set_modified(False)
        self.window.close()
        self.app.processEvents()
        self.temp_directory.cleanup()

    def _rename_current_column(self, name: str) -> None:
        with patch("app.QInputDialog.getText", return_value=(name, True)):
            self.window.rename_current_column()

    def test_parenthesized_column_can_be_renamed_repeatedly_without_stale_refs(self) -> None:
        self.window.new_graph()
        graph_id = self.window.active_graph_id
        self.window.series_by_y["Col 2"].error_column = "Col 3"
        self.window.plot_config.legend_entries = [
            LegendEntryConfig(source_y="Col 2", label="Signal")
        ]
        self.window.table.setCurrentCell(2, 0)

        self._rename_current_column("Voltage (V)")
        self._rename_current_column("Electric field (MV/cm)")

        graph = self.window.graphs[graph_id]
        self.assertEqual(str(self.window.df.columns[0]), "Electric field (MV/cm)")
        self.assertEqual(graph.series_by_y["Col 2"].x, "Electric field (MV/cm)")
        self.assertNotIn("Voltage (V)", map(str, self.window.df.columns))

        self.window.table.setCurrentCell(2, 1)
        self._rename_current_column("Polarization (µC/cm²)")
        self._rename_current_column("Remanent polarization (µC/cm²)")

        graph = self.window.graphs[graph_id]
        self.assertEqual(graph.checked_y, ["Remanent polarization (µC/cm²)"])
        self.assertIn("Remanent polarization (µC/cm²)", graph.series_by_y)
        self.assertEqual(
            graph.series_by_y["Remanent polarization (µC/cm²)"].y,
            "Remanent polarization (µC/cm²)",
        )
        self.assertEqual(
            graph.plot_config.legend_entries[0].source_y,
            "Remanent polarization (µC/cm²)",
        )

        self.window.table.setCurrentCell(2, 2)
        self._rename_current_column("Y error (µC/cm²)")
        self._rename_current_column("Polarization error (µC/cm²)")
        graph = self.window.graphs[graph_id]
        self.assertEqual(
            graph.series_by_y["Remanent polarization (µC/cm²)"].error_column,
            "Polarization error (µC/cm²)",
        )

        path = Path(self.temp_directory.name) / "renamed.json"
        self.window.save_active_state()
        write_project(path, self.window.document)
        restored = read_project(path)
        restored_graph = restored.graphs[graph_id]
        self.assertEqual(
            restored_graph.series_by_y["Remanent polarization (µC/cm²)"].x,
            "Electric field (MV/cm)",
        )

    def test_name_row_and_series_label_rename_propagate_to_every_shared_graph(self) -> None:
        self.window.new_graph()
        first_graph_id = self.window.active_graph_id
        self.window.save_active_state()
        self.window.new_graph()
        second_graph_id = self.window.active_graph_id
        self.window.save_active_state()

        self.window.table.item(NAME_ROW, 1).setText("Direct rename")
        self.app.processEvents()
        for graph_id in (first_graph_id, second_graph_id):
            self.assertEqual(
                self.window.graphs[graph_id].series_by_y["Col 2"].label,
                "Direct rename",
            )

        self.window.handle_table_paste(NAME_ROW, 1, "Pasted rename")
        for graph_id in (first_graph_id, second_graph_id):
            self.assertEqual(
                self.window.graphs[graph_id].series_by_y["Col 2"].label,
                "Pasted rename",
            )

        self.window.series_label_edit.setText("Panel rename")
        self.window.apply_series_widget_state()
        for graph_id in (first_graph_id, second_graph_id):
            self.assertEqual(
                self.window.graphs[graph_id].series_by_y["Col 2"].label,
                "Panel rename",
            )

        path = Path(self.temp_directory.name) / "shared-labels.json"
        self.window.save_active_state()
        write_project(path, self.window.document)
        restored = read_project(path)
        for graph_id in (first_graph_id, second_graph_id):
            self.assertEqual(
                restored.graphs[graph_id].series_by_y["Col 2"].label,
                "Panel rename",
            )

    def test_tree_f2_root_rename_collision_and_noop_are_consistent(self) -> None:
        sheet_node = self.window.active_node()
        original_name = self.window.sheets[sheet_node.ref_id].name
        self.window.figure_tree.setFocus()
        self.app.processEvents()
        self.assertFalse(self.window.focus_widget_uses_text_shortcuts())

        with patch("app.QInputDialog.getText", return_value=("Measurements", True)):
            event = QKeyEvent(QEvent.KeyPress, Qt.Key_F2, Qt.NoModifier)
            QApplication.sendEvent(self.window.figure_tree, event)
        self.assertEqual(self.window.sheets[sheet_node.ref_id].name, "Measurements")
        self.assertEqual(sheet_node.name, "Measurements")

        self.window.undo_workspace()
        sheet_node = self.window._find_node(sheet_node.id)
        self.assertEqual(self.window.sheets[sheet_node.ref_id].name, original_name)
        self.window.redo_workspace()
        sheet_node = self.window._find_node(sheet_node.id)
        self.assertEqual(self.window.sheets[sheet_node.ref_id].name, "Measurements")

        undo_count = self.window.undo_history.undo_count
        with patch("app.QInputDialog.getText", return_value=("Measurements", True)):
            self.window.rename_selected_node()
        self.assertEqual(self.window.undo_history.undo_count, undo_count)

        self.window.new_sheet()
        second = self.window.active_node()
        with patch("app.QInputDialog.getText", return_value=("Measurements", True)), patch(
            "PySide6.QtWidgets.QMessageBox.warning"
        ) as warning:
            self.window.rename_selected_node()
        warning.assert_called_once()
        self.assertNotEqual(self.window.sheets[second.ref_id].name, "Measurements")

        with patch("app.QInputDialog.getText", return_value=("HfO2 project", True)):
            self.window.rename_project_root()
        self.assertEqual(self.window.tree_root.name, "HfO2 project")

        path = Path(self.temp_directory.name) / "node-renames.json"
        write_project(path, self.window.document)
        restored = read_project(path)
        self.assertEqual(restored.tree_root.name, "HfO2 project")
        self.assertEqual(restored.sheets[sheet_node.ref_id].name, "Measurements")
        restored_sheet_node = restored.find_node(sheet_node.id)
        self.assertEqual(restored_sheet_node.name, "Measurements")

    def test_table_context_actions_target_the_right_clicked_column(self) -> None:
        self.window.new_graph()
        self.window.table.resize(500, 300)
        self.window.table.show()
        self.app.processEvents()
        self.window.table.setCurrentCell(2, 0)
        target = self.window.table.table_model.index(2, 2)
        position = self.window.table.visualRect(target).center()
        self.assertEqual(self.window.table.indexAt(position), target)

        self.window._select_table_context_target(position)

        self.assertEqual(self.window.table.currentColumn(), 2)

    def test_header_double_click_renames_clicked_column_with_stale_cell_selection(self) -> None:
        self.window.show()
        self.app.processEvents()
        table = self.window.table
        table.setCurrentCell(2, 0)
        header = table.horizontalHeader()
        position = QPoint(header.sectionViewportPosition(2) + header.sectionSize(2) // 2,
                          header.height() // 2)
        with patch("app.QInputDialog.getText", return_value=("Time (s)", True)) as dialog:
            QTest.mouseDClick(header.viewport(), Qt.LeftButton, pos=position)
        dialog.assert_called_once()
        self.assertEqual(list(self.window.df.columns), ["Col 1", "Col 2", "Time (s)", "Col 4"])
        self.assertTrue(self.window._is_modified)
        self.window.undo_workspace()
        self.assertEqual(str(self.window.df.columns[2]), "Col 3")
        self.window.redo_workspace()
        self.assertEqual(str(self.window.df.columns[2]), "Time (s)")

    def test_header_rename_updates_all_graph_references_and_survives_save(self) -> None:
        self.window.new_graph()
        first_id = self.window.active_graph_id
        self.window.series_by_y["Col 2"].error_column = "Col 3"
        self.window.plot_config.legend_entries = [LegendEntryConfig(source_y="Col 2", label="Signal")]
        self.window.new_graph()
        second_id = self.window.active_graph_id
        self.window.series_by_y["Col 2"].error_column = "Col 3"
        for index, name in [(0, "Voltage (V)"), (1, "Current (A)"), (2, "Error (A)")]:
            with patch("app.QInputDialog.getText", return_value=(name, True)):
                self.window.table.horizontalHeader().sectionDoubleClicked.emit(index)
        for graph_id in (first_id, second_id):
            graph = self.window.graphs[graph_id]
            self.assertEqual(graph.checked_y, ["Current (A)"])
            series = graph.series_by_y["Current (A)"]
            self.assertEqual((series.x, series.y, series.error_column),
                             ("Voltage (V)", "Current (A)", "Error (A)"))
        self.assertEqual(self.window.graphs[first_id].plot_config.legend_entries[0].source_y,
                         "Current (A)")
        path = Path(self.temp_directory.name) / "headers.json"
        self.window.save_active_state()
        write_project(path, self.window.document)
        restored = read_project(path)
        self.assertEqual(restored.graphs[first_id].series_by_y["Current (A)"].x, "Voltage (V)")

    def test_header_cancel_empty_duplicate_and_noop_do_not_add_undo(self) -> None:
        self.window.undo_history.reset()
        for name, accepted in [("ignored", False), ("  ", True), ("Col 1", True), ("Col 2", True)]:
            with self.subTest(name=name), patch("app.QInputDialog.getText", return_value=(name, accepted)), patch(
                "pubfig.ui.table_actions.QMessageBox.warning"
            ):
                self.window.table.horizontalHeader().sectionDoubleClicked.emit(1)
                self.assertEqual(self.window.undo_history.undo_count, 0)
                self.assertEqual(str(self.window.df.columns[1]), "Col 2")

    def test_header_context_menus_target_clicked_section_and_offer_directions(self) -> None:
        self.window.show()
        self.app.processEvents()
        table = self.window.table
        table.setCurrentCell(2, 0)
        horizontal = table.horizontalHeader()
        pos = QPoint(horizontal.sectionViewportPosition(2) + 8, horizontal.height() // 2)
        with patch.object(self.window.table_actions, "_exec_menu") as show:
            horizontal.customContextMenuRequested.emit(pos)
        self.assertEqual(table.currentColumn(), 2)
        self.assertTrue(table.selectionModel().isColumnSelected(2))
        self.assertEqual(show.call_args.args[0], "column")
        menu = self.window.table_actions.create_menu("column")
        try:
            actions = {action.text(): action for action in menu.actions()}
            self.assertIn("Insert column left", actions)
            self.assertIn("Insert column right", actions)
            with patch("app.QInputDialog.getText", return_value=("Header target", True)):
                actions["Rename column"].trigger()
            self.assertEqual(str(self.window.df.columns[2]), "Header target")
        finally:
            menu.deleteLater()
        vertical = table.verticalHeader()
        pos = QPoint(vertical.width() // 2, vertical.sectionViewportPosition(5) + 8)
        with patch.object(self.window.table_actions, "_exec_menu"):
            vertical.customContextMenuRequested.emit(pos)
        self.assertEqual(table.currentRow(), 5)
        self.assertTrue(table.selectionModel().isRowSelected(5))
        menu = self.window.table_actions.create_menu("row")
        try:
            self.assertIn("Insert row above", [action.text() for action in menu.actions()])
            self.assertIn("Insert row below", [action.text() for action in menu.actions()])
        finally:
            menu.deleteLater()

    def test_insert_rows_in_both_directions_preserves_data_and_undo(self) -> None:
        self.window.table.item(2, 0).setText("first")
        self.window.table.item(3, 0).setText("second")
        before = self.window.df.copy(deep=True)
        for position, at in [("above", 3), ("below", 4)]:
            with self.subTest(position=position):
                self.window.table.setCurrentCell(3, 0)
                self.window.undo_history.reset()
                self.window.add_row(position=position)
                expected = pd.concat([before.iloc[:at], pd.DataFrame([[""] * 4], columns=before.columns),
                                      before.iloc[at:]], ignore_index=True)
                assert_frame_equal(self.window.df, expected)
                self.assertEqual(self.window.table.currentRow(), at)
                self.assertEqual(self.window.undo_history.undo_count, 1)
                self.window.undo_workspace()
                assert_frame_equal(self.window.df, before)
                self.window.redo_workspace()
                assert_frame_equal(self.window.df, expected)
                self.window.undo_workspace()

    def test_insert_columns_in_both_directions_preserves_graph_and_undo(self) -> None:
        self.window.new_graph()
        graph_id = self.window.active_graph_id
        before = self.window.df.copy(deep=True)
        for position, at in [("left", 1), ("right", 2)]:
            with self.subTest(position=position):
                self.window.table.setCurrentCell(3, 1)
                self.window.undo_history.reset()
                self.window.add_column(position=position)
                expected = before.copy(deep=True)
                expected.insert(at, "Col 5", "")
                assert_frame_equal(self.window.df, expected)
                self.assertEqual(self.window.table.currentColumn(), at)
                self.assertEqual(self.window.graphs[graph_id].series_by_y["Col 2"].x, "Col 1")
                self.assertEqual(self.window.undo_history.undo_count, 1)
                self.window.undo_workspace()
                assert_frame_equal(self.window.df, before)
                self.window.redo_workspace()
                assert_frame_equal(self.window.df, expected)
                self.window.undo_workspace()

    def test_row_insertion_keeps_metadata_at_top_and_appends_without_selection(self) -> None:
        metadata = self.window.df.iloc[:DATA_START_ROW].copy(deep=True)
        for selected in (0, 1):
            for position in ("above", "below"):
                self.window.table.setCurrentCell(selected, 0)
                self.window.add_row(position=position)
                self.assertEqual(self.window.table.currentRow(), DATA_START_ROW)
                assert_frame_equal(self.window.df.iloc[:DATA_START_ROW], metadata)
        self.window.table.selectionModel().clear()
        previous_rows = len(self.window.df)
        self.window.add_row(position="above")
        self.assertEqual(self.window.table.currentRow(), previous_rows)

    def test_cell_menu_direction_actions_perform_the_selected_insert(self) -> None:
        for label, axis, expected in [("Insert row above", "row", 3), ("Insert row below", "row", 4),
                                      ("Insert column left", "column", 1), ("Insert column right", "column", 2)]:
            with self.subTest(label=label):
                self.window.table.setCurrentCell(3, 1)
                before = self.window.df.copy(deep=True)
                menu = self.window.table_actions.create_menu("cell")
                try:
                    next(action for action in menu.actions() if action.text() == label).trigger()
                finally:
                    menu.deleteLater()
                actual = self.window.table.currentRow() if axis == "row" else self.window.table.currentColumn()
                self.assertEqual(actual, expected)
                self.window.undo_workspace()
                assert_frame_equal(self.window.df, before)


if __name__ == "__main__":
    unittest.main()
