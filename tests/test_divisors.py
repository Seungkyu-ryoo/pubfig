from __future__ import annotations

import unittest

import numpy as np
import pandas as pd
from matplotlib.backends.backend_agg import FigureCanvasAgg
from pandas.testing import assert_frame_equal

from pubfig.plot_config import PlotConfig, SeriesConfig
from pubfig.rendering.core import NumericColumnCache, RenderOptions, render_figure


class DivisorTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame({
            "x": [.001, .002, .003], "y": [1e6, 2e6, 3e6],
            "right": [10, 20, 30],
        })
        self.series = [
            SeriesConfig(x="x", y="y", plot_type="line"),
            SeriesConfig(x="x", y="right", y_axis="right", plot_type="line"),
        ]

    def render(self, config, options=None):
        result = render_figure(self.frame, config, self.series, options=options)
        self.addCleanup(result.figure.clear)
        FigureCanvasAgg(result.figure).draw()
        return result.figure.axes

    def test_divisors_apply_once_and_cached_preview_matches_full_render(self):
        original = self.frame.copy(deep=True)
        baseline, baseline_right = self.render(PlotConfig())
        cache = NumericColumnCache()
        preview = RenderOptions.for_preview(numeric_cache=cache, cache_source=self.frame)
        for xd, yd, y2d in ((.001, 1e6, 10), (.01, 1e3, 100), (1, 1, 1), (.001, 1e6, 10)):
            for options in (None, preview):
                with self.subTest(divisors=(xd, yd, y2d), preview=options is not None):
                    left, right = self.render(PlotConfig(
                        x_scale_divisor=xd, y_scale_divisor=yd, y2_scale_divisor=y2d,
                        x_tick_decimals=3, y_tick_decimals=3, y2_tick_decimals=3,
                    ), options)
                    np.testing.assert_allclose(left.lines[0].get_xdata(), self.frame.x)
                    np.testing.assert_allclose(right.lines[0].get_xdata(), self.frame.x)
                    np.testing.assert_allclose(left.lines[0].get_ydata(), self.frame.y)
                    np.testing.assert_allclose(right.lines[0].get_ydata(), self.frame.right)
                    np.testing.assert_allclose(left.get_xlim(), baseline.get_xlim())
                    np.testing.assert_allclose(left.get_ylim(), baseline.get_ylim())
                    np.testing.assert_allclose(right.get_ylim(), baseline_right.get_ylim())
                    np.testing.assert_allclose(
                        left.transData.transform(np.column_stack((self.frame.x, self.frame.y))),
                        baseline.transData.transform(np.column_stack((self.frame.x, self.frame.y))),
                    )
                    self.assertEqual(left.xaxis.get_major_formatter()(.002), f"{.002 / xd:.3f}")
                    self.assertEqual(left.yaxis.get_major_formatter()(2e6), f"{2e6 / yd:.3f}")
                    self.assertEqual(right.yaxis.get_major_formatter()(20), f"{20 / y2d:.3f}")
        assert_frame_equal(self.frame, original)

    def test_explicit_limits_and_tick_spacing_stay_in_original_units(self):
        for options in (None, RenderOptions.for_preview()):
            left, right = self.render(PlotConfig(
                x_scale_divisor=.001, y_scale_divisor=1e6, y2_scale_divisor=10,
                x_min=.001, x_max=.003, y_min=1e6, y_max=3e6, y2_min=10, y2_max=30,
                x_tick_interval=.001, y_tick_interval=1e6,
                x_tick_decimals=1, y_tick_decimals=2,
                x_scientific_notation=False, y_scientific_notation=False,
            ), options)
            np.testing.assert_allclose(left.get_xlim(), [.001, .003])
            np.testing.assert_allclose(left.get_ylim(), [1e6, 3e6])
            np.testing.assert_allclose(right.get_ylim(), [10, 30])
            np.testing.assert_allclose(left.lines[0].get_xdata(), self.frame.x)
            np.testing.assert_allclose(left.lines[0].get_ydata(), self.frame.y)
            self.assertIn("2.0", [label.get_text() for label in left.get_xticklabels()])
            self.assertIn("2.00", [label.get_text() for label in left.get_yticklabels()])
            self.assertEqual(left.yaxis.get_offset_text().get_text(), "")

    def test_log_axes_divide_labels_without_moving_points(self):
        left, right = self.render(PlotConfig(
            x_scale="log", y_scale="log", y2_scale="log",
            x_scale_divisor=.001, y_scale_divisor=1e6, y2_scale_divisor=10,
        ))
        np.testing.assert_allclose(left.lines[0].get_xdata(), self.frame.x)
        np.testing.assert_allclose(left.lines[0].get_ydata(), self.frame.y)
        np.testing.assert_allclose(right.lines[0].get_ydata(), self.frame.right)
        self.assertIn("10^{0}", left.xaxis.get_major_formatter()(.001))
        self.assertIn("10^{0}", left.yaxis.get_major_formatter()(1e6))

    def test_broken_axis_clipping_does_not_change_with_divisor(self):
        from dataclasses import replace
        self.series = self.series[:1]
        for axis in ("x", "y"):
            with self.subTest(axis=axis):
                config = PlotConfig(
                    x_break_enabled=axis == "x", y_break_enabled=axis == "y",
                    x_break_left_min=.0005, x_break_left_max=.0015,
                    x_break_right_min=.0025, x_break_right_max=.0035,
                    y_break_lower_min=.5e6, y_break_lower_max=1.5e6,
                    y_break_upper_min=2.5e6, y_break_upper_max=3.5e6,
                )
                original = self.render(config)
                divided = self.render(replace(config, x_scale_divisor=.001, y_scale_divisor=1e6))
                self.assertEqual(len(divided), 2)
                for before, after in zip(original, divided):
                    np.testing.assert_allclose(before.get_xlim(), after.get_xlim())
                    np.testing.assert_allclose(before.get_ylim(), after.get_ylim())
                    self.assertEqual(len(before.lines), len(after.lines))
                    for original_line, divided_line in zip(before.lines, after.lines):
                        np.testing.assert_allclose(original_line.get_xdata(), divided_line.get_xdata())
                        np.testing.assert_allclose(original_line.get_ydata(), divided_line.get_ydata())


if __name__ == "__main__":
    unittest.main()
