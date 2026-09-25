"""Qt- and Matplotlib-independent least-squares fitting helpers."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable
import math


class LinearFitError(ValueError):
    """Raised when a linear fit cannot be calculated from the supplied data."""


@dataclass(frozen=True)
class LinearFitResult:
    """Derived ordinary-least-squares statistics for ``y = slope*x + intercept``."""

    slope: float
    intercept: float
    r_squared: float | None
    point_count: int
    x_min: float
    x_max: float

    @property
    def n(self) -> int:
        """Short alias used in equations and interactive result displays."""

        return self.point_count

    @property
    def sample_count(self) -> int:
        """Compatibility alias for callers that use statistical terminology."""

        return self.point_count

    def predict(self, x_value: float) -> float:
        """Evaluate the fitted line while avoiding intermediate overflow."""

        x_number = _finite_number(x_value)
        if x_number is None:
            raise LinearFitError("Linear-fit prediction X must be a finite number.")
        try:
            prediction = _sum_products(
                [(self.slope, x_number), (self.intercept, 1.0)]
            )
        except (ArithmeticError, ValueError) as error:
            raise LinearFitError(
                "Linear-fit prediction is outside the finite numeric range."
            ) from error
        if not math.isfinite(prediction):
            raise LinearFitError(
                "Linear-fit prediction is outside the finite numeric range."
            )
        return prediction

    def equation_text(self, significant_digits: int = 5) -> str:
        """Return a compact equation, goodness of fit, and sample count."""

        digits = max(1, int(significant_digits))
        slope = format(self.slope, f".{digits}g")
        intercept = format(abs(self.intercept), f".{digits}g")
        operator = "−" if self.intercept < 0 else "+"
        r_squared = (
            "undefined"
            if self.r_squared is None
            else format(self.r_squared, f".{digits}g")
        )
        return (
            f"y = {slope}x {operator} {intercept}   "
            f"R² = {r_squared}   n = {self.point_count}"
        )


def _finite_number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _range_bound(value: float | None, label: str) -> float | None:
    if value is None:
        return None
    number = _finite_number(value)
    if number is None:
        raise LinearFitError(f"{label} must be a finite number.")
    return number


def _multiply_ratio(value: float, numerator: float, denominator: float) -> float:
    """Evaluate ``value * numerator / denominator`` without intermediate overflow."""

    value_mantissa, value_exponent = math.frexp(value)
    numerator_mantissa, numerator_exponent = math.frexp(numerator)
    denominator_mantissa, denominator_exponent = math.frexp(denominator)
    mantissa = value_mantissa * numerator_mantissa / denominator_mantissa
    return math.ldexp(
        mantissa,
        value_exponent + numerator_exponent - denominator_exponent,
    )


def _power_of_two_scale(maximum: float) -> float:
    """Return a finite power-of-two scale no larger than ``maximum``."""

    _mantissa, exponent = math.frexp(maximum)
    return math.ldexp(1.0, exponent - 1)


def _translated_coordinates(values: list[float]) -> tuple[float, float, list[float]]:
    """Translate before scaling so small changes on large offsets are retained."""

    origin = values[0]
    deltas = [value - origin for value in values]
    if all(math.isfinite(delta) for delta in deltas):
        maximum = max(abs(delta) for delta in deltas)
        if maximum == 0.0:
            return origin, 1.0, [0.0] * len(values)
        scale = _power_of_two_scale(maximum)
        return origin, scale, [delta / scale for delta in deltas]

    # Opposite-sign values near the float limit can overflow during raw
    # subtraction. Dividing by a power of two first is exact in binary and
    # keeps the translated coordinates finite without discarding low bits in
    # the usual large-offset case handled above.
    maximum = max(abs(value) for value in values)
    scale = _power_of_two_scale(maximum)
    scaled_origin = origin / scale
    return origin, scale, [value / scale - scaled_origin for value in values]


def _sum_products(terms: list[tuple[float, float]]) -> float:
    """Sum two-factor products without overflowing intermediate products."""

    parts: list[tuple[float, int]] = []
    for first, second in terms:
        if first == 0.0 or second == 0.0:
            continue
        first_mantissa, first_exponent = math.frexp(first)
        second_mantissa, second_exponent = math.frexp(second)
        mantissa, exponent_adjustment = math.frexp(
            first_mantissa * second_mantissa
        )
        parts.append(
            (
                mantissa,
                first_exponent + second_exponent + exponent_adjustment,
            )
        )
    if not parts:
        return 0.0
    common_exponent = max(exponent for _mantissa, exponent in parts)
    scaled_sum = math.fsum(
        math.ldexp(mantissa, exponent - common_exponent)
        for mantissa, exponent in parts
    )
    return math.ldexp(scaled_sum, common_exponent)


def linear_fit(
    x_values: Iterable[object],
    y_values: Iterable[object],
    *,
    x_min: float | None = None,
    x_max: float | None = None,
) -> LinearFitResult:
    """Calculate an unweighted linear least-squares fit.

    Non-numeric and non-finite X/Y pairs are ignored. Optional X bounds are
    inclusive, and the returned line extent is the observed range of the
    points actually used rather than an extrapolated requested range.
    """

    lower = _range_bound(x_min, "Linear-fit X minimum")
    upper = _range_bound(x_max, "Linear-fit X maximum")
    if lower is not None and upper is not None and lower > upper:
        raise LinearFitError(
            "Linear-fit X minimum must be less than or equal to the X maximum."
        )

    raw_x = list(x_values)
    raw_y = list(y_values)
    if len(raw_x) != len(raw_y):
        raise LinearFitError("Linear fit requires matching X and Y value counts.")

    points: list[tuple[float, float]] = []
    for raw_x_value, raw_y_value in zip(raw_x, raw_y):
        x_value = _finite_number(raw_x_value)
        y_value = _finite_number(raw_y_value)
        if x_value is None or y_value is None:
            continue
        if lower is not None and x_value < lower:
            continue
        if upper is not None and x_value > upper:
            continue
        points.append((x_value, y_value))

    if len(points) < 2:
        raise LinearFitError(
            "Linear fit requires at least 2 finite points in the selected X range."
        )

    count = len(points)
    x_origin, x_scale, scaled_x = _translated_coordinates(
        [x for x, _y in points]
    )
    y_origin, y_scale, scaled_y = _translated_coordinates(
        [y for _x, y in points]
    )

    try:
        mean_scaled_x = math.fsum(scaled_x) / count
        mean_scaled_y = math.fsum(scaled_y) / count
        centered_x = [x - mean_scaled_x for x in scaled_x]
        centered_y = [y - mean_scaled_y for y in scaled_y]
        x_sum_squares = math.fsum(value * value for value in centered_x)
        covariance = math.fsum(
            x_value * y_value
            for x_value, y_value in zip(centered_x, centered_y)
        )
    except (ArithmeticError, ValueError) as error:
        raise LinearFitError(
            "Linear fit could not be calculated for the magnitude of these values."
        ) from error
    if x_sum_squares == 0.0:
        raise LinearFitError("Linear fit requires at least 2 distinct X values.")

    normalized_slope = covariance / x_sum_squares
    try:
        slope = _multiply_ratio(normalized_slope, y_scale, x_scale)
        normalized_intercept = mean_scaled_y - normalized_slope * mean_scaled_x
        intercept = _sum_products(
            [
                (y_origin, 1.0),
                (normalized_intercept, y_scale),
                (-slope, x_origin),
            ]
        )
    except (ArithmeticError, ValueError) as error:
        raise LinearFitError(
            "Linear fit could not be calculated for the magnitude of these values."
        ) from error
    if not math.isfinite(slope) or not math.isfinite(intercept):
        raise LinearFitError("Linear fit could not be calculated for these values.")

    try:
        total_sum_squares = math.fsum(value * value for value in centered_y)
        inverse_slope = (
            covariance / total_sum_squares
            if total_sum_squares > 0.0
            else 0.0
        )
    except (ArithmeticError, ValueError) as error:
        raise LinearFitError(
            "Linear fit could not be calculated for the magnitude of these values."
        ) from error
    r_squared: float | None
    if total_sum_squares == 0.0:
        r_squared = None
    else:
        # For simple OLS with an intercept, R² is the squared correlation.
        # This form preserves very small positive values that are lost through
        # cancellation in ``1 - residual_sum_squares / total_sum_squares``.
        r_squared = normalized_slope * inverse_slope
        if r_squared > 1.0:
            r_squared = 1.0

    observed_x = [x for x, _y in points]
    return LinearFitResult(
        slope=slope,
        intercept=intercept,
        r_squared=r_squared,
        point_count=count,
        x_min=min(observed_x),
        x_max=max(observed_x),
    )


calculate_linear_fit = linear_fit


__all__ = [
    "LinearFitError",
    "LinearFitResult",
    "calculate_linear_fit",
    "linear_fit",
]
