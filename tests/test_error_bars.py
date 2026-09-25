from __future__ import annotations

import unittest

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.container import ErrorbarContainer
from matplotlib.collections import PathCollection
import numpy as np
import pandas as pd

from pubfig.plot_config import PlotConfig, SeriesConfig
from pubfig.rendering import render_figure


class ErrorBarRenderingTests(unittest.TestCase):
    def render(self, frame, config, series):
        result = render_figure(frame, config, series)
        self.addCleanup(result.figure.clear)
        FigureCanvasAgg(result.figure).draw()
        return result

    @staticmethod
    def error_containers(axis) -> list[ErrorbarContainer]:
        return [
            container
            for container in axis.containers
            if isinstance(container, ErrorbarContainer)
        ]

    def test_scatter_uses_same_row_error_and_blank_cells_skip_only_that_bar(self) -> None:
        result = self.render(
            pd.DataFrame(
                {
                    "x": [0, 1, 2, 3],
                    "y": [10, 20, 30, 40],
                    "error": [1, "", -3, float("inf")],
                }
            ),
            PlotConfig(),
            [
                SeriesConfig(
                    x="x",
                    y="y",
                    label="Measurements",
                    plot_type="scatter",
                    error_column="error",
                    error_cap_size=3.0,
                )
            ],
        )
        axis = result.figure.axes[0]
        containers = self.error_containers(axis)

        self.assertEqual(len(containers), 1)
        segments = containers[0].lines[2][0].get_segments()
        np.testing.assert_allclose(
            segments,
            [
                [[0.0, 9.0], [0.0, 11.0]],
                [[2.0, 27.0], [2.0, 33.0]],
            ],
        )
        scatter = next(
            collection
            for collection in axis.collections
            if isinstance(collection, PathCollection)
        )
        self.assertEqual(len(scatter.get_offsets()), 4)
        self.assertGreater(scatter.get_zorder(), containers[0].lines[2][0].get_zorder())
        self.assertTrue(
            any("1 non-finite value(s) skipped" in warning for warning in result.warnings)
        )
        self.assertEqual(
            [text.get_text() for text in result.legend_artist.get_texts()],
            ["Measurements"],
        )

    def test_right_axis_divisor_does_not_transform_center_or_error(self) -> None:
        result = self.render(
            pd.DataFrame(
                {
                    "x": [0, 1],
                    "y": [100, 200],
                    "error": [10, 20],
                }
            ),
            PlotConfig(y2_scale_divisor=10),
            [
                SeriesConfig(
                    x="x",
                    y="y",
                    y_axis="right",
                    y_offset=2,
                    plot_type="scatter",
                    error_column="error",
                )
            ],
        )
        right_axis = result.figure.axes[1]
        segments = self.error_containers(right_axis)[0].lines[2][0].get_segments()

        np.testing.assert_allclose(
            segments,
            [
                [[0.0, 92.0], [0.0, 112.0]],
                [[1.0, 182.0], [1.0, 222.0]],
            ],
        )
        self.assertEqual(self.error_containers(result.figure.axes[0]), [])

    def test_all_invalid_errors_and_broken_axes_are_clear_nonfatal_states(self) -> None:
        frame = pd.DataFrame(
            {"x": [0, 1, 2], "y": [1, 2, 3], "error": ["", "bad", float("inf")]}
        )
        invalid = self.render(
            frame,
            PlotConfig(),
            [SeriesConfig(x="x", y="y", plot_type="scatter", error_column="error")],
        )
        self.assertEqual(self.error_containers(invalid.figure.axes[0]), [])
        self.assertTrue(
            any("no finite numeric values" in warning for warning in invalid.warnings)
        )

        broken = self.render(
            frame.assign(error=[0.1, 0.2, 0.3]),
            PlotConfig(
                x_break_enabled=True,
                x_break_left_min=0,
                x_break_left_max=1,
                x_break_right_min=2,
                x_break_right_max=3,
            ),
            [SeriesConfig(x="x", y="y", plot_type="scatter", error_column="error")],
        )
        self.assertTrue(
            any("Error bars are not drawn on broken axes" in warning for warning in broken.warnings)
        )
        self.assertTrue(
            all(not self.error_containers(axis) for axis in broken.figure.axes)
        )

    def test_log_axis_skips_nonpositive_lower_bound_and_invalid_divisor_falls_back(self) -> None:
        frame = pd.DataFrame(
            {"x": [0, 1], "y": [1, 10], "error": [2, 2]}
        )
        logarithmic = self.render(
            frame,
            PlotConfig(y_scale="log"),
            [SeriesConfig(x="x", y="y", plot_type="scatter", error_column="error")],
        )
        segments = self.error_containers(logarithmic.figure.axes[0])[0].lines[2][0].get_segments()
        np.testing.assert_allclose(segments, [[[1.0, 8.0], [1.0, 12.0]]])
        self.assertTrue(
            any("lower bound is not positive" in warning for warning in logarithmic.warnings)
        )

        invalid_divisor = self.render(
            frame,
            PlotConfig(y_scale_divisor=float("inf")),
            [SeriesConfig(x="x", y="y", plot_type="scatter", error_column="error")],
        )
        fallback_segments = self.error_containers(invalid_divisor.figure.axes[0])[0].lines[2][0].get_segments()
        np.testing.assert_allclose(
            fallback_segments,
            [
                [[0.0, -1.0], [0.0, 3.0]],
                [[1.0, 8.0], [1.0, 12.0]],
            ],
        )
        self.assertTrue(
            any("divisor must be finite and positive" in warning for warning in invalid_divisor.warnings)
        )


if __name__ == "__main__":
    unittest.main()
