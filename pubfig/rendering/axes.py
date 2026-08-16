"""Figure/Axes construction, styling, limits, ticks, and broken-axis layout."""

from __future__ import annotations

from dataclasses import dataclass
import math
import warnings as py_warnings

import matplotlib as mpl
from matplotlib.figure import Figure
from matplotlib import font_manager
from matplotlib.patches import ArrowStyle, FancyArrowPatch
from matplotlib.ticker import (
    AutoMinorLocator,
    FixedLocator,
    LogFormatterSciNotation,
    LogLocator,
    MultipleLocator,
    NullLocator,
)

from ..plot_config import PALETTE, PlotConfig, SeriesConfig
from .artists import format_plot_text


@dataclass(frozen=True)
class AxesBundle:
    """The related Matplotlib axes used by one logical plot."""

    figure: Figure
    main: object
    plot_axes: list
    right: object | None
    upper: object | None
    left: object | None
    broken_y: bool
    broken_x: bool

    @property
    def title_axis(self):
        if self.broken_y and self.upper is not None:
            return self.upper
        if self.broken_x and self.left is not None:
            return self.left
        return self.main

    @property
    def annotation_axis(self):
        return self.left if self.broken_x and self.left is not None else self.main


def mm_to_inch(value_mm: float) -> float:
    return value_mm / 25.4


def create_axes(
    config: PlotConfig,
    series_configs: list[SeriesConfig],
    warnings: list[str],
) -> AxesBundle:
    """Create the figure and the normal, twin, or broken axes it requires."""
    apply_style(warnings)
    figure_width_mm, figure_height_mm = figure_size_mm(config)
    fig = Figure(
        figsize=(mm_to_inch(figure_width_mm), mm_to_inch(figure_height_mm)),
        dpi=config.dpi,
        facecolor="none" if config.transparent else "white",
    )
    try:
        right_axis_needed = any(
            series.y_axis == "right" for series in series_configs
        )
        broken_y = use_broken_y_axis(config, right_axis_needed, warnings)
        broken_x = use_broken_x_axis(
            config,
            right_axis_needed,
            broken_y,
            warnings,
        )
        if broken_y:
            gridspec = fig.add_gridspec(
                2,
                1,
                height_ratios=broken_y_height_ratios(config),
                hspace=max(config.y_break_gap, 0.01),
            )
            upper = fig.add_subplot(gridspec[0])
            main = fig.add_subplot(gridspec[1], sharex=upper)
            return AxesBundle(
                fig,
                main,
                [upper, main],
                None,
                upper,
                None,
                True,
                False,
            )
        if broken_x:
            gridspec = fig.add_gridspec(
                1,
                2,
                width_ratios=broken_x_width_ratios(config),
                wspace=max(config.x_break_gap, 0.01),
            )
            left = fig.add_subplot(gridspec[0])
            main = fig.add_subplot(gridspec[1], sharey=left)
            return AxesBundle(
                fig,
                main,
                [left, main],
                None,
                None,
                left,
                False,
                True,
            )

        main = fig.add_subplot(111)
        right = main.twinx() if right_axis_needed else None
        return AxesBundle(
            fig,
            main,
            [main],
            right,
            None,
            None,
            False,
            False,
        )
    except BaseException:
        fig.clear()
        raise


def configure_axes(
    axes: AxesBundle,
    config: PlotConfig,
    warnings: list[str],
    categorical_ticks: list[str] | None,
) -> None:
    """Apply titles, scales, spines, ticks, limits, and grids."""
    ax = axes.main
    ax_right = axes.right
    ax_upper = axes.upper
    ax_left = axes.left
    plot_axes = axes.plot_axes

    axes.title_axis.set_title(
        "" if axes.broken_x else format_plot_text(config.title),
        fontsize=config.title_size,
    )
    ax.set_xlabel("")
    if axes.broken_x and ax_left is not None:
        ax_left.set_xlabel("")
    ax.set_ylabel("")
    for plot_axis in plot_axes:
        plot_axis.set_xscale(config.x_scale)
        plot_axis.set_yscale(config.y_scale)
    if ax_right is not None:
        ax_right.set_ylabel(format_plot_text(config.y2_label), fontsize=config.axis_size)
        ax_right.set_yscale(config.y2_scale)

    show_top_ticks = config.show_tick_marks and config.show_top_axis
    show_bottom_ticks = config.show_tick_marks and config.show_bottom_axis
    show_left_ticks = config.show_tick_marks and config.show_left_axis
    show_right_ticks = config.show_tick_marks and config.show_right_axis
    all_axes = plot_axes + ([ax_right] if ax_right is not None else [])
    for axis in all_axes:
        axis.tick_params(
            direction="in",
            top=show_top_ticks,
            bottom=show_bottom_ticks,
            left=show_left_ticks,
            right=show_right_ticks,
            labelsize=config.tick_size,
            width=0.5,
        )
        axis.tick_params(
            which="minor",
            direction="in",
            top=show_top_ticks,
            bottom=show_bottom_ticks,
            left=show_left_ticks,
            right=show_right_ticks,
            width=0.4,
        )
        axis.tick_params(axis="x", pad=config.x_tick_pad)
        axis.tick_params(axis="y", pad=config.y2_tick_pad if axis is ax_right else config.y_tick_pad)

        for side, visible in (
            ("top", config.show_top_axis),
            ("right", config.show_right_axis),
            ("bottom", config.show_bottom_axis),
            ("left", config.show_left_axis),
        ):
            axis.spines[side].set_visible(visible)
            axis.spines[side].set_linewidth(config.axis_line_width)
            if config.show_axis_arrows:
                axis.spines[side].set_capstyle("butt")

    ax.tick_params(
        labelbottom=config.show_bottom_axis and config.show_x_tick_labels,
        labelleft=config.show_left_axis and config.show_y_tick_labels,
    )
    if axes.broken_y and ax_upper is not None:
        ax_upper.tick_params(axis="x", which="both", bottom=False, labelbottom=False, top=show_top_ticks)
        ax_upper.tick_params(labelleft=config.show_left_axis and config.show_y_tick_labels)
        ax.tick_params(
            axis="x",
            which="both",
            top=False,
            labelbottom=config.show_bottom_axis and config.show_x_tick_labels,
        )
        ax_upper.spines["bottom"].set_visible(False)
        ax.spines["top"].set_visible(False)
    if axes.broken_x and ax_left is not None:
        ax_left.tick_params(
            axis="y",
            which="both",
            right=False,
            labelleft=config.show_left_axis and config.show_y_tick_labels,
        )
        ax.tick_params(axis="y", which="both", left=False, labelleft=False, right=show_right_ticks)
        ax_left.tick_params(axis="x", labelbottom=config.show_bottom_axis and config.show_x_tick_labels)
        ax.tick_params(axis="x", labelbottom=config.show_bottom_axis and config.show_x_tick_labels)
        ax_left.spines["right"].set_visible(False)
        ax.spines["left"].set_visible(False)

    for plot_axis in plot_axes:
        plot_axis.tick_params(axis="y", colors=config.y_axis_color)
        plot_axis.spines["left"].set_edgecolor(config.y_axis_color)

    if ax_right is not None:
        ax.tick_params(axis="y", which="both", right=False, labelright=False)
        ax.spines["right"].set_visible(False)
        ax_right.tick_params(
            axis="y",
            which="both",
            left=False,
            labelleft=False,
            right=show_right_ticks,
            labelright=config.show_right_axis and config.show_y2_tick_labels,
            colors=config.y2_axis_color,
        )
        ax_right.spines["left"].set_visible(False)
        ax_right.yaxis.label.set_color(config.y2_axis_color)
        ax_right.spines["right"].set_edgecolor(config.y2_axis_color)

    _configure_limits_and_ticks(axes, config, warnings, show_top_ticks, show_right_ticks)

    if categorical_ticks is not None:
        for category_axis in plot_axes:
            category_axis.set_xticks(range(len(categorical_ticks)))
            category_axis.set_xticklabels(categorical_ticks)
    for axis in all_axes:
        axis.tick_params(which="minor", direction="in", width=0.4, length=2.2)

    if config.grid:
        for plot_axis in plot_axes:
            plot_axis.grid(True, which="major", linestyle="-", linewidth=0.4, alpha=0.25)
            plot_axis.grid(True, which="minor", linestyle=":", linewidth=0.3, alpha=0.18)


def _configure_limits_and_ticks(
    axes: AxesBundle,
    config: PlotConfig,
    warnings: list[str],
    show_top_ticks: bool,
    show_right_ticks: bool,
) -> None:
    ax = axes.main
    ax_right = axes.right
    ax_upper = axes.upper
    ax_left = axes.left

    if axes.broken_y and ax_upper is not None:
        ax.set_ylim(config.y_break_lower_min, config.y_break_lower_max)
        ax_upper.set_ylim(config.y_break_upper_min, config.y_break_upper_max)
        if config.x_min is not None or config.x_max is not None:
            apply_x_limits(ax, config)
            apply_x_limits(ax_upper, config)
    elif axes.broken_x and ax_left is not None:
        ax_left.set_xlim(config.x_break_left_min, config.x_break_left_max)
        ax.set_xlim(config.x_break_right_min, config.x_break_right_max)
        apply_y_limits(ax, config, warnings, "left")
    else:
        apply_limits(ax, config, warnings, "left")
    if ax_right is not None:
        apply_limits(ax_right, config, warnings, "right")

    apply_tick_intervals(ax, config, warnings, "left")
    if axes.broken_y and ax_upper is not None:
        apply_tick_intervals(ax_upper, config, warnings, "left")
        prune_broken_y_boundary_ticks(ax_upper, ax)
        ax_upper.tick_params(axis="x", which="both", bottom=False, labelbottom=False, top=show_top_ticks)
        ax.tick_params(
            axis="x",
            which="both",
            top=False,
            labelbottom=config.show_bottom_axis and config.show_x_tick_labels,
        )
    if axes.broken_x and ax_left is not None:
        apply_tick_intervals(ax_left, config, warnings, "left")
        prune_broken_x_boundary_ticks(ax_left, ax)
        ax_left.tick_params(
            axis="y",
            which="both",
            right=False,
            labelleft=config.show_left_axis and config.show_y_tick_labels,
        )
        ax.tick_params(axis="y", which="both", left=False, labelleft=False, right=show_right_ticks)
    if ax_right is not None:
        apply_tick_intervals(ax_right, config, warnings, "right")


def finish_layout(axes: AxesBundle, config: PlotConfig, warnings: list[str]) -> None:
    """Lay out labels/break marks and replace visible spines with arrows."""
    fig = axes.figure
    ax = axes.main
    ax_right = axes.right
    ax_upper = axes.upper
    ax_left = axes.left

    if axes.broken_y:
        apply_figure_layout(fig, config, warnings)
        fig.subplots_adjust(hspace=max(config.y_break_gap, 0.01))
        draw_fixed_y_label(fig, ax, config, warnings, ax_upper)
        draw_fixed_x_label(fig, ax, ax, config, warnings)
        draw_y_break_marks(
            ax_upper,
            ax,
            left=config.show_left_axis,
            right=config.show_right_axis,
            linewidth=config.axis_line_width * 1.3,
        )
        if config.show_axis_arrows:
            draw_axis_arrows(ax_upper, ("top", "left", "right"))
            draw_axis_arrows(ax, ("bottom",))
    elif axes.broken_x:
        apply_figure_layout(fig, config, warnings)
        fig.subplots_adjust(wspace=max(config.x_break_gap, 0.01))
        draw_fixed_title(fig, ax_left, ax, config, warnings)
        draw_fixed_y_label(fig, ax_left, config, warnings)
        draw_fixed_x_label(fig, ax_left, ax, config, warnings)
        draw_x_break_marks(
            ax_left,
            ax,
            bottom=config.show_bottom_axis,
            top=config.show_top_axis,
            linewidth=config.axis_line_width * 1.3,
        )
        if config.show_axis_arrows:
            draw_axis_arrows(ax_left, ("left",))
            draw_axis_arrows(ax, ("top", "bottom", "right"))
    else:
        apply_figure_layout(fig, config, warnings)
        draw_fixed_y_label(fig, ax, config, warnings)
        draw_fixed_x_label(fig, ax, ax, config, warnings)
        if config.show_axis_arrows:
            if ax_right is None:
                draw_axis_arrows(ax, ("top", "bottom", "left", "right"))
            else:
                ax_right.spines["top"].set_visible(False)
                ax_right.spines["bottom"].set_visible(False)
                draw_axis_arrows(ax, ("top", "bottom", "left"))
                draw_axis_arrows(ax_right, ("right",))


def use_broken_y_axis(config: PlotConfig, right_axis_needed: bool, warnings: list[str]) -> bool:
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


def use_broken_x_axis(
    config: PlotConfig,
    right_axis_needed: bool,
    broken_y: bool,
    warnings: list[str],
) -> bool:
    if not config.x_break_enabled:
        return False
    if broken_y:
        warnings.append("X broken axis is disabled when Y broken axis is active.")
        return False
    if right_axis_needed:
        warnings.append("X broken axis is disabled when Y2 series are plotted.")
        return False
    if config.x_scale != "linear":
        warnings.append("X broken axis is only available for linear X scale.")
        return False
    values = (
        config.x_break_left_min,
        config.x_break_left_max,
        config.x_break_right_min,
        config.x_break_right_max,
    )
    if any(value is None for value in values):
        warnings.append("X broken axis needs left and right X ranges.")
        return False
    left_min, left_max, right_min, right_max = values
    if not (left_min < left_max < right_min < right_max):
        warnings.append("X broken axis ranges must be left min < left max < right min < right max.")
        return False
    return True


def broken_y_height_ratios(config: PlotConfig) -> tuple[float, float]:
    upper_range = max(float(config.y_break_upper_max - config.y_break_upper_min), 1e-9)
    lower_range = max(float(config.y_break_lower_max - config.y_break_lower_min), 1e-9)
    upper_fraction = upper_range / (upper_range + lower_range)
    upper_fraction = min(0.6, max(0.32, upper_fraction))
    return upper_fraction, 1.0 - upper_fraction


def broken_x_width_ratios(config: PlotConfig) -> tuple[float, float]:
    left_range = max(float(config.x_break_left_max - config.x_break_left_min), 1e-9)
    right_range = max(float(config.x_break_right_max - config.x_break_right_min), 1e-9)
    left_fraction = left_range / (left_range + right_range)
    left_fraction = min(0.68, max(0.32, left_fraction))
    return left_fraction, 1.0 - left_fraction


def scale_divisor(value: float, label: str, warnings: list[str]) -> float:
    try:
        divisor = float(value)
    except (TypeError, ValueError):
        warnings.append(f"{label} divisor is invalid; using 1.")
        return 1.0
    if divisor <= 0:
        warnings.append(f"{label} divisor must be positive; using 1.")
        return 1.0
    return divisor


def _preferred_font_family() -> str:
    installed = {font.name for font in font_manager.fontManager.ttflist}
    for family in ("Arial", "Segoe UI", "Noto Sans", "Liberation Sans", "DejaVu Sans"):
        if family in installed:
            return family
    return "DejaVu Sans"


_FONT_FAMILY = _preferred_font_family()
_RC_PARAMS = {
    "font.size": 8,
    "font.family": [_FONT_FAMILY, "DejaVu Sans"],
    "mathtext.fontset": "custom",
    "mathtext.rm": _FONT_FAMILY,
    "mathtext.it": f"{_FONT_FAMILY}:italic",
    "mathtext.bf": f"{_FONT_FAMILY}:bold",
    "mathtext.default": "regular",
    "pdf.fonttype": 42,
    "svg.fonttype": "none",
    "axes.linewidth": 0.5,
    "lines.linewidth": 1.0,
    "xtick.major.width": 0.5,
    "ytick.major.width": 0.5,
    "axes.prop_cycle": mpl.cycler(color=PALETTE),
    "savefig.bbox": None,
}
_SCIENCEPLOTS_STATUS: str | None = None


def apply_style(warnings: list[str]) -> None:
    global _SCIENCEPLOTS_STATUS
    if _SCIENCEPLOTS_STATUS is None:
        try:
            with py_warnings.catch_warnings():
                py_warnings.simplefilter("ignore", mpl.MatplotlibDeprecationWarning)
                import scienceplots  # noqa: F401

            mpl.style.use(["science", "no-latex", "bright"])
            _SCIENCEPLOTS_STATUS = ""
        except Exception as exc:
            message = str(exc).splitlines()[0]
            _SCIENCEPLOTS_STATUS = f"SciencePlots style unavailable; using built-in style. {message}"
    if _SCIENCEPLOTS_STATUS:
        warnings.append(_SCIENCEPLOTS_STATUS)
    mpl.rcParams.update(_RC_PARAMS)


def apply_figure_layout(fig: Figure, config: PlotConfig, warnings: list[str]) -> None:
    total_width, total_height = figure_size_mm(config)
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


def draw_fixed_y_label(fig: Figure, ax, config: PlotConfig, warnings: list[str], ax_upper=None) -> None:
    if not config.y_label:
        return
    total_width, _ = figure_size_mm(config)
    total_width = max(total_width, 1e-9)
    lower_pos = ax.get_position()
    upper_pos = ax_upper.get_position() if ax_upper is not None else lower_pos
    label_width_mm = config.axis_size * 25.4 / 72.0
    x = lower_pos.x0 - max(config.y_label_offset_mm, 0.0) / total_width
    if x - label_width_mm / total_width < 0:
        warnings.append("Y label gap exceeds the left canvas space; increase Plot left margin or lower Y label gap.")
    y = (lower_pos.y0 + upper_pos.y1) * 0.5
    label = fig.text(
        x,
        y,
        format_plot_text(config.y_label),
        rotation=90,
        ha="right",
        va="center",
        fontsize=config.axis_size,
        color=config.y_axis_color,
    )
    label.set_clip_on(False)
    label.set_in_layout(False)


def draw_fixed_title(fig: Figure, ax_left, ax_right, config: PlotConfig, warnings: list[str]) -> None:
    if not config.title:
        return
    _, total_height = figure_size_mm(config)
    total_height = max(total_height, 1e-9)
    left_pos = ax_left.get_position()
    right_pos = ax_right.get_position()
    title_height_mm = config.title_size * 25.4 / 72.0
    requested_y = max(left_pos.y1, right_pos.y1) + title_height_mm * 0.4 / total_height
    max_visible_y = 1.0 - title_height_mm * 0.25 / total_height
    y = min(requested_y, max_visible_y)
    if y != requested_y:
        warnings.append("Title needs more top canvas space; increase Plot top margin.")
    title = fig.text(
        (left_pos.x0 + right_pos.x1) * 0.5,
        y,
        format_plot_text(config.title),
        ha="center",
        va="bottom",
        fontsize=config.title_size,
    )
    title.set_clip_on(False)
    title.set_in_layout(False)


def draw_fixed_x_label(fig: Figure, ax_left, ax_right, config: PlotConfig, warnings: list[str]) -> None:
    if not config.x_label:
        return
    _, total_height = figure_size_mm(config)
    total_height = max(total_height, 1e-9)
    left_pos = ax_left.get_position()
    right_pos = ax_right.get_position()
    label_height_mm = config.axis_size * 25.4 / 72.0
    requested_y = min(left_pos.y0, right_pos.y0) - max(config.x_label_offset_mm, 0.0) / total_height
    min_visible_y = label_height_mm * 0.25 / total_height
    y = max(requested_y, min_visible_y)
    if y != requested_y:
        warnings.append("X label needs more bottom canvas space; increase Plot bottom margin.")
    label = fig.text(
        (left_pos.x0 + right_pos.x1) * 0.5,
        y,
        format_plot_text(config.x_label),
        ha="center",
        va="top",
        fontsize=config.axis_size,
    )
    label.set_clip_on(False)
    label.set_in_layout(False)


def figure_size_mm(config: PlotConfig) -> tuple[float, float]:
    return (
        max(config.width_mm + config.pad_left_mm + config.pad_right_mm, 1.0),
        max(config.height_mm + config.pad_top_mm + config.pad_bottom_mm, 1.0),
    )


def draw_axis_arrows(ax, sides: tuple[str, ...]) -> None:
    endpoints = {
        "top": ((0.0, 1.0), (1.0, 1.0)),
        "bottom": ((0.0, 0.0), (1.0, 0.0)),
        "left": ((0.0, 0.0), (0.0, 1.0)),
        "right": ((1.0, 0.0), (1.0, 1.0)),
    }
    for side in sides:
        spine = ax.spines[side]
        if not spine.get_visible():
            continue
        start, end = endpoints[side]
        color = spine.get_edgecolor()
        spine_width = spine.get_linewidth()
        head_size = max(3.2, 1.6 * spine_width)
        arrow_style = ArrowStyle.Simple(
            head_length=head_size,
            head_width=head_size,
            tail_width=spine_width,
        )
        spine.set_visible(False)
        arrow = FancyArrowPatch(
            start,
            end,
            arrowstyle=arrow_style,
            connectionstyle="arc3,rad=0",
            mutation_scale=1,
            mutation_aspect=1,
            linewidth=0,
            edgecolor="none",
            facecolor=color,
            transform=ax.transAxes,
            shrinkA=0,
            shrinkB=0,
            clip_on=False,
            zorder=spine.get_zorder(),
        )
        arrow.set_gid(f"pubfig_axis_arrow_{side}")
        arrow.set_in_layout(False)
        ax.add_patch(arrow)


def draw_y_break_marks(ax_upper, ax_lower, *, left: bool, right: bool, linewidth: float) -> None:
    size = 0.012
    kwargs = {
        "color": "black",
        "clip_on": False,
        "linewidth": linewidth,
        "solid_capstyle": "round",
        "zorder": 20,
    }
    for x, visible in ((0.0, left), (1.0, right)):
        if not visible:
            continue
        ax_upper.plot((x - size, x + size), (-size, size), transform=ax_upper.transAxes, **kwargs)
        ax_lower.plot((x - size, x + size), (1 - size, 1 + size), transform=ax_lower.transAxes, **kwargs)


def draw_x_break_marks(ax_left, ax_right, *, bottom: bool, top: bool, linewidth: float) -> None:
    size = 0.012
    kwargs = {
        "color": "black",
        "clip_on": False,
        "linewidth": linewidth,
        "solid_capstyle": "round",
        "zorder": 20,
    }
    for y, visible in ((0.0, bottom), (1.0, top)):
        if not visible:
            continue
        ax_left.plot((1 - size, 1 + size), (y - size, y + size), transform=ax_left.transAxes, **kwargs)
        ax_right.plot((-size, size), (y - size, y + size), transform=ax_right.transAxes, **kwargs)


def prune_broken_y_boundary_ticks(ax_upper, ax_lower) -> None:
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


def prune_broken_x_boundary_ticks(ax_left, ax_right) -> None:
    left_low, left_high = ax_left.get_xlim()
    right_low, right_high = ax_right.get_xlim()
    left_tol = max(abs(left_high - left_low), 1.0) * 1e-9
    right_tol = max(abs(right_high - right_low), 1.0) * 1e-9
    ax_left.set_xticks(
        [tick for tick in ax_left.get_xticks() if left_low <= tick < left_high and abs(tick - left_high) > left_tol]
    )
    ax_right.set_xticks(
        [tick for tick in ax_right.get_xticks() if right_low < tick <= right_high and abs(tick - right_low) > right_tol]
    )
    ax_left.set_xlim(left_low, left_high)
    ax_right.set_xlim(right_low, right_high)


def apply_limits(ax, config: PlotConfig, warnings: list[str], side: str) -> None:
    apply_x_limits(ax, config)
    apply_y_limits(ax, config, warnings, side)


def apply_y_limits(ax, config: PlotConfig, warnings: list[str], side: str) -> None:
    y_min = config.y2_min if side == "right" else config.y_min
    y_max = config.y2_max if side == "right" else config.y_max
    y_scale = config.y2_scale if side == "right" else config.y_scale
    if y_min is not None or y_max is not None:
        bottom, top = ax.get_ylim()
        resolved_bottom = resolve_y_limit(y_min, y_scale, warnings, side, "min") if y_min is not None else None
        resolved_top = resolve_y_limit(y_max, y_scale, warnings, side, "max") if y_max is not None else None
        bottom = resolved_bottom if resolved_bottom is not None else bottom
        top = resolved_top if resolved_top is not None else top
        if bottom < top:
            ax.set_ylim(bottom, top)


def apply_x_limits(ax, config: PlotConfig) -> None:
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


def resolve_y_limit(
    value: float,
    scale: str,
    warnings: list[str],
    side: str,
    bound: str,
) -> float | None:
    if scale != "log":
        return value
    if value <= 0:
        label = "Y2" if side == "right" else "Y"
        warnings.append(f"{label} {bound} must be positive on log scale; keeping automatic limit.")
        return None
    return value


def apply_tick_intervals(ax, config: PlotConfig, warnings: list[str], side: str) -> None:
    apply_major_locator(ax.xaxis, config.x_scale, config.x_tick_interval, warnings, "X")
    apply_minor_locator(ax.xaxis, config.x_scale, config.x_minor_divisions)
    y_interval = config.y2_tick_interval if side == "right" else config.y_tick_interval
    y_scale = config.y2_scale if side == "right" else config.y_scale
    y_minor_divisions = config.y2_minor_divisions if side == "right" else config.y_minor_divisions
    label = "Y2" if side == "right" else "Y"
    apply_major_locator(ax.yaxis, y_scale, y_interval, warnings, label)
    apply_minor_locator(ax.yaxis, y_scale, y_minor_divisions)


def apply_major_locator(axis_obj, scale: str, interval: float | None, warnings: list[str], label: str) -> None:
    if interval is None:
        return
    if interval <= 0:
        warnings.append(f"{label} tick interval must be positive.")
        return
    if scale == "log":
        ticks = log_major_ticks(axis_obj.get_view_interval(), interval, warnings, label)
        if ticks:
            axis_obj.set_major_locator(FixedLocator(ticks))
            axis_obj.set_major_formatter(LogFormatterSciNotation(base=10.0, labelOnlyBase=False))
        return
    axis_obj.set_major_locator(MultipleLocator(interval))


def log_major_ticks(view_interval, decade_interval: float, warnings: list[str], label: str) -> list[float]:
    low, high = sorted(float(value) for value in view_interval)
    if low <= 0 or high <= 0:
        warnings.append(f"{label} tick interval needs positive limits on log scale.")
        return []
    log_low = math.log10(low)
    log_high = math.log10(high)
    eps = 1e-10
    start = math.ceil((log_low - eps) / decade_interval) * decade_interval
    stop = math.floor((log_high + eps) / decade_interval) * decade_interval
    if start > stop:
        warnings.append(f"{label} tick interval is wider than the visible log range.")
        return []

    ticks: list[float] = []
    exponent = start
    max_ticks = 200
    while exponent <= stop + eps and len(ticks) < max_ticks:
        ticks.append(10**exponent)
        exponent += decade_interval
    if exponent <= stop + eps:
        warnings.append(f"{label} tick interval creates too many log ticks; showing the first {max_ticks}.")
    return ticks


def apply_minor_locator(axis_obj, scale: str, divisions: int) -> None:
    divisions = max(0, int(divisions))
    if divisions == 0:
        axis_obj.set_minor_locator(NullLocator())
    elif scale == "log":
        axis_obj.set_minor_locator(LogLocator(base=10.0, subs=log_minor_subs(divisions), numticks=100))
    else:
        axis_obj.set_minor_locator(AutoMinorLocator(divisions))


def log_minor_subs(divisions: int) -> tuple[float, ...]:
    if divisions <= 0:
        return ()
    return tuple(float(value) for value in range(2, 10))


__all__ = ["AxesBundle", "create_axes", "configure_axes", "finish_layout", "mm_to_inch"]
