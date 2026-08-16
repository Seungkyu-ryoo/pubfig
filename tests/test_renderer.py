from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from matplotlib.backends.backend_agg import FigureCanvasAgg
import numpy as np
import pandas as pd
from PIL import Image

from plot_config import LegendEntryConfig, PlotConfig, SeriesConfig
from renderer import export_figure, render_figure
import pubfig.rendering.axes as rendering_axes
import pubfig.rendering.core as rendering_core


class RendererTests(unittest.TestCase):
    @staticmethod
    def _axis_arrows(figure):
        return [
            patch
            for axis in figure.axes
            for patch in axis.patches
            if (patch.get_gid() or "").startswith("pubfig_axis_arrow_")
        ]

    def test_duplicate_column_identifiers_are_rejected_at_render_boundary(self) -> None:
        frame = pd.DataFrame([[0, 1], [1, 2]], columns=["value", "value"])

        with self.assertRaisesRegex(ValueError, "must be unique"):
            render_figure(
                frame,
                PlotConfig(),
                [SeriesConfig(x="value", y="value")],
            )

    def test_render_failure_clears_its_partially_built_figure(self) -> None:
        frame = pd.DataFrame({"x": [0, 1], "y": [1, 2]})
        created = []
        original_create_axes = rendering_core.create_axes

        def tracked_create_axes(*args, **kwargs):
            axes = original_create_axes(*args, **kwargs)
            created.append(axes)
            return axes

        with patch.object(
            rendering_core,
            "create_axes",
            side_effect=tracked_create_axes,
        ):
            with self.assertRaises(ValueError):
                render_figure(
                    frame,
                    PlotConfig(),
                    [SeriesConfig(x="x", y="y", color="not-a-color")],
                )

        self.assertEqual(created[0].figure.axes, [])

    def test_axes_creation_failure_clears_the_new_figure(self) -> None:
        figure = MagicMock()
        figure.add_subplot.side_effect = RuntimeError("axes failed")

        with patch.object(rendering_axes, "Figure", return_value=figure):
            with self.assertRaisesRegex(RuntimeError, "axes failed"):
                rendering_axes.create_axes(PlotConfig(), [], [])

        figure.clear.assert_called_once_with()

    def test_axis_arrows_follow_visible_sides_and_axis_width(self) -> None:
        frame = pd.DataFrame({"x": [0, 1, 2], "y": [1, 2, 3]})
        default_result = render_figure(
            frame,
            PlotConfig(),
            [SeriesConfig(x="x", y="y")],
        )
        self.assertEqual(self._axis_arrows(default_result.figure), [])

        config = PlotConfig(
            show_axis_arrows=True,
            axis_line_width=1.75,
            show_top_axis=False,
            show_right_axis=False,
        )

        result = render_figure(frame, config, [SeriesConfig(x="x", y="y")])
        FigureCanvasAgg(result.figure).draw()
        arrows = self._axis_arrows(result.figure)
        axis = result.figure.axes[0]

        self.assertEqual(
            {arrow.get_gid() for arrow in arrows},
            {"pubfig_axis_arrow_bottom", "pubfig_axis_arrow_left"},
        )
        self.assertTrue(all(spine.get_linewidth() == 1.75 for spine in axis.spines.values()))
        self.assertEqual(axis.spines["bottom"].get_capstyle(), "butt")
        self.assertEqual(axis.spines["left"].get_capstyle(), "butt")
        self.assertFalse(axis.spines["bottom"].get_visible())
        self.assertFalse(axis.spines["left"].get_visible())
        self.assertTrue(all(arrow.get_linewidth() == 0 for arrow in arrows))

        arrow_by_side = {arrow.get_gid().rsplit("_", 1)[-1]: arrow for arrow in arrows}
        bottom_vertices = arrow_by_side["bottom"].get_transform().transform(
            arrow_by_side["bottom"].get_path().vertices
        )
        left_vertices = arrow_by_side["left"].get_transform().transform(
            arrow_by_side["left"].get_path().vertices
        )
        self.assertAlmostEqual(float(bottom_vertices[:, 0].max()), axis.bbox.x1, places=6)
        self.assertAlmostEqual(float(left_vertices[:, 1].max()), axis.bbox.y1, places=6)

    def test_axis_arrows_appear_once_per_outer_side_on_twin_and_broken_axes(self) -> None:
        frame = pd.DataFrame(
            {
                "x": [0, 1, 2, 8, 9, 10],
                "y": [0, 1, 2, 8, 9, 10],
                "y2": [10, 9, 8, 2, 1, 0],
            }
        )
        cases = {
            "twin y": (
                PlotConfig(show_axis_arrows=True),
                [
                    SeriesConfig(x="x", y="y"),
                    SeriesConfig(x="x", y="y2", y_axis="right"),
                ],
            ),
            "broken x": (
                PlotConfig(
                    show_axis_arrows=True,
                    x_break_enabled=True,
                    x_break_left_min=0,
                    x_break_left_max=2,
                    x_break_right_min=8,
                    x_break_right_max=10,
                ),
                [SeriesConfig(x="x", y="y")],
            ),
            "broken y": (
                PlotConfig(
                    show_axis_arrows=True,
                    y_break_enabled=True,
                    y_break_lower_min=0,
                    y_break_lower_max=2,
                    y_break_upper_min=8,
                    y_break_upper_max=10,
                ),
                [SeriesConfig(x="x", y="y")],
            ),
        }

        expected_by_case = {
            "twin y": [
                {
                    "pubfig_axis_arrow_top",
                    "pubfig_axis_arrow_bottom",
                    "pubfig_axis_arrow_left",
                },
                {"pubfig_axis_arrow_right"},
            ],
            "broken x": [
                {"pubfig_axis_arrow_left"},
                {
                    "pubfig_axis_arrow_top",
                    "pubfig_axis_arrow_bottom",
                    "pubfig_axis_arrow_right",
                },
            ],
            "broken y": [
                {
                    "pubfig_axis_arrow_top",
                    "pubfig_axis_arrow_left",
                    "pubfig_axis_arrow_right",
                },
                {"pubfig_axis_arrow_bottom"},
            ],
        }
        for name, (config, series) in cases.items():
            with self.subTest(name=name):
                result = render_figure(frame, config, series)
                FigureCanvasAgg(result.figure).draw()
                arrows = self._axis_arrows(result.figure)

                self.assertEqual(len(arrows), 4)
                self.assertEqual(
                    [
                        {
                            patch.get_gid()
                            for patch in axis.patches
                            if (patch.get_gid() or "").startswith("pubfig_axis_arrow_")
                        }
                        for axis in result.figure.axes
                    ],
                    expected_by_case[name],
                )
                for axis in result.figure.axes:
                    for arrow in (
                        patch
                        for patch in axis.patches
                        if (patch.get_gid() or "").startswith("pubfig_axis_arrow_")
                    ):
                        side = arrow.get_gid().rsplit("_", 1)[-1]
                        self.assertFalse(axis.spines[side].get_visible())

    def test_tick_marks_can_be_hidden_without_hiding_spines_or_labels(self) -> None:
        frame = pd.DataFrame({"x": [0, 1, 2], "y": [1, 2, 3]})
        config = PlotConfig(show_tick_marks=False)

        result = render_figure(frame, config, [SeriesConfig(x="x", y="y")])
        FigureCanvasAgg(result.figure).draw()
        axis = result.figure.axes[0]

        self.assertTrue(all(spine.get_visible() for spine in axis.spines.values()))
        for tick in axis.xaxis.get_major_ticks() + axis.xaxis.get_minor_ticks():
            self.assertFalse(tick.tick1line.get_visible())
            self.assertFalse(tick.tick2line.get_visible())
        for tick in axis.yaxis.get_major_ticks() + axis.yaxis.get_minor_ticks():
            self.assertFalse(tick.tick1line.get_visible())
            self.assertFalse(tick.tick2line.get_visible())
        self.assertTrue(any(label.get_visible() for label in axis.get_xticklabels()))
        self.assertTrue(any(label.get_visible() for label in axis.get_yticklabels()))

    def test_hidden_tick_marks_stay_hidden_on_twin_and_broken_axes(self) -> None:
        frame = pd.DataFrame(
            {
                "x": [0, 1, 2, 8, 9, 10],
                "y": [0, 1, 2, 8, 9, 10],
                "y2": [10, 9, 8, 2, 1, 0],
            }
        )
        cases = {
            "twin y": (
                PlotConfig(show_tick_marks=False),
                [
                    SeriesConfig(x="x", y="y"),
                    SeriesConfig(x="x", y="y2", y_axis="right"),
                ],
            ),
            "broken x": (
                PlotConfig(
                    show_tick_marks=False,
                    x_break_enabled=True,
                    x_break_left_min=0,
                    x_break_left_max=2,
                    x_break_right_min=8,
                    x_break_right_max=10,
                ),
                [SeriesConfig(x="x", y="y")],
            ),
            "broken y": (
                PlotConfig(
                    show_tick_marks=False,
                    y_break_enabled=True,
                    y_break_lower_min=0,
                    y_break_lower_max=2,
                    y_break_upper_min=8,
                    y_break_upper_max=10,
                ),
                [SeriesConfig(x="x", y="y")],
            ),
        }

        for name, (config, series) in cases.items():
            with self.subTest(name=name):
                result = render_figure(frame, config, series)
                FigureCanvasAgg(result.figure).draw()

                for axis in result.figure.axes:
                    for axis_object in (axis.xaxis, axis.yaxis):
                        for tick in axis_object.get_major_ticks() + axis_object.get_minor_ticks():
                            self.assertFalse(tick.tick1line.get_visible())
                            self.assertFalse(tick.tick2line.get_visible())
                self.assertTrue(
                    any(
                        label.get_visible()
                        for axis in result.figure.axes
                        for label in axis.get_xticklabels() + axis.get_yticklabels()
                    )
                )
                self.assertTrue(
                    any(
                        spine.get_visible()
                        for axis in result.figure.axes
                        for spine in axis.spines.values()
                    )
                )

    def test_each_axis_side_can_be_hidden_with_its_ticks(self) -> None:
        frame = pd.DataFrame({"x": [0, 1, 2], "y": [1, 2, 3]})
        config = PlotConfig(
            axis_line_width=2.25,
            show_top_axis=False,
            show_bottom_axis=True,
            show_left_axis=False,
            show_right_axis=True,
        )

        result = render_figure(frame, config, [SeriesConfig(x="x", y="y")])
        FigureCanvasAgg(result.figure).draw()
        axis = result.figure.axes[0]

        self.assertFalse(axis.spines["top"].get_visible())
        self.assertTrue(axis.spines["bottom"].get_visible())
        self.assertFalse(axis.spines["left"].get_visible())
        self.assertTrue(axis.spines["right"].get_visible())
        self.assertTrue(
            all(spine.get_linewidth() == 2.25 for spine in axis.spines.values())
        )
        self.assertTrue(any(tick.tick1line.get_visible() for tick in axis.xaxis.get_major_ticks()))
        self.assertFalse(any(tick.tick2line.get_visible() for tick in axis.xaxis.get_major_ticks()))
        self.assertFalse(any(tick.tick1line.get_visible() for tick in axis.yaxis.get_major_ticks()))
        self.assertTrue(any(tick.tick2line.get_visible() for tick in axis.yaxis.get_major_ticks()))
        self.assertFalse(any(label.get_visible() for label in axis.get_yticklabels()))

    def test_categorical_bar_uses_labels_and_draws_every_value(self) -> None:
        frame = pd.DataFrame({"category": ["A", "B", "C"], "value": [1, 2, 3]})
        result = render_figure(
            frame,
            PlotConfig(dpi=100),
            [SeriesConfig(x="category", y="value", plot_type="bar")],
        )
        FigureCanvasAgg(result.figure).draw()

        axis = result.figure.axes[0]
        self.assertEqual(len(axis.patches), 3)
        self.assertEqual([tick.get_text() for tick in axis.get_xticklabels()], ["A", "B", "C"])
        self.assertFalse(any("No plottable numeric data" in item for item in result.warnings))

    def test_categorical_bar_rejects_log_x_with_clear_warning(self) -> None:
        frame = pd.DataFrame({"category": ["A", "B"], "value": [1, 2]})
        result = render_figure(
            frame,
            PlotConfig(x_scale="log"),
            [SeriesConfig(x="category", y="value", plot_type="bar")],
        )

        self.assertTrue(any("requires a linear X scale" in item for item in result.warnings))

    def test_export_replaces_destination_without_leaving_temp_file(self) -> None:
        frame = pd.DataFrame({"x": [1, 2], "y": [3, 4]})
        config = PlotConfig(dpi=100)
        result = render_figure(frame, config, [SeriesConfig(x="x", y="y")])

        with TemporaryDirectory() as directory:
            destination = Path(directory) / "figure.png"
            destination.write_bytes(b"old")
            export_figure(result.figure, destination, config)

            self.assertGreater(destination.stat().st_size, 3)
            self.assertEqual(list(Path(directory).glob(".figure.*")), [])

    def test_export_preserves_configured_canvas_when_trim_is_disabled(self) -> None:
        frame = pd.DataFrame({"x": [2, 5, 10, 15], "y": [380, 375, 375, 365]})
        config = PlotConfig(
            width_mm=84.8,
            height_mm=36.7,
            dpi=100,
            x_label="Working pressure (mTorr)",
            y_label="Resistivity (μΩcm)",
            fixed_plot_area=True,
            plot_width_mm=60.0,
            plot_height_mm=20.0,
            plot_margin_left_mm=13.7,
            plot_margin_right_mm=11.1,
            plot_margin_top_mm=7.7,
            plot_margin_bottom_mm=9.0,
            trim_whitespace=False,
            transparent=True,
        )
        result = render_figure(frame, config, [SeriesConfig(x="x", y="y", marker="o")])

        with TemporaryDirectory() as directory:
            destination = Path(directory) / "fixed-canvas.png"
            export_figure(result.figure, destination, config)
            with Image.open(destination) as image:
                expected = (
                    int(config.width_mm / 25.4 * config.dpi),
                    int(config.height_mm / 25.4 * config.dpi),
                )
                self.assertEqual(image.size, expected)

    def test_tight_export_keeps_fixed_labels_in_bounds(self) -> None:
        frame = pd.DataFrame({"x": [1, 2], "y": [3, 4]})
        config = PlotConfig(
            dpi=100,
            x_label="Horizontal label",
            y_label="Vertical label",
            trim_whitespace=True,
            transparent=True,
        )
        result = render_figure(frame, config, [SeriesConfig(x="x", y="y")])

        with TemporaryDirectory() as directory:
            destination = Path(directory) / "tight.png"
            export_figure(result.figure, destination, config)
            with Image.open(destination) as image:
                alpha_bbox = image.getchannel("A").getbbox()
                self.assertIsNotNone(alpha_bbox)
                self.assertGreater(alpha_bbox[0], 0)
                self.assertGreater(alpha_bbox[1], 0)
                self.assertLess(alpha_bbox[2], image.width)
                self.assertLess(alpha_bbox[3], image.height)

    def test_y_label_gap_is_independent_of_plot_left_margin(self) -> None:
        frame = pd.DataFrame({"x": [0, 1, 2], "y": [0, 1, 4]})
        requested_gap_mm = 10.0
        measured_gaps: list[float] = []

        for left_margin_mm in (4.0, 20.0):
            config = PlotConfig(
                width_mm=89.0,
                height_mm=67.0,
                dpi=100,
                fixed_plot_area=True,
                plot_width_mm=60.0,
                plot_height_mm=45.0,
                plot_margin_left_mm=left_margin_mm,
                plot_margin_right_mm=29.0 - left_margin_mm,
                plot_margin_top_mm=11.0,
                plot_margin_bottom_mm=11.0,
                y_label="Y axis title",
                y_label_offset_mm=requested_gap_mm,
            )
            result = render_figure(frame, config, [SeriesConfig(x="x", y="y")])
            canvas = FigureCanvasAgg(result.figure)
            canvas.draw()
            renderer = canvas.get_renderer()
            axis_left_px = result.figure.axes[0].bbox.x0
            label = next(
                text
                for text in result.figure.texts
                if text.get_text() == "Y axis title"
            )
            label_right_px = label.get_window_extent(renderer).x1
            measured_gaps.append(
                (axis_left_px - label_right_px) / result.figure.dpi * 25.4
            )

        for measured_gap_mm in measured_gaps:
            self.assertAlmostEqual(measured_gap_mm, requested_gap_mm, delta=0.05)
        self.assertAlmostEqual(measured_gaps[0], measured_gaps[1], delta=0.01)

    def test_legend_entries_pair_independent_handles_and_names(self) -> None:
        frame = pd.DataFrame(
            {
                "x": [0, 1, 2],
                "a": [1, 2, 3],
                "b": [3, 2, 1],
            }
        )
        config = PlotConfig(
            legend_entries=[
                LegendEntryConfig(source_y="b", label="Control"),
                LegendEntryConfig(source_y="a", label="Sample A"),
            ]
        )
        series = [
            SeriesConfig(
                x="x",
                y="a",
                label="Same original label",
                color="#ff0000",
                marker="o",
                line_style="solid",
                plot_type="line+marker",
            ),
            SeriesConfig(
                x="x",
                y="b",
                label="Same original label",
                color="#00aa00",
                marker="s",
                line_style="dashed",
                plot_type="line+marker",
                y_axis="right",
            ),
        ]

        result = render_figure(frame, config, series)
        FigureCanvasAgg(result.figure).draw()

        self.assertEqual(
            [text.get_text() for text in result.legend_artist.get_texts()],
            ["Control", "Sample A"],
        )
        handles = result.legend_artist.legend_handles
        self.assertEqual([handle.get_color() for handle in handles], ["#00aa00", "#ff0000"])
        self.assertEqual([handle.get_marker() for handle in handles], ["s", "o"])
        self.assertEqual([handle.get_linestyle() for handle in handles], ["--", "-"])

    def test_legend_entries_can_reuse_the_same_handle(self) -> None:
        frame = pd.DataFrame({"x": [0, 1], "y": [1, 2]})
        config = PlotConfig(
            legend_entries=[
                LegendEntryConfig(source_y="y", label="First meaning"),
                LegendEntryConfig(source_y="y", label="Second meaning"),
            ]
        )
        series = [
            SeriesConfig(
                x="x",
                y="y",
                color="#4477aa",
                marker="D",
                plot_type="line+marker",
            )
        ]

        result = render_figure(frame, config, series)
        FigureCanvasAgg(result.figure).draw()

        self.assertEqual(
            [text.get_text() for text in result.legend_artist.get_texts()],
            ["First meaning", "Second meaning"],
        )
        self.assertEqual(
            [handle.get_marker() for handle in result.legend_artist.legend_handles],
            ["D", "D"],
        )

    def test_legend_entries_skip_hidden_and_missing_sources_without_breaking_rows(self) -> None:
        frame = pd.DataFrame(
            {
                "x": [0, 1],
                "a": [1, 2],
                "b": [2, 3],
                "c": [3, 4],
            }
        )
        config = PlotConfig(
            legend_entries=[
                LegendEntryConfig(source_y="a", label="Visible A"),
                LegendEntryConfig(source_y="deleted", label="Deleted"),
                LegendEntryConfig(source_y="b", label="Hidden B"),
                LegendEntryConfig(source_y="c", label="Visible C"),
            ],
            legend_row_lengths=[2, 2],
        )
        series = [
            SeriesConfig(x="x", y="a", marker="o", plot_type="line+marker"),
            SeriesConfig(
                x="x",
                y="b",
                marker="s",
                plot_type="line+marker",
                show_in_legend=False,
            ),
            SeriesConfig(x="x", y="c", marker="D", plot_type="line+marker"),
        ]

        result = render_figure(frame, config, series)
        FigureCanvasAgg(result.figure).draw()

        self.assertEqual(
            [text.get_text() for text in result.legend_artist.get_texts()],
            ["Visible A", "Visible C"],
        )
        self.assertEqual(
            [handle.get_marker() for handle in result.legend_artist.legend_handles],
            ["o", "D"],
        )
        self.assertEqual(result.legend_artist._ncols, 1)

    def test_broken_axis_legend_stays_above_the_sibling_plot(self) -> None:
        frame = pd.DataFrame(
            {
                "x": np.linspace(0.0, 2.0, 20),
                "y": np.linspace(0.2, 1.8, 20),
            }
        )
        cases = {
            "broken x": PlotConfig(
                dpi=100,
                width_mm=120,
                height_mm=70,
                fixed_plot_area=False,
                x_break_enabled=True,
                x_break_left_min=0.0,
                x_break_left_max=2.0,
                x_break_right_min=8.0,
                x_break_right_max=10.0,
                legend_anchor_x=1.25,
                legend_anchor_y=0.85,
            ),
            "broken y": PlotConfig(
                dpi=100,
                width_mm=100,
                height_mm=90,
                fixed_plot_area=False,
                y_break_enabled=True,
                y_break_lower_min=0.0,
                y_break_lower_max=2.0,
                y_break_upper_min=8.0,
                y_break_upper_max=10.0,
                legend_anchor_x=0.15,
                legend_anchor_y=-0.2,
            ),
        }

        for name, config in cases.items():
            with self.subTest(name=name):
                result = render_figure(
                    frame,
                    config,
                    [
                        SeriesConfig(
                            x="x",
                            y="y",
                            label="Visible legend",
                            marker="o",
                            plot_type="line+marker",
                        )
                    ],
                )
                legend_axis = result.legend_artist.axes
                sibling_axis = next(
                    axis
                    for axis in result.figure.axes
                    if axis is not legend_axis
                )
                sibling_axis.set_facecolor("#ff00ff")
                canvas = FigureCanvasAgg(result.figure)
                canvas.draw()

                pixels = np.asarray(canvas.buffer_rgba())
                x, y, width, height = result.legend_artist.get_window_extent(
                    canvas.get_renderer()
                ).bounds
                image_height = pixels.shape[0]
                x0 = max(0, int(x))
                x1 = min(pixels.shape[1], int(np.ceil(x + width)))
                y0 = max(0, image_height - int(np.ceil(y + height)))
                y1 = min(image_height, image_height - int(y))
                legend_pixels = pixels[y0:y1, x0:x1, :3]
                magenta = np.all(
                    legend_pixels == np.array([255, 0, 255]),
                    axis=2,
                )

                self.assertGreater(
                    legend_axis.get_zorder(),
                    sibling_axis.get_zorder(),
                )
                self.assertFalse(legend_axis.patch.get_visible())
                self.assertGreater(int((~magenta).sum()), 250)


if __name__ == "__main__":
    unittest.main()
