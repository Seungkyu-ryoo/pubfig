"""Small, reusable bindings between Qt widgets and configuration objects."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, MutableMapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from PySide6.QtCore import QObject, QSignalBlocker


ValueT = TypeVar("ValueT")
ModelT = TypeVar("ModelT")
ChangeCallback = Callable[[], None]


def _unique_qobjects(
    values: tuple[QObject | Iterable[QObject], ...],
) -> list[QObject]:
    if len(values) == 1 and not isinstance(values[0], QObject):
        candidates = values[0]
    else:
        candidates = values

    objects: list[QObject] = []
    seen: set[int] = set()
    for candidate in candidates:
        if not isinstance(candidate, QObject):
            raise TypeError(
                "signals_blocked expects QObject instances; "
                f"got {type(candidate).__name__}"
            )
        identity = id(candidate)
        if identity not in seen:
            seen.add(identity)
            objects.append(candidate)
    return objects


@contextmanager
def signals_blocked(
    *objects: QObject | Iterable[QObject],
) -> Iterator[None]:
    """Temporarily block signals for one or more Qt objects.

    Either ``signals_blocked(first, second)`` or
    ``signals_blocked([first, second])`` may be used. Duplicate objects are
    blocked once. ``QSignalBlocker`` remembers each object's previous state,
    which means objects that were already blocked remain blocked afterwards.
    All objects are restored even when the protected operation raises.
    """

    qt_objects = _unique_qobjects(objects)
    blockers = [QSignalBlocker(obj) for obj in qt_objects]
    try:
        yield
    finally:
        # Explicitly restoring in reverse order makes the lifetime guarantee
        # independent of Python garbage-collection timing.
        for blocker in reversed(blockers):
            blocker.unblock()


@dataclass(slots=True)
class FieldBinding(Generic[ValueT]):
    """Describe one configuration field backed by one Qt widget.

    ``read`` and ``write`` are intentionally arbitrary callables rather than a
    hard-coded widget protocol.  A normal spin box can use ``spin.value`` and
    ``spin.setValue``; optional numbers, positive-number validation, color
    buttons, and compound controls can use small conversion functions instead.

    ``signal`` may be a single bound Qt signal, several bound signals, or
    ``None`` when a field is only loaded/saved programmatically.
    """

    field: str
    widget: QObject
    read: Callable[[], ValueT]
    write: Callable[[ValueT], None]
    signal: Any | Iterable[Any] | None = None

    def __post_init__(self) -> None:
        if not self.field:
            raise ValueError("A field binding requires a non-empty field name")
        if not isinstance(self.widget, QObject):
            raise TypeError("FieldBinding.widget must be a QObject")
        if not callable(self.read):
            raise TypeError("FieldBinding.read must be callable")
        if not callable(self.write):
            raise TypeError("FieldBinding.write must be callable")

    def value(self) -> ValueT:
        """Read and convert the current widget value."""

        return self.read()

    def set_value(self, value: ValueT) -> None:
        """Convert and write a model value to the widget."""

        self.write(value)

    def signals(self) -> tuple[Any, ...]:
        """Return this binding's change signals in a uniform form."""

        if self.signal is None:
            return ()
        if hasattr(self.signal, "connect"):
            return (self.signal,)
        try:
            signals = tuple(self.signal)
        except TypeError as error:
            raise TypeError(
                f"Signal for field {self.field!r} must provide connect() or be iterable"
            ) from error
        for signal in signals:
            if not hasattr(signal, "connect"):
                raise TypeError(
                    f"Signal for field {self.field!r} does not provide connect()"
                )
        return signals


class BindingRegistry:
    """Coordinate a collection of :class:`FieldBinding` objects.

    The registry is deliberately independent of any particular config class.
    It can load from and update either attribute-based objects (including
    dataclasses) or mappings.
    """

    def __init__(self, bindings: Iterable[FieldBinding[Any]] = ()) -> None:
        self._bindings: list[FieldBinding[Any]] = []
        self._by_field: dict[str, FieldBinding[Any]] = {}
        self._connections: list[tuple[Any, Callable[..., None]]] = []
        self.extend(bindings)

    def __iter__(self) -> Iterator[FieldBinding[Any]]:
        return iter(self._bindings)

    def __len__(self) -> int:
        return len(self._bindings)

    def __contains__(self, field: object) -> bool:
        return field in self._by_field

    def __getitem__(self, field: str) -> FieldBinding[Any]:
        return self._by_field[field]

    @property
    def fields(self) -> tuple[str, ...]:
        return tuple(binding.field for binding in self._bindings)

    @property
    def widgets(self) -> tuple[QObject, ...]:
        """Return unique bound widgets in registration order."""

        widgets: list[QObject] = []
        seen: set[int] = set()
        for binding in self._bindings:
            identity = id(binding.widget)
            if identity not in seen:
                seen.add(identity)
                widgets.append(binding.widget)
        return tuple(widgets)

    def add(self, binding: FieldBinding[Any]) -> FieldBinding[Any]:
        """Register one binding and return it."""

        if not isinstance(binding, FieldBinding):
            raise TypeError("BindingRegistry only accepts FieldBinding instances")
        if binding.field in self._by_field:
            raise ValueError(f"Duplicate field binding: {binding.field!r}")
        self._bindings.append(binding)
        self._by_field[binding.field] = binding
        return binding

    def extend(self, bindings: Iterable[FieldBinding[Any]]) -> None:
        for binding in bindings:
            self.add(binding)

    @staticmethod
    def _model_value(source: object, field: str) -> Any:
        if isinstance(source, Mapping):
            return source[field]
        return getattr(source, field)

    @staticmethod
    def _set_model_value(target: object, field: str, value: Any) -> None:
        if isinstance(target, MutableMapping):
            target[field] = value
            return
        setattr(target, field, value)

    def load(self, source: Mapping[str, Any] | object) -> None:
        """Load all model values into widgets without emitting their signals.

        Values are collected before any widget is changed.  During writes the
        complete unique widget set is guarded by :func:`signals_blocked`, so a
        failed custom writer cannot leave signals blocked.
        """

        values = [
            (binding, self._model_value(source, binding.field))
            for binding in self._bindings
        ]
        with signals_blocked(self.widgets):
            for binding, value in values:
                binding.set_value(value)

    def values(self) -> dict[str, Any]:
        """Read all widget values as a new field-to-value dictionary."""

        return {binding.field: binding.value() for binding in self._bindings}

    def update(self, target: ModelT) -> ModelT:
        """Write all current widget values to ``target`` and return it."""

        values = self.values()
        for field, value in values.items():
            self._set_model_value(target, field, value)
        return target

    def connect(self, callback: ChangeCallback) -> None:
        """Invoke a no-argument callback when any bound signal fires.

        Qt signal payloads are deliberately discarded, giving all widget types
        one consistent callback contract. The originating field can still be
        determined by registering separate registries when needed.
        """

        if not callable(callback):
            raise TypeError("BindingRegistry.connect requires a callable")
        for binding in self._bindings:
            for signal in binding.signals():
                slot = lambda *_args, _callback=callback: _callback()
                signal.connect(slot)
                self._connections.append((signal, slot))

    def disconnect(self) -> None:
        """Disconnect callbacks previously connected by this registry."""

        while self._connections:
            signal, slot = self._connections.pop()
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                # The sender may already have been destroyed, or external code
                # may have disconnected the slot.
                pass


__all__ = ["BindingRegistry", "FieldBinding", "signals_blocked"]
