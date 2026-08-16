"""Shared display-space geometry for rendered and interactive annotations."""

from __future__ import annotations

from typing import Any

from matplotlib.transforms import Affine2D

from .plot_config import AnnotationConfig


def annotation_display_geometry(
    axes: Any,
    annotation: AnnotationConfig,
) -> tuple[float, float, float, float]:
    """Return annotation origin and extent in display pixels."""

    axes_bbox = axes.get_window_extent()
    unit_px = max(min(axes_bbox.width, axes_bbox.height), 1.0)
    start_x, start_y = axes.transAxes.transform((annotation.x, annotation.y))
    return (
        float(start_x),
        float(start_y),
        float(annotation.width * unit_px),
        float(annotation.height * unit_px),
    )


def rotated_endpoint(
    start_x: float,
    start_y: float,
    width_px: float,
    height_px: float,
    angle: float,
) -> tuple[float, float]:
    """Return the rotated opposite endpoint of a display-space rectangle."""

    if not angle:
        return start_x + width_px, start_y + height_px
    point = Affine2D().rotate_deg(angle).transform((width_px, height_px))
    return start_x + float(point[0]), start_y + float(point[1])


__all__ = ["annotation_display_geometry", "rotated_endpoint"]
