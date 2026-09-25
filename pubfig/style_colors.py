"""Color recipes shared by style capture, persistence, and application."""

from __future__ import annotations

from collections.abc import Sequence
import math

import matplotlib as mpl
from matplotlib.colors import to_hex

from .plot_config import SeriesColorRecipe, SeriesConfig


def sample_series_color_recipe(
    recipe: SeriesColorRecipe | None,
    count: int,
) -> list[str]:
    """Return ``count`` colors spanning a supported full-series recipe."""

    if recipe is None or count <= 0:
        return []
    if (
        recipe.kind != "matplotlib_colormap"
        or recipe.scope != "all_plotted"
        or recipe.sampling != "linear_endpoints_v1"
    ):
        return []
    try:
        start = float(recipe.start)
        end = float(recipe.end)
    except (TypeError, ValueError):
        return []
    if (
        not math.isfinite(start)
        or not math.isfinite(end)
        or not 0.0 <= start <= 1.0
        or not 0.0 <= end <= 1.0
    ):
        return []
    try:
        cmap = mpl.colormaps[recipe.name]
    except (KeyError, TypeError):
        return []

    if count == 1:
        positions = [(start + end) / 2.0]
    else:
        positions = [
            start + (end - start) * index / (count - 1)
            for index in range(count)
        ]
    return [to_hex(cmap(position)) for position in positions]


def series_color_recipe_matches(
    recipe: SeriesColorRecipe | None,
    series_configs: Sequence[SeriesConfig],
) -> bool:
    """Whether a recipe exactly describes the current complete series list."""

    if (
        recipe is None
        or recipe.series_count != len(series_configs)
        or not series_configs
    ):
        return False
    expected = sample_series_color_recipe(recipe, len(series_configs))
    if len(expected) != len(series_configs):
        return False
    for series, color in zip(series_configs, expected):
        try:
            actual = to_hex(series.color)
        except (TypeError, ValueError):
            return False
        try:
            alpha_is_opaque = math.isclose(float(series.alpha), 1.0, abs_tol=1e-9)
        except (TypeError, ValueError):
            return False
        if (
            actual.casefold() != color.casefold()
            or not alpha_is_opaque
            or not series.force_opaque
        ):
            return False
    return True


__all__ = ["sample_series_color_recipe", "series_color_recipe_matches"]
