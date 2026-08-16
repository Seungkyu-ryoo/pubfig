from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from pubfig.plot_config import PlotConfig, SeriesConfig
from pubfig.rendering import (
    BatchExportResult,
    ExportFailure,
    ExportJob,
    ExportOutcome,
    RenderCoordinator,
    RenderRequest,
    RenderResult,
)


class _FakeFigure:
    def __init__(self, payload: bytes = b"figure") -> None:
        self.payload = payload
        self.clear_count = 0
        self.savefig_calls: list[dict] = []

    def clear(self) -> None:
        self.clear_count += 1

    def savefig(self, buffer, **kwargs) -> None:
        self.savefig_calls.append(kwargs)
        buffer.write(self.payload)


class _FakeCanvas:
    instances = []

    def __init__(self, figure) -> None:
        self.figure = figure
        self.drawn = False
        self.renderer = object()
        self.instances.append(self)

    def draw(self) -> None:
        self.drawn = True

    def get_renderer(self):
        return self.renderer


class RenderCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.frame = pd.DataFrame({"x": [0, 1], "y": [1, 2]})
        self.config = PlotConfig(dpi=144, transparent=True)
        self.series = [SeriesConfig(x="x", y="y")]
        self.rendered: list[tuple[RenderRequest, _FakeFigure]] = []
        self.exported: list[tuple[_FakeFigure, Path, PlotConfig]] = []

        def render(frame, config, series):
            request = RenderRequest(frame, config, tuple(series))
            figure = _FakeFigure(payload=f"render-{len(self.rendered)}".encode())
            self.rendered.append((request, figure))
            return RenderResult(figure, ["check scale"], [])

        def export(figure, path, config):
            self.exported.append((figure, Path(path), config))

        self.render_function = render
        self.coordinator = RenderCoordinator(
            render=self.render_function,
            export=export,
            canvas_factory=_FakeCanvas,
        )

    def request(self, label: str = "graph") -> RenderRequest:
        return RenderRequest(self.frame, self.config, self.series, label)

    def test_render_for_display_transfers_figure_after_callback(self) -> None:
        accepted = []
        result = self.coordinator.render_for_display(self.request(), accepted.append)

        self.assertIs(accepted[0], result)
        self.assertEqual(result.figure.clear_count, 0)
        self.assertIs(self.rendered[0][0].dataframe, self.frame)
        self.assertIs(self.rendered[0][0].config, self.config)
        self.assertEqual(self.rendered[0][0].series_configs, tuple(self.series))

    def test_display_callback_failure_cleans_before_reraising(self) -> None:
        def reject(_result):
            raise RuntimeError("canvas rejected result")

        with self.assertRaisesRegex(RuntimeError, "canvas rejected"):
            self.coordinator.render_for_display(self.request(), reject)
        self.assertEqual(self.rendered[-1][1].clear_count, 1)

    def test_temporary_cleans_on_normal_and_exceptional_exit(self) -> None:
        with self.coordinator.temporary(self.request()) as result:
            first = result.figure
            self.assertEqual(first.clear_count, 0)
        self.assertEqual(first.clear_count, 1)

        with self.assertRaisesRegex(ValueError, "measurement failed"):
            with self.coordinator.temporary(self.request()) as result:
                second = result.figure
                raise ValueError("measurement failed")
        self.assertEqual(second.clear_count, 1)

    def test_measure_draws_agg_canvas_and_owns_temporary_figure(self) -> None:
        _FakeCanvas.instances.clear()
        with self.coordinator.measure(self.request()) as measured:
            figure = measured.result.figure
            canvas = _FakeCanvas.instances[-1]
            self.assertTrue(canvas.drawn)
            self.assertIs(measured.renderer, canvas.renderer)
            self.assertEqual(figure.clear_count, 0)
        self.assertEqual(figure.clear_count, 1)

    def test_render_bytes_uses_config_defaults_and_cleans(self) -> None:
        encoded = self.coordinator.render_bytes(
            self.request(),
            savefig_kwargs={"metadata": {"source": "test"}},
        )
        figure = self.rendered[-1][1]

        self.assertEqual(encoded.data, figure.payload)
        self.assertEqual(encoded.warnings, ("check scale",))
        self.assertEqual(encoded.format, "png")
        self.assertEqual(
            figure.savefig_calls,
            [{
                "format": "png",
                "dpi": 144,
                "facecolor": "none",
                "transparent": True,
                "metadata": {"source": "test"},
            }],
        )
        self.assertEqual(figure.clear_count, 1)

    def test_single_export_has_explicit_temporary_or_retained_ownership(self) -> None:
        with TemporaryDirectory() as folder:
            temporary_job = ExportJob(self.request("temporary"), Path(folder) / "temp.png")
            temporary = self.coordinator.export_one(temporary_job)
            temporary_figure = self.rendered[-1][1]
            self.assertIsInstance(temporary, ExportOutcome)
            self.assertIsNone(temporary.render_result)
            self.assertEqual(temporary_figure.clear_count, 1)

            accepted = []
            retained_job = ExportJob(self.request("retained"), Path(folder) / "keep.png")
            retained = self.coordinator.export_one(
                retained_job,
                retain_figure=True,
                accept=accepted.append,
            )
            retained_figure = self.rendered[-1][1]
            self.assertIs(accepted[0], retained.render_result)
            self.assertEqual(retained_figure.clear_count, 0)
            self.coordinator.cleanup(retained.render_result)
            self.assertEqual(retained_figure.clear_count, 1)

        self.assertEqual([item[1].name for item in self.exported], ["temp.png", "keep.png"])

    def test_single_export_failure_and_accept_failure_both_cleanup(self) -> None:
        def fail_export(_figure, _path, _config):
            raise OSError("disk full")

        coordinator = RenderCoordinator(
            render=self.render_function,
            export=fail_export,
            canvas_factory=_FakeCanvas,
        )
        with self.assertRaisesRegex(OSError, "disk full"):
            coordinator.export_one(ExportJob(self.request(), "bad.png"), retain_figure=True)
        self.assertEqual(self.rendered[-1][1].clear_count, 1)

        def reject(_result):
            raise RuntimeError("preview swap failed")

        with self.assertRaisesRegex(RuntimeError, "preview swap failed"):
            self.coordinator.export_one(
                ExportJob(self.request(), "ok.png"),
                retain_figure=True,
                accept=reject,
            )
        self.assertEqual(self.rendered[-1][1].clear_count, 1)

    def test_batch_continues_after_failure_cleans_all_and_reports_callbacks(self) -> None:
        figures: list[_FakeFigure] = []

        def render(frame, config, series):
            figure = _FakeFigure()
            figures.append(figure)
            return RenderResult(figure, ["warning"], [])

        def export(_figure, path, _config):
            if Path(path).stem == "bad":
                raise OSError("cannot write")

        coordinator = RenderCoordinator(render=render, export=export)
        jobs = [
            ExportJob(self.request("first"), "one.png"),
            ExportJob(self.request("broken"), "bad.png"),
            ExportJob(self.request("third"), "three.png"),
        ]
        progress = []
        failures = []
        report = coordinator.export_many(
            jobs,
            on_progress=lambda done, total, event: progress.append((done, total, event)),
            on_failure=failures.append,
        )

        self.assertIsInstance(report, BatchExportResult)
        self.assertEqual(report.exported_paths, (Path("one.png"), Path("three.png")))
        self.assertEqual((report.succeeded, report.failure_count), (2, 1))
        self.assertEqual(report.warnings, ("first: warning", "third: warning"))
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ExportFailure)
        self.assertEqual([done for done, _total, _event in progress], [1, 2, 3])
        self.assertEqual([total for _done, total, _event in progress], [3, 3, 3])
        self.assertTrue(all(figure.clear_count == 1 for figure in figures))


if __name__ == "__main__":
    unittest.main()
