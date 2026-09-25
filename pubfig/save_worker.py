"""Non-blocking, atomic project saves for the desktop UI.

The worker owns one background thread so writes are ordered and two saves can
never race while replacing the same file.  A caller can submit an already
encoded payload, or make a short, mutation-safe snapshot of a
``ProjectDocument`` and perform the expensive payload/JSON conversion in the
worker.

This module deliberately has no Qt dependency.  Returned
``concurrent.futures.Future`` objects let a UI bridge completion back to its
event loop without the persistence layer owning widgets or Qt objects.
"""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from .model import ProjectDocument
from .project_io import document_to_payload, write_json_atomic
from .workspace_history import clone_project_document


_PROJECT_PAYLOAD_KEYS = frozenset(
    {"schema_version", "active_node_id", "sheets", "graphs", "tree"}
)


@dataclass(frozen=True)
class SaveResult:
    """Successful background-save result."""

    path: Path
    autosave: bool = False


@dataclass(frozen=True)
class RemovalResult:
    """Result of an ordered background file removal."""

    path: Path
    removed: bool


def snapshot_document(document: ProjectDocument) -> ProjectDocument:
    """Return a mutation-independent project snapshot for a worker thread.

    Graph configuration and the project tree are deep-copied, while DataFrames
    use the same pandas Copy-on-Write snapshots as undo history.  This keeps
    submission time independent of row count.  Later edits detach the affected
    pandas storage while cell normalization, trimming, list creation,
    encoding, ``fsync``, and atomic replacement all happen in the worker.
    """

    if not isinstance(document, ProjectDocument):
        raise TypeError("snapshot_document expects ProjectDocument")

    return clone_project_document(document)


class BackgroundProjectWriter:
    """Serialize and atomically write projects on one background thread.

    ``submit_document`` snapshots DataFrames before returning, then performs
    payload construction and all file I/O off-thread.  ``submit_payload`` is
    useful when the caller already owns a detached payload; that payload is
    transferred to the job and must not be mutated afterwards.  The original
    document passed to ``submit_document`` remains safe to edit immediately.

    Autosaves to the same path are coalesced when an older one has not started;
    manual saves are never discarded.  A cancelled superseded autosave future
    follows the normal :mod:`concurrent.futures` cancellation contract.
    """

    def __init__(self, *, thread_name_prefix: str = "pubfig-save") -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=thread_name_prefix,
        )
        self._lock = RLock()
        self._closed = False
        self._autosaves: dict[Path, Future[SaveResult]] = {}
        self._futures: set[Future[SaveResult | RemovalResult]] = set()

    @property
    def pending_count(self) -> int:
        """Number of queued or running save jobs."""

        with self._lock:
            return sum(not future.done() for future in self._futures)

    def submit_payload(
        self,
        path: str | Path,
        payload: Mapping[str, Any],
        *,
        indent: int | None = None,
        autosave: bool = False,
    ) -> Future[SaveResult]:
        """Queue an owned payload for compact atomic writing."""

        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping")
        target = Path(path)
        return self._submit(
            target,
            payload,
            indent=indent,
            autosave=autosave,
            document=False,
        )

    def submit_document(
        self,
        path: str | Path,
        document: ProjectDocument,
        *,
        extra_payload: Mapping[str, Any] | None = None,
        indent: int | None = None,
        autosave: bool = False,
    ) -> Future[SaveResult]:
        """Snapshot a document and queue its encoding and atomic write.

        ``extra_payload`` supports autosave-only envelope fields such as
        ``autosave_origin`` without changing the persisted project schema.
        """

        if extra_payload is not None and not isinstance(extra_payload, Mapping):
            raise TypeError("extra_payload must be a mapping")
        extras = dict(extra_payload or {})
        collisions = _PROJECT_PAYLOAD_KEYS.intersection(extras)
        if collisions:
            names = ", ".join(sorted(collisions))
            raise ValueError(f"extra_payload cannot replace project fields: {names}")

        target = Path(path)
        with self._lock:
            self._ensure_open()

        snapshot = snapshot_document(document)
        return self._submit(
            target,
            (snapshot, extras),
            indent=indent,
            autosave=autosave,
            document=True,
        )

    def submit_removal(self, path: str | Path) -> Future[RemovalResult]:
        """Cancel a queued same-path autosave, then remove the file in order.

        Because removal uses the same single executor as writes, it runs after
        any save that has already started.  This is suitable for clearing a
        recovery file during New/Open/clean shutdown without blocking the UI.
        """

        target = Path(path)
        with self._lock:
            self._ensure_open()
            previous = self._autosaves.pop(target, None)
            if previous is not None and not previous.done():
                previous.cancel()
            future = self._executor.submit(self._remove_file, target)
            self._futures.add(future)
            future.add_done_callback(
                lambda completed, save_path=target: self._job_finished(
                    save_path,
                    completed,
                )
            )
            return future

    def discard_autosave(self, path: str | Path) -> Future[RemovalResult]:
        """Alias for :meth:`submit_removal` named for the UI recovery flow."""

        return self.submit_removal(path)

    def shutdown(self, *, wait: bool = True, cancel_pending: bool = False) -> None:
        """Reject new work and release the worker thread."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=cancel_pending)

    def __enter__(self) -> "BackgroundProjectWriter":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.shutdown()

    def _submit(
        self,
        target: Path,
        value: Any,
        *,
        indent: int | None,
        autosave: bool,
        document: bool,
    ) -> Future[SaveResult]:
        with self._lock:
            self._ensure_open()
            self._cancel_superseded_autosave(target, autosave)
            function = (
                self._write_document_snapshot
                if document
                else self._write_payload
            )
            future = self._executor.submit(
                function,
                target,
                value,
                indent,
                autosave,
            )
            self._futures.add(future)
            if autosave:
                self._autosaves[target] = future
            future.add_done_callback(
                lambda completed, save_path=target: self._job_finished(
                    save_path,
                    completed,
                )
            )
            return future

    def _cancel_superseded_autosave(self, target: Path, autosave: bool) -> None:
        if not autosave:
            return
        previous = self._autosaves.get(target)
        if previous is not None and not previous.done():
            previous.cancel()

    def _job_finished(
        self,
        target: Path,
        future: Future[SaveResult | RemovalResult],
    ) -> None:
        with self._lock:
            self._futures.discard(future)
            if self._autosaves.get(target) is future:
                self._autosaves.pop(target, None)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("BackgroundProjectWriter is shut down")

    @staticmethod
    def _write_payload(
        target: Path,
        payload: Mapping[str, Any],
        indent: int | None,
        autosave: bool,
    ) -> SaveResult:
        write_json_atomic(target, payload, indent=indent)
        return SaveResult(path=target, autosave=autosave)

    @staticmethod
    def _write_document_snapshot(
        target: Path,
        snapshot_and_extras: tuple[ProjectDocument, dict[str, Any]],
        indent: int | None,
        autosave: bool,
    ) -> SaveResult:
        snapshot, extras = snapshot_and_extras
        payload = document_to_payload(snapshot)
        payload.update(extras)
        write_json_atomic(target, payload, indent=indent)
        return SaveResult(path=target, autosave=autosave)

    @staticmethod
    def _remove_file(target: Path) -> RemovalResult:
        try:
            target.unlink()
        except FileNotFoundError:
            return RemovalResult(path=target, removed=False)
        return RemovalResult(path=target, removed=True)


__all__ = [
    "BackgroundProjectWriter",
    "RemovalResult",
    "SaveResult",
    "snapshot_document",
]
