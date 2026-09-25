from __future__ import annotations

import unittest
from unittest.mock import patch

from matplotlib.collections import PathCollection
import numpy as np
import pandas as pd

from pubfig.plot_config import PlotConfig, SeriesConfig
from pubfig.rendering import NumericColumnCache, RenderOptions, render_figure
import pubfig.rendering.core as rendering_core


class PreviewRenderingTests(unittest.TestCase):
    def render(self, frame, config, series, *, point_limit=128):
        result = render_figure(
            frame,
            config,
            series,
            options=RenderOptions.for_preview(point_limit),
        )
        self.addCleanup(result.figure.clear)
        return result

    @staticmethod
    def data_line(result):
        return next(
            line
            for line in result.figure.axes[0].lines
            if not (line.get_gid() or "").startswith("pubfig_linear_fit_")
        )

    def test_full_render_is_default_and_preview_preserves_extrema(self) -> None:
        count = 20_001
        x = np.arange(count, dtype=float)
        y = np.zeros(count, dtype=float)
        y[3_217] = 1234.0
        y[14_333] = -987.0
        frame = pd.DataFrame({"x": x, "y": y})
        series = [SeriesConfig(x="x", y="y")]

        full = render_figure(frame, PlotConfig(), series)
        self.addCleanup(full.figure.clear)
        preview = self.render(frame, PlotConfig(), series, point_limit=128)

        full_y = np.asarray(self.data_line(full).get_ydata(), dtype=float)
        preview_y = np.asarray(self.data_line(preview).get_ydata(), dtype=float)
        self.assertEqual(len(full_y), count)
        self.assertLessEqual(len(preview_y), 128)
        self.assertEqual(float(preview_y.max()), 1234.0)
        self.assertEqual(float(preview_y.min()), -987.0)

    def test_explicit_x_range_keeps_crossing_line_vertices(self) -> None:
        frame = pd.DataFrame(
            {
                "x": np.arange(10_000, dtype=float),
                "y": np.arange(10_000, dtype=float),
            }
        )
        config = PlotConfig(x_min=4_000.5, x_max=4_010.5)

        line_result = self.render(
            frame,
            config,
            [SeriesConfig(x="x", y="y", plot_type="line")],
            point_limit=32,
        )
        line_x = np.asarray(self.data_line(line_result).get_xdata(), dtype=float)
        np.testing.assert_array_equal(line_x, np.arange(4_000, 4_012, dtype=float))

        scatter_result = self.render(
            frame,
            config,
            [SeriesConfig(x="x", y="y", plot_type="scatter")],
            point_limit=32,
        )
        scatter = next(
            collection
            for collection in scatter_result.figure.axes[0].collections
            if isinstance(collection, PathCollection)
        )
        scatter_x = np.asarray(scatter.get_offsets())[:, 0]
        np.testing.assert_array_equal(
            scatter_x,
            np.arange(4_001, 4_011, dtype=float),
        )

        crossing = self.render(
            pd.DataFrame({"x": [0.0, 10.0], "y": [0.0, 10.0]}),
            PlotConfig(x_min=4.0, x_max=6.0),
            [SeriesConfig(x="x", y="y")],
            point_limit=16,
        )
        np.testing.assert_array_equal(
            np.asarray(self.data_line(crossing).get_xdata(), dtype=float),
            [0.0, 10.0],
        )

    def test_x_filtering_preserves_full_resolution_autoscale_bounds(self) -> None:
        x = np.arange(1_001, dtype=float)
        y = np.sin(x / 20.0)
        y[900] = 10_000.0
        frame = pd.DataFrame({"x": x, "y": y})
        config = PlotConfig(x_min=100.0, x_max=200.0)
        series = [SeriesConfig(x="x", y="y")]

        full = render_figure(frame, config, series)
        self.addCleanup(full.figure.clear)
        preview = self.render(frame, config, series, point_limit=32)

        np.testing.assert_allclose(
            preview.figure.axes[0].get_ylim(),
            full.figure.axes[0].get_ylim(),
        )
        self.assertNotIn(900.0, self.data_line(preview).get_xdata())

    def test_preview_reduction_does_not_reduce_linear_fit_input(self) -> None:
        count = 2_001
        x = np.linspace(-5.0, 5.0, count)
        frame = pd.DataFrame({"x": x, "y": 3.0 * x + 7.0})
        result = self.render(
            frame,
            PlotConfig(),
            [SeriesConfig(x="x", y="y", linear_fit_enabled=True)],
            point_limit=32,
        )

        self.assertLessEqual(len(self.data_line(result).get_xdata()), 32)
        fit = result.linear_fit_results["y"]
        self.assertEqual(fit.point_count, count)
        self.assertAlmostEqual(fit.slope, 3.0)
        self.assertAlmostEqual(fit.intercept, 7.0)

    def test_shared_numeric_columns_are_coerced_once_per_render(self) -> None:
        frame = pd.DataFrame(
            {
                "x": ["0", "1", "2"],
                "a": ["1", "2", "3"],
                "b": ["2", "3", "4"],
                "error": ["0.1", "0.2", "0.3"],
            }
        )
        real_coerce = rendering_core.coerce_numeric
        converted_names: list[str] = []

        def tracked_coerce(values):
            converted_names.append(str(values.name))
            return real_coerce(values)

        with patch.object(rendering_core, "coerce_numeric", side_effect=tracked_coerce):
            result = self.render(
                frame,
                PlotConfig(),
                [
                    SeriesConfig(x="x", y="a", error_column="error"),
                    SeriesConfig(x="x", y="b", error_column="error"),
                ],
                point_limit=32,
            )

        self.assertEqual(converted_names.count("x"), 1)
        self.assertEqual(converted_names.count("a"), 1)
        self.assertEqual(converted_names.count("b"), 1)
        self.assertEqual(converted_names.count("error"), 1)
        self.assertTrue(result.figure.axes[0].lines)

    def test_explicit_cache_reuses_conversions_and_detects_source_change(self) -> None:
        first_source = pd.DataFrame({"x": ["0", "1"], "y": ["1", "2"]})
        second_source = pd.DataFrame({"x": ["0", "1"], "y": ["8", "9"]})
        cache = NumericColumnCache()
        real_coerce = rendering_core.coerce_numeric
        converted_names: list[str] = []

        def tracked_coerce(values):
            converted_names.append(str(values.name))
            return real_coerce(values)

        def cached_render(source):
            result = render_figure(
                source.reset_index(drop=True),
                PlotConfig(),
                [SeriesConfig(x="x", y="y")],
                options=RenderOptions.for_preview(
                    32,
                    numeric_cache=cache,
                    cache_source=source,
                ),
            )
            self.addCleanup(result.figure.clear)
            return result

        with patch.object(rendering_core, "coerce_numeric", side_effect=tracked_coerce):
            cached_render(first_source)
            cached_render(first_source)
            switched = cached_render(second_source)

            second_source.iat[0, 1] = "80"
            cache.clear()
            edited = cached_render(second_source)

        self.assertEqual(converted_names, ["x", "y", "x", "y", "x", "y"])
        np.testing.assert_array_equal(
            np.asarray(self.data_line(switched).get_ydata(), dtype=float),
            [8.0, 9.0],
        )
        np.testing.assert_array_equal(
            np.asarray(self.data_line(edited).get_ydata(), dtype=float),
            [80.0, 9.0],
        )

    def test_cache_auto_invalidates_on_row_or_column_schema_change(self) -> None:
        source = object()
        cache = NumericColumnCache()
        frame = pd.DataFrame({"x": ["1", "2"], "y": ["3", "4"]})
        real_coerce = rendering_core.coerce_numeric

        with patch.object(
            rendering_core,
            "coerce_numeric",
            wraps=real_coerce,
        ) as converted:
            cache.bind(frame, source=source).numeric("x")
            cache.bind(frame, source=source).numeric("x")
            cache.bind(frame.iloc[:1], source=source).numeric("x")
            cache.bind(frame[["y", "x"]], source=source).numeric("x")

        self.assertEqual(converted.call_count, 3)

    def test_render_options_reject_an_unsafe_tiny_budget(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 16"):
            RenderOptions.for_preview(8)


if __name__ == "__main__":
    unittest.main()
