"""High-level rendering pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from matplotlib.figure import Figure
import pandas as pd

from ..data_parser import coerce_numeric
from ..plot_config import PALETTE, PlotConfig, SeriesConfig
from .artists import (
    draw_annotations,
    format_plot_text,
    legend_grid_layout,
    plot_broken_x_series,
    plot_broken_y_series,
    plot_error_bars,
    plot_series,
    resolve_explicit_legend,
)
from .axes import AxesBundle, apply_figure_layout, configure_axes, create_axes, finish_layout, scale_divisor


@dataclass
class RenderResult:
    figure: Figure
    warnings: list[str]
    annotation_artists: list[Any]
    legend_artist: Any | None = None


@dataclass(frozen=True)
class _PlotSummary:
    plotted: int
    legend_metadata: dict[str, tuple[str, str]]
    categorical_ticks: list[str] | None


def render_figure(
    df: pd.DataFrame,
    config: PlotConfig,
    series_configs: list[SeriesConfig],
) -> RenderResult:
    """Render a DataFrame according to the publication-figure configuration."""
    if not df.columns.is_unique:
        raise ValueError(
            "DataFrame column identifiers must be unique before rendering"
        )
    warnings: list[str] = []
    axes = create_axes(config, series_configs, warnings)
    try:
        if df.empty or not series_configs:
            _show_empty_message(axes, "Paste data and select X/Y columns")
            apply_figure_layout(axes.figure, config, warnings)
            return RenderResult(axes.figure, warnings, [])

        summary = _plot_all_series(df, config, series_configs, axes, warnings)
        if summary.plotted == 0:
            _show_empty_message(axes, "No plottable numeric data")
            apply_figure_layout(axes.figure, config, warnings)
            return RenderResult(axes.figure, warnings, [])

        configure_axes(axes, config, warnings, summary.categorical_ticks)
        legend_artist = _draw_legend(axes, config, summary)
        finish_layout(axes, config, warnings)
        annotation_artists = draw_annotations(
            axes.annotation_axis,
            config.annotations or [],
        )
        return RenderResult(
            axes.figure,
            warnings,
            annotation_artists,
            legend_artist,
        )
    except BaseException:
        axes.figure.clear()
        raise


def _show_empty_message(axes: AxesBundle, message: str) -> None:
    axes.main.text(
        0.5,
        0.5,
        message,
        ha="center",
        va="center",
        transform=axes.main.transAxes,
    )
    axes.main.set_axis_off()


def _plot_all_series(
    df: pd.DataFrame,
    config: PlotConfig,
    series_configs: list[SeriesConfig],
    axes: AxesBundle,
    warnings: list[str],
) -> _PlotSummary:
    plotted = 0
    legend_metadata: dict[str, tuple[str, str]] = {}
    categorical_ticks: list[str] | None = None
    x_divisor = scale_divisor(config.x_scale_divisor, "X", warnings)
    y_divisor = scale_divisor(config.y_scale_divisor, "Y", warnings)
    y2_divisor = scale_divisor(config.y2_scale_divisor, "Y2", warnings)

    for idx, series in enumerate(series_configs):
        if series.x not in df.columns or series.y not in df.columns:
            warnings.append(f"Missing column for series {series.label or series.y}.")
            continue

        target_ax = axes.right if series.y_axis == "right" and axes.right is not None else axes.main
        target_y_scale = config.y2_scale if target_ax is axes.right else config.y_scale
        target_y_divisor = y2_divisor if target_ax is axes.right else y_divisor
        prepared = _prepare_series_values(
            df,
            config,
            series,
            axes.broken_x,
            x_divisor,
            target_y_divisor,
            target_y_scale,
            plotted,
            warnings,
        )
        if prepared is None:
            continue
        x_values, y_values, valid, categorical_labels = prepared

        color = series.color or PALETTE[idx % len(PALETTE)]
        raw_label = format_plot_text(series.label or series.y)
        legend_token = f"pubfig_series_{idx}"
        legend_metadata[legend_token] = (series.y, raw_label)
        label = legend_token if series.show_in_legend else "_nolegend_"
        _draw_prepared_series(
            df,
            config,
            series,
            axes,
            target_ax,
            x_values,
            y_values,
            valid,
            target_y_divisor,
            color,
            label,
            warnings,
        )
        if categorical_labels is not None:
            categorical_ticks = categorical_labels
        plotted += 1

    return _PlotSummary(plotted, legend_metadata, categorical_ticks)


def _prepare_series_values(
    df: pd.DataFrame,
    config: PlotConfig,
    series: SeriesConfig,
    broken_x: bool,
    x_divisor: float,
    target_y_divisor: float,
    target_y_scale: str,
    plotted: int,
    warnings: list[str],
):
    raw_x = df[series.x]
    numeric_x = coerce_numeric(raw_x)
    normalized_x = raw_x.astype(str).str.strip()
    nonblank_x = normalized_x.ne("")
    categorical_labels: list[str] | None = None
    categorical_x = (
        series.plot_type == "bar"
        and nonblank_x.any()
        and not numeric_x[nonblank_x].notna().all()
    )
    if categorical_x:
        if config.x_scale != "linear":
            warnings.append(f"{series.x}: categorical bar data requires a linear X scale.")
            return None
        if broken_x:
            warnings.append(f"{series.x}: categorical bar data is not supported on a broken X axis.")
            return None
        categorical_labels = list(dict.fromkeys(normalized_x[nonblank_x].tolist()))
        category_positions = {label: position for position, label in enumerate(categorical_labels)}
        x = normalized_x.map(category_positions).astype(float)
    else:
        x = numeric_x / x_divisor
    y = coerce_numeric(df[series.y]) / target_y_divisor
    valid = x.notna() & y.notna()

    if config.x_scale == "log":
        invalid = valid & (x <= 0)
        if invalid.any():
            warnings.append(f"{series.x}: {int(invalid.sum())} non-positive x values skipped for log scale.")
        valid &= x > 0
    if target_y_scale == "log":
        invalid = valid & (y <= 0)
        if invalid.any():
            warnings.append(f"{series.y}: {int(invalid.sum())} non-positive y values skipped for log scale.")
        valid &= y > 0

    x_values = x[valid]
    y_values = y[valid] + series.y_offset + config.y_offset_step * plotted
    if x_values.empty:
        warnings.append(f"No plottable numeric data for {series.label or series.y}.")
        return None
    return x_values, y_values, valid, categorical_labels


def _draw_prepared_series(
    df: pd.DataFrame,
    config: PlotConfig,
    series: SeriesConfig,
    axes: AxesBundle,
    target_ax,
    x_values,
    y_values,
    valid,
    target_y_divisor: float,
    color: str,
    label: str,
    warnings: list[str],
) -> None:
    plot_type = series.plot_type
    if axes.broken_y and series.y_axis != "right":
        plot_broken_y_series(
            axes.upper,
            x_values,
            y_values,
            series,
            color,
            label,
            plot_type,
            config.y_break_upper_min,
            config.y_break_upper_max,
        )
        plot_broken_y_series(
            axes.main,
            x_values,
            y_values,
            series,
            color,
            "_nolegend_",
            plot_type,
            config.y_break_lower_min,
            config.y_break_lower_max,
        )
        if getattr(series, "error_column", ""):
            warnings.append(f"Error bars are not drawn on broken axes ({series.label or series.y}).")
    elif axes.broken_x:
        plot_broken_x_series(
            axes.left,
            x_values,
            y_values,
            series,
            color,
            label,
            plot_type,
            config.x_break_left_min,
            config.x_break_left_max,
        )
        plot_broken_x_series(
            axes.main,
            x_values,
            y_values,
            series,
            color,
            "_nolegend_",
            plot_type,
            config.x_break_right_min,
            config.x_break_right_max,
        )
        if getattr(series, "error_column", ""):
            warnings.append(f"Error bars are not drawn on broken axes ({series.label or series.y}).")
    else:
        plot_series(target_ax, x_values, y_values, series, color, label, plot_type)
        plot_error_bars(
            target_ax,
            df,
            series,
            x_values,
            y_values,
            valid,
            target_y_divisor,
            color,
            warnings,
        )


def _draw_legend(axes: AxesBundle, config: PlotConfig, summary: _PlotSummary):
    if not config.legend or summary.plotted <= 0:
        return None

    legend_axis = axes.title_axis
    handles, tokens = legend_axis.get_legend_handles_labels()
    if axes.right is not None:
        right_handles, right_tokens = axes.right.get_legend_handles_labels()
        handles += right_handles
        tokens += right_tokens
    records = [
        (summary.legend_metadata[token][0], handle, summary.legend_metadata[token][1])
        for handle, token in zip(handles, tokens)
        if token in summary.legend_metadata
    ]
    handles = [record[1] for record in records]
    labels = [record[2] for record in records]
    effective_row_lengths = config.legend_row_lengths
    if config.legend_entries is not None:
        handles, labels, effective_row_lengths = resolve_explicit_legend(
            records,
            config.legend_entries,
            config.legend_row_lengths,
        )
    handles, labels, legend_ncol = legend_grid_layout(handles, labels, effective_row_lengths)
    if not handles:
        return None

    legend_kwargs = {
        "frameon": False,
        "fontsize": config.legend_size,
        "ncol": legend_ncol,
        "columnspacing": 0.7,
        "handletextpad": 0.35,
        "handlelength": 1.8,
    }
    if config.legend_anchor_x is not None and config.legend_anchor_y is not None:
        legend_kwargs.update(
            {
                "loc": "upper left",
                "bbox_to_anchor": (config.legend_anchor_x, config.legend_anchor_y),
                "bbox_transform": legend_axis.transAxes,
            }
        )
    legend_artist = legend_axis.legend(handles, labels, **legend_kwargs)
    legend_artist.set_picker(True)
    legend_artist.set_in_layout(False)
    if axes.broken_x or axes.broken_y:
        sibling_zorder = max(
            (
                axis.get_zorder()
                for axis in axes.figure.axes
                if axis is not legend_axis
            ),
            default=legend_axis.get_zorder(),
        )
        legend_axis.set_zorder(sibling_zorder + 1)
        legend_axis.patch.set_visible(False)
        legend_artist.set_zorder(1000)
    return legend_artist


__all__ = ["RenderResult", "render_figure"]
