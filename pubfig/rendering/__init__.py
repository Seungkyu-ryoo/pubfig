"""Stable Matplotlib rendering facade."""

from .artists import ArrowAnnotationArtist
from .axes import mm_to_inch
from .coordinator import (
    BatchExportResult,
    ExportFailure,
    ExportJob,
    ExportOutcome,
    MeasuredRender,
    RenderCoordinator,
    RenderedBytes,
    RenderRequest,
    clear_render_result,
)
from .core import (
    DEFAULT_PREVIEW_POINT_LIMIT,
    NumericColumnCache,
    RenderOptions,
    RenderResult,
    render_figure,
)
from .export import export_figure

__all__ = [
    "ArrowAnnotationArtist",
    "BatchExportResult",
    "DEFAULT_PREVIEW_POINT_LIMIT",
    "ExportFailure",
    "ExportJob",
    "ExportOutcome",
    "MeasuredRender",
    "NumericColumnCache",
    "RenderCoordinator",
    "RenderOptions",
    "RenderResult",
    "RenderedBytes",
    "RenderRequest",
    "clear_render_result",
    "export_figure",
    "mm_to_inch",
    "render_figure",
]
