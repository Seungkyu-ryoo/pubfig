"""Qt-independent undo/redo history management.

The UI owns the mechanics of capturing and restoring a workspace.  This
module owns the history rules: a bounded undo/redo stack, a baseline used by
post-change widget signals, and time-based coalescing of rapid edits.

``capture`` must return a snapshot that remains isolated from later workspace
mutations.  This may be a deep copy or a structurally shared, copy-on-write
snapshot.  Stored snapshots are never exposed to callers.  Before restoring,
the manager clones a snapshot by default, so a restore function may install
and mutate the supplied object without corrupting history.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from time import monotonic
from typing import Callable, Generic, Hashable, Iterator, Mapping, TypeVar


SnapshotT = TypeVar("SnapshotT")
ResultT = TypeVar("ResultT")

DEFAULT_UNDO_LIMIT = 50
DEFAULT_COALESCE_INTERVAL_SECONDS = 0.7


@dataclass(frozen=True)
class _HistoryEntry(Generic[SnapshotT]):
    snapshot: SnapshotT
    weight: int
    resources: tuple[tuple[Hashable, int], ...] = ()


class UndoManager(Generic[SnapshotT]):
    """Manage bounded workspace snapshots without depending on Qt.

    There are two ways to record an edit:

    * :meth:`checkpoint_current` records the current state immediately before
      a discrete mutation.
    * :meth:`perform_change` uses the last baseline for controls whose Qt
      signal arrives after the widget has already changed.  Repeated calls
      with the same non-``None`` key inside the coalescing interval form one
      undo entry.

    The baseline is moved into the undo stack instead of being deep-copied.
    It is safe to do so because captures are manager-owned and baseline
    refresh replaces the snapshot rather than mutating it.
    """

    def __init__(
        self,
        capture: Callable[[], SnapshotT],
        restore: Callable[[SnapshotT], None],
        *,
        limit: int = DEFAULT_UNDO_LIMIT,
        coalesce_interval: float = DEFAULT_COALESCE_INTERVAL_SECONDS,
        clone_for_restore: Callable[[SnapshotT], SnapshotT] | None = deepcopy,
        snapshot_weight: Callable[[SnapshotT], int] | None = None,
        snapshot_resources: (
            Callable[[SnapshotT], Mapping[Hashable, int]] | None
        ) = None,
        max_stack_weight: int | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        if coalesce_interval < 0:
            raise ValueError("coalesce_interval cannot be negative")
        has_weight_source = (
            snapshot_weight is not None or snapshot_resources is not None
        )
        if has_weight_source != (max_stack_weight is not None):
            raise ValueError(
                "a snapshot weight/resource callback and max_stack_weight "
                "must be configured together"
            )
        if max_stack_weight is not None and (
            isinstance(max_stack_weight, bool)
            or not isinstance(max_stack_weight, int)
            or max_stack_weight <= 0
        ):
            raise ValueError("max_stack_weight must be a positive integer")

        self._capture = capture
        self._restore = restore
        self._clone_for_restore = clone_for_restore
        self._snapshot_weight = snapshot_weight
        self._snapshot_resources = snapshot_resources
        self._max_stack_weight = max_stack_weight
        self._clock = clock
        self._limit = limit
        self._coalesce_interval = coalesce_interval

        self._undo: list[_HistoryEntry[SnapshotT]] = []
        self._redo: list[_HistoryEntry[SnapshotT]] = []
        self._undo_weight = 0
        self._redo_weight = 0
        self._baseline: _HistoryEntry[SnapshotT] | None = None
        self._coalesce_key: Hashable | None = None
        self._coalesce_deadline: float | None = None
        self._restore_depth = 0
        self._suspend_depth = 0

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def undo_count(self) -> int:
        return len(self._undo)

    @property
    def redo_count(self) -> int:
        return len(self._redo)

    @property
    def undo_weight(self) -> int:
        return self._undo_weight

    @property
    def redo_weight(self) -> int:
        return self._redo_weight

    @property
    def baseline_weight(self) -> int:
        return 0 if self._baseline is None else self._baseline.weight

    @property
    def max_stack_weight(self) -> int | None:
        return self._max_stack_weight

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def has_baseline(self) -> bool:
        return self._baseline is not None

    @property
    def is_restoring(self) -> bool:
        return self._restore_depth > 0

    @property
    def is_suspended(self) -> bool:
        return self._suspend_depth > 0

    @property
    def coalescing_key(self) -> Hashable | None:
        """Return the active key, expiring it first when its window elapsed."""

        if self._coalescing_expired(self._clock()):
            self.end_coalescing()
        return self._coalesce_key

    def reset(self, *, capture_baseline: bool = True) -> None:
        """Clear both stacks and optionally capture a fresh baseline."""

        baseline = self._capture_entry() if capture_baseline else None
        self._clear_stack(self._undo, undo=True)
        self._clear_stack(self._redo, undo=False)
        self.end_coalescing()
        self._baseline = baseline

    def refresh_baseline(self) -> bool:
        """Replace the baseline with the current workspace snapshot."""

        if not self._is_recording:
            return False
        baseline = self._capture_entry()
        self._baseline = baseline
        self._rebalance_stack(self._undo, undo=True)
        self._rebalance_stack(self._redo, undo=False)
        return True

    def checkpoint_current(self) -> bool:
        """Record current state before a discrete change and clear redo."""

        if not self._is_recording:
            return False
        entry = self._capture_entry()
        self.end_coalescing()
        self._clear_stack(self._redo, undo=False)
        return self._append_bounded(self._undo, entry, undo=True)

    def checkpoint_baseline(self) -> bool:
        """Record the pre-change baseline and clear redo.

        This operation intentionally does not clone the baseline.  The
        manager owns it, and the next :meth:`refresh_baseline` replaces it.
        """

        if not self._is_recording or self._baseline is None:
            return False
        entry = self._baseline
        self._clear_stack(self._redo, undo=False)
        return self._append_bounded(self._undo, entry, undo=True)

    def perform_change(
        self,
        change: Callable[[], ResultT],
        *,
        coalesce_key: Hashable | None = None,
    ) -> ResultT:
        """Run ``change`` and record its pre-change baseline.

        A ``None`` key always starts a new undo entry.  A non-``None`` key
        continues the current entry only when it matches the preceding key
        and arrives before the rolling deadline.  This mirrors the old
        single-shot 700 ms Qt timer without importing Qt.
        """

        if not self._is_recording:
            return change()

        now = self._clock()
        continues = (
            coalesce_key is not None
            and coalesce_key == self._coalesce_key
            and not self._coalescing_expired(now)
        )
        if not continues:
            self.checkpoint_baseline()

        if coalesce_key is None:
            self.end_coalescing()
        else:
            self._coalesce_key = coalesce_key
            self._coalesce_deadline = now + self._coalesce_interval

        result = change()
        self.refresh_baseline()
        return result

    def end_coalescing(self) -> None:
        """Force the next edit to start a new undo entry."""

        self._coalesce_key = None
        self._coalesce_deadline = None

    def undo(self) -> bool:
        """Restore the latest undo snapshot, returning whether one existed."""

        self.end_coalescing()
        if not self._undo or not self._is_recording:
            return False

        current = self._capture_entry()
        target = self._undo[-1]
        self._restore_safely(target.snapshot)
        self._pop_latest(self._undo, undo=True)
        self._append_bounded(self._redo, current, undo=False)
        self._baseline = target
        self._rebalance_stack(self._undo, undo=True)
        self._rebalance_stack(self._redo, undo=False)
        return True

    def redo(self) -> bool:
        """Restore the latest redo snapshot, returning whether one existed."""

        self.end_coalescing()
        if not self._redo or not self._is_recording:
            return False

        current = self._capture_entry()
        target = self._redo[-1]
        self._restore_safely(target.snapshot)
        self._pop_latest(self._redo, undo=False)
        self._append_bounded(self._undo, current, undo=True)
        self._baseline = target
        self._rebalance_stack(self._undo, undo=True)
        self._rebalance_stack(self._redo, undo=False)
        return True

    @contextmanager
    def suspended(self) -> Iterator[None]:
        """Temporarily run model-loading code without recording history."""

        self.end_coalescing()
        self._suspend_depth += 1
        try:
            yield
        finally:
            self._suspend_depth -= 1

    @property
    def _is_recording(self) -> bool:
        return not self.is_restoring and not self.is_suspended

    def _coalescing_expired(self, now: float) -> bool:
        deadline = self._coalesce_deadline
        return deadline is None or now >= deadline

    def _entry(self, snapshot: SnapshotT) -> _HistoryEntry[SnapshotT]:
        weight = (
            0
            if self._snapshot_weight is None
            else self._snapshot_weight(snapshot)
        )
        if isinstance(weight, bool) or not isinstance(weight, int) or weight < 0:
            raise ValueError("snapshot_weight must return a non-negative integer")
        resources = self._snapshot_resource_items(snapshot)
        return _HistoryEntry(
            snapshot=snapshot,
            weight=weight,
            resources=resources,
        )

    def _snapshot_resource_items(
        self,
        snapshot: SnapshotT,
    ) -> tuple[tuple[Hashable, int], ...]:
        if self._snapshot_resources is None:
            return ()
        raw_resources = self._snapshot_resources(snapshot)
        if not isinstance(raw_resources, Mapping):
            raise ValueError("snapshot_resources must return a mapping")
        resources: list[tuple[Hashable, int]] = []
        for token, size in raw_resources.items():
            try:
                hash(token)
            except TypeError as exc:
                raise ValueError("snapshot resource keys must be hashable") from exc
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise ValueError(
                    "snapshot resource sizes must be non-negative integers"
                )
            resources.append((token, size))
        return tuple(resources)

    def _capture_entry(self) -> _HistoryEntry[SnapshotT]:
        return self._entry(self._capture())

    def _append_bounded(
        self,
        stack: list[_HistoryEntry[SnapshotT]],
        entry: _HistoryEntry[SnapshotT],
        *,
        undo: bool,
    ) -> bool:
        stack.append(entry)
        self._rebalance_stack(stack, undo=undo)
        return any(candidate is entry for candidate in stack)

    def _set_stack_weight(self, value: int, *, undo: bool) -> None:
        if undo:
            self._undo_weight = value
        else:
            self._redo_weight = value

    def _calculate_stack_weight(
        self,
        stack: list[_HistoryEntry[SnapshotT]],
    ) -> int:
        total = sum(entry.weight for entry in stack)
        if self._snapshot_resources is None:
            return total

        # Buffers in the baseline are also part of the live workspace, so the
        # history does not retain an additional copy of them.  Every other
        # backing buffer is counted once even when many style-only snapshots
        # refer to it.
        seen = {
            token
            for token, _size in (
                self._baseline.resources if self._baseline is not None else ()
            )
        }
        for entry in reversed(stack):
            for token, size in entry.resources:
                if token in seen:
                    continue
                seen.add(token)
                total += size
        return total

    def _rebalance_stack(
        self,
        stack: list[_HistoryEntry[SnapshotT]],
        *,
        undo: bool,
    ) -> None:
        overflow = len(stack) - self._limit
        if overflow > 0:
            del stack[:overflow]

        weight = self._calculate_stack_weight(stack)
        maximum = self._max_stack_weight
        while stack and maximum is not None and weight > maximum:
            stack.pop(0)
            weight = self._calculate_stack_weight(stack)
        self._set_stack_weight(weight, undo=undo)


    def _pop_latest(
        self,
        stack: list[_HistoryEntry[SnapshotT]],
        *,
        undo: bool,
    ) -> _HistoryEntry[SnapshotT]:
        entry = stack.pop()
        self._set_stack_weight(self._calculate_stack_weight(stack), undo=undo)
        return entry

    def _clear_stack(
        self,
        stack: list[_HistoryEntry[SnapshotT]],
        *,
        undo: bool,
    ) -> None:
        stack.clear()
        if undo:
            self._undo_weight = 0
        else:
            self._redo_weight = 0

    def _restore_safely(self, snapshot: SnapshotT) -> None:
        restored = (
            self._clone_for_restore(snapshot)
            if self._clone_for_restore is not None
            else snapshot
        )
        self._restore_depth += 1
        try:
            self._restore(restored)
        finally:
            self._restore_depth -= 1


__all__ = [
    "DEFAULT_COALESCE_INTERVAL_SECONDS",
    "DEFAULT_UNDO_LIMIT",
    "UndoManager",
]
