"""Plot configuration and project schema for Graph_drawer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any

import pandas as pd

from .dataframe_codec import dataframe_from_payload, dataframe_to_payload


SCHEMA_VERSION = 1

PALETTE = [
    "#000000",
    "#0072B2",
    "#D55E00",
    "#009E73",
    "#CC79A7",
    "#E69F00",
    "#56B4E9",
    "#666666",
]

RECOMMENDED_PALETTES = {
    "Colorblind safe": [
        "#000000",
        "#0072B2",
        "#D55E00",
        "#009E73",
        "#CC79A7",
        "#E69F00",
        "#56B4E9",
        "#666666",
    ],
    "Paul Tol Bright": [
        "#4477AA",
        "#EE6677",
        "#228833",
        "#CCBB44",
        "#66CCEE",
        "#AA3377",
        "#BBBBBB",
    ],
    "Paul Tol Muted": [
        "#332288",
        "#88CCEE",
        "#44AA99",
        "#117733",
        "#999933",
        "#DDCC77",
        "#CC6677",
        "#882255",
        "#AA4499",
        "#DDDDDD",
    ],
    "Nature/Wong": [
        "#0072B2",
        "#D55E00",
        "#009E73",
        "#CC79A7",
        "#E69F00",
        "#56B4E9",
        "#F0E442",
        "#000000",
    ],
    "ColorBrewer Dark2": [
        "#1B9E77",
        "#D95F02",
        "#7570B3",
        "#E7298A",
        "#66A61E",
        "#E6AB02",
        "#A6761D",
        "#666666",
    ],
    "Tableau 10": [
        "#4E79A7",
        "#F28E2B",
        "#59A14F",
        "#E15759",
        "#76B7B2",
        "#EDC948",
        "#B07AA1",
        "#FF9DA7",
        "#9C755F",
        "#BAB0AC",
    ],
    "Grayscale print": [
        "#000000",
        "#444444",
        "#777777",
        "#999999",
        "#BBBBBB",
        "#666666",
        "#222222",
    ],
}


@dataclass(frozen=True)
class FigurePreset:
    name: str
    width_mm: float
    height_mm: float
    dpi: int
    font_size: int = 8
    axis_size: int = 7
    tick_size: int = 6
    legend_size: int = 6


PRESETS: dict[str, FigurePreset] = {
    "Nature 1-col": FigurePreset("Nature 1-col", 89.0, 67.0, 300),
    "Nature 2-col": FigurePreset("Nature 2-col", 183.0, 137.0, 300),
    "ACS 1-col": FigurePreset("ACS 1-col", 84.7, 63.5, 300),
    "ACS 2-col": FigurePreset("ACS 2-col", 177.8, 133.4, 300),
    "Physical Review 1-col": FigurePreset("Physical Review 1-col", 86.0, 64.5, 600),
    "Physical Review 2-col": FigurePreset("Physical Review 2-col", 178.0, 133.5, 600),
    "Generic single-column": FigurePreset("Generic single-column", 85.0, 64.0, 300),
    "Generic double-column": FigurePreset("Generic double-column", 180.0, 120.0, 300),
}


PLOT_TYPES = ["line", "scatter", "line+marker", "bar", "step", "area", "stem"]
# Human-readable marker names are kept beside their Matplotlib values so the
# UI never has to expose implementation codes such as ``s`` or ``v``.  Keep
# ``MARKERS`` as the public list of codes for compatibility with callers that
# imported it before the labelled selector was introduced.
MARKER_CHOICES = (
    ("o", "Circle"),
    ("s", "Square"),
    ("^", "Triangle up"),
    ("v", "Triangle down"),
    ("<", "Triangle left"),
    (">", "Triangle right"),
    ("D", "Diamond"),
    ("d", "Thin diamond"),
    ("p", "Pentagon"),
    ("h", "Hexagon"),
    ("H", "Rotated hexagon"),
    ("8", "Octagon"),
    ("*", "Star"),
    ("P", "Filled plus"),
    ("X", "Filled X"),
    (".", "Point"),
    (r"$\odot$", "Bullseye"),
    (r"$\oplus$", "Circled plus"),
    ("+", "Plus"),
    ("x", "X"),
    (",", "Pixel"),
    ("|", "Vertical line"),
    ("_", "Horizontal line"),
    ("1", "Tripod down"),
    ("2", "Tripod up"),
    ("3", "Tripod left"),
    ("4", "Tripod right"),
    ("None", "No marker"),
)
MARKERS = [code for code, _label in MARKER_CHOICES]
MARKER_FILL_STYLES = ("full", "none", "left", "right", "bottom", "top")
PARTIAL_MARKER_FILL_STYLES = ("left", "right", "bottom", "top")
FILLABLE_MARKERS = frozenset(
    {".", "o", "v", "^", "<", ">", "8", "s", "p", "*", "h", "H", "D", "d", "P", "X"}
)
# Line styles offered for data series (the four common publication choices).
LINE_STYLES = ["solid", "dashed", "dotted", "dashdot"]
ANNOTATION_LINE_STYLES = [
    "solid",
    "dashed",
    "dotted",
    "dashdot",
    "loosely dashed",
    "densely dashed",
    "loosely dotted",
    "densely dotted",
    "loosely dashdot",
    "densely dashdot",
]


@dataclass
class SeriesConfig:
    x: str = ""
    y: str = ""
    label: str = ""
    color: str = PALETTE[0]
    plot_type: str = "line"
    y_axis: str = "left"
    marker: str = "o"
    marker_fill_style: str = "full"
    line_style: str = "solid"
    line_width: float = 1.0
    marker_size: float = 4.0
    y_offset: float = 0.0
    alpha: float = 1.0
    force_opaque: bool = False
    show_in_legend: bool = True
    error_column: str = ""
    error_cap_size: float = 2.0
    linear_fit_enabled: bool = False
    linear_fit_x_min: float | None = None
    linear_fit_x_max: float | None = None
    linear_fit_line_style: str = "dashed"
    linear_fit_line_width: float = 1.0


@dataclass
class AnnotationConfig:
    kind: str = "text"
    text: str = ""
    x: float = 0.0
    y: float = 0.0
    x2: float = 0.0
    y2: float = 0.0
    width: float = 1.0
    height: float = 1.0
    angle: float = 0.0
    color: str = "#000000"
    line_style: str = "solid"
    alpha: float = 1.0
    fill: bool = False
    font_size: int = 8
    arrow_head_size: float = 12.0


@dataclass
class LegendEntryConfig:
    """One explicit legend item.

    ``source_y`` chooses the plotted series whose marker/line handle is used,
    while ``label`` is the independent text displayed beside that handle.
    An empty ``source_y`` creates a text-only item (or a spacer when ``label``
    is also empty).  Origin-style ``%(n)`` references in ``label`` are resolved
    against the current plotted-series order when the legend is rendered.
    The optional font fields style the entire label.  Empty font-family and
    color values, and a ``None`` font size, inherit the legend defaults.
    """

    source_y: str = ""
    label: str = ""
    font_family: str = ""
    font_size: float | None = None
    font_bold: bool = False
    font_italic: bool = False
    text_color: str = ""


@dataclass
class SeriesColorRecipe:
    """Reproducible color mapping for every plotted series in a graph.

    Final per-series colors remain stored in :class:`SeriesConfig`.  This
    optional recipe records how those colors were generated so style copy can
    resample the same colormap when the destination has a different number of
    plotted series.
    """

    kind: str = "matplotlib_colormap"
    name: str = "viridis"
    start: float = 0.05
    end: float = 0.95
    scope: str = "all_plotted"
    series_count: int = 0
    sampling: str = "linear_endpoints_v1"


@dataclass
class PlotConfig:
    preset: str = "Nature 1-col"
    width_mm: float = 89.0
    height_mm: float = 67.0
    dpi: int = 300
    title: str = ""
    x_label: str = ""
    y_label: str = ""
    y2_label: str = ""
    title_size: int = 9
    axis_size: int = 7
    axis_line_width: float = 0.5
    tick_size: int = 6
    legend_size: int = 6
    x_label_offset_mm: float = 4.0
    y_label_offset_mm: float = 6.0
    x_tick_pad: float = 3.5
    y_tick_pad: float = 3.5
    y2_tick_pad: float = 3.5
    y_axis_color: str = "#000000"
    y2_axis_color: str = "#000000"
    grid: bool = False
    legend: bool = True
    legend_anchor_x: float | None = None
    legend_anchor_y: float | None = None
    legend_row_lengths: list[int] = field(default_factory=list)
    legend_entries: list[LegendEntryConfig] | None = None
    x_scale: str = "linear"
    y_scale: str = "linear"
    y2_scale: str = "linear"
    x_scale_divisor: float = 1.0
    y_scale_divisor: float = 1.0
    y2_scale_divisor: float = 1.0
    x_min: float | None = None
    x_max: float | None = None
    y_min: float | None = None
    y_max: float | None = None
    y2_min: float | None = None
    y2_max: float | None = None
    x_tick_interval: float | None = None
    y_tick_interval: float | None = None
    y2_tick_interval: float | None = None
    x_tick_decimals: int | None = None
    y_tick_decimals: int | None = None
    y2_tick_decimals: int | None = None
    x_scientific_notation: bool = True
    y_scientific_notation: bool = True
    y2_scientific_notation: bool = True
    x_tick_notation: str = "auto"
    y_tick_notation: str = "auto"
    y2_tick_notation: str = "auto"
    x_minor_divisions: int = 5
    y_minor_divisions: int = 5
    y2_minor_divisions: int = 5
    y_offset_step: float = 0.0
    x_break_enabled: bool = False
    x_break_left_min: float | None = None
    x_break_left_max: float | None = None
    x_break_right_min: float | None = None
    x_break_right_max: float | None = None
    x_break_gap: float = 0.08
    y_break_enabled: bool = False
    y_break_lower_min: float | None = None
    y_break_lower_max: float | None = None
    y_break_upper_min: float | None = None
    y_break_upper_max: float | None = None
    y_break_gap: float = 0.08
    show_x_tick_labels: bool = True
    show_y_tick_labels: bool = True
    show_y2_tick_labels: bool = True
    show_tick_marks: bool = True
    show_axis_arrows: bool = False
    show_top_axis: bool = True
    show_bottom_axis: bool = True
    show_left_axis: bool = True
    show_right_axis: bool = True
    fixed_plot_area: bool = True
    plot_width_mm: float = 50.0
    plot_height_mm: float = 50.0
    plot_ratio_locked: bool = False
    plot_ratio_preset: str = "Current"
    plot_aspect_ratio: float = 0.0
    plot_margin_left_mm: float = 14.0
    plot_margin_right_mm: float = 4.0
    plot_margin_top_mm: float = 4.0
    plot_margin_bottom_mm: float = 12.0
    pad_left_mm: float = 0.0
    pad_right_mm: float = 0.0
    pad_top_mm: float = 0.0
    pad_bottom_mm: float = 0.0
    series_color_recipe: SeriesColorRecipe | None = None
    annotations: list[AnnotationConfig] | None = None
    trim_whitespace: bool = False
    transparent: bool = False

    def __post_init__(self) -> None:
        if self.legend_entries is not None:
            normalized_entries: list[LegendEntryConfig] = []
            for entry in self.legend_entries:
                if isinstance(entry, LegendEntryConfig):
                    normalized_entries.append(entry)
                elif isinstance(entry, dict):
                    known = _known_fields(LegendEntryConfig)
                    normalized_entries.append(
                        LegendEntryConfig(
                            **{
                                key: value
                                for key, value in entry.items()
                                if key in known
                            }
                        )
                    )
            self.legend_entries = normalized_entries
        if isinstance(self.series_color_recipe, dict):
            known = _known_fields(SeriesColorRecipe)
            self.series_color_recipe = SeriesColorRecipe(
                **{
                    key: value
                    for key, value in self.series_color_recipe.items()
                    if key in known
                }
            )
        elif not isinstance(
            self.series_color_recipe,
            (SeriesColorRecipe, type(None)),
        ):
            self.series_color_recipe = None
        if self.plot_width_mm <= 0:
            self.plot_width_mm = max(self.width_mm - self.plot_margin_left_mm - self.plot_margin_right_mm, 1.0)
        if self.plot_height_mm <= 0:
            self.plot_height_mm = max(self.height_mm - self.plot_margin_top_mm - self.plot_margin_bottom_mm, 1.0)


def apply_preset(config: PlotConfig, preset_name: str) -> PlotConfig:
    preset = PRESETS[preset_name]
    config.preset = preset.name
    config.width_mm = preset.width_mm
    config.height_mm = preset.height_mm
    config.plot_width_mm = max(config.width_mm - config.plot_margin_left_mm - config.plot_margin_right_mm, 1.0)
    config.plot_height_mm = max(config.height_mm - config.plot_margin_top_mm - config.plot_margin_bottom_mm, 1.0)
    config.dpi = preset.dpi
    config.title_size = preset.font_size + 1
    config.axis_size = preset.axis_size
    config.tick_size = preset.tick_size
    config.legend_size = preset.legend_size
    return config


TICK_NOTATIONS = {
    "auto": "Auto",
    "plain": "Plain numbers",
    "scientific": "Scientific — each tick",
    "shared": "Scientific — shared exponent",
}


def axis_tick_notation(config: PlotConfig, axis: str) -> str:
    mode = getattr(config, f"{axis}_tick_notation")
    if mode not in TICK_NOTATIONS:
        mode = "auto"
    # Honor projects saved with the earlier scientific-notation checkbox.
    if (mode == "auto" and not getattr(config, f"{axis}_scientific_notation")
            and getattr(config, f"{axis}_scale") == "linear"):
        return "plain"
    return mode


def _known_fields(cls) -> set[str]:
    return {item.name for item in fields(cls)}


def plot_config_from_payload(payload: dict[str, Any]) -> PlotConfig:
    """Build a PlotConfig, ignoring keys this version does not know.

    Projects saved by a newer pubfig may carry extra fields; dropping them is
    better than refusing to open the file.
    """
    known = _known_fields(PlotConfig)
    return PlotConfig(**{key: value for key, value in payload.items() if key in known})


def series_config_from_payload(payload: dict[str, Any]) -> SeriesConfig:
    known = _known_fields(SeriesConfig)
    return SeriesConfig(**{key: value for key, value in payload.items() if key in known})


def annotation_config_from_payload(payload: dict[str, Any]) -> AnnotationConfig:
    known = _known_fields(AnnotationConfig)
    return AnnotationConfig(**{key: value for key, value in payload.items() if key in known})


def legend_entry_config_from_payload(payload: dict[str, Any]) -> LegendEntryConfig:
    known = _known_fields(LegendEntryConfig)
    return LegendEntryConfig(**{key: value for key, value in payload.items() if key in known})


def project_to_payload(
    df: pd.DataFrame,
    plot_config: PlotConfig,
    series_configs: list[SeriesConfig],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "data": dataframe_to_payload(df),
        "plot_config": asdict(plot_config),
        "series_config": [asdict(series) for series in series_configs],
    }


def project_from_payload(payload: dict[str, Any]) -> tuple[pd.DataFrame, PlotConfig, list[SeriesConfig]]:
    version = payload.get("schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise ValueError(f"Unsupported project schema_version: {version!r}")

    df = dataframe_from_payload(payload.get("data", {}))
    plot_payload = dict(payload.get("plot_config", {}))
    annotations = [annotation_config_from_payload(item) for item in plot_payload.pop("annotations", None) or []]
    plot_config = plot_config_from_payload(plot_payload)
    plot_config.annotations = annotations
    series_configs = [series_config_from_payload(item) for item in payload.get("series_config", [])]
    return df, plot_config, series_configs


def default_series(x_column: str, y_column: str, index: int = 0) -> SeriesConfig:
    return SeriesConfig(
        x=x_column,
        y=y_column,
        label=y_column,
        color=PALETTE[index % len(PALETTE)],
    )
