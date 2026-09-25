from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
from PySide6.QtWidgets import QApplication

from pubfig.fitting import linear_fit
from pubfig.model import Graph, ProjectDocument, Sheet, TreeNode
from pubfig.plot_config import SeriesConfig, series_config_from_payload
from pubfig.project_io import document_from_payload, document_to_payload
from pubfig.ui.series_settings import SeriesSettingsPanel


class LinearFitUiAndIoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_series_panel_round_trip_controls_and_result(self) -> None:
        panel = SeriesSettingsPanel()
        self.addCleanup(panel.deleteLater)
        changes = []
        panel.connect_changed(changes.append)
        series = SeriesConfig(
            x="x",
            y="y",
            linear_fit_enabled=True,
            linear_fit_x_min=1.25,
            linear_fit_x_max=8.75,
            linear_fit_line_style="dashdot",
            linear_fit_line_width=2.25,
        )

        panel.load_series(series, error_columns=["x", "y"])
        self.assertEqual(changes, [])
        self.assertTrue(panel.linear_fit_enabled_check.isChecked())
        self.assertEqual(panel.linear_fit_x_min_edit.text(), "1.25")
        self.assertEqual(panel.linear_fit_x_max_edit.text(), "8.75")
        self.assertTrue(panel.linear_fit_x_min_edit.isEnabled())

        panel.linear_fit_x_min_edit.setText("2")
        panel.linear_fit_x_max_edit.clear()
        panel.linear_fit_line_style_combo.setCurrentText("dotted")
        panel.linear_fit_line_width_spin.setValue(1.75)
        panel.update_series(series)
        self.assertEqual(series.linear_fit_x_min, 2.0)
        self.assertIsNone(series.linear_fit_x_max)
        self.assertEqual(series.linear_fit_line_style, "dotted")
        self.assertEqual(series.linear_fit_line_width, 1.75)

        panel.set_linear_fit_result(linear_fit([0, 1], [1, 3]), enabled=True)
        self.assertIn("y = 2x + 1", panel.linear_fit_result_label.text())
        panel.linear_fit_enabled_check.setChecked(False)
        self.assertTrue(panel.linear_fit_x_min_edit.isEnabled())

    def test_invalid_bound_preserves_model_and_is_reset_when_series_loads(self) -> None:
        panel = SeriesSettingsPanel()
        self.addCleanup(panel.deleteLater)
        series = SeriesConfig(
            x="x",
            y="y",
            linear_fit_enabled=True,
            linear_fit_x_min=1.5,
        )
        panel.load_series(series)

        panel.linear_fit_x_min_edit.setText("not a number")
        panel.update_series(series)
        panel.set_linear_fit_result(linear_fit([0, 1], [1, 3]), enabled=True)

        self.assertEqual(series.linear_fit_x_min, 1.5)
        self.assertFalse(panel.linear_fit_bounds_are_valid())
        self.assertIn("finite numeric X bounds", panel.linear_fit_result_label.text())

        panel.load_series(SeriesConfig(x="x", y="other", linear_fit_x_min=2.0))
        self.assertTrue(panel.linear_fit_bounds_are_valid())
        self.assertFalse(panel.linear_fit_x_min_edit.property("invalid"))

    def test_project_round_trip_and_legacy_series_defaults(self) -> None:
        series = SeriesConfig(
            x="x",
            y="y",
            linear_fit_enabled=True,
            linear_fit_x_min=2.0,
            linear_fit_x_max=9.0,
            linear_fit_line_style="dotted",
            linear_fit_line_width=1.5,
        )
        sheet = Sheet("sh1", "Data", pd.DataFrame({"x": [0, 1], "y": [1, 3]}))
        graph = Graph(
            "gr1",
            "Graph",
            sheet.id,
            series_by_y={"y": series},
            checked_y=["y"],
        )
        graph_node = TreeNode("ndg", "graph", graph.name, graph.id)
        sheet_node = TreeNode("nds", "sheet", sheet.name, sheet.id, [graph_node])
        root = TreeNode("ndr", "folder", "Project", children=[sheet_node])
        document = ProjectDocument(
            sheets={sheet.id: sheet},
            graphs={graph.id: graph},
            tree_root=root,
            active_node_id=graph_node.id,
        )

        restored = document_from_payload(document_to_payload(document))
        restored_series = restored.graphs[graph.id].series_by_y["y"]
        self.assertTrue(restored_series.linear_fit_enabled)
        self.assertEqual(restored_series.linear_fit_x_min, 2.0)
        self.assertEqual(restored_series.linear_fit_x_max, 9.0)
        self.assertEqual(restored_series.linear_fit_line_style, "dotted")
        self.assertEqual(restored_series.linear_fit_line_width, 1.5)

        legacy = series_config_from_payload({"x": "x", "y": "y"})
        self.assertFalse(legacy.linear_fit_enabled)
        self.assertIsNone(legacy.linear_fit_x_min)
        self.assertIsNone(legacy.linear_fit_x_max)


if __name__ == "__main__":
    unittest.main()
