"""Qt-independent undo/redo history management.

The UI owns the mechanics of capturing and restoring a workspace.  This
module owns the history rules: a bounded undo/redo stack, a baseline used by
post-change widget signals, and time-based coalescing of rapid edits.

``capture`` must return a detached snapshot that the manager can own.  In
particular, pandas objects should be copied deeply as part of the workspace
snapshot.  Stored snapshots are never exposed to callers.  Before restoring,
the manager clones a snapshot by default, so a restore function may install
and mutate the supplied object without corrupting history.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from time import monotonic
from typing import Callable, Generic, Hashable, Iterator, TypeVar


SnapshotT = TypeVar("SnapshotT")
ResultT = TypeVar("ResultT")

DEFAULT_UNDO_LIMIT = 50
DEFAULT_COALESCE_INTERVAL_SECONDS = 0.7


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
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        if coalesce_interval < 0:
            raise ValueError("coalesce_interval cannot be negative")

        self._capture = capture
        self._restore = restore
        self._clone_for_restore = clone_for_restore
        self._clock = clock
        self._limit = limit
        self._coalesce_interval = coalesce_interval

        self._undo: list[SnapshotT] = []
        self._redo: list[SnapshotT] = []
        self._baseline: SnapshotT | None = None
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

        self._undo.clear()
        self._redo.clear()
        self.end_coalescing()
        self._baseline = self._capture() if capture_baseline else None

    def refresh_baseline(self) -> bool:
        """Replace the baseline with the current workspace snapshot."""

        if not self._is_recording:
            return False
        self._baseline = self._capture()
        return True

    def checkpoint_current(self) -> bool:
        """Record current state before a discrete change and clear redo."""

        if not self._is_recording:
            return False
        self.end_coalescing()
        self._append_bounded(self._undo, self._capture())
        self._redo.clear()
        return True

    def checkpoint_baseline(self) -> bool:
        """Record the pre-change baseline and clear redo.

        This operation intentionally does not clone the baseline.  The
        manager owns it, and the next :meth:`refresh_baseline` replaces it.
        """

        if not self._is_recording or self._baseline is None:
            return False
        self._append_bounded(self._undo, self._baseline)
        self._redo.clear()
        return True

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

        current = self._capture()
        target = self._undo[-1]
        self._restore_safely(target)
        self._undo.pop()
        self._append_bounded(self._redo, current)
        self._baseline = target
        return True

    def redo(self) -> bool:
        """Restore the latest redo snapshot, returning whether one existed."""

        self.end_coalescing()
        if not self._redo or not self._is_recording:
            return False

        current = self._capture()
        target = self._redo[-1]
        self._restore_safely(target)
        self._redo.pop()
        self._append_bounded(self._undo, current)
        self._baseline = target
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

    def _append_bounded(self, stack: list[SnapshotT], snapshot: SnapshotT) -> None:
        stack.append(snapshot)
        overflow = len(stack) - self._limit
        if overflow > 0:
            del stack[:overflow]

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
