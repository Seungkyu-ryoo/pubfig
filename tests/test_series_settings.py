from __future__ import annotations

import os
from types import SimpleNamespace
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from pubfig.plot_config import SeriesConfig
from pubfig.ui.series_settings import SERIES_WIDGET_ALIASES, SeriesSettingsPanel


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
        self.assertEqual(self.panel.color_button.text(), "#123456")
        self.assertEqual(self.panel.error_combo.currentText(), "sigma")

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
