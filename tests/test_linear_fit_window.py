from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from pubfig.ui.main_window import GraphDrawerWindow


class LinearFitWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

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
        self.window.settings = QSettings(
            str(Path(self.temp_directory.name) / "settings.ini"),
            QSettings.IniFormat,
        )
        self.app.processEvents()

    def tearDown(self) -> None:
        self.window._set_modified(False)
        self.window.close()
        self.app.processEvents()
        self.temp_directory.cleanup()

    def test_fit_control_renders_result_and_is_undoable(self) -> None:
        self.window.new_graph()
        self.window.table.set_block(2, 0, [[0, 1], [1, 3], [2, 5]])
        self.window.undo_history.reset()

        self.window.linear_fit_enabled_check.setChecked(True)
        self.window.render_timer.stop()
        self.window.render_plot()

        series = self.window.series_by_y["Col 2"]
        result = self.window.linear_fit_results_by_y["Col 2"]
        self.assertTrue(series.linear_fit_enabled)
        self.assertAlmostEqual(result.slope, 2.0)
        self.assertAlmostEqual(result.intercept, 1.0)
        self.assertIn("y = 2x + 1", self.window.linear_fit_result_label.text())
        self.assertTrue(
            any(
                (line.get_gid() or "").startswith("pubfig_linear_fit_")
                for axis in self.window.current_figure.axes
                for line in axis.lines
            )
        )

        self.window.undo_workspace()
        self.assertFalse(self.window.series_by_y["Col 2"].linear_fit_enabled)
        self.assertFalse(self.window.linear_fit_enabled_check.isChecked())

    def test_saved_style_copies_fit_appearance_but_not_analysis_range(self) -> None:
        self.window.new_graph()
        source = self.window.series_by_y["Col 2"]
        source.linear_fit_enabled = True
        source.linear_fit_x_min = 2.0
        source.linear_fit_x_max = 8.0
        source.linear_fit_line_style = "dotted"
        source.linear_fit_line_width = 2.5
        self.window.update_style_targets()

        bundle = self.window.capture_current_style()
        payload = self.window.style_bundle_to_payload(bundle)
        template_payload = payload["series_templates"][0]
        self.assertNotIn("linear_fit_enabled", template_payload)
        self.assertNotIn("linear_fit_x_min", template_payload)
        self.assertNotIn("linear_fit_x_max", template_payload)

        source.linear_fit_enabled = False
        source.linear_fit_x_min = 5.0
        source.linear_fit_x_max = None
        source.linear_fit_line_style = "solid"
        source.linear_fit_line_width = 1.0
        self.window.apply_style_bundle(bundle, "fit style")

        target = self.window.series_by_y["Col 2"]
        self.assertFalse(target.linear_fit_enabled)
        self.assertEqual(target.linear_fit_x_min, 5.0)
        self.assertIsNone(target.linear_fit_x_max)
        self.assertEqual(target.linear_fit_line_style, "dotted")
        self.assertEqual(target.linear_fit_line_width, 2.5)


if __name__ == "__main__":
    unittest.main()
