from __future__ import annotations

import unittest

from pubfig.legend_layout import (
    legend_entries_to_text,
    legend_row_ids,
    legend_text_to_entries,
    new_row_flags,
    normalize_row_lengths,
)
from pubfig.plot_config import LegendEntryConfig


class LegendLayoutTests(unittest.TestCase):
    def test_rows_have_one_shared_mapping_policy(self) -> None:
        self.assertEqual(normalize_row_lengths([2, -1, 1]), [2, 0, 1])
        self.assertEqual(legend_row_ids(5, [2, 0, 1]), [0, 0, 2, 3, 4])
        self.assertEqual(new_row_flags(5, [2, 0, 1]), [True, False, True, True, True])

    def test_empty_layout_means_one_entry_per_row(self) -> None:
        self.assertEqual(legend_row_ids(3, []), [0, 1, 2])
        self.assertEqual(new_row_flags(3, []), [True, True, True])

    def test_editor_text_compacts_legacy_zero_length_rows(self) -> None:
        entries = [
            LegendEntryConfig(source_y=source, label=source.upper())
            for source in ("a", "b", "c")
        ]

        text = legend_entries_to_text(entries, [2, 0, 1], ["a", "b", "c"])
        restored, row_lengths = legend_text_to_entries(text, ["a", "b", "c"])

        self.assertEqual(text, "\\L(1) A\t\\L(2) B\n\\L(3) C")
        self.assertEqual(restored, entries)
        self.assertEqual(row_lengths, [2, 1])

    def test_origin_text_compiles_free_text_blank_rows_and_tabbed_cells(self) -> None:
        entries, row_lengths = legend_text_to_entries(
            "Measurements\n\\L(2) Control\t\\L(1) %(1)\n\nNotes",
            ["a", "b"],
        )

        self.assertEqual(
            entries,
            [
                LegendEntryConfig(label="Measurements"),
                LegendEntryConfig(source_y="b", label="Control"),
                LegendEntryConfig(source_y="a", label=""),
                LegendEntryConfig(label=""),
                LegendEntryConfig(label="Notes"),
            ],
        )
        self.assertEqual(row_lengths, [1, 2, 1, 1])

    def test_repeated_samples_after_spaces_create_same_row_cells(self) -> None:
        sources = [
            "col5",
            "col10",
            "col15",
            "col20",
            "col25",
            "col30",
            "col35",
            "col40",
        ]
        text = (
            "\\L(1) %(1) \\L(5) %(5)\n"
            "\\L(2) %(2) \\L(6) %(6)\n"
            "\\L(3) %(3) \\L(7) %(7)\n"
            "\\L(4) %(4) \\L(8) %(8)"
        )

        entries, row_lengths = legend_text_to_entries(text, sources)

        self.assertEqual(
            [entry.source_y for entry in entries],
            ["col5", "col25", "col10", "col30", "col15", "col35", "col20", "col40"],
        )
        self.assertEqual([entry.label for entry in entries], [""] * 8)
        self.assertEqual(row_lengths, [2, 2, 2, 2])
        self.assertEqual(
            legend_entries_to_text(entries, row_lengths, sources),
            text.replace(" \\L", "\t\\L"),
        )

    def test_spaces_in_a_single_sample_label_are_not_split(self) -> None:
        entries, row_lengths = legend_text_to_entries(
            "\\L(1) A label with ordinary spaces",
            ["a"],
        )

        self.assertEqual(
            entries,
            [LegendEntryConfig(source_y="a", label="A label with ordinary spaces")],
        )
        self.assertEqual(row_lengths, [])

    def test_invalid_later_sample_token_remains_literal_label_text(self) -> None:
        entries, row_lengths = legend_text_to_entries(
            "\\L(1) Valid label \\L(99) remains literal",
            ["a", "b"],
        )

        self.assertEqual(
            entries,
            [
                LegendEntryConfig(
                    source_y="a",
                    label="Valid label \\L(99) remains literal",
                )
            ],
        )
        self.assertEqual(row_lengths, [])

    def test_origin_text_round_trip_keeps_stable_and_missing_sources(self) -> None:
        entries = [
            LegendEntryConfig(label="  literal text  "),
            LegendEntryConfig(source_y="b", label="Custom"),
            LegendEntryConfig(source_y="missing ) / source", label="Still here"),
            LegendEntryConfig(source_y="a", label=""),
        ]

        text = legend_entries_to_text(entries, [2, 2], ["a", "b"])
        restored, row_lengths = legend_text_to_entries(text, ["a", "b"])

        self.assertEqual(
            text,
            "  literal text  \t\\L(2) Custom\n"
            "\\L{missing%20%29%20%2F%20source} Still here\t\\L(1) %(1)",
        )
        self.assertEqual(restored, entries)
        self.assertEqual(row_lengths, [2, 2])

    def test_origin_text_preserves_sample_only_and_invalid_tokens(self) -> None:
        sample_only, _rows = legend_text_to_entries("\\L(1)", ["a"])
        encoded = legend_entries_to_text(sample_only, [], ["a"])
        invalid, invalid_rows = legend_text_to_entries(
            "Before \\L(9) after",
            ["a"],
        )

        self.assertEqual(encoded, "\\L(1)")
        self.assertEqual(invalid, [LegendEntryConfig(label="Before \\L(9) after")])
        self.assertEqual(invalid_rows, [])

    def test_empty_editor_is_an_explicit_empty_legend(self) -> None:
        self.assertEqual(legend_text_to_entries("", ["a"]), ([], []))


if __name__ == "__main__":
    unittest.main()
