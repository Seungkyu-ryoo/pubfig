"""Plot primitives, annotations, text formatting, and legend helpers."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any

from matplotlib.colors import is_color_like
from matplotlib.legend_handler import HandlerBase
from matplotlib.lines import Line2D
from matplotlib.markers import MarkerStyle
from matplotlib.patches import Ellipse, FancyArrowPatch, Rectangle
from matplotlib.transforms import Affine2D, IdentityTransform
import numpy as np
import pandas as pd

from ..annotation_geometry import annotation_display_geometry, rotated_endpoint
from ..legend_layout import legend_row_ids, normalize_row_lengths

from ..data_parser import coerce_numeric
from ..plot_config import (
    FILLABLE_MARKERS,
    MARKER_FILL_STYLES,
    PARTIAL_MARKER_FILL_STYLES,
    AnnotationConfig,
    SeriesConfig,
)


class ArrowAnnotationArtist:
    """Keep an annotation shaft and its independently sized arrow head in sync."""

    def __init__(
        self,
        shaft_artist: Any,
        head_artist: FancyArrowPatch,
        tip: tuple[float, float],
        head_size: float = 12.0,
    ) -> None:
        self.shaft_artist = shaft_artist
        self.head_artist = head_artist
        self._tip = tip
        self._head_size = head_size
        self._update_head()

    @property
    def xy(self):
        return self._tip

    @xy.setter
    def xy(self, value) -> None:
        self._tip = value
        self._update_head()

    def set_position(self, value) -> None:
        self.shaft_artist.set_position(value)
        self._update_head()

    def contains(self, event):
        contains_shaft, shaft_info = self.shaft_artist.contains(event)
        if contains_shaft:
            return True, shaft_info
        return self.head_artist.contains(event)

    def _update_head(self) -> None:
        start = self.shaft_artist.get_position()
        head_start = _arrow_head_start(start, self._tip, self._head_size)
        self.shaft_artist.xy = head_start
        self.head_artist.set_positions(head_start, self._tip)


def series_marker_fill_style(series: SeriesConfig) -> str:
    fill_style = getattr(series, "marker_fill_style", "full")
    if fill_style not in MARKER_FILL_STYLES:
        return "full"
    if (
        fill_style in PARTIAL_MARKER_FILL_STYLES
        and series.marker not in FILLABLE_MARKERS
    ):
        return "full"
    return fill_style


def series_marker_style(series: SeriesConfig) -> MarkerStyle:
    """Return one normalized marker shared by scatter, line, stem and legend."""

    return MarkerStyle(
        series.marker,
        fillstyle=series_marker_fill_style(series),
    )


def plot_partially_filled_scatter(
    target_ax,
    x_values,
    y_values,
    series: SeriesConfig,
    color: str,
    label: str,
    alpha: float,
) -> None:
    """Draw a half-filled scatter without losing MarkerStyle's second half."""

    fill_style = series_marker_fill_style(series)
    # PathCollection consumes only MarkerStyle.get_path() and silently drops
    # get_alt_path(), producing a clipped half-marker.  A marker-only Line2D
    # retains both halves, uses the same point-sized marker scale, and also
    # supplies the correct automatic/manual legend handle.
    target_ax.plot(
        x_values,
        y_values,
        color=color,
        marker=MarkerStyle(series.marker, fillstyle=fill_style),
        markerfacecolor=color,
        markerfacecoloralt="none",
        markeredgecolor=color,
        markersize=series.marker_size,
        linestyle="none",
        label=label,
        alpha=alpha,
        zorder=2,
    )


def plot_series(
    target_ax,
    x_values,
    y_values,
    series: SeriesConfig,
    color: str,
    label: str,
    plot_type: str,
) -> None:
    alpha = 1.0 if series.force_opaque else series.alpha
    line_style = resolve_line_style(getattr(series, "line_style", "solid"))
    if plot_type == "scatter":
        if series_marker_fill_style(series) in PARTIAL_MARKER_FILL_STYLES:
            plot_partially_filled_scatter(
                target_ax,
                x_values,
                y_values,
                series,
                color,
                label,
                alpha,
            )
        else:
            target_ax.scatter(
                x_values,
                y_values,
                s=series.marker_size**2,
                color=color,
                marker=series_marker_style(series),
                label=label,
                alpha=alpha,
                zorder=2,
            )
    elif plot_type == "bar":
        target_ax.bar(x_values, y_values, color=color, alpha=alpha, label=label)
    elif plot_type == "line+marker":
        target_ax.plot(
            x_values,
            y_values,
            color=color,
            marker=series_marker_style(series),
            markerfacecolor=color,
            markerfacecoloralt="none",
            markeredgecolor=color,
            linestyle=line_style,
            linewidth=series.line_width,
            markersize=series.marker_size,
            label=label,
            alpha=alpha,
        )
    elif plot_type == "step":
        target_ax.step(
            x_values,
            y_values,
            where="mid",
            color=color,
            linestyle=line_style,
            linewidth=series.line_width,
            label=label,
            alpha=alpha,
        )
    elif plot_type == "area":
        fill_alpha = alpha if series.force_opaque else alpha * 0.5
        target_ax.fill_between(x_values, y_values, color=color, alpha=fill_alpha, label=label)
        target_ax.plot(
            x_values,
            y_values,
            color=color,
            linestyle=line_style,
            linewidth=series.line_width,
            alpha=alpha,
        )
    elif plot_type == "stem":
        # Matplotlib's StemContainer legend handler rebuilds its marker and
        # drops MarkerStyle(fillstyle="none").  Let the configured marker line
        # own the label so the plot and legend preserve the same open/filled
        # appearance.
        markerline, stemlines, baseline = target_ax.stem(
            x_values,
            y_values,
            label="_nolegend_",
        )
        markerline.set_marker(series_marker_style(series))
        markerline.set_markersize(series.marker_size)
        markerline.set_color(color)
        markerline.set_markerfacecolor(color)
        markerline.set_markerfacecoloralt("none")
        markerline.set_markeredgecolor(color)
        markerline.set_alpha(alpha)
        markerline.set_label(label)
        stemlines.set_color(color)
        stemlines.set_linewidth(series.line_width)
        stemlines.set_alpha(alpha)
        baseline.set_color("0.35")
        baseline.set_linewidth(0.5)
    else:
        target_ax.plot(
            x_values,
            y_values,
            color=color,
            linestyle=line_style,
            linewidth=series.line_width,
            label=label,
            alpha=alpha,
        )


def plot_broken_y_series(
    target_ax,
    x_values,
    y_values,
    series: SeriesConfig,
    color: str,
    label: str,
    plot_type: str,
    y_min: float,
    y_max: float,
) -> None:
    in_range = (y_values >= y_min) & (y_values <= y_max)
    if plot_type in {"scatter", "bar", "stem"}:
        plot_series(target_ax, x_values[in_range], y_values[in_range], series, color, label, plot_type)
        return
    plot_series(target_ax, x_values, y_values.where(in_range), series, color, label, plot_type)


def plot_broken_x_series(
    target_ax,
    x_values,
    y_values,
    series: SeriesConfig,
    color: str,
    label: str,
    plot_type: str,
    x_min: float,
    x_max: float,
) -> None:
    in_range = (x_values >= x_min) & (x_values <= x_max)
    if plot_type in {"scatter", "bar", "stem"}:
        plot_series(target_ax, x_values[in_range], y_values[in_range], series, color, label, plot_type)
        return
    plot_series(target_ax, x_values, y_values.where(in_range), series, color, label, plot_type)


def plot_error_bars(
    target_ax,
    df: pd.DataFrame,
    series: SeriesConfig,
    x_values,
    y_values,
    valid,
    color: str,
    warnings: list[str],
    y_scale: str = "linear",
    *,
    numeric_errors: pd.Series | None = None,
) -> None:
    column = getattr(series, "error_column", "")
    if not column:
        return
    if column not in df.columns:
        warnings.append(f"Error column '{column}' not found for {series.label or series.y}.")
        return
    error_source = (
        numeric_errors
        if numeric_errors is not None
        else coerce_numeric(df[column])
    )
    errors = error_source.abs()[valid]
    # Preview X-range filtering can legitimately leave no visible points.  It
    # is not evidence that the source error column itself is invalid.
    if errors.empty:
        return
    error_values = errors.to_numpy(dtype=float, na_value=np.nan, copy=False)
    finite = np.isfinite(error_values)
    mask = pd.Series(finite, index=errors.index, copy=False)
    if not bool(np.any(finite)):
        warnings.append(
            f"Error column '{column}' has no finite numeric values for "
            f"{series.label or series.y}."
        )
        return
    nonfinite_count = int(np.count_nonzero(~np.isnan(error_values) & ~finite))
    if nonfinite_count:
        warnings.append(
            f"Error column '{column}': {nonfinite_count} non-finite value(s) "
            f"skipped for {series.label or series.y}."
        )
    if y_scale == "log":
        invalid_log = mask & (y_values - errors <= 0)
        invalid_log_count = int(invalid_log.sum())
        if invalid_log_count:
            warnings.append(
                f"Error column '{column}': {invalid_log_count} error bar(s) "
                "skipped because the lower bound is not positive on a "
                "logarithmic Y axis."
            )
            mask &= ~invalid_log
        if not mask.any():
            return
    alpha = 1.0 if series.force_opaque else series.alpha
    target_ax.errorbar(
        x_values[mask],
        y_values[mask],
        yerr=errors[mask],
        fmt="none",
        ecolor=color,
        elinewidth=max(series.line_width * 0.75, 0.4),
        capsize=max(getattr(series, "error_cap_size", 2.0), 0.0),
        capthick=max(series.line_width * 0.75, 0.4),
        alpha=alpha,
        label="_nolegend_",
        zorder=1.5,
    )


@dataclass(frozen=True)
class _LegendTextStyle:
    font_family: str = ""
    font_size: float | None = None
    font_bold: bool = False
    font_italic: bool = False
    text_color: str = ""


class _TextOnlyLegendHandle:
    """Sentinel whose legend handler removes the per-entry handle slot."""


class _TextOnlyLegendHandler(HandlerBase):
    """Lay out a text-only legend entry from the column's content edge."""

    def legend_artist(self, legend, orig_handle, fontsize, handlebox):
        artist = Line2D([], [], linestyle="none", marker="", alpha=0)
        handlebox.add_artist(artist)
        # HPacker excludes invisible children, so both this handle box and the
        # separator after it disappear without replacing Matplotlib's Legend.
        handlebox.set_visible(False)
        return artist


def legend_handler_map() -> dict[type, HandlerBase]:
    """Return handlers needed by PubFig's standard Matplotlib legend."""

    return {_TextOnlyLegendHandle: _TextOnlyLegendHandler()}


def _legend_grid_layout_with_styles(
    handles: list[Any],
    labels: list[str],
    row_lengths: list[int],
    styles: list[_LegendTextStyle | None],
) -> tuple[list[Any], list[str], int, list[_LegendTextStyle | None]]:
    if len(styles) != len(handles):
        raise ValueError("Legend styles must match the handle count.")
    if not row_lengths or not handles:
        return handles, labels, 1, styles

    clean_lengths = normalize_row_lengths(row_lengths)
    if not clean_lengths or max(clean_lengths, default=0) <= 1:
        return handles, labels, 1, styles

    rows: list[list[tuple[Any, str, _LegendTextStyle | None] | None]] = []
    handle_idx = 0
    for length in clean_lengths:
        row: list[tuple[Any, str, _LegendTextStyle | None] | None] = []
        for _ in range(length):
            if handle_idx >= len(handles):
                break
            row.append(
                (
                    handles[handle_idx],
                    labels[handle_idx],
                    styles[handle_idx],
                )
            )
            handle_idx += 1
        rows.append(row)
    while handle_idx < len(handles):
        rows.append(
            [
                (
                    handles[handle_idx],
                    labels[handle_idx],
                    styles[handle_idx],
                )
            ]
        )
        handle_idx += 1

    ncol = max((len(row) for row in rows), default=1)
    if ncol <= 1:
        return handles, labels, 1, styles

    # A grid filler deliberately remains a normal invisible Line2D.  Unlike a
    # text-only entry it must retain the standard handle slot so later columns
    # keep the same geometry as ordinary legend entries.
    spacer_handle = Line2D([], [], linestyle="none", marker="", alpha=0)
    output_handles: list[Any] = []
    output_labels: list[str] = []
    output_styles: list[_LegendTextStyle | None] = []
    for col in range(ncol):
        for row in rows:
            cell = row[col] if col < len(row) else None
            if cell is None:
                output_handles.append(spacer_handle)
                output_labels.append(" ")
                output_styles.append(None)
            else:
                output_handles.append(cell[0])
                output_labels.append(cell[1])
                output_styles.append(cell[2])
    return output_handles, output_labels, ncol, output_styles


def legend_grid_layout(
    handles: list[Any],
    labels: list[str],
    row_lengths: list[int],
) -> tuple[list[Any], list[str], int]:
    """Arrange legend items without changing this helper's public result."""

    output_handles, output_labels, ncol, _styles = _legend_grid_layout_with_styles(
        handles,
        labels,
        row_lengths,
        [None] * len(handles),
    )
    return output_handles, output_labels, ncol


def _legend_row_ids(entry_count: int, row_lengths: list[int]) -> list[int]:
    return legend_row_ids(entry_count, row_lengths)


def _text_only_legend_handle() -> _TextOnlyLegendHandle:
    """Return a sentinel that removes a text-only entry's handle slot."""

    return _TextOnlyLegendHandle()


def _entry_value(entry, name: str, default):
    if isinstance(entry, dict):
        return entry.get(name, default)
    return getattr(entry, name, default)


def _legend_text_style(entry) -> _LegendTextStyle:
    raw_family = _entry_value(entry, "font_family", "")
    font_family = "" if raw_family is None else str(raw_family).strip()

    font_size: float | None = None
    raw_size = _entry_value(entry, "font_size", None)
    if raw_size is not None and not isinstance(raw_size, bool):
        try:
            numeric_size = float(raw_size)
        except (TypeError, ValueError, OverflowError):
            pass
        else:
            if math.isfinite(numeric_size) and numeric_size > 0:
                font_size = numeric_size

    raw_color = _entry_value(entry, "text_color", "")
    color = "" if raw_color is None else str(raw_color).strip()
    if color and not is_color_like(color):
        color = ""

    return _LegendTextStyle(
        font_family=font_family,
        font_size=font_size,
        font_bold=bool(_entry_value(entry, "font_bold", False)),
        font_italic=bool(_entry_value(entry, "font_italic", False)),
        text_color=color,
    )


def _origin_legend_text(label: str, records: list[tuple[str, Any, str]]) -> str:
    """Resolve Origin-style ``%(n)`` plot-name tokens in free legend text."""

    default_labels = [default_label for _source_y, _handle, default_label in records]

    def replace(match: re.Match) -> str:
        plot_index = int(match.group(1)) - 1
        if 0 <= plot_index < len(default_labels):
            return default_labels[plot_index]
        return match.group(0)

    return re.sub(r"%\((\d+)\)", replace, label)


def _resolve_explicit_legend_with_styles(
    records: list[tuple[str, Any, str]],
    entries,
    row_lengths: list[int],
) -> tuple[
    list[Any],
    list[str],
    list[int],
    list[_LegendTextStyle],
]:
    """Resolve an authoritative free-form legend against plotted series.

    An empty or missing ``source_y`` is a text-only item.  A stale source is
    treated the same way so user-authored text is not lost when plot data
    changes.  ``%(n)`` tokens use one-based plotted-record order, matching the
    dynamic plot-name references used by Origin legends.
    """
    by_source = {source_y: (handle, default_label) for source_y, handle, default_label in records}
    configured_entries = list(entries or [])
    try:
        row_ids = _legend_row_ids(len(configured_entries), row_lengths)
    except (TypeError, ValueError):
        row_ids = list(range(len(configured_entries)))
    resolved: list[tuple[int, Any, str, _LegendTextStyle]] = []

    for index, entry in enumerate(configured_entries):
        if isinstance(entry, dict):
            raw_source = entry.get("source_y", "")
            raw_label = entry.get("label", "")
            visible = entry.get("visible", True)
        else:
            raw_source = getattr(entry, "source_y", "")
            raw_label = getattr(entry, "label", "")
            visible = getattr(entry, "visible", True)
        if not visible:
            continue
        source_y = "" if raw_source is None else str(raw_source)
        label = "" if raw_label is None else str(raw_label)
        record = by_source.get(source_y) if source_y else None
        if record is None:
            handle = _text_only_legend_handle()
            resolved_label = _origin_legend_text(label, records)
        else:
            handle, default_label = record
            # Empty labels in existing projects mean "use the series name".
            # Empty text-only items remain real blank/spacer rows.
            resolved_label = (
                _origin_legend_text(label, records)
                if label
                else default_label
            )
        resolved.append(
            (
                row_ids[index],
                handle,
                format_plot_text(resolved_label),
                _legend_text_style(entry),
            )
        )

    handles = [handle for _row_id, handle, _label, _style in resolved]
    labels = [label for _row_id, _handle, label, _style in resolved]
    styles = [style for _row_id, _handle, _label, style in resolved]
    effective_lengths: list[int] = []
    previous_row: int | None = None
    for row_id, _handle, _label, _style in resolved:
        if row_id != previous_row:
            effective_lengths.append(1)
            previous_row = row_id
        else:
            effective_lengths[-1] += 1
    if all(length == 1 for length in effective_lengths):
        effective_lengths = []
    return handles, labels, effective_lengths, styles


def resolve_explicit_legend(
    records: list[tuple[str, Any, str]],
    entries,
    row_lengths: list[int],
) -> tuple[list[Any], list[str], list[int]]:
    """Resolve explicit legend entries while preserving the public result."""

    handles, labels, effective_lengths, _styles = (
        _resolve_explicit_legend_with_styles(records, entries, row_lengths)
    )
    return handles, labels, effective_lengths


def apply_legend_text_styles(
    legend,
    styles: list[_LegendTextStyle | None],
) -> None:
    """Apply validated per-entry styles to an already-created Legend."""

    texts = legend.get_texts()
    if len(texts) != len(styles):
        raise ValueError("Legend styles must match the rendered text count.")

    styled = False
    for text, style in zip(texts, styles):
        if style is None:
            continue
        if style.font_family:
            text.set_fontfamily(style.font_family)
        if style.font_size is not None:
            text.set_fontsize(style.font_size)
        text.set_fontweight("bold" if style.font_bold else "normal")
        text.set_fontstyle("italic" if style.font_italic else "normal")
        if style.text_color:
            text.set_color(style.text_color)
        styled = True
    if styled:
        legend.stale = True


def draw_annotations(ax, annotations: list[AnnotationConfig]) -> list[Any]:
    artists: list[Any] = []
    for annotation in annotations:
        start_x, start_y, width_px, height_px = _annotation_display_geometry(ax, annotation)
        text = format_plot_text(annotation.text)
        alpha = max(0.0, min(annotation.alpha, 1.0))
        line_style = resolve_line_style(annotation.line_style)
        if annotation.kind == "line":
            end_x, end_y = _rotated_endpoint(start_x, start_y, width_px, height_px, annotation.angle)
            patch = FancyArrowPatch(
                (start_x, start_y),
                (end_x, end_y),
                arrowstyle="-",
                linewidth=1.0,
                edgecolor=annotation.color,
                facecolor="none",
                alpha=alpha,
                linestyle=line_style,
                transform=IdentityTransform(),
            )
            ax.add_artist(patch)
            patch.set_clip_on(False)
            patch.set_in_layout(False)
            patch.set_picker(5)
            artists.append(patch)
        elif annotation.kind == "arrow":
            end_x, end_y = _rotated_endpoint(start_x, start_y, width_px, height_px, annotation.angle)
            head_size = max(annotation.arrow_head_size, 1.0)
            head_start = _arrow_head_start((start_x, start_y), (end_x, end_y), head_size)
            shaft_artist = ax.annotate(
                text,
                xy=head_start,
                xytext=(start_x, start_y),
                xycoords="figure pixels",
                textcoords="figure pixels",
                fontsize=annotation.font_size,
                color=annotation.color,
                alpha=alpha,
                arrowprops={
                    "arrowstyle": "-",
                    "color": annotation.color,
                    "linewidth": 1.0,
                    "linestyle": line_style,
                    "alpha": alpha,
                },
                annotation_clip=False,
            )
            head_artist = FancyArrowPatch(
                head_start,
                (end_x, end_y),
                arrowstyle="-|>",
                mutation_scale=head_size,
                linewidth=1.0,
                edgecolor=annotation.color,
                facecolor=annotation.color,
                alpha=alpha,
                linestyle="solid",
                transform=IdentityTransform(),
            )
            ax.add_artist(head_artist)
            head_artist.set_zorder(shaft_artist.get_zorder() + 0.1)
            shaft_artist.set_clip_on(False)
            shaft_artist.set_in_layout(False)
            shaft_artist.set_picker(5)
            head_artist.set_clip_on(False)
            head_artist.set_in_layout(False)
            head_artist.set_picker(True)
            artists.append(ArrowAnnotationArtist(shaft_artist, head_artist, (end_x, end_y), head_size))
        elif annotation.kind == "box":
            patch = Rectangle(
                (start_x, start_y),
                width_px,
                height_px,
                linewidth=1.0,
                edgecolor=annotation.color,
                facecolor=annotation.color if annotation.fill else "none",
                linestyle=line_style,
                alpha=(0.18 if annotation.fill else 1.0) * alpha,
                transform=Affine2D().rotate_deg_around(start_x, start_y, annotation.angle) + IdentityTransform(),
            )
            ax.add_patch(patch)
            patch.set_clip_on(False)
            patch.set_in_layout(False)
            patch.set_picker(True)
            artists.append(patch)
        elif annotation.kind == "circle":
            patch = Ellipse(
                (start_x + width_px / 2, start_y + height_px / 2),
                width=abs(width_px),
                height=abs(height_px),
                angle=annotation.angle,
                linewidth=1.0,
                edgecolor=annotation.color,
                facecolor=annotation.color if annotation.fill else "none",
                linestyle=line_style,
                alpha=(0.18 if annotation.fill else 1.0) * alpha,
                transform=IdentityTransform(),
            )
            ax.add_patch(patch)
            patch.set_clip_on(False)
            patch.set_in_layout(False)
            patch.set_picker(True)
            artists.append(patch)
        elif annotation.kind == "textbox":
            artist = ax.text(
                annotation.x,
                annotation.y,
                text,
                fontsize=annotation.font_size,
                color=annotation.color,
                rotation=annotation.angle,
                transform=ax.transAxes,
                alpha=alpha,
                bbox={
                    "boxstyle": "round,pad=0.25",
                    "facecolor": "white",
                    "edgecolor": annotation.color,
                    "linestyle": line_style,
                    "alpha": 0.9 * alpha,
                },
            )
            artist.set_clip_on(False)
            artist.set_in_layout(False)
            artist.set_picker(5)
            artists.append(artist)
        else:
            artist = ax.text(
                annotation.x,
                annotation.y,
                text,
                fontsize=annotation.font_size,
                color=annotation.color,
                rotation=annotation.angle,
                transform=ax.transAxes,
                alpha=alpha,
            )
            artist.set_clip_on(False)
            artist.set_in_layout(False)
            artist.set_picker(5)
            artists.append(artist)
    return artists


def format_plot_text(text: str) -> str:
    if "$" in text:
        return text

    def overbar_braced(match: re.Match) -> str:
        return _overbar_text(match.group(1))

    def braced(match: re.Match) -> str:
        return _subscript_math(match.group(1))

    def superscript_braced(match: re.Match) -> str:
        return _superscript_math(match.group(1))

    formatted = re.sub(r"\\?(?:bar|overbar)\{([^{}]+)\}", overbar_braced, text)
    formatted = re.sub(r"_\{([^{}]+)\}", braced, formatted)
    formatted = re.sub(r"\^\{([^{}]+)\}", superscript_braced, formatted)
    formatted = re.sub(r"_\(([^()]+)\)", braced, formatted)
    formatted = re.sub(r"\^\(([^()]+)\)", superscript_braced, formatted)
    formatted = re.sub(
        r"_([0-9]+(?:\.[0-9]+)?|[A-Za-z])",
        lambda match: _subscript_math(match.group(1)),
        formatted,
    )
    return re.sub(
        r"\^([+-]?[0-9]+(?:\.[0-9]+)?|[A-Za-z])",
        lambda match: _superscript_math(match.group(1)),
        formatted,
    )


def _subscript_math(value: str) -> str:
    value = value.replace("\\", r"\\").replace(" ", r"\ ")
    return rf"$_{{\mathrm{{{value}}}}}$"


def _superscript_math(value: str) -> str:
    value = value.replace("\\", r"\\").replace(" ", r"\ ")
    return rf"$^{{\mathrm{{{value}}}}}$"


def _overbar_text(value: str) -> str:
    return "".join(f"{char}\u0305" if not char.isspace() else char for char in value)


def resolve_line_style(name: str):
    return {
        "solid": "solid",
        "dashed": "dashed",
        "dotted": "dotted",
        "dashdot": "dashdot",
        "loosely dashed": (0, (5, 8)),
        "densely dashed": (0, (5, 2)),
        "loosely dotted": (0, (1, 7)),
        "densely dotted": (0, (1, 2)),
        "loosely dashdot": (0, (3, 6, 1, 6)),
        "densely dashdot": (0, (3, 2, 1, 2)),
    }.get(name, "solid")


def _annotation_display_geometry(ax, annotation: AnnotationConfig) -> tuple[float, float, float, float]:
    return annotation_display_geometry(ax, annotation)


def _rotated_endpoint(
    start_x: float,
    start_y: float,
    width_px: float,
    height_px: float,
    angle: float,
) -> tuple[float, float]:
    return rotated_endpoint(start_x, start_y, width_px, height_px, angle)


def _arrow_head_start(
    start: tuple[float, float],
    end: tuple[float, float],
    head_size: float = 12.0,
) -> tuple[float, float]:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = (dx**2 + dy**2) ** 0.5
    if length <= 1e-9:
        return start
    head_length = min(max(head_size, 1.0), length * 0.8)
    return end[0] - dx / length * head_length, end[1] - dy / length * head_length


__all__ = ["ArrowAnnotationArtist"]
