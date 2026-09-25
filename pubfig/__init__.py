"""Publication figure editor core package."""

from .fitting import LinearFitError, LinearFitResult, calculate_linear_fit, linear_fit
from .plot_config import AnnotationConfig, PlotConfig, SeriesConfig

__all__ = [
    "AnnotationConfig",
    "LinearFitError",
    "LinearFitResult",
    "PlotConfig",
    "SeriesConfig",
    "calculate_linear_fit",
    "linear_fit",
]
