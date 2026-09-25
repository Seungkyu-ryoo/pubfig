from __future__ import annotations

import unittest

import pandas as pd

from pubfig.sheet_data import (
    DATA_START_ROW,
    NAME_ROW,
    ROLE_ROW,
    blank_dataframe,
    column_name,
    column_role,
    nearest_left_x,
    next_column_name,
    normalize_column_names,
    normalize_dataframe_columns,
    plot_dataframe,
    trim_trailing_empty_rows,
    with_metadata_rows,
    x_columns,
    y_columns,
)


class SheetDataTests(unittest.TestCase):
    def test_column_normalization_has_one_deterministic_policy(self) -> None:
        self.assertEqual(
            normalize_column_names(["A", "A_2", "A", "", ""]),
            ["A", "A_2", "A_3", "Col 4", "Col 5"],
        )

    def test_dataframe_normalization_is_non_mutating_by_default(self) -> None:
        source = pd.DataFrame([[1, 2, 3]], columns=["Value", "Value", 7])

        normalized = normalize_dataframe_columns(source)

        self.assertEqual(list(source.columns), ["Value", "Value", 7])
        self.assertEqual(list(normalized.columns), ["Value", "Value_2", "7"])

    def test_next_column_name_is_shared_by_table_and_window(self) -> None:
        self.assertEqual(next_column_name(["Col 1", "Signal", "Col 3"]), "Col 2")

    def test_blank_dataframe_and_metadata_helpers(self) -> None:
        frame = blank_dataframe(rows=3, columns=3)

        self.assertEqual(frame.shape, (3 + DATA_START_ROW, 3))
        self.assertEqual(frame.iat[ROLE_ROW, 0], "X")
        self.assertEqual(frame.iat[ROLE_ROW, 1], "Y")
        self.assertEqual(frame.iat[NAME_ROW, 0], "")
        self.assertEqual(len(plot_dataframe(frame)), 3)

    def test_role_name_and_nearest_x_are_centralized(self) -> None:
        frame = pd.DataFrame(
            [
                ["X1", "Y", "X", "y values"],
                ["Time", "Signal A", "Frequency", "Signal B"],
                [0, 1, 10, 2],
            ],
            columns=["x1", "a", "x2", "b"],
        )

        self.assertEqual(column_role(frame, "x1"), "X")
        self.assertEqual(column_role(frame, "b"), "Y")
        self.assertEqual(column_name(frame, "a"), "Signal A")
        self.assertEqual(nearest_left_x(frame, "b"), "x2")
        self.assertEqual(x_columns(frame), ["x1", "x2"])
        self.assertEqual(y_columns(frame, require_left_x=True), ["a", "b"])

    def test_imported_data_gets_metadata_and_unique_columns(self) -> None:
        data = pd.DataFrame([[1, 2], [3, 4]], columns=["Signal", "Signal"])

        sheet = with_metadata_rows(data)

        self.assertEqual(list(sheet.columns), ["Signal", "Signal_2"])
        self.assertEqual(sheet.iloc[ROLE_ROW].tolist(), ["X", "Y"])
        self.assertEqual(sheet.iloc[NAME_ROW].tolist(), ["Signal", "Signal_2"])
        self.assertEqual(plot_dataframe(sheet).values.tolist(), [["1", "2"], ["3", "4"]])

    def test_trimming_preserves_metadata_and_internal_empty_rows(self) -> None:
        source = pd.DataFrame(
            [
                ["X", "Y"],
                ["", ""],
                [1, 2],
                ["", None],
                [3, ""],
                ["", None],
                [pd.NA, float("nan")],
            ],
            columns=["x", "y"],
            dtype=object,
        )

        trimmed = trim_trailing_empty_rows(source)

        self.assertEqual(trimmed.shape, (5, 2))
        self.assertEqual(trimmed.iloc[3].tolist(), ["", None])
        self.assertEqual(source.shape, (7, 2))

    def test_trimming_never_removes_two_blank_metadata_rows(self) -> None:
        source = pd.DataFrame("", index=range(5), columns=["x", "y"])

        trimmed = trim_trailing_empty_rows(source)

        self.assertEqual(trimmed.shape, (DATA_START_ROW, 2))

    def test_whitespace_is_data_but_import_drops_a_truly_empty_tail(self) -> None:
        source = pd.DataFrame(
            [[1, 2], ["", ""], [" ", ""], [None, ""]],
            columns=["x", "y"],
        )

        imported = with_metadata_rows(source)

        self.assertEqual(imported.shape, (5, 2))
        self.assertEqual(imported.iloc[-2].tolist(), ["", ""])
        self.assertEqual(imported.iloc[-1].tolist(), [" ", ""])


if __name__ == "__main__":
    unittest.main()
