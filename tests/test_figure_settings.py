from __future__ import annotations

from copy import deepcopy
from dataclasses import fields
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from pubfig.plot_config import PlotConfig
from pubfig.ui.figure_settings import FigureSettingsPanel


EXTERNAL_OR_DERIVED_FIELDS = {
    "annotations",
    "legend_anchor_x",
    "legend_anchor_y",
    "legend_entries",
    "legend_row_lengths",
    "plot_aspect_ratio",
    "transparent",
    "trim_whitespace",
}


def populated_config() -> PlotConfig:
    config = PlotConfig()
    values = {
        "preset": "ACS 2-col",
        "title": "A title",
        "x_label": "distance",
        "y_label": "intensity",
        "y2_label": "temperature",
        "width_mm": 123.4,
        "height_mm": 87.6,
        "plot_width_mm": 66.6,
        "plot_height_mm": 44.4,
        "plot_ratio_locked": True,
        "plot_ratio_preset": "16:9",
        "dpi": 777,
        "title_size": 11,
        "axis_size": 12,
        "axis_line_width": 1.25,
        "tick_size": 13,
        "legend_size": 14,
        "x_label_offset_mm": 7.5,
        "y_label_offset_mm": 8.5,
        "x_tick_pad": 4.5,
        "y_tick_pad": 5.5,
        "y2_tick_pad": 6.5,
        "y_axis_color": "#123456",
        "y2_axis_color": "#abcdef",
        "pad_left_mm": 1.1,
        "pad_right_mm": 2.2,
        "pad_top_mm": 3.3,
        "pad_bottom_mm": 4.4,
        "fixed_plot_area": False,
        "plot_margin_left_mm": 15.1,
        "plot_margin_right_mm": 5.2,
        "plot_margin_top_mm": 6.3,
        "plot_margin_bottom_mm": 13.4,
        "x_scale": "log",
        "y_scale": "linear",
        "y2_scale": "log",
        "x_scale_divisor": 2.5,
        "y_scale_divisor": 3.5,
        "y2_scale_divisor": 4.5,
        "x_min": -10.25,
        "x_max": 99.75,
        "y_min": None,
        "y_max": 88.5,
        "y2_min": -3.25,
        "y2_max": None,
        "x_tick_interval": 2.25,
        "y_tick_interval": None,
        "y2_tick_interval": 4.75,
        "x_minor_divisions": 2,
        "y_minor_divisions": 3,
        "y2_minor_divisions": 4,
        "x_break_enabled": True,
        "x_break_left_min": 0.25,
        "x_break_left_max": 1.25,
        "x_break_right_min": 8.25,
        "x_break_right_max": 9.25,
        "x_break_gap": 0.123,
        "y_break_enabled": True,
        "y_break_lower_min": None,
        "y_break_lower_max": 2.5,
        "y_break_upper_min": 7.5,
        "y_break_upper_max": None,
        "y_break_gap": 0.234,
        "y_offset_step": -3.125,
        "show_x_tick_labels": False,
        "show_y_tick_labels": False,
        "show_y2_tick_labels": False,
        "show_tick_marks": False,
        "show_axis_arrows": True,
        "show_top_axis": False,
        "show_bottom_axis": False,
        "show_left_axis": False,
        "show_right_axis": False,
        "grid": True,
        "legend": False,
    }
    for field, value in values.items():
        setattr(config, field, value)
    return config


class FigureSettingsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_registry_covers_every_plot_config_field_owned_by_panel(self) -> None:
        panel = FigureSettingsPanel(PlotConfig())
        expected = {field.name for field in fields(PlotConfig)} - EXTERNAL_OR_DERIVED_FIELDS
        self.assertEqual(set(panel.bindings.fields), expected)
        self.assertEqual(len(panel.bindings.fields), len(set(panel.bindings.fields)))

    def test_constructor_and_explicit_load_round_trip_every_bound_field(self) -> None:
        source = populated_config()
        panel = FigureSettingsPanel(source)

        # The constructor's config argument must initialize all controls, not
        # only the subset whose values happen to be passed to widget factories.
        constructor_target = PlotConfig()
        panel.update(constructor_target)
        for field in panel.bindings.fields:
            self.assertEqual(
                getattr(constructor_target, field),
                getattr(source, field),
                field,
            )

        second_source = deepcopy(source)
        second_source.title = "Loaded later"
        second_source.x_min = None
        second_source.y_min = -42.125
        second_source.plot_ratio_preset = "Golden 1.618:1"
        panel.load(second_source)
        target = PlotConfig()
        panel.update(target)
        for field in panel.bindings.fields:
            self.assertEqual(getattr(target, field), getattr(second_source, field), field)

    def test_unknown_preset_and_ratio_have_deterministic_fallbacks(self) -> None:
        panel = FigureSettingsPanel(PlotConfig())
        config = PlotConfig(
            preset="Removed publication preset",
            plot_ratio_preset="Removed ratio preset",
        )
        panel.load(config)

        self.assertEqual(panel.preset_combo.currentText(), "Custom")
        self.assertEqual(panel.plot_ratio_preset_combo.currentText(), "Current")
        panel.update(config)
        self.assertEqual(config.preset, "Custom")
        self.assertEqual(config.plot_ratio_preset, "Current")

    def test_load_blocks_change_notifications_and_preserves_prior_block_state(self) -> None:
        panel = FigureSettingsPanel(PlotConfig())
        sources = []
        panel.connect_changed(sources.append)
        already_blocked = panel.title_edit
        already_blocked.blockSignals(True)

        panel.load(populated_config())

        self.assertEqual(sources, [])
        self.assertTrue(already_blocked.signalsBlocked())
        for widget in panel.bindings.widgets:
            if widget is not already_blocked:
                self.assertFalse(widget.signalsBlocked())
        already_blocked.blockSignals(False)

    def test_load_restores_every_signal_block_after_writer_exception(self) -> None:
        panel = FigureSettingsPanel(PlotConfig())
        failing_binding = panel.bindings["y_break_upper_max"]
        original_write = failing_binding.write

        def fail(_value) -> None:
            raise RuntimeError("test writer failure")

        failing_binding.write = fail
        try:
            with self.assertRaisesRegex(RuntimeError, "test writer failure"):
                panel.load(populated_config())
        finally:
            failing_binding.write = original_write

        for widget in panel.bindings.widgets:
            self.assertFalse(widget.signalsBlocked(), binding_field_for(panel, widget))

    def test_changed_signal_and_callback_report_the_originating_widget(self) -> None:
        panel = FigureSettingsPanel(PlotConfig())
        callback_sources = []
        signal_sources = []
        panel.connect_changed(callback_sources.append)
        panel.changed.connect(signal_sources.append)

        panel.title_edit.setText("changed")
        panel.width_spin.setValue(panel.width_spin.value() + 0.1)
        panel.plot_ratio_lock_check.toggle()

        expected = [
            panel.title_edit,
            panel.width_spin,
            panel.plot_ratio_lock_check,
        ]
        self.assertEqual(callback_sources, expected)
        self.assertEqual(signal_sources, expected)


def binding_field_for(panel: FigureSettingsPanel, widget) -> str:
    return next(
        (binding.field for binding in panel.bindings if binding.widget is widget),
        type(widget).__name__,
    )


if __name__ == "__main__":
    unittest.main()
