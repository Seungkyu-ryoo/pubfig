from __future__ import annotations

from dataclasses import asdict
import unittest

from plot_config import (
    LegendEntryConfig,
    PlotConfig,
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
                LegendEntryConfig(source_y="Col 2", label="Sample"),
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

    def test_legacy_plot_config_keeps_automatic_legend_mode(self) -> None:
        restored = plot_config_from_payload({"legend": True})

        self.assertIsNone(restored.legend_entries)

    def test_legacy_plot_config_keeps_all_four_axes_visible(self) -> None:
        restored = plot_config_from_payload({})

        self.assertEqual(restored.axis_line_width, 0.5)
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
