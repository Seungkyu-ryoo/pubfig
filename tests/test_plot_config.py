from __future__ import annotations

from dataclasses import asdict
import unittest

from plot_config import (
    LegendEntryConfig,
    PlotConfig,
    legend_entry_config_from_payload,
    plot_config_from_payload,
)


class PlotConfigTests(unittest.TestCase):
    def test_legacy_single_figure_loader_rejects_future_and_boolean_versions(self) -> None:
        from plot_config import project_from_payload

        for version in (True, 2, 999, "1", None):
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    project_from_payload({"schema_version": version})

    def test_legend_entries_round_trip_as_typed_pairs(self) -> None:
        config = PlotConfig(
            legend_entries=[
                LegendEntryConfig(source_y="Col 4", label="Control"),
                LegendEntryConfig(
                    source_y="Col 2",
                    label="Sample",
                    font_family="Arial",
                    font_size=9.5,
                    font_bold=True,
                    font_italic=True,
                    text_color="#336699",
                ),
            ],
            legend_row_lengths=[2],
        )

        restored = plot_config_from_payload(asdict(config))

        self.assertEqual(restored.legend_entries, config.legend_entries)
        self.assertTrue(
            all(
                isinstance(entry, LegendEntryConfig)
                for entry in restored.legend_entries
            )
        )
        self.assertEqual(restored.legend_row_lengths, [2])

    def test_legacy_legend_entry_payload_uses_default_text_style(self) -> None:
        restored = legend_entry_config_from_payload(
            {"source_y": "Col 2", "label": "Legacy"}
        )

        self.assertEqual(
            restored,
            LegendEntryConfig(source_y="Col 2", label="Legacy"),
        )
        self.assertEqual(restored.font_family, "")
        self.assertIsNone(restored.font_size)
        self.assertFalse(restored.font_bold)
        self.assertFalse(restored.font_italic)
        self.assertEqual(restored.text_color, "")

    def test_legacy_plot_config_keeps_automatic_legend_mode(self) -> None:
        restored = plot_config_from_payload({"legend": True})

        self.assertIsNone(restored.legend_entries)

    def test_legacy_plot_config_keeps_all_four_axes_visible(self) -> None:
        restored = plot_config_from_payload({})

        self.assertEqual(restored.axis_line_width, 0.5)
        self.assertEqual(restored.y_label_offset_mm, 6.0)
        self.assertTrue(restored.show_tick_marks)
        self.assertFalse(restored.show_axis_arrows)
        self.assertTrue(restored.show_top_axis)
        self.assertTrue(restored.show_bottom_axis)
        self.assertTrue(restored.show_left_axis)
        self.assertTrue(restored.show_right_axis)

    def test_axis_arrow_setting_round_trips(self) -> None:
        restored = plot_config_from_payload(asdict(PlotConfig(show_axis_arrows=True)))

        self.assertTrue(restored.show_axis_arrows)


if __name__ == "__main__":
    unittest.main()
