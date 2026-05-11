"""Matplotlib rendering and export for Graph_drawer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import matplotlib as mpl
from matplotlib.figure import Figure
from matplotlib.patches import FancyArrowPatch, Rectangle
from matplotlib.ticker import AutoMinorLocator, LogLocator, MultipleLocator, NullLocator
from matplotlib.transforms import Affine2D, IdentityTransform
import pandas as pd

from data_parser import coerce_numeric
from plot_config import AnnotationConfig, PALETTE, PlotConfig, SeriesConfig


@dataclass
class RenderResult:
    figure: Figure
    warnings: list[str]
    annotation_artists: list[Any]
    legend_artist: Any | None = None


class ArrowAnnotationArtist:
    def __init__(self, shaft_artist: Any, head_artist: FancyArrowPatch, tip: tuple[float, float]) -> None:
        self.shaft_artist = shaft_artist
        self.head_artist = head_artist
        self._tip = tip
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
        head_start = _arrow_head_start(start, self._tip)
        self.shaft_artist.xy = head_start
        self.head_artist.set_positions(head_start, self._tip)


def mm_to_inch(value_mm: float) -> float:
    return value_mm / 25.4


def _use_broken_y_axis(config: PlotConfig, right_axis_needed: bool, warnings: list[str]) -> bool:
    if not config.y_break_enabled:
        return False
    if right_axis_needed:
        warnings.append("Y broken axis is disabled when Y2 series are plotted.")
        return False
    if config.y_scale != "linear":
        warnings.append("Y broken axis is only available for linear Y scale.")
        return False
    values = (
        config.y_break_lower_min,
        config.y_break_lower_max,
        config.y_break_upper_min,
        config.y_break_upper_max,
    )
    if any(value is None for value in values):
        warnings.append("Y broken axis needs lower and upper Y ranges.")
        return False
    lower_min, lower_max, upper_min, upper_max = values
    if not (lower_min < lower_max < upper_min < upper_max):
        warnings.append("Y broken axis ranges must be lower min < lower max < upper min < upper max.")
        return False
    return True


def _plot_series(target_ax, x_values, y_values, series: SeriesConfig, color: str, label: str, plot_type: str) -> None:
    alpha = series.alpha
    if plot_type == "scatter":
        target_ax.scatter(x_values, y_values, s=series.marker_size**2, color=color, marker=series.marker, label=label, alpha=alpha)
    elif plot_type == "bar":
        target_ax.bar(x_values, y_values, color=color, alpha=alpha, label=label)
    elif plot_type == "line+marker":
        target_ax.plot(
            x_values,
            y_values,
            color=color,
            marker=series.marker,
            linewidth=series.line_width,
            markersize=series.marker_size,
            label=label,
            alpha=alpha,
        )
    elif plot_type == "step":
        target_ax.step(x_values, y_values, where="mid", color=color, linewidth=series.line_width, label=label, alpha=alpha)
    elif plot_type == "area":
        target_ax.fill_between(x_values, y_values, color=color, alpha=alpha * 0.5, label=label)
        target_ax.plot(x_values, y_values, color=color, linewidth=series.line_width, alpha=alpha)
    elif plot_type == "stem":
        markerline, stemlines, baseline = target_ax.stem(x_values, y_values, label=label)
        markerline.set_marker(series.marker)
        markerline.set_markersize(series.marker_size)
        markerline.set_color(color)
        markerline.set_alpha(alpha)
        stemlines.set_color(color)
        stemlines.set_linewidth(series.line_width)
        stemlines.set_alpha(alpha)
        baseline.set_color("0.35")
        baseline.set_linewidth(0.5)
    else:
        target_ax.plot(x_values, y_values, color=color, linewidth=series.line_width, label=label, alpha=alpha)


def _plot_broken_y_series(
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
        _plot_series(target_ax, x_values[in_range], y_values[in_range], series, color, label, plot_type)
        return

    y_masked = y_values.where(in_range)
    _plot_series(target_ax, x_values, y_masked, series, color, label, plot_type)


def _broken_y_height_ratios(config: PlotConfig) -> tuple[float, float]:
    upper_range = max(float(config.y_break_upper_max - config.y_break_upper_min), 1e-9)
    lower_range = max(float(config.y_break_lower_max - config.y_break_lower_min), 1e-9)
    upper_fraction = upper_range / (upper_range + lower_range)
    upper_fraction = min(0.6, max(0.32, upper_fraction))
    return upper_fraction, 1.0 - upper_fraction


def render_figure(df: pd.DataFrame, config: PlotConfig, series_configs: list[SeriesConfig]) -> RenderResult:
    warnings: list[str] = []
    _apply_style(warnings)

    figure_width_mm, figure_height_mm = _figure_size_mm(config)
    fig = Figure(
        figsize=(
            mm_to_inch(figure_width_mm),
            mm_to_inch(figure_height_mm),
        ),
        dpi=config.dpi,
        facecolor="none" if config.transparent else "white",
    )
    right_axis_needed = any(series.y_axis == "right" for series in series_configs)
    broken_y = _use_broken_y_axis(config, right_axis_needed, warnings)
    if broken_y:
        gridspec = fig.add_gridspec(2, 1, height_ratios=_broken_y_height_ratios(config), hspace=max(config.y_break_gap, 0.01))
        ax_upper = fig.add_subplot(gridspec[0])
        ax = fig.add_subplot(gridspec[1], sharex=ax_upper)
        plot_axes = [ax_upper, ax]
        ax_right = None
    else:
        ax_upper = None
        ax = fig.add_subplot(111)
        plot_axes = [ax]
        ax_right = ax.twinx() if right_axis_needed else None

    if df.empty or not series_configs:
        ax.text(0.5, 0.5, "Paste data and select X/Y columns", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
        _apply_figure_layout(fig, config, warnings)
        return RenderResult(fig, warnings, [])

    plotted = 0
    for idx, series in enumerate(series_configs):
        if series.x not in df.columns or series.y not in df.columns:
            warnings.append(f"Missing column for series {series.label or series.y}.")
            continue

        x = coerce_numeric(df[series.x])
        y = coerce_numeric(df[series.y])
        valid = x.notna() & y.notna()

        if config.x_scale == "log":
            invalid = valid & (x <= 0)
            if invalid.any():
                warnings.append(f"{series.x}: {int(invalid.sum())} non-positive x values skipped for log scale.")
            valid &= x > 0
        target_ax = ax_right if series.y_axis == "right" and ax_right is not None else ax
        target_y_scale = config.y2_scale if target_ax is ax_right else config.y_scale

        if target_y_scale == "log":
            invalid = valid & (y <= 0)
            if invalid.any():
                warnings.append(f"{series.y}: {int(invalid.sum())} non-positive y values skipped for log scale.")
            valid &= y > 0

        x_values = x[valid]
        y_values = y[valid] + series.y_offset + config.y_offset_step * plotted
        if x_values.empty:
            warnings.append(f"No plottable numeric data for {series.label or series.y}.")
            continue

        color = series.color or PALETTE[idx % len(PALETTE)]
        raw_label = _format_plot_text(series.label or series.y)
        label = raw_label if series.show_in_legend else "_nolegend_"
        plot_type = series.plot_type

        if broken_y and series.y_axis != "right":
            _plot_broken_y_series(ax_upper, x_values, y_values, series, color, label, plot_type, config.y_break_upper_min, config.y_break_upper_max)
            _plot_broken_y_series(ax, x_values, y_values, series, color, "_nolegend_", plot_type, config.y_break_lower_min, config.y_break_lower_max)
        else:
            _plot_series(target_ax, x_values, y_values, series, color, label, plot_type)
        plotted += 1

    if plotted == 0:
        ax.text(0.5, 0.5, "No plottable numeric data", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
        _apply_figure_layout(fig, config, warnings)
        return RenderResult(fig, warnings, [])

    title_axis = ax_upper if broken_y and ax_upper is not None else ax
    title_axis.set_title(_format_plot_text(config.title), fontsize=config.title_size)
    ax.set_xlabel(_format_plot_text(config.x_label), fontsize=config.axis_size)
    ax.set_ylabel("")
    for plot_axis in plot_axes:
        plot_axis.set_xscale(config.x_scale)
        plot_axis.set_yscale(config.y_scale)
    if ax_right is not None:
        ax_right.set_ylabel(_format_plot_text(config.y2_label), fontsize=config.axis_size)
        ax_right.set_yscale(config.y2_scale)

    for axis in plot_axes + ([ax_right] if ax_right is not None else []):
        axis.tick_params(direction="in", top=True, right=True, labelsize=config.tick_size, width=0.5)
        axis.tick_params(which="minor", direction="in", top=True, right=True, width=0.4)

        for side in ("top", "right", "bottom", "left"):
            axis.spines[side].set_visible(True)
            axis.spines[side].set_linewidth(0.5)

    ax.tick_params(labelbottom=config.show_x_tick_labels, labelleft=config.show_y_tick_labels)
    if broken_y and ax_upper is not None:
        ax_upper.tick_params(axis="x", which="both", bottom=False, labelbottom=False, top=True)
        ax_upper.tick_params(labelleft=config.show_y_tick_labels)
        ax.tick_params(axis="x", which="both", top=False, labelbottom=config.show_x_tick_labels)
        ax_upper.spines["bottom"].set_visible(False)
        ax.spines["top"].set_visible(False)
    if ax_right is not None:
        ax.tick_params(axis="y", which="both", right=False, labelright=False)
        ax.spines["right"].set_visible(False)

        ax_right.tick_params(
            axis="y",
            which="both",
            left=False,
            labelleft=False,
            right=True,
            labelright=config.show_y2_tick_labels,
            colors=config.y2_axis_color,
        )
        ax_right.spines["left"].set_visible(False)
        ax_right.yaxis.label.set_color(config.y2_axis_color)
        ax_right.spines["right"].set_edgecolor(config.y2_axis_color)

    if broken_y and ax_upper is not None:
        ax.set_ylim(config.y_break_lower_min, config.y_break_lower_max)
        ax_upper.set_ylim(config.y_break_upper_min, config.y_break_upper_max)
        if config.x_min is not None or config.x_max is not None:
            _apply_x_limits(ax, config)
            _apply_x_limits(ax_upper, config)
    else:
        _apply_limits(ax, config, warnings, "left")
    if ax_right is not None:
        _apply_limits(ax_right, config, warnings, "right")
    _apply_tick_intervals(ax, config, warnings, "left")
    if broken_y and ax_upper is not None:
        _apply_tick_intervals(ax_upper, config, warnings, "left")
        _prune_broken_y_boundary_ticks(ax_upper, ax)
        ax_upper.tick_params(axis="x", which="both", bottom=False, labelbottom=False, top=True)
        ax.tick_params(axis="x", which="both", top=False, labelbottom=config.show_x_tick_labels)
    if ax_right is not None:
        _apply_tick_intervals(ax_right, config, warnings, "right")
    for axis in plot_axes + ([ax_right] if ax_right is not None else []):
        axis.tick_params(which="minor", direction="in", width=0.4, length=2.2)

    if config.grid:
        for plot_axis in plot_axes:
            plot_axis.grid(True, which="major", linestyle="-", linewidth=0.4, alpha=0.25)
            plot_axis.grid(True, which="minor", linestyle=":", linewidth=0.3, alpha=0.18)

    legend_artist = None
    if config.legend and plotted > 0:
        handles, labels = (ax_upper if broken_y and ax_upper is not None else ax).get_legend_handles_labels()
        if ax_right is not None:
            right_handles, right_labels = ax_right.get_legend_handles_labels()
            handles += right_handles
            labels += right_labels
        legend_kwargs = {"frameon": False, "fontsize": config.legend_size}
        if config.legend_anchor_x is not None and config.legend_anchor_y is not None:
            legend_kwargs.update({"loc": "upper left", "bbox_to_anchor": (config.legend_anchor_x, config.legend_anchor_y), "bbox_transform": ax.transAxes})
        legend_artist = ax.legend(handles, labels, **legend_kwargs)
        legend_artist.set_picker(True)
        legend_artist.set_in_layout(False)

    if broken_y:
        _apply_figure_layout(fig, config, warnings)
        fig.subplots_adjust(hspace=max(config.y_break_gap, 0.01))
        _draw_fixed_y_label(fig, ax, config, warnings, ax_upper)
        _draw_y_break_marks(ax_upper, ax)
    else:
        _apply_figure_layout(fig, config, warnings)
        _draw_fixed_y_label(fig, ax, config, warnings)
    annotation_artists = _draw_annotations(ax, config.annotations or [])
    return RenderResult(fig, warnings, annotation_artists, legend_artist)


def export_figure(fig: Figure, path: str | Path, config: PlotConfig) -> None:
    save_kwargs = {
        "dpi": config.dpi,
        "facecolor": "none" if config.transparent else "white",
        "transparent": config.transparent,
    }
    if config.trim_whitespace:
        save_kwargs["bbox_inches"] = "tight"
    fig.savefig(path, **save_kwargs)


def _apply_style(warnings: list[str]) -> None:
    rc_params = {
        "font.size": 8,
        "font.family": ["Arial", "Segoe UI Symbol", "DejaVu Sans"],
        "pdf.fonttype": 42,
        "svg.fonttype": "none",
        "axes.linewidth": 0.5,
        "lines.linewidth": 1.0,
        "xtick.major.width": 0.5,
        "ytick.major.width": 0.5,
        "axes.prop_cycle": mpl.cycler(color=PALETTE),
    }
    mpl.rcParams.update(rc_params)

    try:
        import scienceplots  # noqa: F401

        mpl.style.use(["science", "no-latex", "bright"])
        mpl.rcParams.update(rc_params)
    except Exception as exc:
        message = str(exc).splitlines()[0]
        warnings.append(f"SciencePlots style unavailable; using built-in style. {message}")


def _apply_figure_layout(fig: Figure, config: PlotConfig, warnings: list[str]) -> None:
    total_width, total_height = _figure_size_mm(config)
    total_width = max(total_width, 1e-9)
    total_height = max(total_height, 1e-9)
    if config.fixed_plot_area:
        left = (config.pad_left_mm + config.plot_margin_left_mm) / total_width
        right = left + config.plot_width_mm / total_width
        bottom = (config.pad_bottom_mm + config.plot_margin_bottom_mm) / total_height
        top = bottom + config.plot_height_mm / total_height
        if left < right and bottom < top:
            fig.subplots_adjust(left=left, right=right, bottom=bottom, top=top)
            if right > (config.pad_left_mm + config.width_mm) / total_width or top > (
                config.pad_bottom_mm + config.height_mm
            ) / total_height:
                warnings.append("Plot box exceeds canvas; increase canvas size or reduce plot size/margins.")
            return
        warnings.append("Fixed plot area margins are too large; using auto layout.")

    if not any((config.pad_left_mm, config.pad_right_mm, config.pad_top_mm, config.pad_bottom_mm)):
        fig.tight_layout()
        return
    rect = (
        config.pad_left_mm / total_width,
        config.pad_bottom_mm / total_height,
        (config.pad_left_mm + config.width_mm) / total_width,
        (config.pad_bottom_mm + config.height_mm) / total_height,
    )
    fig.tight_layout(rect=rect)


def _draw_fixed_y_label(fig: Figure, ax, config: PlotConfig, warnings: list[str], ax_upper=None) -> None:
    if not config.y_label:
        return
    total_width, _ = _figure_size_mm(config)
    total_width = max(total_width, 1e-9)
    lower_pos = ax.get_position()
    upper_pos = ax_upper.get_position() if ax_upper is not None else lower_pos
    label_half_width_mm = config.axis_size * 25.4 / 72.0 * 0.6
    requested_x = lower_pos.x0 - (max(config.y_label_offset_mm, 0.0) + label_half_width_mm) / total_width
    min_visible_x = label_half_width_mm / total_width
    x = max(requested_x, min_visible_x)
    if x != requested_x:
        warnings.append("Y label gap exceeds the left canvas space; increase Plot left margin or lower Y label gap.")
    y = (lower_pos.y0 + upper_pos.y1) * 0.5
    label = fig.text(
        x,
        y,
        _format_plot_text(config.y_label),
        rotation=90,
        ha="center",
        va="center",
        fontsize=config.axis_size,
    )
    label.set_clip_on(False)
    label.set_in_layout(False)


def _figure_size_mm(config: PlotConfig) -> tuple[float, float]:
    return (
        max(config.width_mm + config.pad_left_mm + config.pad_right_mm, 1.0),
        max(config.height_mm + config.pad_top_mm + config.pad_bottom_mm, 1.0),
    )


def _draw_annotations(ax, annotations: list[AnnotationConfig]) -> list[Any]:
    artists: list[Any] = []
    for annotation in annotations:
        start_x, start_y, width_px, height_px = _annotation_display_geometry(ax, annotation)
        text = _format_plot_text(annotation.text)
        alpha = max(0.0, min(annotation.alpha, 1.0))
        line_style = _annotation_line_style(annotation.line_style)
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
            head_start = _arrow_head_start((start_x, start_y), (end_x, end_y))
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
                mutation_scale=max(annotation.arrow_head_size, 1.0),
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
            artists.append(ArrowAnnotationArtist(shaft_artist, head_artist, (end_x, end_y)))
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


def _format_plot_text(text: str) -> str:
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
    formatted = re.sub(r"_([0-9]+(?:\.[0-9]+)?|[A-Za-z])", lambda match: _subscript_math(match.group(1)), formatted)
    return re.sub(r"\^([+-]?[0-9]+(?:\.[0-9]+)?|[A-Za-z])", lambda match: _superscript_math(match.group(1)), formatted)


def _subscript_math(value: str) -> str:
    value = value.replace("\\", r"\\").replace(" ", r"\ ")
    return rf"$_{{\mathrm{{{value}}}}}$"


def _superscript_math(value: str) -> str:
    value = value.replace("\\", r"\\").replace(" ", r"\ ")
    return rf"$^{{\mathrm{{{value}}}}}$"


def _overbar_text(value: str) -> str:
    return "".join(f"{char}\u0305" if not char.isspace() else char for char in value)


def _annotation_line_style(name: str):
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


def _draw_y_break_marks(ax_upper, ax_lower) -> None:
    size = 0.012
    kwargs = {
        "color": "black",
        "clip_on": False,
        "linewidth": 0.65,
        "solid_capstyle": "round",
        "zorder": 20,
    }
    for x in (0.0, 1.0):
        ax_upper.plot((x - size, x + size), (-size, size), transform=ax_upper.transAxes, **kwargs)
        ax_lower.plot((x - size, x + size), (1 - size, 1 + size), transform=ax_lower.transAxes, **kwargs)


def _prune_broken_y_boundary_ticks(ax_upper, ax_lower) -> None:
    upper_bottom, upper_top = ax_upper.get_ylim()
    lower_bottom, lower_top = ax_lower.get_ylim()
    upper_tol = max(abs(upper_top - upper_bottom), 1.0) * 1e-9
    lower_tol = max(abs(lower_top - lower_bottom), 1.0) * 1e-9
    ax_upper.set_yticks(
        [tick for tick in ax_upper.get_yticks() if upper_bottom < tick <= upper_top and abs(tick - upper_bottom) > upper_tol]
    )
    ax_lower.set_yticks(
        [tick for tick in ax_lower.get_yticks() if lower_bottom <= tick < lower_top and abs(tick - lower_top) > lower_tol]
    )
    ax_upper.set_ylim(upper_bottom, upper_top)
    ax_lower.set_ylim(lower_bottom, lower_top)


def _annotation_display_geometry(ax, annotation: AnnotationConfig) -> tuple[float, float, float, float]:
    axes_bbox = ax.get_window_extent()
    unit_px = max(min(axes_bbox.width, axes_bbox.height), 1.0)
    start_x, start_y = ax.transAxes.transform((annotation.x, annotation.y))
    return start_x, start_y, annotation.width * unit_px, annotation.height * unit_px


def _rotated_endpoint(start_x: float, start_y: float, width_px: float, height_px: float, angle: float) -> tuple[float, float]:
    if not angle:
        return start_x + width_px, start_y + height_px
    point = Affine2D().rotate_deg_around(0.0, 0.0, angle).transform((width_px, height_px))
    return start_x + point[0], start_y + point[1]


def _arrow_head_start(start: tuple[float, float], end: tuple[float, float]) -> tuple[float, float]:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = (dx**2 + dy**2) ** 0.5
    if length <= 1e-9:
        return start
    head_length = min(12.0, length * 0.8)
    return end[0] - dx / length * head_length, end[1] - dy / length * head_length


def _apply_limits(ax, config: PlotConfig, warnings: list[str], side: str) -> None:
    _apply_x_limits(ax, config)

    y_min = config.y2_min if side == "right" else config.y_min
    y_max = config.y2_max if side == "right" else config.y_max
    y_scale = config.y2_scale if side == "right" else config.y_scale
    if y_min is not None or y_max is not None:
        bottom, top = ax.get_ylim()
        resolved_bottom = _resolve_y_limit(y_min, y_scale, warnings, side, "min") if y_min is not None else None
        resolved_top = _resolve_y_limit(y_max, y_scale, warnings, side, "max") if y_max is not None else None
        bottom = resolved_bottom if resolved_bottom is not None else bottom
        top = resolved_top if resolved_top is not None else top
        if bottom < top:
            ax.set_ylim(bottom, top)


def _apply_x_limits(ax, config: PlotConfig) -> None:
    if config.x_min is not None or config.x_max is not None:
        left, right = ax.get_xlim()
        left = config.x_min if config.x_min is not None else left
        right = config.x_max if config.x_max is not None else right
        if config.x_scale == "log" and left <= 0:
            left = ax.get_xlim()[0]
        if config.x_scale == "log" and right <= 0:
            right = ax.get_xlim()[1]
        if left < right:
            ax.set_xlim(left, right)


def _resolve_y_limit(value: float, scale: str, warnings: list[str], side: str, bound: str) -> float | None:
    if scale != "log":
        return value
    try:
        return 10 ** value
    except OverflowError:
        label = "Y2" if side == "right" else "Y"
        warnings.append(f"{label} {bound} log exponent is too large; keeping automatic limit.")
        return None


def _apply_tick_intervals(ax, config: PlotConfig, warnings: list[str], side: str) -> None:
    if config.x_tick_interval is not None:
        if config.x_scale == "log":
            warnings.append("X tick interval is ignored on log scale.")
        elif config.x_tick_interval > 0:
            ax.xaxis.set_major_locator(MultipleLocator(config.x_tick_interval))
        else:
            warnings.append("X tick interval must be positive.")
    _apply_minor_locator(ax.xaxis, config.x_scale, config.x_minor_divisions)

    y_interval = config.y2_tick_interval if side == "right" else config.y_tick_interval
    y_scale = config.y2_scale if side == "right" else config.y_scale
    y_minor_divisions = config.y2_minor_divisions if side == "right" else config.y_minor_divisions
    label = "Y2" if side == "right" else "Y"
    if y_interval is not None:
        if y_scale == "log":
            warnings.append(f"{label} tick interval is ignored on log scale.")
        elif y_interval > 0:
            ax.yaxis.set_major_locator(MultipleLocator(y_interval))
        else:
            warnings.append(f"{label} tick interval must be positive.")
    _apply_minor_locator(ax.yaxis, y_scale, y_minor_divisions)


def _apply_minor_locator(axis_obj, scale: str, divisions: int) -> None:
    divisions = max(0, int(divisions))
    if divisions == 0:
        axis_obj.set_minor_locator(NullLocator())
    elif scale == "log":
        axis_obj.set_minor_locator(LogLocator(base=10.0, subs=_log_minor_subs(divisions), numticks=100))
    else:
        axis_obj.set_minor_locator(AutoMinorLocator(divisions))


def _log_minor_subs(divisions: int) -> tuple[float, ...]:
    if divisions <= 1:
        return ()
    return tuple(10 ** (idx / divisions) for idx in range(1, divisions))
