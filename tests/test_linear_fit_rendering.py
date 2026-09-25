from __future__ import annotations

import unittest

from matplotlib.backends.backend_agg import FigureCanvasAgg
import pandas as pd

from pubfig.plot_config import PlotConfig, SeriesConfig
from pubfig.rendering import render_figure


class LinearFitRenderingTests(unittest.TestCase):
    @staticmethod
    def fit_lines(result):
        return [
            line
            for axis in result.figure.axes
            for line in axis.lines
            if (line.get_gid() or "").startswith("pubfig_linear_fit_")
        ]

    def render(self, frame, config, series):
        result = render_figure(frame, config, series)
        self.addCleanup(result.figure.clear)
        return result

    def test_fit_is_opt_in_and_enabled_fit_is_not_added_to_legend(self) -> None:
        frame = pd.DataFrame({"x": [0, 1, 2, 3], "y": [1, 3, 5, 7]})
        disabled = self.render(
            frame,
            PlotConfig(),
            [SeriesConfig(x="x", y="y", plot_type="scatter")],
        )
        self.assertEqual(self.fit_lines(disabled), [])
        self.assertEqual(disabled.linear_fit_results, {})

        enabled = self.render(
            frame,
            PlotConfig(),
            [
                SeriesConfig(
                    x="x",
                    y="y",
                    label="Signal",
                    plot_type="scatter",
                    linear_fit_enabled=True,
                    linear_fit_line_style="dotted",
                    linear_fit_line_width=2.5,
                )
            ],
        )
        FigureCanvasAgg(enabled.figure).draw()

        fit = enabled.linear_fit_results["y"]
        line = self.fit_lines(enabled)[0]
        self.assertAlmostEqual(fit.slope, 2.0)
        self.assertAlmostEqual(fit.intercept, 1.0)
        self.assertEqual(list(line.get_xdata()), [0.0, 3.0])
        self.assertEqual(list(line.get_ydata()), [1.0, 7.0])
        self.assertEqual(line.get_linestyle(), ":")
        self.assertEqual(line.get_linewidth(), 2.5)
        self.assertEqual(
            [text.get_text() for text in enabled.legend_artist.get_texts()],
            ["Signal"],
        )

    def test_divisor_does_not_change_fit_or_visual_y_offset(self) -> None:
        frame = pd.DataFrame({"x": [0, 2, 4], "y": [2, 6, 10]})
        config = PlotConfig(x_scale_divisor=2, y_scale_divisor=2)
        series = SeriesConfig(
            x="x",
            y="y",
            plot_type="scatter",
            y_offset=3,
            linear_fit_enabled=True,
        )

        result = self.render(frame, config, [series])
        fit = result.linear_fit_results["y"]
        line = self.fit_lines(result)[0]

        self.assertAlmostEqual(fit.slope, 2.0)
        self.assertAlmostEqual(fit.intercept, 2.0)
        self.assertEqual(list(line.get_xdata()), [0.0, 4.0])
        self.assertEqual(list(line.get_ydata()), [5.0, 13.0])

    def test_range_right_axis_and_invalid_data_are_nonfatal(self) -> None:
        frame = pd.DataFrame(
            {"x": [0, 1, 2, 3], "left": [1, 2, 3, 4], "right": [9, 3, 5, 7]}
        )
        series = [
            SeriesConfig(x="x", y="left"),
            SeriesConfig(
                x="x",
                y="right",
                y_axis="right",
                plot_type="scatter",
                linear_fit_enabled=True,
                linear_fit_x_min=1,
                linear_fit_x_max=3,
            ),
        ]

        result = self.render(frame, PlotConfig(), series)
        fit = result.linear_fit_results["right"]
        right_axis = result.figure.axes[1]

        self.assertAlmostEqual(fit.slope, 2.0)
        self.assertAlmostEqual(fit.intercept, 1.0)
        self.assertEqual(fit.point_count, 3)
        self.assertTrue(
            any((line.get_gid() or "").startswith("pubfig_linear_fit_") for line in right_axis.lines)
        )

        invalid = self.render(
            pd.DataFrame({"x": [1, 1], "y": [2, 3]}),
            PlotConfig(),
            [SeriesConfig(x="x", y="y", linear_fit_enabled=True)],
        )
        self.assertEqual(invalid.linear_fit_results, {})
        self.assertEqual(self.fit_lines(invalid), [])
        self.assertTrue(any("distinct X" in warning for warning in invalid.warnings))

    def test_categorical_and_log_axes_skip_fit_with_warning(self) -> None:
        categorical = self.render(
            pd.DataFrame({"x": ["A", "B"], "y": [1, 2]}),
            PlotConfig(),
            [
                SeriesConfig(
                    x="x",
                    y="y",
                    plot_type="bar",
                    linear_fit_enabled=True,
                )
            ],
        )
        self.assertEqual(categorical.linear_fit_results, {})
        self.assertTrue(any("numeric X data" in warning for warning in categorical.warnings))

        logarithmic = self.render(
            pd.DataFrame({"x": [1, 2, 3], "y": [2, 4, 6]}),
            PlotConfig(x_scale="log"),
            [SeriesConfig(x="x", y="y", linear_fit_enabled=True)],
        )
        self.assertEqual(logarithmic.linear_fit_results, {})
        self.assertTrue(any("linear X and Y axes" in warning for warning in logarithmic.warnings))

    def test_broken_x_draws_one_clipped_segment_on_each_axis(self) -> None:
        frame = pd.DataFrame({"x": [0, 1, 2, 8, 9, 10], "y": [1, 3, 5, 17, 19, 21]})
        config = PlotConfig(
            x_break_enabled=True,
            x_break_left_min=0,
            x_break_left_max=2,
            x_break_right_min=8,
            x_break_right_max=10,
        )
        result = self.render(
            frame,
            config,
            [SeriesConfig(x="x", y="y", linear_fit_enabled=True)],
        )

        lines = self.fit_lines(result)
        self.assertEqual(len(lines), 2)
        self.assertEqual(
            [list(line.get_xdata()) for line in lines],
            [[0.0, 2], [8, 10.0]],
        )

    def test_broken_axis_warns_when_fit_is_outside_visible_ranges(self) -> None:
        result = self.render(
            pd.DataFrame({"x": [3, 4, 5], "y": [7, 9, 11]}),
            PlotConfig(
                x_break_enabled=True,
                x_break_left_min=0,
                x_break_left_max=2,
                x_break_right_min=8,
                x_break_right_max=10,
            ),
            [SeriesConfig(x="x", y="y", linear_fit_enabled=True)],
        )

        self.assertIn("y", result.linear_fit_results)
        self.assertEqual(self.fit_lines(result), [])
        self.assertTrue(
            any("outside the visible broken-axis ranges" in warning for warning in result.warnings)
        )

    def test_duplicate_y_fit_results_and_invalid_style_are_reported(self) -> None:
        result = self.render(
            pd.DataFrame({"x": [0, 1, 2], "y": [1, 3, 5]}),
            PlotConfig(),
            [
                SeriesConfig(x="x", y="y", linear_fit_enabled=True),
                SeriesConfig(
                    x="x",
                    y="y",
                    label="Duplicate",
                    linear_fit_enabled=True,
                    linear_fit_line_style="not-a-style",
                    linear_fit_line_width=float("nan"),
                ),
            ],
        )

        self.assertEqual(list(result.linear_fit_results), ["y"])
        self.assertEqual(len(self.fit_lines(result)), 2)
        self.assertTrue(any("duplicate Y column" in warning for warning in result.warnings))
        self.assertTrue(any("unknown line style" in warning for warning in result.warnings))
        self.assertTrue(any("line width must be positive" in warning for warning in result.warnings))


if __name__ == "__main__":
    unittest.main()
