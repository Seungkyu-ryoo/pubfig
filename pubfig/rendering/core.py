"""High-level rendering pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

from matplotlib.figure import Figure
import numpy as np
import pandas as pd

from ..data_parser import coerce_numeric
from ..fitting import LinearFitError, LinearFitResult, linear_fit
from ..plot_config import LINE_STYLES, PALETTE, PlotConfig, SeriesConfig
from .artists import (
    _legend_grid_layout_with_styles,
    _resolve_explicit_legend_with_styles,
    apply_legend_text_styles,
    draw_annotations,
    format_plot_text,
    legend_handler_map,
    plot_broken_x_series,
    plot_broken_y_series,
    plot_error_bars,
    plot_series,
)
from .axes import AxesBundle, apply_figure_layout, configure_axes, create_axes, finish_layout


DEFAULT_PREVIEW_POINT_LIMIT = 10_000


@dataclass(frozen=True)
class RenderOptions:
    """Transient rendering policy that is deliberately not project data.

    Full-resolution rendering remains the default.  A preview may filter data
    that Matplotlib would clip outside explicit X limits and reduce the number
    of displayed vertices with an extrema-preserving envelope.  Calculations
    such as linear fitting always receive the complete finite series.
    """

    preview: bool = False
    max_points_per_series: int | None = None
    numeric_cache: NumericColumnCache | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    cache_source: object | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        limit = self.max_points_per_series
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 16
        ):
            raise ValueError("max_points_per_series must be an integer of at least 16")
        if self.numeric_cache is not None and not isinstance(
            self.numeric_cache,
            NumericColumnCache,
        ):
            raise TypeError("numeric_cache must be NumericColumnCache")
        if self.cache_source is not None and self.numeric_cache is None:
            raise ValueError("cache_source requires numeric_cache")

    @classmethod
    def for_preview(
        cls,
        max_points_per_series: int = DEFAULT_PREVIEW_POINT_LIMIT,
        *,
        numeric_cache: NumericColumnCache | None = None,
        cache_source: object | None = None,
    ) -> RenderOptions:
        return cls(
            preview=True,
            max_points_per_series=max_points_per_series,
            numeric_cache=numeric_cache,
            cache_source=cache_source,
        )

    @property
    def preview_point_limit(self) -> int | None:
        if not self.preview:
            return None
        return self.max_points_per_series or DEFAULT_PREVIEW_POINT_LIMIT


@dataclass
class RenderResult:
    figure: Figure
    warnings: list[str]
    annotation_artists: list[Any]
    legend_artist: Any | None = None
    linear_fit_results: dict[str, LinearFitResult] = field(default_factory=dict)


@dataclass(frozen=True)
class _PlotSummary:
    plotted: int
    legend_metadata: dict[str, tuple[str, str, bool]]
    categorical_ticks: list[str] | None
    linear_fit_results: dict[str, LinearFitResult]


@dataclass(frozen=True)
class _PreparedSeries:
    """Full calculation values paired with an optional reduced display view."""

    x_values: pd.Series
    y_values: pd.Series
    valid: pd.Series
    display_x_values: pd.Series
    display_y_values: pd.Series
    display_valid: pd.Series
    categorical_labels: list[str] | None
    display_y_offset: float


class NumericColumnCache:
    """Reusable numeric conversions for one explicitly managed data source.

    Call :meth:`clear` whenever cell values change in place.  :meth:`bind`
    additionally discards cached arrays when the source object, row count, or
    ordered column identifiers differ, protecting graph/sheet switches and
    structural table edits even if the caller forgets to invalidate manually.
    """

    def __init__(self) -> None:
        self._dataframe: pd.DataFrame | None = None
        self._source: object | None = None
        self._schema: tuple[int, tuple[object, ...]] | None = None
        self._numeric: dict[str, pd.Series] = {}
        self._finite: dict[str, np.ndarray] = {}

    def bind(
        self,
        dataframe: pd.DataFrame,
        *,
        source: object | None = None,
    ) -> NumericColumnCache:
        """Bind a plot view, clearing values when its source/schema changed."""

        if not isinstance(dataframe, pd.DataFrame):
            raise TypeError("NumericColumnCache.bind expects a pandas DataFrame")
        effective_source = dataframe if source is None else source
        schema = (len(dataframe), tuple(dataframe.columns))
        if self._source is not effective_source or self._schema != schema:
            self.clear()
        self._source = effective_source
        self._schema = schema
        self._dataframe = dataframe
        return self

    def clear(self) -> None:
        """Invalidate every converted column after an in-place data edit."""

        self._dataframe = None
        self._source = None
        self._schema = None
        self._numeric.clear()
        self._finite.clear()

    invalidate = clear

    def numeric(self, column: str) -> pd.Series:
        if self._dataframe is None:
            raise RuntimeError("NumericColumnCache must be bound before use")
        cached = self._numeric.get(column)
        if cached is not None:
            return cached
        converted = coerce_numeric(self._dataframe[column])
        # Rendering is positional.  A private RangeIndex prevents surprising
        # alignment (and duplicate-index failures) without copying the values.
        numeric = pd.Series(
            converted.to_numpy(copy=False),
            index=pd.RangeIndex(len(converted)),
            name=converted.name,
            copy=False,
        )
        self._numeric[column] = numeric
        return numeric

    def finite(self, column: str) -> np.ndarray:
        cached = self._finite.get(column)
        if cached is not None:
            return cached
        values = self.numeric(column).to_numpy(
            dtype=float,
            na_value=np.nan,
            copy=False,
        )
        finite = np.isfinite(values)
        self._finite[column] = finite
        return finite


def render_figure(
    df: pd.DataFrame,
    config: PlotConfig,
    series_configs: list[SeriesConfig],
    *,
    options: RenderOptions | None = None,
) -> RenderResult:
    """Render a DataFrame according to the publication-figure configuration."""
    if not df.columns.is_unique:
        raise ValueError(
            "DataFrame column identifiers must be unique before rendering"
        )
    if options is None:
        options = RenderOptions()
    elif not isinstance(options, RenderOptions):
        raise TypeError("options must be RenderOptions")
    warnings: list[str] = []
    axes = create_axes(config, series_configs, warnings)
    try:
        if df.empty or not series_configs:
            _show_empty_message(axes, "Paste data and select X/Y columns")
            apply_figure_layout(axes.figure, config, warnings)
            annotation_artists = draw_annotations(
                axes.annotation_axis,
                config.annotations or [],
            )
            return RenderResult(axes.figure, warnings, annotation_artists)

        summary = _plot_all_series(
            df,
            config,
            series_configs,
            axes,
            warnings,
            options,
        )
        if summary.plotted == 0:
            _show_empty_message(axes, "No plottable numeric data")
            apply_figure_layout(axes.figure, config, warnings)
            annotation_artists = draw_annotations(
                axes.annotation_axis,
                config.annotations or [],
            )
            return RenderResult(axes.figure, warnings, annotation_artists)

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
            summary.linear_fit_results,
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
    options: RenderOptions,
) -> _PlotSummary:
    plotted = 0
    legend_metadata: dict[str, tuple[str, str, bool]] = {}
    categorical_ticks: list[str] | None = None
    linear_fit_results: dict[str, LinearFitResult] = {}
    numeric_columns = (options.numeric_cache or NumericColumnCache()).bind(
        df,
        source=options.cache_source,
    )

    for idx, series in enumerate(series_configs):
        if series.x not in df.columns or series.y not in df.columns:
            warnings.append(f"Missing column for series {series.label or series.y}.")
            continue

        target_ax = axes.right if series.y_axis == "right" and axes.right is not None else axes.main
        target_y_scale = config.y2_scale if target_ax is axes.right else config.y_scale
        prepared = _prepare_series_values(
            df,
            config,
            series,
            axes.broken_x,
            target_y_scale,
            plotted,
            warnings,
            numeric_columns,
            options,
        )
        if prepared is None:
            continue

        color = series.color or PALETTE[idx % len(PALETTE)]
        raw_label = str(series.label or series.y)
        legend_token = f"pubfig_series_{idx}"
        legend_metadata[legend_token] = (
            series.y,
            raw_label,
            bool(series.show_in_legend),
        )
        if options.preview and len(prepared.display_x_values) < len(
            prepared.x_values
        ):
            _preserve_full_data_limits(
                axes,
                target_ax,
                series,
                prepared.x_values,
                prepared.y_values,
            )
        _draw_prepared_series(
            df,
            config,
            series,
            axes,
            target_ax,
            prepared.display_x_values,
            prepared.display_y_values,
            prepared.display_valid,
            target_y_scale,
            color,
            legend_token,
            warnings,
            numeric_columns,
        )
        fit_result = _draw_linear_fit_for_series(
            config,
            series,
            axes,
            target_ax,
            prepared.x_values,
            prepared.y_values - prepared.display_y_offset,
            prepared.display_y_offset,
            color,
            target_y_scale,
            prepared.categorical_labels is not None,
            idx,
            warnings,
        )
        if fit_result is not None:
            if series.y in linear_fit_results:
                warnings.append(
                    f"Linear fit for {series.label or series.y}: duplicate Y column; "
                    "the first result is retained in the result summary."
                )
            else:
                linear_fit_results[series.y] = fit_result
        if prepared.categorical_labels is not None:
            categorical_ticks = prepared.categorical_labels
        plotted += 1

    return _PlotSummary(
        plotted,
        legend_metadata,
        categorical_ticks,
        linear_fit_results,
    )


def _preserve_full_data_limits(
    axes: AxesBundle,
    target_ax,
    series: SeriesConfig,
    x_values: pd.Series,
    y_values: pd.Series,
) -> None:
    """Keep preview autoscaling identical to the full finite data bounds."""

    extent = np.array(
        [
            [float(x_values.min()), float(y_values.min())],
            [float(x_values.max()), float(y_values.max())],
        ]
    )
    if axes.broken_y and series.y_axis != "right":
        targets = (axes.upper, axes.main)
    elif axes.broken_x:
        targets = (axes.left, axes.main)
    else:
        targets = (target_ax,)
    for axis in targets:
        if axis is not None:
            axis.update_datalim(extent)


def _prepare_series_values(
    df: pd.DataFrame,
    config: PlotConfig,
    series: SeriesConfig,
    broken_x: bool,
    target_y_scale: str,
    plotted: int,
    warnings: list[str],
    numeric_columns: NumericColumnCache,
    options: RenderOptions,
) -> _PreparedSeries | None:
    numeric_x = numeric_columns.numeric(series.x)
    categorical_labels: list[str] | None = None
    categorical_x = False
    normalized_x: pd.Series | None = None
    nonblank_x: pd.Series | None = None
    # String normalization is expensive on object columns containing hundreds
    # of thousands of numeric strings.  It is needed only for categorical bars.
    if series.plot_type == "bar":
        raw_x = pd.Series(
            df[series.x].to_numpy(copy=False),
            index=pd.RangeIndex(len(df)),
            copy=False,
        )
        normalized_x = raw_x.astype("string").fillna("").str.strip()
        nonblank_x = normalized_x.ne("")
        categorical_x = bool(
            nonblank_x.any()
            and not numeric_x[nonblank_x].notna().all()
        )
    if categorical_x:
        if config.x_scale != "linear":
            warnings.append(f"{series.x}: categorical bar data requires a linear X scale.")
            return None
        if broken_x:
            warnings.append(f"{series.x}: categorical bar data is not supported on a broken X axis.")
            return None
        assert normalized_x is not None and nonblank_x is not None
        categorical_labels = list(dict.fromkeys(normalized_x[nonblank_x].tolist()))
        category_positions = {label: position for position, label in enumerate(categorical_labels)}
        x = normalized_x.map(category_positions).astype(float)
        finite_x = np.isfinite(
            x.to_numpy(dtype=float, na_value=np.nan, copy=False)
        )
    else:
        x = numeric_x
        finite_x = numeric_columns.finite(series.x)
    numeric_y = numeric_columns.numeric(series.y)
    y = numeric_y
    finite_y = numeric_columns.finite(series.y)
    x_values_array = x.to_numpy(dtype=float, na_value=np.nan, copy=False)
    y_values_array = y.to_numpy(dtype=float, na_value=np.nan, copy=False)
    numeric_pair = ~np.isnan(x_values_array) & ~np.isnan(y_values_array)
    valid_array = finite_x & finite_y
    nonfinite = numeric_pair & ~valid_array
    nonfinite_count = int(np.count_nonzero(nonfinite))
    if nonfinite_count:
        warnings.append(
            f"{series.label or series.y}: {nonfinite_count} non-finite "
            "X/Y value(s) skipped."
        )
    display_y_offset = series.y_offset + config.y_offset_step * plotted

    if config.x_scale == "log":
        invalid = valid_array & (x_values_array <= 0)
        invalid_count = int(np.count_nonzero(invalid))
        if invalid_count:
            warnings.append(f"{series.x}: {invalid_count} non-positive x values skipped for log scale.")
        valid_array &= x_values_array > 0
    if target_y_scale == "log":
        invalid = valid_array & (y_values_array + display_y_offset <= 0)
        invalid_count = int(np.count_nonzero(invalid))
        if invalid_count:
            warnings.append(
                f"{series.y}: {invalid_count} non-positive displayed Y "
                "value(s) skipped for log scale."
            )
        valid_array &= y_values_array + display_y_offset > 0

    valid = pd.Series(valid_array, index=pd.RangeIndex(len(df)), copy=False)
    x_values = x[valid]
    y_values = y[valid] + display_y_offset
    if x_values.empty:
        warnings.append(f"No plottable numeric data for {series.label or series.y}.")
        return None
    display_x, display_y, display_valid = _prepare_preview_values(
        x_values,
        y_values,
        valid,
        config,
        series.plot_type,
        broken_x,
        categorical_x,
        options,
    )
    return _PreparedSeries(
        x_values=x_values,
        y_values=y_values,
        valid=valid,
        display_x_values=display_x,
        display_y_values=display_y,
        display_valid=display_valid,
        categorical_labels=categorical_labels,
        display_y_offset=display_y_offset,
    )


def _prepare_preview_values(
    x_values: pd.Series,
    y_values: pd.Series,
    valid: pd.Series,
    config: PlotConfig,
    plot_type: str,
    broken_x: bool,
    categorical_x: bool,
    options: RenderOptions,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    point_limit = options.preview_point_limit
    if point_limit is None or categorical_x:
        return x_values, y_values, valid

    selected = np.arange(len(x_values), dtype=np.intp)
    protected = np.empty(0, dtype=np.intp)
    intervals = _preview_x_intervals(config, broken_x)
    if intervals and plot_type != "bar":
        selected, protected = _x_range_positions(
            x_values.to_numpy(dtype=float, copy=False),
            intervals,
            connected=plot_type not in {"scatter", "stem"},
        )

    filtered_x = x_values.iloc[selected]
    filtered_y = y_values.iloc[selected]
    reduced = _extrema_envelope_positions(
        filtered_x.to_numpy(dtype=float, copy=False),
        filtered_y.to_numpy(dtype=float, copy=False),
        point_limit,
        protected=protected,
    )
    display_x = filtered_x.iloc[reduced]
    display_y = filtered_y.iloc[reduced]
    if len(display_x) == len(x_values) and display_x.index.equals(x_values.index):
        return x_values, y_values, valid

    display_valid_array = np.zeros(len(valid), dtype=bool)
    display_valid_array[display_x.index.to_numpy(dtype=np.intp, copy=False)] = True
    display_valid = pd.Series(
        display_valid_array,
        index=valid.index,
        copy=False,
    )
    return display_x, display_y, display_valid


def _preview_x_intervals(
    config: PlotConfig,
    broken_x: bool,
) -> tuple[tuple[float | None, float | None], ...]:
    if broken_x:
        raw_intervals = (
            (config.x_break_left_min, config.x_break_left_max),
            (config.x_break_right_min, config.x_break_right_max),
        )
    else:
        if config.x_min is None and config.x_max is None:
            return ()
        raw_intervals = ((config.x_min, config.x_max),)

    intervals: list[tuple[float | None, float | None]] = []
    for raw_lower, raw_upper in raw_intervals:
        lower = _finite_optional_bound(raw_lower)
        upper = _finite_optional_bound(raw_upper)
        # If a supplied value is invalid, axis configuration will reject the
        # corresponding limit too; retain all vertices rather than guessing.
        if (raw_lower is not None and lower is None) or (
            raw_upper is not None and upper is None
        ):
            return ()
        if lower is not None and upper is not None and lower >= upper:
            return ()
        intervals.append((lower, upper))
    return tuple(intervals)


def _finite_optional_bound(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _x_range_positions(
    x_values: np.ndarray,
    intervals: tuple[tuple[float | None, float | None], ...],
    *,
    connected: bool,
) -> tuple[np.ndarray, np.ndarray]:
    """Return vertices relevant to visible X intervals.

    For connected plots, both endpoints of every segment that intersects a
    visible interval are retained.  This remains correct for unsorted X data
    and also keeps a clipped line crossing when neither endpoint is in range.
    Returned protected positions are relative to the filtered array and keep
    clipping/separator vertices from being removed by preview downsampling.
    """

    count = len(x_values)
    if count == 0:
        empty = np.empty(0, dtype=np.intp)
        return empty, empty
    point_visible = np.zeros(count, dtype=bool)
    segment_visible = np.zeros(max(count - 1, 0), dtype=bool)
    if connected and count > 1:
        segment_min = np.minimum(x_values[:-1], x_values[1:])
        segment_max = np.maximum(x_values[:-1], x_values[1:])

    for lower, upper in intervals:
        inside = np.ones(count, dtype=bool)
        if lower is not None:
            inside &= x_values >= lower
        if upper is not None:
            inside &= x_values <= upper
        point_visible |= inside
        if connected and count > 1:
            intersects = np.ones(count - 1, dtype=bool)
            if lower is not None:
                intersects &= segment_max >= lower
            if upper is not None:
                intersects &= segment_min <= upper
            segment_visible |= intersects

    keep = point_visible.copy()
    if connected and count > 1:
        keep[:-1] |= segment_visible
        keep[1:] |= segment_visible
    selected = np.flatnonzero(keep)
    protected = np.flatnonzero(~point_visible[selected])
    return selected, protected


def _extrema_envelope_positions(
    x_values: np.ndarray,
    y_values: np.ndarray,
    point_limit: int,
    *,
    protected: np.ndarray | None = None,
) -> np.ndarray:
    """Select an ordered four-extrema envelope within a strict point budget.

    Each source-order bucket contributes its minimum and maximum X and Y
    positions.  Consequently narrow spikes and the original autoscale extents
    survive in the interactive preview, unlike stride-only sampling.
    """

    count = len(x_values)
    if count <= point_limit:
        return np.arange(count, dtype=np.intp)
    mandatory = {0, count - 1}
    if protected is not None:
        mandatory.update(int(position) for position in protected)
    available = point_limit - len(mandatory)
    if available < 4:
        # There are too many clipping-critical vertices for a scientifically
        # safe reduction within this budget.  Keep the filtered data intact.
        return np.arange(count, dtype=np.intp)

    bucket_count = max(1, available // 4)
    boundaries = np.linspace(0, count, bucket_count + 1, dtype=np.intp)
    selected = set(mandatory)
    for start, stop in zip(boundaries[:-1], boundaries[1:]):
        if stop <= start:
            continue
        bucket_x = x_values[start:stop]
        bucket_y = y_values[start:stop]
        selected.update(
            {
                start + int(np.argmin(bucket_x)),
                start + int(np.argmax(bucket_x)),
                start + int(np.argmin(bucket_y)),
                start + int(np.argmax(bucket_y)),
            }
        )
    # Four candidates per bucket plus mandatory vertices never exceeds the
    # limit.  Sorting restores the source row/line order.
    return np.fromiter(sorted(selected), dtype=np.intp)


def _draw_linear_fit_for_series(
    config: PlotConfig,
    series: SeriesConfig,
    axes: AxesBundle,
    target_ax,
    x_values,
    unoffset_y_values,
    display_y_offset: float,
    color: str,
    target_y_scale: str,
    categorical_x: bool,
    series_index: int,
    warnings: list[str],
) -> LinearFitResult | None:
    """Calculate and draw one opt-in linear fit without affecting the legend."""

    if not getattr(series, "linear_fit_enabled", False):
        return None

    label = series.label or series.y
    if categorical_x:
        warnings.append(f"Linear fit for {label}: numeric X data is required.")
        return None
    if config.x_scale != "linear" or target_y_scale != "linear":
        warnings.append(
            f"Linear fit for {label}: linear X and Y axes are required."
        )
        return None

    try:
        result = linear_fit(
            x_values,
            unoffset_y_values,
            x_min=getattr(series, "linear_fit_x_min", None),
            x_max=getattr(series, "linear_fit_x_max", None),
        )
    except LinearFitError as error:
        warnings.append(f"Linear fit for {label}: {error}")
        return None

    line_style = str(getattr(series, "linear_fit_line_style", "dashed"))
    if line_style not in LINE_STYLES:
        warnings.append(
            f"Linear fit for {label}: unknown line style; using dashed."
        )
        line_style = "dashed"
    invalid_line_width = False
    try:
        line_width = float(getattr(series, "linear_fit_line_width", 1.0))
    except (TypeError, ValueError, OverflowError):
        line_width = 1.0
        invalid_line_width = True
    if invalid_line_width or not math.isfinite(line_width) or line_width <= 0:
        warnings.append(
            f"Linear fit for {label}: line width must be positive; using 1.0."
        )
        line_width = 1.0
    alpha = 1.0 if series.force_opaque else series.alpha
    artist_kwargs = {
        "color": color,
        "linestyle": line_style,
        "linewidth": line_width,
        "alpha": alpha,
        "label": "_nolegend_",
        "zorder": 3,
    }
    gid = f"pubfig_linear_fit_{series_index}"

    drawn_segments = 0
    if axes.broken_x:
        for target, bounds in (
            (
                axes.left,
                (config.x_break_left_min, config.x_break_left_max),
            ),
            (
                axes.main,
                (config.x_break_right_min, config.x_break_right_max),
            ),
        ):
            drawn_segments += _draw_linear_fit_segment(
                target,
                result,
                display_y_offset,
                gid,
                artist_kwargs,
                x_bounds=bounds,
            )
    elif axes.broken_y and series.y_axis != "right":
        for target, bounds in (
            (
                axes.upper,
                (config.y_break_upper_min, config.y_break_upper_max),
            ),
            (
                axes.main,
                (config.y_break_lower_min, config.y_break_lower_max),
            ),
        ):
            drawn_segments += _draw_linear_fit_segment(
                target,
                result,
                display_y_offset,
                gid,
                artist_kwargs,
                y_bounds=bounds,
            )
    else:
        drawn_segments += _draw_linear_fit_segment(
            target_ax,
            result,
            display_y_offset,
            gid,
            artist_kwargs,
        )
    if drawn_segments == 0:
        warnings.append(
            f"Linear fit for {label}: the fitted line is outside the visible "
            "broken-axis ranges."
        )
    return result


def _draw_linear_fit_segment(
    target_ax,
    result: LinearFitResult,
    display_y_offset: float,
    gid: str,
    artist_kwargs: dict[str, Any],
    *,
    x_bounds: tuple[float | None, float | None] | None = None,
    y_bounds: tuple[float | None, float | None] | None = None,
) -> bool:
    """Draw the part of a fitted line that belongs on one normal/broken axis."""

    start = result.x_min
    end = result.x_max
    if x_bounds is not None:
        lower_x, upper_x = x_bounds
        if lower_x is not None:
            start = max(start, lower_x)
        if upper_x is not None:
            end = min(end, upper_x)

    displayed_intercept = result.intercept + display_y_offset
    if y_bounds is not None:
        lower_y, upper_y = y_bounds
        if lower_y is not None and upper_y is not None:
            if result.slope == 0.0:
                if not lower_y <= displayed_intercept <= upper_y:
                    return False
            else:
                first_crossing = (lower_y - displayed_intercept) / result.slope
                second_crossing = (upper_y - displayed_intercept) / result.slope
                start = max(start, min(first_crossing, second_crossing))
                end = min(end, max(first_crossing, second_crossing))

    if start > end:
        return False
    x_line = [start, end]
    displayed_result = LinearFitResult(
        slope=result.slope,
        intercept=displayed_intercept,
        r_squared=result.r_squared,
        point_count=result.point_count,
        x_min=result.x_min,
        x_max=result.x_max,
    )
    y_line = [displayed_result.predict(x_value) for x_value in x_line]
    artist = target_ax.plot(x_line, y_line, **artist_kwargs)[0]
    artist.set_gid(gid)
    return True


def _draw_prepared_series(
    df: pd.DataFrame,
    config: PlotConfig,
    series: SeriesConfig,
    axes: AxesBundle,
    target_ax,
    x_values,
    y_values,
    valid,
    target_y_scale: str,
    color: str,
    label: str,
    warnings: list[str],
    numeric_columns: NumericColumnCache,
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
            color,
            warnings,
            target_y_scale,
            numeric_errors=(
                numeric_columns.numeric(series.error_column)
                if getattr(series, "error_column", "") in df.columns
                else None
            ),
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
    records: list[tuple[str, Any, str]] = []
    automatic_records: list[tuple[str, Any, str]] = []
    for handle, token in zip(handles, tokens):
        metadata = summary.legend_metadata.get(token)
        if metadata is None:
            continue
        source_y, default_label, automatic_visible = metadata
        record = (source_y, handle, default_label)
        records.append(record)
        if automatic_visible:
            automatic_records.append(record)

    effective_row_lengths = config.legend_row_lengths
    if config.legend_entries is not None:
        handles, labels, effective_row_lengths, text_styles = (
            _resolve_explicit_legend_with_styles(
                records,
                config.legend_entries,
                config.legend_row_lengths,
            )
        )
    else:
        handles = [record[1] for record in automatic_records]
        labels = [format_plot_text(record[2]) for record in automatic_records]
        text_styles = [None] * len(handles)
    handles, labels, legend_ncol, text_styles = _legend_grid_layout_with_styles(
        handles,
        labels,
        effective_row_lengths,
        text_styles,
    )
    if not handles:
        return None

    legend_kwargs = {
        "frameon": False,
        "fontsize": config.legend_size,
        "ncol": legend_ncol,
        "columnspacing": 0.7,
        "handletextpad": 0.35,
        "handlelength": 1.8,
        "handler_map": legend_handler_map(),
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
    apply_legend_text_styles(legend_artist, text_styles)
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


__all__ = [
    "DEFAULT_PREVIEW_POINT_LIMIT",
    "NumericColumnCache",
    "RenderOptions",
    "RenderResult",
    "render_figure",
]
