from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import matplotlib as mpl
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.ticker import ScalarFormatter, LogFormatterSciNotation
import pandas as pd

from pubfig.model import Graph, ProjectDocument, Sheet
from pubfig.plot_config import PlotConfig, SeriesConfig
from pubfig.project_io import document_from_payload, document_to_payload
from pubfig.rendering.core import RenderOptions, render_figure
from pubfig.rendering.export import export_figure


class TickDecimalTests(unittest.TestCase):
    def render(self, config, *, right=False, preview=False, frame=None):
        if frame is None:
            frame = pd.DataFrame({"x": [0.1, 1, 10], "y": [0.2, 1, 9]})
        series = [SeriesConfig(x="x", y="y")]
        if right:
            series.append(SeriesConfig(x="x", y="y", y_axis="right"))
        result = render_figure(
            frame, config, series,
            options=RenderOptions.for_preview() if preview else None,
        )
        self.addCleanup(result.figure.clear)
        FigureCanvasAgg(result.figure).draw()
        return result

    def test_fixed_decimals_are_independent_and_preserve_trailing_zeros(self):
        for preview in (False, True):
            with self.subTest(preview=preview):
                result = self.render(
                    PlotConfig(x_tick_decimals=0, y_tick_decimals=2, y2_tick_decimals=3),
                    right=True, preview=preview,
                )
                left, right = result.figure.axes
                self.assertEqual(left.xaxis.get_major_formatter()(1.25), "1")
                self.assertEqual(left.yaxis.get_major_formatter()(1.2), "1.20")
                self.assertEqual(right.yaxis.get_major_formatter()(1.2), "1.200")
                self.assertEqual(left.yaxis.get_major_formatter()(-0.00001), "0.00")
                self.assertEqual(left.yaxis.get_major_formatter().get_offset(), "")
                # Changing the displayed range must not change the precision.
                left.set_ylim(10000, 10002)
                result.figure.canvas.draw()
                self.assertIn("10000.00", [t.get_text() for t in left.get_yticklabels()])

    def test_auto_keeps_default_linear_and_log_formatting(self):
        for scale, formatter in (("linear", ScalarFormatter), ("log", LogFormatterSciNotation)):
            with self.subTest(scale=scale):
                result = self.render(PlotConfig(x_scale=scale, y_scale=scale))
                axis = result.figure.axes[0]
                self.assertIsInstance(axis.xaxis.get_major_formatter(), formatter)
                self.assertIsInstance(axis.yaxis.get_major_formatter(), formatter)

    def test_log_tick_interval_does_not_override_fixed_decimals(self):
        result = self.render(PlotConfig(
            x_scale="log", y_scale="log", x_tick_interval=1, y_tick_interval=1,
            x_tick_decimals=2, y_tick_decimals=3,
        ))
        axis = result.figure.axes[0]
        self.assertIn("1.00", [t.get_text() for t in axis.get_xticklabels()])
        self.assertIn("1.000", [t.get_text() for t in axis.get_yticklabels()])

    def test_broken_axes_apply_decimals_on_both_segments(self):
        for broken in ("x", "y"):
            with self.subTest(broken=broken):
                config = PlotConfig(x_tick_decimals=2, y_tick_decimals=3)
                if broken == "x":
                    config.x_break_enabled = True
                    config.x_break_left_min, config.x_break_left_max = 0, 2
                    config.x_break_right_min, config.x_break_right_max = 8, 10
                else:
                    config.y_break_enabled = True
                    config.y_break_lower_min, config.y_break_lower_max = 0, 2
                    config.y_break_upper_min, config.y_break_upper_max = 8, 10
                result = self.render(config)
                self.assertEqual(len(result.figure.axes), 2)
                for axis in result.figure.axes:
                    self.assertEqual(axis.xaxis.get_major_formatter()(1), "1.00")
                    self.assertEqual(axis.yaxis.get_major_formatter()(1), "1.000")

    def test_categorical_labels_are_preserved(self):
        result = render_figure(
            pd.DataFrame({"x": ["Alpha", "Beta"], "y": [1, 2]}),
            PlotConfig(x_tick_decimals=2, y_tick_decimals=1, x_scientific_notation=False),
            [SeriesConfig(x="x", y="y", plot_type="bar")],
        )
        self.addCleanup(result.figure.clear)
        FigureCanvasAgg(result.figure).draw()
        self.assertEqual([t.get_text() for t in result.figure.axes[0].get_xticklabels()],
                         ["Alpha", "Beta"])

    def test_export_contains_fixed_labels(self):
        config = PlotConfig(x_min=0, x_max=2, x_tick_interval=1, x_tick_decimals=3)
        result = self.render(config)
        with TemporaryDirectory() as directory, mpl.rc_context({"svg.fonttype": "none"}):
            path = Path(directory) / "decimals.svg"
            export_figure(result.figure, path, config)
            svg = path.read_text()
        self.assertIn(">1.000</text>", svg)

    def test_project_round_trip_and_older_project_defaults(self):
        config = PlotConfig(
            x_tick_decimals=0, y_tick_decimals=2, y2_tick_decimals=12,
            x_scientific_notation=False, y_scientific_notation=False,
            y2_scientific_notation=False,
        )
        document = ProjectDocument(
            sheets={"s": Sheet("s", "Sheet", pd.DataFrame({"x": [1], "y": [2]}))},
            graphs={"g": Graph("g", "Graph", "s", plot_config=config)},
        )
        payload = json.loads(json.dumps(document_to_payload(document)))
        restored = document_from_payload(payload).graphs["g"].plot_config
        self.assertEqual((restored.x_tick_decimals, restored.y_tick_decimals,
                          restored.y2_tick_decimals), (0, 2, 12))
        for axis in ("x", "y", "y2"):
            self.assertFalse(getattr(restored, f"{axis}_scientific_notation"))
            payload["graphs"][0]["plot_config"].pop(f"{axis}_tick_decimals")
            payload["graphs"][0]["plot_config"].pop(f"{axis}_scientific_notation")
        restored = document_from_payload(payload).graphs["g"].plot_config
        self.assertEqual((restored.x_tick_decimals, restored.y_tick_decimals,
                          restored.y2_tick_decimals), (None, None, None))
        for axis in ("x", "y", "y2"):
            self.assertTrue(getattr(restored, f"{axis}_scientific_notation"))

    def test_scientific_notation_can_be_disabled_independently_in_preview_and_export(self):
        frame = pd.DataFrame({"x": [0, 1e6, 2e6], "y": [0, 1e6, 2e6]})
        config = PlotConfig(
            y_scientific_notation=False, y2_scientific_notation=False,
            y_min=0, y_max=2e6, y_tick_interval=1e6,
        )
        for preview in (False, True):
            with self.subTest(preview=preview):
                result = self.render(config, right=True, preview=preview, frame=frame)
                left, right = result.figure.axes
                self.assertTrue(left.xaxis.get_offset_text().get_text())
                for axis in (left.yaxis, right.yaxis):
                    self.assertEqual(axis.get_offset_text().get_text(), "")
                    self.assertIn("1000000", axis.get_major_formatter()(1e6))
                with TemporaryDirectory() as directory, mpl.rc_context({"svg.fonttype": "none"}):
                    path = Path(directory) / "full-values.svg"
                    export_figure(result.figure, path, config)
                    self.assertIn("1000000", path.read_text())
        config.y_scientific_notation = True
        result = self.render(config, frame=frame)
        self.assertTrue(result.figure.axes[0].yaxis.get_offset_text().get_text())

    def test_plain_ticks_disable_additive_offset_and_keep_small_values(self):
        cases = [
            ([1e6, 1e6 + 1, 1e6 + 2], 1e6 + 1, "1000001"),
            ([0, 1e-7, 2e-7], 1e-7, "0.0000001"),
        ]
        for values, tick, expected in cases:
            with self.subTest(expected=expected):
                result = self.render(
                    PlotConfig(x_scientific_notation=False, y_scientific_notation=False),
                    frame=pd.DataFrame({"x": values, "y": values}),
                )
                axis = result.figure.axes[0]
                for dimension in (axis.xaxis, axis.yaxis):
                    self.assertEqual(dimension.get_offset_text().get_text(), "")
                    self.assertIn(expected, dimension.get_major_formatter()(tick))

    def test_plain_notation_combines_with_decimals_and_preserves_log_formatting(self):
        result = self.render(PlotConfig(y_scientific_notation=False, y_tick_decimals=2))
        self.assertEqual(result.figure.axes[0].yaxis.get_major_formatter()(1e6), "1000000.00")
        result = self.render(PlotConfig(
            x_scale="log", y_scale="log",
            x_scientific_notation=False, y_scientific_notation=False,
        ))
        for axis in (result.figure.axes[0].xaxis, result.figure.axes[0].yaxis):
            self.assertIsInstance(axis.get_major_formatter(), LogFormatterSciNotation)

    def test_plain_notation_on_both_broken_axis_segments(self):
        result = self.render(PlotConfig(
            y_scientific_notation=False, y_break_enabled=True,
            y_break_lower_min=0, y_break_lower_max=2e6,
            y_break_upper_min=8e6, y_break_upper_max=10e6,
        ))
        self.assertEqual(len(result.figure.axes), 2)
        for axis in result.figure.axes:
            self.assertEqual(axis.yaxis.get_offset_text().get_text(), "")
            labels = [label.get_text() for label in axis.get_yticklabels()]
            self.assertTrue(any("000000" in label for label in labels))

    def test_each_tick_and_shared_exponent_show_distinct_formats(self):
        frame = pd.DataFrame({"x": [0, 1, 2], "y": [0, 1e6, 2e6]})
        for mode in ("plain", "scientific", "shared"):
            for preview in (False, True):
                for decimals in (None, 0, 2):
                    with self.subTest(mode=mode, preview=preview, decimals=decimals):
                        config = PlotConfig(
                            y_tick_notation=mode, y_tick_decimals=decimals,
                            y_min=0, y_max=2e6, y_tick_interval=1e6,
                        )
                        result = self.render(config, preview=preview, frame=frame)
                        axis = result.figure.axes[0].yaxis
                        formatter = axis.get_major_formatter()
                        text = formatter(1e6)
                        coefficient = "1.00" if decimals == 2 else "1"
                        if mode == "scientific":
                            self.assertEqual(text, rf"${coefficient}\times10^{{6}}$")
                            self.assertEqual(axis.get_offset_text().get_text(), "")
                        elif mode == "shared":
                            self.assertEqual(text, coefficient)
                            self.assertEqual(axis.get_offset_text().get_text(), r"$\times10^{6}$")
                            self.assertEqual(formatter(-0.000001), "0.00" if decimals == 2 else "0")
                        else:
                            self.assertIn("1000000", text)
                            self.assertEqual(axis.get_offset_text().get_text(), "")

    def test_shared_exponent_updates_after_zoom_without_additive_offset(self):
        result = self.render(PlotConfig(y_tick_notation="shared", y_tick_decimals=2))
        axis = result.figure.axes[0]
        axis.set_ylim(0, .000003)
        result.figure.canvas.draw()
        self.assertEqual(axis.yaxis.get_offset_text().get_text(), r"$\times10^{-6}$")
        self.assertEqual(axis.yaxis.get_major_formatter()(.000001), "1.00")
        axis.set_ylim(1e6, 1e6 + 2)
        result.figure.canvas.draw()
        self.assertEqual(axis.yaxis.get_offset_text().get_text(), r"$\times10^{6}$")

    def test_formats_are_independent_on_twin_axes_and_follow_divisors(self):
        frame = pd.DataFrame({"x": [.001, .002, .003], "y": [1e9, 2e9, 3e9]})
        result = self.render(PlotConfig(
            x_scale_divisor=.001, y_scale_divisor=1000, y2_scale_divisor=1e6,
            x_tick_notation="plain", y_tick_notation="shared", y2_tick_notation="scientific",
            x_tick_decimals=0, y_tick_decimals=1, y2_tick_decimals=2,
        ), frame=frame, right=True)
        left, right = result.figure.axes
        self.assertEqual(left.xaxis.get_major_formatter()(.002), "2")
        self.assertEqual(left.yaxis.get_major_formatter()(2e9), "2.0")
        self.assertEqual(left.yaxis.get_offset_text().get_text(), r"$\times10^{6}$")
        self.assertEqual(right.yaxis.get_major_formatter()(2e9), r"$2.00\times10^{3}$")
        self.assertEqual(right.yaxis.get_offset_text().get_text(), "")

    def test_explicit_formats_work_on_log_axes_and_broken_axes(self):
        for mode in ("plain", "scientific", "shared"):
            with self.subTest(mode=mode):
                result = self.render(PlotConfig(
                    x_scale="log", x_tick_notation=mode, y_tick_notation=mode,
                    y_break_enabled=True, y_break_lower_min=0, y_break_lower_max=2,
                    y_break_upper_min=8, y_break_upper_max=10,
                ))
                for axis in result.figure.axes:
                    formatted = axis.yaxis.get_major_formatter()(1)
                    self.assertTrue(formatted)
                    if mode == "scientific":
                        self.assertEqual(axis.xaxis.get_major_formatter()(1e-7), r"$1\times10^{-7}$")
                    elif mode == "plain":
                        self.assertEqual(axis.xaxis.get_major_formatter()(1e-7), "0.0000001")
                    else:
                        self.assertIn("times10", axis.xaxis.get_offset_text().get_text())

    def test_number_format_modes_persist_and_export(self):
        for mode in ("scientific", "shared"):
            config = PlotConfig(
                y_tick_notation=mode, y_tick_decimals=2,
                y_min=0, y_max=2e6, y_tick_interval=1e6,
            )
            document = ProjectDocument(
                sheets={"s": Sheet("s", "Sheet", pd.DataFrame({"x": [0, 1], "y": [0, 1e6]}))},
                graphs={"g": Graph("g", "Graph", "s", plot_config=config)},
            )
            restored = document_from_payload(json.loads(json.dumps(document_to_payload(document))))
            self.assertEqual(restored.graphs["g"].plot_config.y_tick_notation, mode)
            result = self.render(config, frame=document.sheets["s"].df)
            with TemporaryDirectory() as directory:
                for extension in ("svg", "pdf"):
                    path = Path(directory) / f"{mode}.{extension}"
                    export_figure(result.figure, path, config)
                    self.assertGreater(path.stat().st_size, 100)


if __name__ == "__main__":
    unittest.main()
