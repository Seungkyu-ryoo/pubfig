from __future__ import annotations

import os
from types import SimpleNamespace
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from pubfig.plot_config import FILLABLE_MARKERS, SeriesConfig
from pubfig.ui.series_settings import SERIES_WIDGET_ALIASES, SeriesSettingsPanel
from pubfig.ui.widgets import marker_preview_icon


class SeriesSettingsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.panel = SeriesSettingsPanel()

    def tearDown(self) -> None:
        self.panel.deleteLater()

    def test_binding_load_and_update_round_trip_preserves_non_widget_fields(self) -> None:
        changes = []
        self.panel.connect_changed(changes.append)
        source = SeriesConfig(
            x="time",
            y="signal",
            label="Signal",
            color="#123456",
            plot_type="line+marker",
            y_axis="right",
            marker="s",
            marker_fill_style="none",
            line_style="dashed",
            line_width=2.5,
            marker_size=7.0,
            y_offset=0.25,
            alpha=0.6,
            force_opaque=True,
            show_in_legend=False,
            error_column="sigma",
            error_cap_size=3.5,
        )

        self.panel.load_series(source, error_columns=["time", "signal", "sigma"])
        self.assertEqual(changes, [])
        self.assertEqual(self.panel.label_edit.text(), "Signal")
        self.assertEqual(self.panel.axis_combo.currentText(), "right")
        self.assertEqual(self.panel.type_combo.currentText(), "line+marker")
        self.assertEqual(self.panel.marker_combo.marker_code(), "s")
        self.assertEqual(self.panel.marker_combo.fill_style(), "none")
        self.assertEqual(self.panel.marker_combo.text(), "Open square")
        self.assertEqual(self.panel.color_button.text(), "#123456")
        self.assertEqual(self.panel.error_combo.currentText(), "sigma")
        self.assertEqual(self.panel.error_cap_spin.value(), 3.5)
        self.assertIn(
            "Error bars",
            [
                self.panel.tabs.tabText(index)
                for index in range(self.panel.tabs.count())
            ],
        )

        self.panel.label_edit.setText("  Revised  ")
        self.panel.axis_combo.setCurrentText("left")
        self.panel.type_combo.setCurrentText("scatter")
        self.panel.line_width_spin.setValue(4.0)
        self.panel.alpha_spin.setValue(0.35)
        self.panel.legend_check.setChecked(True)
        self.panel.error_combo.setCurrentText("(none)")
        self.panel.set_color("#abcdef")
        result = self.panel.update_series(source)

        self.assertIs(result, source)
        self.assertEqual(result.label, "Revised")
        self.assertEqual(result.y_axis, "left")
        self.assertEqual(result.plot_type, "scatter")
        self.assertEqual(result.line_width, 4.0)
        self.assertEqual(result.alpha, 0.35)
        self.assertTrue(result.show_in_legend)
        self.assertEqual(result.error_column, "")
        self.assertEqual(result.color, "#abcdef")
        self.assertEqual((result.x, result.y, result.force_opaque), ("time", "signal", True))
        self.assertGreaterEqual(len(changes), 7)

    def test_marker_picker_is_a_nine_by_twelve_icon_grid(self) -> None:
        picker = self.panel.marker_combo

        self.assertEqual(picker.option_count, 108)
        self.assertEqual(picker.grid_shape, (9, 12))
        self.assertTrue(all(not button.icon().isNull() for button in picker._buttons.values()))
        for marker in FILLABLE_MARKERS:
            for fill_style in ("full", "none", "left", "right", "bottom", "top"):
                self.assertIn((marker, fill_style), picker._buttons)
        self.assertIn((r"$\odot$", "full"), picker._buttons)
        self.assertIn((r"$\oplus$", "full"), picker._buttons)
        self.assertIn(("^", "full"), picker._buttons)
        self.assertIn(("^", "none"), picker._buttons)
        self.assertIn(("v", "full"), picker._buttons)
        self.assertIn(("v", "none"), picker._buttons)

        changes = []
        self.panel.connect_changed(changes.append)
        picker.set_choice("v", "none")

        self.assertEqual(len(changes), 1)
        self.assertIs(changes[0], picker)
        self.assertEqual(picker.marker_code(), "v")
        self.assertEqual(picker.fill_style(), "none")
        self.assertEqual(picker.text(), "Open triangle down")

        picker.set_choice("o", "left")
        self.assertEqual(len(changes), 2)
        self.assertEqual((picker.marker_code(), picker.fill_style()), ("o", "left"))
        self.assertEqual(picker.text(), "Left-half circle")

    def test_marker_picker_round_trips_display_and_custom_markers(self) -> None:
        series = SeriesConfig(marker="^", marker_fill_style="none")
        self.panel.load_series(series)

        self.assertEqual(self.panel.marker_combo.text(), "Open triangle up")
        self.assertNotIn(self.panel.marker_combo.text(), {"^", "v", "s"})
        self.panel.marker_combo.set_choice("p", "full")
        self.panel.update_series(series)
        self.assertEqual((series.marker, series.marker_fill_style), ("p", "full"))

        custom = SeriesConfig(marker="$A$", marker_fill_style="none")
        self.panel.load_series(custom)
        self.panel.update_series(custom)
        self.assertEqual((custom.marker, custom.marker_fill_style), ("$A$", "none"))

    def test_partial_circle_icons_fill_the_requested_half(self) -> None:
        def alpha(fill_style: str, x: int, y: int) -> int:
            image = marker_preview_icon("o", fill_style).pixmap(26, 26).toImage()
            return image.pixelColor(x, y).alpha()

        self.assertGreater(alpha("left", 8, 13), 200)
        self.assertLess(alpha("left", 18, 13), 20)
        self.assertLess(alpha("right", 8, 13), 20)
        self.assertGreater(alpha("right", 18, 13), 200)
        self.assertLess(alpha("bottom", 13, 8), 20)
        self.assertGreater(alpha("bottom", 13, 18), 200)
        self.assertGreater(alpha("top", 13, 8), 200)
        self.assertLess(alpha("top", 13, 18), 20)

    def test_point_and_pixel_icons_preserve_their_relative_size(self) -> None:
        def opaque_width(marker: str) -> int:
            image = marker_preview_icon(marker).pixmap(26, 26).toImage()
            occupied_x = [
                x
                for x in range(image.width())
                if any(
                    image.pixelColor(x, y).alpha() > 0
                    for y in range(image.height())
                )
            ]
            return max(occupied_x) - min(occupied_x) + 1

        self.assertGreater(opaque_width("o"), opaque_width("."))
        self.assertGreater(opaque_width("s"), opaque_width(","))

    def test_empty_label_falls_back_to_series_y_and_unknown_error_is_none(self) -> None:
        series = SeriesConfig(x="x", y="temperature", label="Old", error_column="missing")
        self.panel.load(series, error_columns=["x", "temperature"])
        self.assertEqual(self.panel.error_combo.currentText(), "(none)")
        self.panel.label_edit.clear()

        self.assertIs(self.panel.update(series), series)
        self.assertEqual(series.label, "temperature")
        self.assertEqual(series.error_column, "")

    def test_targets_enable_editor_and_do_not_emit_during_population(self) -> None:
        target_changes: list[str] = []
        self.panel.target_combo.currentTextChanged.connect(target_changes.append)

        selected = self.panel.set_targets(["y1", "y2", "y1"], preferred="y2")
        self.assertEqual(selected, "y2")
        self.assertEqual(target_changes, [])
        self.assertTrue(all(widget.isEnabled() for widget in self.panel.bindings.widgets))

        self.panel.set_targets([])
        self.assertEqual(target_changes, [])
        self.assertTrue(all(not widget.isEnabled() for widget in self.panel.bindings.widgets))

    def test_compatibility_alias_map_exposes_owned_widgets(self) -> None:
        host = SimpleNamespace()
        self.panel.install_compatibility_aliases(host)

        self.assertEqual(set(self.panel.compatibility_widgets), set(SERIES_WIDGET_ALIASES))
        for old_name, panel_name in SERIES_WIDGET_ALIASES.items():
            widget = getattr(self.panel, panel_name)
            self.assertIs(getattr(host, old_name), widget)
            self.assertTrue(self.panel.isAncestorOf(widget) or widget is self.panel)

    def test_colormap_selection_mode_and_gradient_payload_are_self_contained(self) -> None:
        panel = SeriesSettingsPanel(
            gradient_stops='[[0.0, "#000000", 0.2], [1.0, "#ffffff", 1.0]]'
        )
        self.addCleanup(panel.deleteLater)
        panel.set_cmap_columns(["y1", "y2"])
        self.assertEqual(panel.selected_cmap_columns(), ["y1", "y2"])
        panel.clear_cmap_columns()
        self.assertEqual(panel.selected_cmap_columns(), [])
        panel.select_all_cmap_columns()
        self.assertEqual(panel.selected_cmap_columns(), ["y1", "y2"])

        panel.set_cmap_mode(True)
        self.assertFalse(panel.cmap_color_section.isVisible())
        # Visibility to the screen also depends on ancestors; hidden state is
        # the stable assertion for an unshown offscreen panel.
        self.assertFalse(panel.cmap_alpha_section.isHidden())
        self.assertEqual(panel.gradient_editor.stops_payload()[0], [0.0, "#000000", 0.2])


if __name__ == "__main__":
    unittest.main()
