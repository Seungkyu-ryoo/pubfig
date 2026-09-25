"""Project persistence, recovery, and background-writer lifecycle."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QFileDialog, QMessageBox

from ..model import Graph, Sheet, TreeNode
from ..project_io import load_payload_into_model as decode_project_payload
from ..project_io import project_path_revision, read_project_payload, write_json_atomic
from ..project_io import read_project as read_project_document
from ..save_worker import BackgroundProjectWriter
from .constants import _SESSION_AUTOSAVE_RE, AUTOSAVE_INTERVAL_MS, RECENT_FILES_LIMIT

if TYPE_CHECKING:
    from .main_window import GraphDrawerWindow


class _SaveCompletionBridge(QObject):
    """Deliver ``Future`` completion from the writer to the Qt UI thread."""

    completed = Signal(object)


class ProjectFilesController(QObject):
    """Project persistence, recovery, and background-writer lifecycle."""

    def __init__(self, window: GraphDrawerWindow) -> None:
        super().__init__(window)
        self.window = window
        self.session = window.session
        self.autosave_path = Path.home() / (
            f".pubfig_autosave.{os.getpid()}.{uuid4().hex}.json"
        )

        self._default_autosave_path = self.autosave_path

        self.legacy_autosave_path = Path.home() / ".pubfig_autosave.json"

        self.last_folder = Path.cwd()

        self._closing = False

        self._recovery_source_path: Path | None = None

        self._recovery_origin_path: Path | None = None

        self._recovery_origin_revision: tuple[int, int, int, int] | None = None

        self._autosave_futures: set[object] = set()

        self._save_completion_bridge = _SaveCompletionBridge(self.window)

        self.save_writer = BackgroundProjectWriter()

        self._save_completion_bridge.completed.connect(self._background_save_finished)
        self.autosave_timer = QTimer(window)
        self.autosave_timer.setInterval(AUTOSAVE_INTERVAL_MS)
        self.autosave_timer.timeout.connect(self.autosave_project)

    @property
    def current_project_path(self) -> Path | None:
        return self.session.document.source_path

    @current_project_path.setter
    def current_project_path(self, value: Path | None) -> None:
        self.session.document.source_path = value

    @property
    def _current_project_revision(self):
        return self.session.document.source_revision

    @_current_project_revision.setter
    def _current_project_revision(self, value) -> None:
        self.session.document.source_revision = value

    def recent_files(self) -> list[str]:
        value = self.window.settings.value("recent_files", [])
        if isinstance(value, str):
            value = [value]
        return [item for item in (value or []) if item]

    def add_recent_file(self, path: Path) -> None:
        items = [str(path)] + [
            item for item in self.recent_files() if item != str(path)
        ]
        self.window.settings.setValue("recent_files", items[:RECENT_FILES_LIMIT])
        self._update_recent_menu()

    def _update_recent_menu(self) -> None:
        if not hasattr(self.window, "recent_menu"):
            return
        self.window.recent_menu.clear()
        files = self.recent_files()
        if not files:
            placeholder = self.window.recent_menu.addAction("(no recent projects)")
            placeholder.setEnabled(False)
            return
        for path_text in files:
            action = self.window.recent_menu.addAction(path_text)
            action.triggered.connect(
                lambda checked=False, p=path_text: self.open_project_path(Path(p))
            )
        self.window.recent_menu.addSeparator()
        self.window.recent_menu.addAction("Clear List", self._clear_recent_files)

    def _clear_recent_files(self) -> None:
        self.window.settings.setValue("recent_files", [])
        self._update_recent_menu()

    def autosave_project(self) -> None:
        if not self.session.modified:
            return
        try:
            self.window.workspace.save_active_state()
            future = self.save_writer.submit_document(
                self.autosave_path,
                self.session.document,
                indent=None,
                autosave=True,
                extra_payload=self._autosave_origin_metadata(),
            )
            self._autosave_futures.add(future)
            future.add_done_callback(self._notify_background_save_finished)
            self.window.set_status("Autosaving in the background…")
        except Exception as exc:
            self.window.set_status(f"Autosave failed: {exc}")

    def _notify_background_save_finished(self, future) -> None:
        """Run in the worker thread and queue completion on Qt's thread."""

        try:
            self._save_completion_bridge.completed.emit(future)
        except RuntimeError:
            # The window/bridge can already be deleted during application
            # shutdown.  The atomic worker job remains safe without a UI.
            pass

    def _background_save_finished(self, future) -> None:
        """Consume a completed autosave without ever blocking the UI thread."""

        tracked = future in self._autosave_futures
        self._autosave_futures.discard(future)
        if not tracked:
            return
        if future.cancelled():
            return
        try:
            result = future.result()
        except Exception as exc:
            if not self._closing:
                self.window.set_status(f"Autosave failed: {exc}")
            return
        if not self._closing and result.autosave:
            self.window.set_status(f"Autosaved in background: {result.path}")

    @staticmethod
    def _write_json_atomic(
        path: Path,
        payload: dict,
        indent: int | None = None,
        *,
        expected_revision: tuple[int, int, int, int] | None = None,
    ) -> None:
        write_json_atomic(
            path,
            payload,
            indent=indent,
            expected_revision=expected_revision,
        )

    def _autosave_origin_metadata(self) -> dict[str, object]:
        """Describe the intended project file, never the recovery storage."""

        if self._recovery_source_path is not None:
            origin_path = self._recovery_origin_path
            revision = self._recovery_origin_revision
        else:
            origin_path = self.current_project_path
            revision = self._current_project_revision

        metadata: dict[str, object] = {
            "autosave_origin": (
                str(origin_path.resolve()) if origin_path is not None else ""
            )
        }
        if revision is not None:
            metadata["autosave_origin_revision"] = list(revision)
        return metadata

    def _remove_autosave(self) -> None:
        for path in self._autosave_cleanup_paths():
            self._unlink_path(path)

    def _autosave_cleanup_paths(self) -> tuple[Path, ...]:
        """Return only this session's file and the recovery it accepted."""

        paths = [self.autosave_path]
        if self._recovery_source_path is not None:
            paths.append(self._recovery_source_path)
        return tuple(dict.fromkeys(paths))

    @staticmethod
    def _parse_project_revision(
        value: object,
    ) -> tuple[int, int, int, int] | None:
        if (
            not isinstance(value, (list, tuple))
            or len(value) != 4
            or any(type(part) is not int for part in value)
        ):
            return None
        return value[0], value[1], value[2], value[3]

    @staticmethod
    def _session_autosave_pid(path: Path) -> int | None:
        match = _SESSION_AUTOSAVE_RE.fullmatch(path.name)
        return int(match.group(1)) if match is not None else None

    @staticmethod
    def _process_is_running(pid: int) -> bool:
        if pid == os.getpid():
            return True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except (PermissionError, OSError):
            # Never claim another process's recovery unless liveness is known
            # to be false.
            return True
        return True

    def _recovery_candidates(self) -> tuple[Path, ...]:
        """Return newest-first recoveries not owned by a live process."""

        candidates: list[Path] = []
        if self.autosave_path != self._default_autosave_path:
            # Tests and embedders often redirect recovery storage.  Avoid
            # scanning the user's real home directory in that configuration.
            candidates.append(self.autosave_path)
            if self.legacy_autosave_path.parent == self.autosave_path.parent:
                candidates.append(self.legacy_autosave_path)
        else:
            candidates.append(self.legacy_autosave_path)
            try:
                for candidate in self.autosave_path.parent.glob(
                    ".pubfig_autosave.*.*.json"
                ):
                    pid = self._session_autosave_pid(candidate)
                    if pid is not None and not self._process_is_running(pid):
                        candidates.append(candidate)
            except OSError:
                pass

        existing: list[tuple[int, Path]] = []
        for candidate in dict.fromkeys(candidates):
            try:
                if candidate.is_symlink() or not candidate.is_file():
                    continue
                existing.append((candidate.stat().st_mtime_ns, candidate))
            except OSError:
                continue
        existing.sort(key=lambda item: item[0], reverse=True)
        return tuple(path for _mtime, path in existing)

    def _is_recovery_path(self, path: Path) -> bool:
        """Whether ``path`` is reserved for crash recovery rather than a project."""

        resolved = path.resolve()
        configured = {self.autosave_path, self.legacy_autosave_path}
        if self._recovery_source_path is not None:
            configured.add(self._recovery_source_path)
        known = {candidate.resolve() for candidate in configured}
        return (
            resolved in known
            or path.name == ".pubfig_autosave.json"
            or resolved.name == ".pubfig_autosave.json"
            or _SESSION_AUTOSAVE_RE.fullmatch(path.name) is not None
            or _SESSION_AUTOSAVE_RE.fullmatch(resolved.name) is not None
        )

    @staticmethod
    def _unlink_path(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _reset_save_writer(self, *, remove_autosave: bool) -> None:
        """Finish/cancel old jobs before changing the project they represent."""

        self.save_writer.shutdown(wait=True, cancel_pending=True)
        self._autosave_futures.clear()
        if remove_autosave:
            self._remove_autosave()
        self.save_writer = BackgroundProjectWriter()

    def _discard_autosave_in_background(self) -> None:
        """Order recovery cleanup after any running write without UI waiting."""

        pending_autosaves = tuple(self._autosave_futures)
        for future in pending_autosaves:
            future.add_done_callback(
                lambda _completed, path=self.autosave_path: self._unlink_path(path)
            )
        self._autosave_futures.clear()
        self.save_writer.discard_autosave(self.autosave_path)
        # Remove an already-complete recovery file immediately as well.  The
        # queued removal handles the case where a running job replaces it
        # after this unlink.
        self._remove_autosave()

    def maybe_restore_autosave(self) -> None:
        recovery_path = next(iter(self._recovery_candidates()), None)
        if recovery_path is None:
            return
        reply = QMessageBox.question(
            self.window,
            "Recover work",
            "pubfig found an autosaved session (possibly from a crash). Restore it?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            self._unlink_path(recovery_path)
            return
        try:
            payload, _recovery_revision = read_project_payload(recovery_path)
            sheets, graphs, tree_root, active_node_id = self.load_payload_into_model(
                payload, "Recovered"
            )
        except Exception as exc:
            QMessageBox.warning(
                self.window, "Recover work", f"Could not restore the autosave:\n{exc}"
            )
            # Keep the only recovery copy for diagnostics or a later build
            # which may be able to repair it.
            self.window.set_status(
                f"Autosave recovery failed; file kept at {recovery_path}"
            )
            return

        raw_origin = payload.get("autosave_origin") or ""
        origin_path = (
            Path(raw_origin).resolve()
            if isinstance(raw_origin, str) and raw_origin
            else None
        )
        origin_revision = self._parse_project_revision(
            payload.get("autosave_origin_revision")
        )
        origin_is_current = False
        if (
            origin_path is not None
            and origin_path.suffix.lower() == ".json"
            and not self._is_recovery_path(origin_path)
            and origin_revision is not None
        ):
            try:
                origin_is_current = (
                    project_path_revision(origin_path) == origin_revision
                )
            except Exception:
                origin_is_current = False

        self.window.workspace.set_model(
            sheets,
            graphs,
            tree_root,
            active_node_id,
            source_path=origin_path if origin_is_current else None,
            source_revision=origin_revision if origin_is_current else None,
        )
        self.current_project_path = origin_path if origin_is_current else None
        self._current_project_revision = origin_revision if origin_is_current else None
        self._recovery_source_path = recovery_path.resolve()
        self._recovery_origin_path = origin_path
        self._recovery_origin_revision = origin_revision
        self.window.workspace.undo_history.reset()
        self.window.workspace._set_modified(True)
        if origin_path is not None and not origin_is_current:
            self.window.set_status(
                "Restored autosaved session; the original project could not be "
                "verified unchanged, so Save As is required."
            )
        else:
            self.window.set_status("Restored autosaved session.")

    def save_project(self) -> None:
        self.window.workspace.save_active_state()
        if not self.session.sheets:
            QMessageBox.information(
                self.window, "Save project", "Paste or load data first."
            )
            return
        path = self.current_project_path or self._choose_project_save_path()
        if path is not None:
            self._save_project_to(path)

    def _choose_project_save_path(self) -> Path | None:
        path, _ = QFileDialog.getSaveFileName(
            self.window,
            "Save project",
            str(self.last_folder / "graph_project.json"),
            "JSON (*.json)",
        )
        return Path(path) if path else None

    def _save_project_to(self, path: Path) -> bool:
        try:
            saved_path = self.write_project(path)
        except Exception as exc:
            QMessageBox.warning(
                self.window, "Save project", f"Could not save the project:\n{exc}"
            )
            self.window.set_status(f"Save failed: {exc}")
            return False
        self.last_folder = saved_path.parent
        self.add_recent_file(saved_path)
        self.window.set_status(f"Saved project: {saved_path}")
        return True

    def new_project(self) -> None:
        if self.session.modified:
            reply = QMessageBox.question(
                self.window,
                "New project",
                "Discard unsaved changes and start a new project?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        self.window.preview.render_timer.stop()
        self._discard_autosave_in_background()
        self.window.workspace._init_blank_project()
        self.current_project_path = None
        self._current_project_revision = None
        self._recovery_source_path = None
        self._recovery_origin_path = None
        self._recovery_origin_revision = None
        self.window.workspace.undo_history.reset()
        self.window.workspace._set_modified(False)
        self.window.set_status("Started a new project.")

    def write_project(self, path: Path) -> Path:
        # Avoid running a manual encoder beside an older autosave snapshot.
        # Draining is rare (only an autosave already in flight) and keeps peak
        # memory bounded while the periodic path remains fully non-blocking.
        target = path if path.suffix.lower() == ".json" else path.with_suffix(".json")
        if self._is_recovery_path(target):
            raise ValueError(
                "The autosave recovery path is reserved; choose another project name."
            )
        self._reset_save_writer(remove_autosave=False)
        expected_revision = (
            self._current_project_revision
            if self.current_project_path is not None
            and self._current_project_revision is not None
            and self.current_project_path.resolve() == target.resolve()
            else None
        )
        self._write_json_atomic(
            target,
            self.window.workspace.project_payload(),
            indent=None,
            expected_revision=expected_revision,
        )
        revision = project_path_revision(target)
        self.current_project_path = target
        self._current_project_revision = revision
        self.session.document.source_path = target.resolve()
        self.session.document.source_revision = revision
        self.window.workspace._set_modified(False)
        # Remove an accepted stale recovery before forgetting which file this
        # session owns.  Other sessions' recoveries are never included here.
        self._remove_autosave()
        self._recovery_source_path = None
        self._recovery_origin_path = None
        self._recovery_origin_revision = None
        return target

    def save_project_as(self) -> None:
        self.window.workspace.save_active_state()
        if not self.session.sheets:
            QMessageBox.information(
                self.window, "Save project", "Paste or load data first."
            )
            return
        path = self._choose_project_save_path()
        if path is not None:
            self._save_project_to(path)

    def load_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self.window, "Load project", str(self.last_folder), "JSON (*.json)"
        )
        if not path:
            return
        self.open_project_path(Path(path))

    def _confirm_project_replacement(self) -> bool:
        """Offer to save a dirty project before another project replaces it."""

        if not self.session.modified:
            return True
        reply = QMessageBox.question(
            self.window,
            "Unsaved changes",
            "There are unsaved changes. Save before opening another project?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if reply == QMessageBox.Cancel:
            return False
        if reply == QMessageBox.Save:
            self.save_project()
            return not self.session.modified
        return reply == QMessageBox.Discard

    def open_project_path(self, path: Path) -> None:
        if self._is_recovery_path(path):
            QMessageBox.warning(
                self.window,
                "Load project",
                "Autosave recovery files are reserved. Restart pubfig and use "
                "the recovery prompt instead.",
            )
            return
        if not self._confirm_project_replacement():
            return
        try:
            document = read_project_document(path)
        except Exception as exc:
            QMessageBox.warning(
                self.window, "Load project", f"Could not load {path}:\n{exc}"
            )
            return

        self._discard_autosave_in_background()
        self.window.workspace.set_model(
            document.sheets,
            document.graphs,
            document.tree_root,
            document.active_node_id,
            source_path=document.source_path,
            source_revision=document.source_revision,
        )
        self.last_folder = path.parent
        self._recovery_source_path = None
        self._recovery_origin_path = None
        self._recovery_origin_revision = None
        self.current_project_path = document.source_path or path.resolve()
        self._current_project_revision = document.source_revision
        self.window.workspace.undo_history.reset()
        self.window.workspace._set_modified(False)
        self.add_recent_file(path)
        self.window.set_status(f"Loaded project: {path}")

    def load_payload_into_model(
        self, payload: dict, fallback_name: str
    ) -> tuple[dict[str, Sheet], dict[str, Graph], TreeNode, str | None]:
        return decode_project_payload(payload, fallback_name)

    def confirm_close(self) -> bool:
        """Resolve unsaved work before the window releases its resources."""
        if not self.session.modified:
            return True
        reply = QMessageBox.question(
            self.window,
            "Unsaved changes",
            "There are unsaved changes. Save before closing?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        )
        if reply == QMessageBox.Save:
            self.save_project()
            return not self.session.modified
        return reply == QMessageBox.Discard

    def shutdown(self) -> None:
        """Stop autosave and clean this session's recovery after in-flight jobs."""
        self._closing = True
        self.autosave_timer.stop()
        pending_autosaves = tuple(self._autosave_futures)
        self.save_writer.shutdown(wait=False, cancel_pending=True)
        for future in pending_autosaves:
            future.add_done_callback(
                lambda _completed, path=self.autosave_path: self._unlink_path(path)
            )
        self._autosave_futures.clear()
        self._remove_autosave()
