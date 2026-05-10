"""Plot configuration and project schema for Graph_drawer."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

import pandas as pd


SCHEMA_VERSION = 1

PALETTE = [
    "#4477AA",
    "#EE6677",
    "#228833",
    "#CCBB44",
    "#66CCEE",
    "#AA3377",
    "#BBBBBB",
    "#000000",
]


@dataclass(frozen=True)
class FigurePreset:
    name: str
    width_mm: float
    height_mm: float
    dpi: int
    font_size: int = 8
    axis_size: int = 8
    tick_size: int = 7
    legend_size: int = 7


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
MARKERS = ["o", "s", "^", "v", "D", "x", "+", "*", "."]
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
    line_width: float = 1.0
    marker_size: float = 4.0
    y_offset: float = 0.0
    alpha: float = 1.0
    show_in_legend: bool = True


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
    axis_size: int = 8
    tick_size: int = 7
    legend_size: int = 7
    y_label_offset_mm: float = 10.0
    y2_axis_color: str = "#000000"
    grid: bool = True
    legend: bool = True
    legend_anchor_x: float | None = None
    legend_anchor_y: float | None = None
    x_scale: str = "linear"
    y_scale: str = "linear"
    y2_scale: str = "linear"
    x_min: float | None = None
    x_max: float | None = None
    y_min: float | None = None
    y_max: float | None = None
    y2_min: float | None = None
    y2_max: float | None = None
    x_tick_interval: float | None = None
    y_tick_interval: float | None = None
    y2_tick_interval: float | None = None
    y_offset_step: float = 0.0
    y_break_enabled: bool = False
    y_break_lower_min: float | None = None
    y_break_lower_max: float | None = None
    y_break_upper_min: float | None = None
    y_break_upper_max: float | None = None
    y_break_gap: float = 0.08
    show_x_tick_labels: bool = True
    show_y_tick_labels: bool = True
    show_y2_tick_labels: bool = True
    fixed_plot_area: bool = True
    plot_width_mm: float = 0.0
    plot_height_mm: float = 0.0
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
    annotations: list[AnnotationConfig] | None = None
    trim_whitespace: bool = False
    transparent: bool = False

    def __post_init__(self) -> None:
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


def dataframe_to_payload(df: pd.DataFrame) -> dict[str, Any]:
    rows: list[list[Any]] = []
    for _, row in df.iterrows():
        values: list[Any] = []
        for value in row.tolist():
            if pd.isna(value):
                values.append(None)
            elif isinstance(value, float) and not math.isfinite(value):
                values.append(None)
            else:
                values.append(value)
        rows.append(values)
    return {"columns": list(map(str, df.columns)), "rows": rows}


def dataframe_from_payload(payload: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(payload.get("rows", []), columns=payload.get("columns", []))


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
    if version != SCHEMA_VERSION:
        raise ValueError(f"Unsupported project schema_version: {version!r}")

    df = dataframe_from_payload(payload.get("data", {}))
    plot_payload = payload.get("plot_config", {})
    annotations = [AnnotationConfig(**item) for item in plot_payload.pop("annotations", [])] if plot_payload.get("annotations") else []
    plot_config = PlotConfig(**plot_payload)
    plot_config.annotations = annotations
    series_configs = [SeriesConfig(**item) for item in payload.get("series_config", [])]
    return df, plot_config, series_configs


def default_series(x_column: str, y_column: str, index: int = 0) -> SeriesConfig:
    return SeriesConfig(
        x=x_column,
        y=y_column,
        label=y_column,
        color=PALETTE[index % len(PALETTE)],
    )
