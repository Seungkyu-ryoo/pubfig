from __future__ import annotations

from dataclasses import asdict
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import matplotlib as mpl
from matplotlib.colors import to_hex
import pandas as pd
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from pubfig.plot_config import PlotConfig, SeriesColorRecipe, SeriesConfig
from pubfig.project_io import document_from_payload, document_to_payload
from pubfig.sheet_data import with_metadata_rows
from pubfig.style_colors import sample_series_color_recipe
from pubfig.ui.main_window import GraphDrawerWindow


class StyleColormapCopyTests(unittest.TestCase):
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
        self.window.new_graph()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.window._set_modified(False)
        self.window.close()
        self.app.processEvents()
        self.temp_directory.cleanup()

    def configure_plotted_series(self, count: int) -> list[str]:
        columns = ["X", *[f"Y{index + 1}" for index in range(count)]]
        data = pd.DataFrame(
            [
                [0.0, *[float(index) for index in range(count)]],
                [1.0, *[float(index + 1) for index in range(count)]],
            ],
            columns=columns,
        )
        self.window.render_timer.stop()
        self.window.plot_config = PlotConfig()
        self.window.annotations = []
        self.window.series_by_y.clear()
        self.window.df = with_metadata_rows(data)
        if self.window.active_sheet_id in self.window.sheets:
            self.window.sheets[self.window.active_sheet_id].df = self.window.df
        self.window.populate_table()
        y_columns = columns[1:]
        self.window.populate_columns(preferred_y=y_columns)
        self.window._set_checked_y_columns(y_columns)
        self.window.refresh_series_configs()
        self.window.update_style_targets()
        self.window.refresh_cmap_column_list()
        self.window._load_config_into_widgets()
        self.window.render_timer.stop()
        return y_columns

    def apply_full_viridis(self, y_columns: list[str]) -> None:
        self.window.series_settings.set_cmap_columns(
            y_columns,
            preserve_selection=False,
        )
        self.window.cmap_alpha_only_check.setChecked(False)
        self.window.cmap_combo.setCurrentText("viridis")
        self.window.cmap_start_spin.setValue(0.05)
        self.window.cmap_end_spin.setValue(0.95)
        self.window.apply_colormap_to_plotted_series()
        self.window.render_timer.stop()

    def test_style_copy_preserves_marker_shape_and_partial_fill(self) -> None:
        source_columns = self.configure_plotted_series(1)
        source = self.window.series_by_y[source_columns[0]]
        source.marker = "o"
        source.marker_fill_style = "left"
        source.plot_type = "line+marker"
        bundle = self.window.capture_current_style()

        target_columns = self.configure_plotted_series(2)
        self.window.apply_style_bundle(bundle, "copied style")

        self.assertEqual(
            [
                (
                    self.window.series_by_y[column].marker,
                    self.window.series_by_y[column].marker_fill_style,
                )
                for column in target_columns
            ],
            [("o", "left"), ("o", "left")],
        )

    def test_copy_resamples_full_colormap_across_larger_target(self) -> None:
        source_columns = self.configure_plotted_series(5)
        self.apply_full_viridis(source_columns)
        self.window.copy_current_style()
        bundle = self.window.style_clipboard

        self.assertEqual(bundle["plot_config"].series_color_recipe.series_count, 5)

        target_columns = self.configure_plotted_series(6)
        self.window.apply_copied_style()
        expected = [
            to_hex(mpl.colormaps["viridis"](0.05 + 0.90 * index / 5))
            for index in range(6)
        ]
        actual = [self.window.series_by_y[column].color for column in target_columns]

        self.assertEqual(actual, expected)
        self.assertNotEqual(actual[-2], actual[-1])
        self.assertTrue(
            all(self.window.series_by_y[column].force_opaque for column in target_columns)
        )
        self.assertEqual(self.window.plot_config.series_color_recipe.series_count, 6)

    def test_copy_resamples_both_endpoints_across_smaller_target(self) -> None:
        source_columns = self.configure_plotted_series(5)
        self.apply_full_viridis(source_columns)
        bundle = self.window.capture_current_style()

        target_columns = self.configure_plotted_series(2)
        self.window.apply_style_bundle(bundle, "copied style")

        self.assertEqual(
            [self.window.series_by_y[column].color for column in target_columns],
            [
                to_hex(mpl.colormaps["viridis"](0.05)),
                to_hex(mpl.colormaps["viridis"](0.95)),
            ],
        )

    def test_partial_colormap_and_legacy_manual_colors_do_not_resample(self) -> None:
        y_columns = self.configure_plotted_series(3)
        self.window.series_settings.set_cmap_columns(
            y_columns,
            preserve_selection=False,
        )
        self.window.cmap_column_list.item(2).setCheckState(Qt.Unchecked)
        self.window.apply_colormap_to_plotted_series()
        self.assertIsNone(self.window.plot_config.series_color_recipe)

        bundle = {
            "plot_config": PlotConfig(),
            "series_templates": [
                SeriesConfig(color="#ff0000"),
                SeriesConfig(color="#0000ff"),
            ],
            "annotations": [],
            "has_annotations": False,
        }
        self.window.apply_style_bundle(bundle, "legacy style")
        self.assertEqual(
            [self.window.series_by_y[column].color for column in y_columns],
            ["#ff0000", "#0000ff", "#0000ff"],
        )

    def test_recipe_round_trips_through_named_style_and_project_payloads(self) -> None:
        y_columns = self.configure_plotted_series(4)
        self.apply_full_viridis(y_columns)
        bundle = self.window.capture_current_style()

        style_payload = self.window.style_bundle_to_payload(bundle)
        restored_bundle = self.window.style_bundle_from_payload(style_payload)
        restored_recipe = restored_bundle["plot_config"].series_color_recipe
        self.assertIsInstance(restored_recipe, SeriesColorRecipe)
        self.assertEqual(
            asdict(restored_recipe),
            asdict(bundle["plot_config"].series_color_recipe),
        )

        self.window.capture_active_graph()
        project_payload = document_to_payload(self.window.document)
        restored_document = document_from_payload(project_payload)
        restored_graph = restored_document.graphs[self.window.active_graph_id]
        self.assertIsInstance(
            restored_graph.plot_config.series_color_recipe,
            SeriesColorRecipe,
        )
        self.assertEqual(restored_graph.plot_config.series_color_recipe.series_count, 4)

    def test_manual_color_change_invalidates_full_colormap_recipe(self) -> None:
        y_columns = self.configure_plotted_series(3)
        self.apply_full_viridis(y_columns)

        self.window.series_by_y[y_columns[1]].color = "#ffffff"
        self.window._discard_invalid_series_color_recipe()

        self.assertIsNone(self.window.plot_config.series_color_recipe)

    def test_single_destination_series_uses_colormap_midpoint(self) -> None:
        recipe = SeriesColorRecipe(start=0.2, end=0.8, series_count=3)

        self.assertEqual(
            sample_series_color_recipe(recipe, 1),
            [to_hex(mpl.colormaps["viridis"](0.5))],
        )


if __name__ == "__main__":
    unittest.main()
