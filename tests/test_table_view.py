from __future__ import annotations

import os
import unittest
import warnings

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
from pandas.testing import assert_frame_equal
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import QApplication, QTableView

from table_view import SpreadsheetTableWidget, TableSelectionRange


class SpreadsheetTableWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.table = SpreadsheetTableWidget()

    def tearDown(self) -> None:
        self.table.close()
        self.app.processEvents()

    def test_large_dataframe_uses_one_lazy_model_reset(self) -> None:
        row_count = 100_000
        dataframe = pd.DataFrame(
            {f"Col {column}": range(row_count) for column in range(1, 9)}
        )
        reset_events: list[str] = []
        data_calls = 0
        original_data = self.table.table_model.data

        def tracked_data(index, role=Qt.DisplayRole):
            nonlocal data_calls
            data_calls += 1
            return original_data(index, role)

        self.table.table_model.modelAboutToBeReset.connect(
            lambda: reset_events.append("about")
        )
        self.table.table_model.modelReset.connect(lambda: reset_events.append("reset"))
        self.table.table_model.data = tracked_data

        self.table.set_dataframe(dataframe)

        self.assertIsInstance(self.table, QTableView)
        self.assertEqual(reset_events, ["about", "reset"])
        self.assertEqual(data_calls, 0)
        self.assertEqual(self.table.rowCount(), row_count)
        self.assertEqual(self.table.columnCount(), 8)

    def test_edit_updates_dataframe_and_emits_item_changed(self) -> None:
        dataframe = pd.DataFrame({"X": ["1", "2"], "Y": ["3", "4"]})
        changed_items = []
        self.table.set_dataframe(dataframe)
        self.table.itemChanged.connect(changed_items.append)

        self.table.item(1, 0).setText("updated")

        self.assertEqual(dataframe.iat[1, 0], "updated")
        self.assertEqual(self.table.dataframe_copy().iat[1, 0], "updated")
        self.assertEqual(len(changed_items), 1)
        self.assertEqual(changed_items[0].row(), 1)
        self.assertEqual(changed_items[0].column(), 0)
        self.assertEqual(changed_items[0].text(), "updated")

        self.table.item(1, 0).setText("updated")
        self.assertEqual(len(changed_items), 1)

    def test_set_block_writes_rectangular_and_ragged_data_with_one_signal(
        self,
    ) -> None:
        cases = {
            "rectangular": (
                1,
                1,
                [["A", "B"], ["C", "D"]],
                [
                    ["00", "01", "02"],
                    ["10", "A", "B"],
                    ["20", "C", "D"],
                ],
            ),
            "ragged": (
                0,
                1,
                [["A", "B"], ["C"]],
                [
                    ["00", "A", "B"],
                    ["10", "C", "12"],
                    ["20", "21", "22"],
                ],
            ),
        }
        data_changed_events: list[tuple[int, int, int, int]] = []
        item_changed_events = []
        self.table.table_model.dataChanged.connect(
            lambda top_left, bottom_right, _roles: data_changed_events.append(
                (
                    top_left.row(),
                    top_left.column(),
                    bottom_right.row(),
                    bottom_right.column(),
                )
            )
        )
        self.table.itemChanged.connect(item_changed_events.append)

        for name, (start_row, start_column, grid, expected_values) in cases.items():
            with self.subTest(name=name):
                self.table.set_dataframe(
                    pd.DataFrame(
                        [
                            ["00", "01", "02"],
                            ["10", "11", "12"],
                            ["20", "21", "22"],
                        ],
                        columns=["A", "B", "C"],
                    )
                )
                data_changed_events.clear()
                item_changed_events.clear()

                self.table.set_block(start_row, start_column, grid)

                expected = pd.DataFrame(expected_values, columns=["A", "B", "C"])
                assert_frame_equal(
                    self.table.dataframe_copy(),
                    expected,
                    check_dtype=False,
                )
                self.assertEqual(
                    data_changed_events,
                    [(start_row, start_column, start_row + 1, start_column + 1)],
                )
                self.assertEqual(item_changed_events, [])

    def test_text_edit_converts_only_numeric_target_column_to_object(self) -> None:
        dataframe = pd.DataFrame(
            {"Edited": [1.0, 2.0], "Untouched": [3.0, 4.0]}
        )
        self.table.set_dataframe(dataframe)

        with warnings.catch_warnings():
            warnings.simplefilter("error", FutureWarning)
            self.table.item(0, 0).setText("not numeric")

        self.assertEqual(dataframe.iat[0, 0], "not numeric")
        self.assertTrue(pd.api.types.is_object_dtype(dataframe["Edited"].dtype))
        self.assertTrue(pd.api.types.is_float_dtype(dataframe["Untouched"].dtype))

    def test_rectangular_selection_copies_tabular_text(self) -> None:
        self.table.set_dataframe(
            pd.DataFrame(
                [
                    ["a", "b", "c"],
                    ["d", "e", "f"],
                    ["g", "h", "i"],
                ],
                columns=["A", "B", "C"],
            )
        )
        self.table.clearSelection()
        self.table.setRangeSelected(TableSelectionRange(0, 1, 1, 2), True)
        copied_messages: list[str] = []
        self.table.copied.connect(copied_messages.append)
        QApplication.clipboard().clear()

        shortcut = QKeySequence(QKeySequence.Copy)[0]
        event = QKeyEvent(
            QEvent.KeyPress,
            shortcut.key(),
            shortcut.keyboardModifiers(),
        )
        self.table.keyPressEvent(event)

        self.assertEqual(
            [(item.row(), item.column()) for item in self.table.selectedItems()],
            [(0, 1), (0, 2), (1, 1), (1, 2)],
        )
        self.assertEqual(self.table.selected_text(), "b\tc\ne\tf")
        self.assertEqual(QApplication.clipboard().text(), "b\tc\ne\tf")
        self.assertEqual(copied_messages, ["Copied selected cells."])

    def test_insert_and_remove_rows_preserve_surrounding_data(self) -> None:
        self.table.set_dataframe(
            pd.DataFrame([["a", "b"], ["c", "d"]], columns=["A", "B"])
        )

        self.table.insertRow(1)

        self.assertEqual(self.table.rowCount(), 3)
        self.assertEqual(self.table.item(0, 0).text(), "a")
        self.assertEqual(self.table.item(1, 0).text(), "")
        self.assertEqual(self.table.item(1, 1).text(), "")
        self.assertEqual(self.table.item(2, 1).text(), "d")

        self.table.removeRow(0)

        expected = pd.DataFrame([["", ""], ["c", "d"]], columns=["A", "B"])
        assert_frame_equal(self.table.dataframe_copy(), expected)

    def test_remove_row_with_duplicate_index_removes_only_selected_position(
        self,
    ) -> None:
        self.table.set_dataframe(
            pd.DataFrame(
                [["first", "a"], ["second", "b"], ["third", "c"]],
                columns=["Value", "Label"],
                index=[7, 7, 9],
            )
        )

        self.table.removeRow(1)

        expected = pd.DataFrame(
            [["first", "a"], ["third", "c"]],
            columns=["Value", "Label"],
        )
        self.assertEqual(self.table.rowCount(), 2)
        assert_frame_equal(self.table.dataframe_copy(), expected)

    def test_row_growth_canonicalizes_non_string_headers_without_adding_columns(
        self,
    ) -> None:
        for operation in ("insert_row", "set_row_count"):
            with self.subTest(operation=operation):
                self.table.set_dataframe(
                    pd.DataFrame([["a", "b"], ["c", "d"]], columns=[101, 202])
                )

                if operation == "insert_row":
                    self.table.insertRow(1)
                    expected_rows = 3
                else:
                    self.table.setRowCount(4)
                    expected_rows = 4

                self.assertEqual(self.table.columnCount(), 2)
                self.assertEqual(
                    [
                        self.table.horizontalHeaderItem(column).text()
                        for column in range(self.table.columnCount())
                    ],
                    ["101", "202"],
                )
                round_trip = self.table.dataframe_copy()
                self.assertEqual(round_trip.shape, (expected_rows, 2))
                self.assertEqual(list(round_trip.columns), ["101", "202"])

    def test_insert_and_remove_columns_preserve_names_and_data(self) -> None:
        self.table.set_dataframe(
            pd.DataFrame([["a", "b"], ["c", "d"]], columns=["A", "B"])
        )

        self.table.insertColumn(1)

        self.assertEqual(self.table.columnCount(), 3)
        self.assertEqual(
            [self.table.horizontalHeaderItem(column).text() for column in range(3)],
            ["A", "Col 1", "B"],
        )
        self.assertEqual(self.table.item(0, 0).text(), "a")
        self.assertEqual(self.table.item(0, 1).text(), "")
        self.assertEqual(self.table.item(0, 2).text(), "b")

        self.table.removeColumn(0)

        expected = pd.DataFrame([["", "b"], ["", "d"]], columns=["Col 1", "B"])
        assert_frame_equal(self.table.dataframe_copy(), expected)

    def test_insert_column_allows_explicit_duplicate_name_without_model_drift(
        self,
    ) -> None:
        self.table.set_dataframe(
            pd.DataFrame([["a", "b"], ["c", "d"]], columns=["Duplicate", "Other"])
        )

        self.table.insertColumn(1, "Duplicate")
        self.table.item(0, 1).setText("inserted")

        expected = pd.DataFrame(
            [["a", "inserted", "b"], ["c", "", "d"]],
            columns=["Duplicate", "Duplicate", "Other"],
        )
        self.assertEqual(self.table.columnCount(), 3)
        self.assertEqual(
            [
                self.table.horizontalHeaderItem(column).text()
                for column in range(self.table.columnCount())
            ],
            ["Duplicate", "Duplicate", "Other"],
        )
        assert_frame_equal(
            self.table.dataframe_copy(),
            expected,
            check_dtype=False,
        )

    def test_remove_column_with_duplicate_names_removes_only_selected_position(self) -> None:
        self.table.set_dataframe(
            pd.DataFrame(
                [
                    ["a", "first-1", "second-1", "b"],
                    ["c", "first-2", "second-2", "d"],
                ],
                columns=["A", "Duplicate", "Duplicate", "B"],
            )
        )

        self.table.removeColumn(1)

        expected = pd.DataFrame(
            [["a", "second-1", "b"], ["c", "second-2", "d"]],
            columns=["A", "Duplicate", "B"],
        )
        self.assertEqual(self.table.columnCount(), 3)
        assert_frame_equal(self.table.dataframe_copy(), expected)

    def test_dataframe_round_trip_is_equal_and_returned_copy_is_detached(self) -> None:
        dataframe = pd.DataFrame(
            {
                "Distance": [0.0, 1.25, float("nan")],
                "Label": ["origin", None, "end"],
            }
        )
        self.table.set_dataframe(dataframe)

        round_trip = self.table.dataframe_copy()

        assert_frame_equal(round_trip, dataframe)
        round_trip.iat[0, 0] = 99.0
        self.assertEqual(self.table.dataframe_copy().iat[0, 0], 0.0)


if __name__ == "__main__":
    unittest.main()
