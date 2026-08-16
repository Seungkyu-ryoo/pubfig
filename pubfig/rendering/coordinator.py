"""Qt-independent orchestration for rendering, measuring, and exporting.

The low-level :func:`render_figure` and :func:`export_figure` APIs remain the
stable primitives.  This module defines the ownership boundary around them:

* display renders transfer their Figure to the caller;
* temporary, byte, measurement, and batch renders are always cleared here;
* a retained single export transfers its Figure only after export succeeds.

Callbacks are plain Python callables, so a Qt window can inject canvas/status
updates without making this module depend on Qt.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

from matplotlib.backends.backend_agg import FigureCanvasAgg
import pandas as pd

from ..plot_config import PlotConfig, SeriesConfig
from .core import RenderResult, render_figure
from .export import export_figure


@dataclass(frozen=True)
class RenderRequest:
    """Borrowed, synchronous inputs for one render operation."""

    dataframe: pd.DataFrame
    config: PlotConfig
    series_configs: Sequence[SeriesConfig]
    label: str = ""


@dataclass(frozen=True)
class MeasuredRender:
    """A rendered result paired with an initialized Agg renderer."""

    result: RenderResult
    renderer: Any


@dataclass(frozen=True)
class RenderedBytes:
    """Encoded temporary render output and its non-fatal warnings."""

    data: bytes
    warnings: tuple[str, ...]
    format: str


@dataclass(frozen=True)
class ExportJob:
    """One output path paired with the inputs needed to render it."""

    request: RenderRequest
    path: Path | str

    @property
    def resolved_path(self) -> Path:
        return Path(self.path)

    @property
    def label(self) -> str:
        return self.request.label or self.resolved_path.stem


@dataclass(frozen=True)
class ExportOutcome:
    """Successful export metadata.

    ``render_result`` is set only when ``retain_figure=True`` was requested.
    That Figure belongs to the caller and must eventually be cleared/replaced.
    """

    job: ExportJob
    warnings: tuple[str, ...]
    render_result: RenderResult | None = None

    @property
    def path(self) -> Path:
        return self.job.resolved_path

    @property
    def label(self) -> str:
        return self.job.label


@dataclass(frozen=True)
class ExportFailure:
    job: ExportJob
    error: Exception

    @property
    def path(self) -> Path:
        return self.job.resolved_path

    @property
    def label(self) -> str:
        return self.job.label


BatchEvent = ExportOutcome | ExportFailure
ProgressCallback = Callable[[int, int, BatchEvent], None]
FailureCallback = Callable[[ExportFailure], None]
ResultCallback = Callable[[RenderResult], None]


@dataclass(frozen=True)
class BatchExportResult:
    """Complete, ordered report from a batch export."""

    exported: tuple[ExportOutcome, ...] = field(default_factory=tuple)
    failed: tuple[ExportFailure, ...] = field(default_factory=tuple)

    @property
    def exported_paths(self) -> tuple[Path, ...]:
        return tuple(outcome.path for outcome in self.exported)

    @property
    def succeeded(self) -> int:
        return len(self.exported)

    @property
    def failure_count(self) -> int:
        return len(self.failed)

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(
            f"{outcome.label}: {warning}"
            for outcome in self.exported
            for warning in outcome.warnings
        )


def clear_render_result(result: RenderResult) -> None:
    """Release a render's Matplotlib artist graph promptly and idempotently."""
    figure = getattr(result, "figure", None)
    if figure is not None:
        figure.clear()


class RenderCoordinator:
    """Share render/lifecycle behaviour across UI workflows.

    Dependencies are injectable both for tests and for alternate render/export
    backends. No callback is retained after a call, preventing a long-lived
    coordinator from accidentally owning a Qt window.
    """

    def __init__(
        self,
        *,
        render: Callable[[pd.DataFrame, PlotConfig, list[SeriesConfig]], RenderResult] = render_figure,
        export: Callable[[Any, str | Path, PlotConfig], None] = export_figure,
        cleanup: Callable[[RenderResult], None] = clear_render_result,
        canvas_factory: Callable[[Any], Any] = FigureCanvasAgg,
    ) -> None:
        self._render = render
        self._export = export
        self._cleanup = cleanup
        self._canvas_factory = canvas_factory

    def render(self, request: RenderRequest) -> RenderResult:
        """Render once and transfer the resulting Figure to the caller."""
        self._validate_request(request)
        return self._render(
            request.dataframe,
            request.config,
            list(request.series_configs),
        )

    def render_for_display(
        self,
        request: RenderRequest,
        accept: ResultCallback,
    ) -> RenderResult:
        """Render and inject the result into a display owner.

        A successful callback owns the Figure. If the callback fails before it
        can adopt the result, the coordinator clears the Figure and re-raises.
        """
        if not callable(accept):
            raise TypeError("render_for_display requires a callable accept callback")
        result = self.render(request)
        try:
            accept(result)
        except BaseException:
            self.cleanup(result)
            raise
        return result

    @contextmanager
    def temporary(self, request: RenderRequest) -> Iterator[RenderResult]:
        """Yield a render that is cleared on every exit path."""
        result = self.render(request)
        try:
            yield result
        finally:
            self.cleanup(result)

    @contextmanager
    def measure(self, request: RenderRequest) -> Iterator[MeasuredRender]:
        """Yield a drawn Agg renderer and clear its Figure after measurement."""
        with self.temporary(request) as result:
            canvas = self._canvas_factory(result.figure)
            canvas.draw()
            yield MeasuredRender(result=result, renderer=canvas.get_renderer())

    def render_bytes(
        self,
        request: RenderRequest,
        *,
        format: str = "png",
        savefig_kwargs: Mapping[str, Any] | None = None,
    ) -> RenderedBytes:
        """Encode a temporary render, suitable for clipboard or IPC use."""
        image_format = str(format).strip().lower()
        if not image_format:
            raise ValueError("render_bytes requires a non-empty format")
        kwargs: dict[str, Any] = {
            "format": image_format,
            "dpi": request.config.dpi,
            "facecolor": "none" if request.config.transparent else "white",
            "transparent": request.config.transparent,
        }
        if savefig_kwargs:
            if "format" in savefig_kwargs:
                raise ValueError("Pass the image format through the format argument")
            kwargs.update(savefig_kwargs)
        buffer = BytesIO()
        with self.temporary(request) as result:
            result.figure.savefig(buffer, **kwargs)
            return RenderedBytes(
                data=buffer.getvalue(),
                warnings=tuple(result.warnings),
                format=image_format,
            )

    def export_one(
        self,
        job: ExportJob,
        *,
        retain_figure: bool = False,
        accept: ResultCallback | None = None,
    ) -> ExportOutcome:
        """Render and export one job with explicit post-export ownership.

        Temporary export is the default. With ``retain_figure=True`` the
        result is returned to the caller (and optionally passed to ``accept``)
        only after the file export succeeds. Every failure path is cleaned.
        """
        if not isinstance(job, ExportJob):
            raise TypeError("export_one expects ExportJob")
        if accept is not None and not retain_figure:
            raise ValueError("accept requires retain_figure=True")
        if accept is not None and not callable(accept):
            raise TypeError("accept must be callable")

        result = self.render(job.request)
        retained = False
        try:
            self._export(result.figure, job.resolved_path, job.request.config)
            if accept is not None:
                accept(result)
            retained = retain_figure
            return ExportOutcome(
                job=job,
                warnings=tuple(result.warnings),
                render_result=result if retain_figure else None,
            )
        finally:
            if not retained:
                self.cleanup(result)

    def export_many(
        self,
        jobs: Iterable[ExportJob],
        *,
        on_progress: ProgressCallback | None = None,
        on_failure: FailureCallback | None = None,
    ) -> BatchExportResult:
        """Export independent jobs, continuing after individual failures."""
        if on_progress is not None and not callable(on_progress):
            raise TypeError("on_progress must be callable")
        if on_failure is not None and not callable(on_failure):
            raise TypeError("on_failure must be callable")
        queued = tuple(jobs)
        for job in queued:
            if not isinstance(job, ExportJob):
                raise TypeError("export_many expects ExportJob items")

        exported: list[ExportOutcome] = []
        failed: list[ExportFailure] = []
        total = len(queued)
        for index, job in enumerate(queued, start=1):
            try:
                event: BatchEvent = self.export_one(job)
                exported.append(event)
            except Exception as error:
                event = ExportFailure(job=job, error=error)
                failed.append(event)
                if on_failure is not None:
                    on_failure(event)
            if on_progress is not None:
                on_progress(index, total, event)
        return BatchExportResult(tuple(exported), tuple(failed))

    def cleanup(self, result: RenderResult) -> None:
        self._cleanup(result)

    @staticmethod
    def _validate_request(request: RenderRequest) -> None:
        if not isinstance(request, RenderRequest):
            raise TypeError("render operations expect RenderRequest")
        if not isinstance(request.dataframe, pd.DataFrame):
            raise TypeError("RenderRequest.dataframe must be a pandas DataFrame")
        if not isinstance(request.config, PlotConfig):
            raise TypeError("RenderRequest.config must be PlotConfig")


__all__ = [
    "BatchExportResult",
    "ExportFailure",
    "ExportJob",
    "ExportOutcome",
    "MeasuredRender",
    "RenderCoordinator",
    "RenderedBytes",
    "RenderRequest",
    "clear_render_result",
]
