"""Numeric tick formats independent of the data scale and divisor."""

from decimal import Decimal
import math

from matplotlib.ticker import Formatter, FuncFormatter, NullFormatter, ScalarFormatter


class _DividedAxis:
    """Present scaled intervals to a formatter without changing the real axis."""

    def __init__(self, axis, divisor):
        self._axis = axis
        self._divisor = divisor

    def __getattr__(self, name):
        return getattr(self._axis, name)

    def get_view_interval(self):
        return self._axis.get_view_interval() / self._divisor

    def get_data_interval(self):
        return self._axis.get_data_interval() / self._divisor

    def get_minpos(self):
        return self._axis.get_minpos() / self._divisor


class DividedTickFormatter(Formatter):
    """Divide labels only; data coordinates, limits and tick positions stay raw."""

    def __init__(self, formatter, divisor):
        super().__init__()
        self.formatter = formatter
        self.divisor = divisor

    def set_axis(self, axis):
        super().set_axis(axis)
        self.formatter.set_axis(_DividedAxis(axis, self.divisor))

    def set_locs(self, locs):
        super().set_locs(locs)
        self.formatter.set_locs([value / self.divisor for value in locs])

    def __call__(self, value, pos=None):
        return self.formatter(value / self.divisor, pos)

    def get_offset(self):
        return self.formatter.get_offset()

    def format_data(self, value):
        return self.formatter.format_data(value / self.divisor)

    def format_data_short(self, value):
        return self.formatter.format_data_short(value / self.divisor)


def apply_tick_divisor(axis, divisor):
    for get_formatter, set_formatter in (
        (axis.get_major_formatter, axis.set_major_formatter),
        (axis.get_minor_formatter, axis.set_minor_formatter),
    ):
        formatter = get_formatter()
        # Shared X axes may be configured once for each Y axis. Never stack
        # division wrappers, and restore ordinary formatting when reset to 1.
        if isinstance(formatter, DividedTickFormatter):
            formatter = formatter.formatter
        set_formatter(DividedTickFormatter(formatter, divisor) if divisor != 1 else formatter)


def _fixed(value: float, decimals: int) -> str:
    text = f"{value:.{decimals}f}"
    if text == f"-{0:.{decimals}f}":
        text = text[1:]
    return Formatter.fix_minus(text)


def _scientific(value: float, decimals: int | None) -> str:
    if not math.isfinite(value):
        return ""
    if value == 0:
        return "0" if decimals is None else _fixed(0, decimals)
    coefficient, exponent = f"{value:.{10 if decimals is None else decimals}e}".split("e")
    if decimals is None:
        coefficient = coefficient.rstrip("0").rstrip(".")
    return rf"${coefficient}\times10^{{{int(exponent)}}}$"


class SharedExponentFormatter(Formatter):
    """One multiplier at the axis edge, with optional coefficient precision.

    A separate ScalarFormatter chooses automatic decimal precision on normalized
    ticks using public APIs, without relying on Matplotlib's internal fields.
    """

    def __init__(self, decimals: int | None):
        super().__init__()
        self.decimals = decimals
        self.exponent = 0
        self.factor = 1.0
        self._has_locs = False
        self._automatic = ScalarFormatter(useOffset=False, useMathText=False)
        self._automatic.set_scientific(False)
        self._automatic.create_dummy_axis()

    def set_locs(self, locs):
        super().set_locs(locs)
        self._has_locs = bool(len(locs))
        low, high = sorted(self.axis.get_view_interval())
        visible = [abs(value) for value in locs if math.isfinite(value) and low <= value <= high]
        magnitude = max(visible, default=max(abs(low), abs(high)))
        self.exponent = max(-323, min(308, math.floor(math.log10(magnitude)))) if magnitude else 0
        self.factor = 10.0 ** self.exponent
        self._automatic.axis.set_view_interval(low / self.factor, high / self.factor)
        self._automatic.set_locs([value / self.factor for value in locs])

    def __call__(self, value, pos=None):
        scaled = value / self.factor
        if self.decimals is not None:
            return _fixed(scaled, self.decimals)
        return self._automatic(scaled, pos)

    def get_offset(self):
        return rf"$\times10^{{{self.exponent}}}$" if self._has_locs else ""


def apply_tick_format(axis, mode: str, decimals: int | None, warnings: list[str], label: str):
    if decimals is not None and (type(decimals) is not int or not 0 <= decimals <= 12):
        warnings.append(f"{label} tick decimals must be an integer from 0 to 12.")
        decimals = None
    if mode == "scientific":
        axis.set_major_formatter(FuncFormatter(lambda value, _pos: _scientific(value, decimals)))
    elif mode == "shared":
        axis.set_major_formatter(SharedExponentFormatter(decimals))
    elif decimals is not None:
        # Preserve the original Auto + fixed decimals behavior.
        axis.set_major_formatter(FuncFormatter(lambda value, _pos: _fixed(value, decimals)))
    elif mode == "plain":
        if axis.get_scale() == "linear":
            formatter = ScalarFormatter(useOffset=False)
            formatter.set_scientific(False)
            axis.set_major_formatter(formatter)
        else:
            # Decimal expands exponent notation without rounding tiny log ticks
            # to zero or introducing binary floating-point tails.
            axis.set_major_formatter(FuncFormatter(
                lambda value, _pos: Formatter.fix_minus(format(Decimal(str(value)), "f"))
            ))
    if mode != "auto" and axis.get_scale() == "log":
        axis.set_minor_formatter(NullFormatter())
