from __future__ import annotations

from concurrent.futures import Future
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
from pandas.testing import assert_frame_equal
from matplotlib.backend_bases import MouseEvent
from PIL import Image
from PySide6.QtCore import QEvent, QModelIndex, QSettings, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import QApplication, QMessageBox

from app import GraphDrawerWindow, LegendEditorDialog
from plot_config import AnnotationConfig, LegendEntryConfig
from pubfig.sheet_data import NAME_ROW
from pubfig.save_worker import SaveResult
from pubfig.project_io import project_path_revision, write_json_atomic
from pubfig.ui.main_window import MAX_UNDO_STACK_WEIGHT_BYTES
from pubfig.workspace_history import project_snapshot_resources
from table_view import TableSelectionRange
from theme import apply_theme


class GraphDrawerWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        apply_theme(cls.app)

    def setUp(self) -> None:
        self.temp_directory = TemporaryDirectory()
        original_restore = GraphDrawerWindow.maybe_restore_autosave
        GraphDrawerWindow.maybe_restore_autosave = lambda _window: None
        try:
            self.window = GraphDrawerWindow()
        finally:
            GraphDrawerWindow.maybe_restore_autosave = original_restore
        self.window.autosave_timer.stop()
        self.window.autosave_path = Path(self.temp_directory.name) / "autosave.json"
        self.settings_path = Path(self.temp_directory.name) / "settings.ini"
        self.window.settings = QSettings(str(self.settings_path), QSettings.IniFormat)
        self.window.refresh_saved_style_combo()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.window._set_modified(False)
        self.window.close()
        self.app.processEvents()
        self.temp_directory.cleanup()

    def test_direct_cell_edit_is_dirty_and_undoable(self) -> None:
        self.window.new_graph()
        self.window.undo_history.reset()
        self.window._set_modified(False)

        item = self.window.table.item(2, 0)
        original = item.text()
        item.setText("1.25")
        self.app.processEvents()

        self.assertTrue(self.window._is_modified)
        self.assertEqual(self.window.df.iat[2, 0], "1.25")
        self.assertGreaterEqual(self.window.undo_history.undo_count, 1)

        # A manual save clears the dirty marker. Undoing after that save must
        # mark the restored, different project state dirty again.
        self.window._set_modified(False)
        self.window.undo_workspace()
        self.assertTrue(self.window._is_modified)
        self.assertEqual(self.window.table.item(2, 0).text(), original)

    def test_tick_decimal_edit_is_dirty_rendered_and_undoable(self) -> None:
        self.window.new_graph()
        self.window.undo_history.reset()
        self.window._set_modified(False)
        with patch.object(self.window.preview, "schedule_render") as schedule:
            self.window.figure_settings.x_tick_decimals_spin.setValue(2)
            schedule.assert_called_once()
        self.assertTrue(self.window.session.modified)
        self.assertEqual(self.window.session.plot_config.x_tick_decimals, 2)
        self.window.undo_workspace()
        self.assertIsNone(self.window.session.plot_config.x_tick_decimals)
        self.assertEqual(self.window.figure_settings.x_tick_decimals_spin.text(), "Auto")
        self.window.redo_workspace()
        self.assertEqual(self.window.session.plot_config.x_tick_decimals, 2)
        self.assertEqual(self.window.figure_settings.x_tick_decimals_spin.value(), 2)

    def test_number_format_selection_renders_and_supports_undo(self) -> None:
        self.window.new_graph()
        self.window.undo_history.reset()
        self.window._set_modified(False)
        panel = self.window.figure_settings
        with patch.object(self.window.preview, "schedule_render") as schedule:
            panel.y_tick_notation_combo.setCurrentIndex(panel.y_tick_notation_combo.findData("shared"))
            schedule.assert_called_once()
        self.assertTrue(self.window.session.modified)
        self.assertEqual(self.window.session.plot_config.y_tick_notation, "shared")
        self.window.undo_workspace()
        self.assertEqual(panel.y_tick_notation_combo.currentData(), "auto")
        self.assertEqual(self.window.session.plot_config.y_tick_notation, "auto")
        self.window.redo_workspace()
        self.assertEqual(panel.y_tick_notation_combo.currentData(), "shared")
        self.assertEqual(self.window.session.plot_config.y_tick_notation, "shared")
        panel.y_scale_combo.setCurrentText("log")
        self.assertTrue(panel.y_tick_notation_combo.isEnabled())
        panel.y_scale_combo.setCurrentText("linear")
        self.assertTrue(panel.y_tick_notation_combo.isEnabled())
        self.assertEqual(panel.y_tick_notation_combo.currentData(), "shared")

    def test_divisor_input_updates_plot_and_preserves_scale_during_incomplete_input(self) -> None:
        self.window.new_graph()
        self.window.handle_table_paste(2, 0, "0.001\t1000000\n0.002\t2000000")
        self.window.undo_history.reset()
        panel = self.window.figure_settings
        panel.x_scale_divisor_edit.setText("0.001")
        panel.y_scale_divisor_edit.setText("1e6")
        self.window.render_plot()
        axis = self.window.preview.current_figure.axes[0]
        self.assertEqual(list(axis.lines[0].get_xdata()), [.001, .002])
        self.assertEqual(list(axis.lines[0].get_ydata()), [1e6, 2e6])
        panel.y_scale_divisor_edit.setText("1e")
        self.window.render_plot()
        axis = self.window.preview.current_figure.axes[0]
        self.assertEqual(list(axis.lines[0].get_ydata()), [1e6, 2e6])
        self.assertEqual(self.window.current_workspace_snapshot().graphs[
            self.window.session.active_graph_id
        ].plot_config.y_scale_divisor, 1e6)
        self.window.undo_workspace()
        self.assertEqual(self.window.session.plot_config.y_scale_divisor, 1)
        self.window.redo_workspace()
        self.assertEqual(self.window.session.plot_config.y_scale_divisor, 1e6)

    def test_undo_history_uses_structurally_shared_dataframe_budget(self) -> None:
        with patch.object(
            pd.DataFrame,
            "memory_usage",
            side_effect=AssertionError("undo must not deep-scan DataFrames"),
        ):
            snapshot = self.window.current_workspace_snapshot()
            metadata_weight = self.window.workspace_snapshot_weight(snapshot)

        self.assertEqual(
            self.window.undo_history.max_stack_weight,
            MAX_UNDO_STACK_WEIGHT_BYTES,
        )
        self.assertGreater(metadata_weight, 0)
        self.assertLess(metadata_weight, MAX_UNDO_STACK_WEIGHT_BYTES)
        self.assertEqual(
            project_snapshot_resources(snapshot),
            project_snapshot_resources(self.window.document),
        )
        for sheet_id, sheet in snapshot.sheets.items():
            self.assertIsNot(sheet.df, self.window.sheets[sheet_id].df)

    def test_plot_y_all_and_none_buttons_toggle_available_series(self) -> None:
        self.window.table.item(0, 2).setText("Y")
        self.window.new_graph()

        self.assertEqual(self.window.plot_y_all_btn.text(), "All")
        self.assertEqual(self.window.plot_y_none_btn.text(), "None")
        self.assertEqual(self.window.checked_y_columns(), ["Col 2", "Col 3"])

        self.window._set_modified(False)
        with patch.object(self.window.preview, "schedule_render") as schedule_render:
            self.window.plot_y_none_btn.click()

            self.assertEqual(self.window.checked_y_columns(), [])
            self.assertEqual(self.window.selected_series_configs(), [])
            self.assertEqual(schedule_render.call_count, 1)
            self.assertTrue(self.window._is_modified)

            self.window.plot_y_none_btn.click()
            self.assertEqual(schedule_render.call_count, 1)

            self.window.plot_y_all_btn.click()

            self.assertEqual(self.window.checked_y_columns(), ["Col 2", "Col 3"])
            self.assertEqual(
                [series.y for series in self.window.selected_series_configs()],
                ["Col 2", "Col 3"],
            )
            self.assertEqual(schedule_render.call_count, 2)

    def test_plot_y_selection_is_dirty_and_undoable(self) -> None:
        self.window.table.item(0, 2).setText("Y")
        self.window.new_graph()
        expected = ["Col 2", "Col 3"]
        self.assertEqual(self.window.checked_y_columns(), expected)

        self.window._set_modified(False)
        self.window.plot_y_none_btn.click()

        self.assertTrue(self.window._is_modified)
        self.assertEqual(self.window.checked_y_columns(), [])
        self.assertEqual(self.window.capture_active_graph().checked_y, [])

        self.window.undo_workspace()

        self.assertEqual(self.window.checked_y_columns(), expected)
        self.assertEqual(self.window.capture_active_graph().checked_y, expected)

    def test_marker_grid_choice_is_dirty_and_undoable(self) -> None:
        self.window.new_graph()
        target = self.window.style_target_combo.currentText()
        self.assertTrue(target)
        original = self.window.series_by_y[target]
        original_choice = (original.marker, original.marker_fill_style)
        self.window.undo_history.reset()
        self.window._set_modified(False)

        self.window.marker_combo.set_choice("o", "left")

        changed = self.window.series_by_y[target]
        self.assertEqual((changed.marker, changed.marker_fill_style), ("o", "left"))
        self.assertTrue(self.window._is_modified)
        self.assertEqual(self.window.undo_history.undo_count, 1)

        self.window.undo_workspace()
        restored = self.window.series_by_y[target]
        self.assertEqual((restored.marker, restored.marker_fill_style), original_choice)


    def test_autosave_is_submitted_to_background_writer_and_reported_on_qt_thread(self) -> None:
        future = Future()
        self.window._set_modified(True)

        with patch.object(
            self.window.save_writer,
            "submit_document",
            return_value=future,
        ) as submit:
            self.window.autosave_project()

        submit.assert_called_once_with(
            self.window.autosave_path,
            self.window.document,
            indent=None,
            autosave=True,
            extra_payload={"autosave_origin": ""},
        )
        self.assertIn(future, self.window._autosave_futures)
        self.assertIn("background", self.window.statusBar().currentMessage())

        future.set_result(
            SaveResult(path=self.window.autosave_path, autosave=True)
        )
        self.app.processEvents()

        self.assertNotIn(future, self.window._autosave_futures)
        self.assertIn("Autosaved in background", self.window.statusBar().currentMessage())

    def test_discarded_running_autosave_cannot_reappear_after_cleanup(self) -> None:
        running = Future()
        self.window._autosave_futures.add(running)

        with patch.object(
            self.window.save_writer,
            "discard_autosave",
            return_value=Future(),
        ):
            self.window._discard_autosave_in_background()

        # Simulate the already-running atomic writer replacing the path after
        # the immediate unlink and after its queued removal was cancelled.
        self.window.autosave_path.write_bytes(b"late autosave")
        running.set_result(
            SaveResult(path=self.window.autosave_path, autosave=True)
        )
        self.assertFalse(self.window.autosave_path.exists())

    def test_default_autosave_path_is_unique_per_window_session(self) -> None:
        other = None
        original_restore = GraphDrawerWindow.maybe_restore_autosave
        GraphDrawerWindow.maybe_restore_autosave = lambda _window: None
        try:
            other = GraphDrawerWindow()
        finally:
            GraphDrawerWindow.maybe_restore_autosave = original_restore
        try:
            other.autosave_timer.stop()
            first = self.window._default_autosave_path
            second = other._default_autosave_path
            self.assertNotEqual(first, second)
            self.assertRegex(
                first.name,
                rf"^\.pubfig_autosave\.{os.getpid()}\.[0-9a-f]{{32}}\.json$",
            )
            self.assertRegex(
                second.name,
                rf"^\.pubfig_autosave\.{os.getpid()}\.[0-9a-f]{{32}}\.json$",
            )
        finally:
            if other is not None:
                other._set_modified(False)
                other.close()
                self.app.processEvents()

    def test_cleanup_removes_only_owned_and_accepted_recovery_paths(self) -> None:
        owned = self.window.autosave_path
        accepted = Path(self.temp_directory.name) / (
            ".pubfig_autosave.900001.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json"
        )
        unrelated = Path(self.temp_directory.name) / (
            ".pubfig_autosave.900002.bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.json"
        )
        for path in (owned, accepted, unrelated):
            path.write_bytes(b"recovery")
        self.window._recovery_source_path = accepted

        self.window._remove_autosave()

        self.assertFalse(owned.exists())
        self.assertFalse(accepted.exists())
        self.assertTrue(unrelated.exists())

    def test_recovery_scan_skips_files_owned_by_live_processes(self) -> None:
        directory = Path(self.temp_directory.name)
        own = directory / (
            f".pubfig_autosave.{os.getpid()}.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json"
        )
        stale = directory / (
            ".pubfig_autosave.900003.bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.json"
        )
        self.window.autosave_path = own
        self.window._default_autosave_path = own
        self.window.legacy_autosave_path = directory / ".pubfig_autosave.json"
        own.write_bytes(b"live")
        stale.write_bytes(b"stale")

        with (
            patch.object(
                self.window,
                "_process_is_running",
                side_effect=lambda pid: pid == os.getpid(),
            ),
            patch(
                "PySide6.QtWidgets.QMessageBox.question",
                return_value=QMessageBox.No,
            ),
        ):
            self.window.maybe_restore_autosave()

        self.assertTrue(own.exists())
        self.assertFalse(stale.exists())

    def test_legacy_fixed_autosave_is_still_discovered(self) -> None:
        legacy = Path(self.temp_directory.name) / ".pubfig_autosave.json"
        self.window.legacy_autosave_path = legacy
        legacy.write_bytes(b"legacy recovery")

        with patch(
            "PySide6.QtWidgets.QMessageBox.question",
            return_value=QMessageBox.No,
        ):
            self.window.maybe_restore_autosave()

        self.assertFalse(legacy.exists())

    def test_corrupt_autosave_is_preserved_after_failed_recovery(self) -> None:
        self.window.autosave_path.write_bytes(b"damaged recovery")

        with (
            patch(
                "PySide6.QtWidgets.QMessageBox.question",
                return_value=QMessageBox.Yes,
            ),
            patch("PySide6.QtWidgets.QMessageBox.warning") as warning,
        ):
            self.window.maybe_restore_autosave()

        warning.assert_called_once()
        self.assertEqual(
            self.window.autosave_path.read_bytes(),
            b"damaged recovery",
        )
        self.assertIn("file kept", self.window.statusBar().currentMessage())

    def test_changed_recovery_origin_forces_save_as(self) -> None:
        directory = Path(self.temp_directory.name)
        origin = directory / "origin.json"
        write_json_atomic(origin, self.window.project_payload())
        original_revision = project_path_revision(origin)
        recovery_payload = self.window.project_payload()
        recovery_payload.update(
            {
                "autosave_origin": str(origin.resolve()),
                "autosave_origin_revision": list(original_revision),
            }
        )
        write_json_atomic(self.window.autosave_path, recovery_payload)
        write_json_atomic(origin, {**self.window.project_payload(), "external": True})
        external_bytes = origin.read_bytes()

        with (
            patch(
                "PySide6.QtWidgets.QMessageBox.question",
                return_value=QMessageBox.Yes,
            ),
            patch.object(self.window.preview, "render_plot"),
        ):
            self.window.maybe_restore_autosave()

        self.assertIsNone(self.window.current_project_path)
        self.assertIn("Save As is required", self.window.statusBar().currentMessage())
        self.assertEqual(
            self.window._autosave_origin_metadata(),
            {
                "autosave_origin": str(origin.resolve()),
                "autosave_origin_revision": list(original_revision),
            },
        )
        with patch.object(
            self.window.files,
            "_choose_project_save_path",
            return_value=None,
        ) as choose:
            self.window.save_project()
        choose.assert_called_once_with()
        self.assertEqual(origin.read_bytes(), external_bytes)

    def test_recovery_origin_without_revision_forces_save_as(self) -> None:
        directory = Path(self.temp_directory.name)
        origin = directory / "origin.json"
        write_json_atomic(origin, self.window.project_payload())
        recovery_payload = self.window.project_payload()
        recovery_payload["autosave_origin"] = str(origin.resolve())
        write_json_atomic(self.window.autosave_path, recovery_payload)

        with (
            patch(
                "PySide6.QtWidgets.QMessageBox.question",
                return_value=QMessageBox.Yes,
            ),
            patch.object(self.window.preview, "render_plot"),
        ):
            self.window.maybe_restore_autosave()

        self.assertIsNone(self.window.current_project_path)
        self.assertIsNone(self.window._current_project_revision)
        self.assertIn("Save As is required", self.window.statusBar().currentMessage())

    def test_recovery_file_cannot_be_trusted_as_its_own_origin(self) -> None:
        directory = Path(self.temp_directory.name)
        fake_origin = directory / (
            ".pubfig_autosave.900004.cccccccccccccccccccccccccccccccc.json"
        )
        write_json_atomic(fake_origin, self.window.project_payload())
        recovery_payload = self.window.project_payload()
        recovery_payload.update(
            {
                "autosave_origin": str(fake_origin.resolve()),
                "autosave_origin_revision": list(
                    project_path_revision(fake_origin)
                ),
            }
        )
        write_json_atomic(self.window.autosave_path, recovery_payload)

        with (
            patch(
                "PySide6.QtWidgets.QMessageBox.question",
                return_value=QMessageBox.Yes,
            ),
            patch.object(self.window.preview, "render_plot"),
        ):
            self.window.maybe_restore_autosave()

        self.assertIsNone(self.window.current_project_path)
        self.assertIn("Save As is required", self.window.statusBar().currentMessage())

    def test_origin_changed_after_recovery_is_rejected_by_manual_save_cas(self) -> None:
        directory = Path(self.temp_directory.name)
        origin = directory / "origin.json"
        write_json_atomic(origin, self.window.project_payload())
        original_revision = project_path_revision(origin)
        recovery_payload = self.window.project_payload()
        recovery_payload.update(
            {
                "autosave_origin": str(origin.resolve()),
                "autosave_origin_revision": list(original_revision),
            }
        )
        write_json_atomic(self.window.autosave_path, recovery_payload)
        with (
            patch(
                "PySide6.QtWidgets.QMessageBox.question",
                return_value=QMessageBox.Yes,
            ),
            patch.object(self.window.preview, "render_plot"),
        ):
            self.window.maybe_restore_autosave()
        self.assertEqual(self.window.current_project_path, origin.resolve())

        write_json_atomic(origin, {**self.window.project_payload(), "external": True})
        external_bytes = origin.read_bytes()
        with patch("PySide6.QtWidgets.QMessageBox.warning") as warning:
            self.assertFalse(self.window._save_project_to(origin))

        warning.assert_called_once()
        self.assertEqual(origin.read_bytes(), external_bytes)
        self.assertTrue(self.window.autosave_path.exists())

    def test_reserved_recovery_path_cannot_be_opened_or_manually_saved(self) -> None:
        recovery = self.window.autosave_path
        write_json_atomic(recovery, self.window.project_payload())
        recovery_bytes = recovery.read_bytes()
        self.window._set_modified(True)

        with (
            patch("pubfig.ui.project_files.read_project_document") as read,
            patch("PySide6.QtWidgets.QMessageBox.warning") as warning,
        ):
            self.window.open_project_path(recovery)
            saved = self.window._save_project_to(recovery)

        read.assert_not_called()
        self.assertFalse(saved)
        self.assertEqual(warning.call_count, 2)
        self.assertEqual(recovery.read_bytes(), recovery_bytes)
        self.assertTrue(self.window._is_modified)

    def test_dirty_open_cancel_does_not_read_the_candidate(self) -> None:
        candidate = Path(self.temp_directory.name) / "candidate.json"
        self.window._set_modified(True)

        with (
            patch(
                "PySide6.QtWidgets.QMessageBox.question",
                return_value=QMessageBox.Cancel,
            ),
            patch("pubfig.ui.project_files.read_project_document") as read,
        ):
            self.window.open_project_path(candidate)

        read.assert_not_called()
        self.assertTrue(self.window._is_modified)

    def test_dirty_open_save_that_does_not_complete_aborts_open(self) -> None:
        candidate = Path(self.temp_directory.name) / "candidate.json"
        self.window._set_modified(True)

        with (
            patch(
                "PySide6.QtWidgets.QMessageBox.question",
                return_value=QMessageBox.Save,
            ),
            patch.object(self.window.files, "save_project") as save,
            patch("pubfig.ui.project_files.read_project_document") as read,
        ):
            self.window.open_project_path(candidate)

        save.assert_called_once_with()
        read.assert_not_called()
        self.assertTrue(self.window._is_modified)

    def test_dirty_open_discard_loads_the_candidate(self) -> None:
        candidate = Path(self.temp_directory.name) / "candidate.json"
        write_json_atomic(candidate, self.window.project_payload())
        self.window._set_modified(True)

        with (
            patch(
                "PySide6.QtWidgets.QMessageBox.question",
                return_value=QMessageBox.Discard,
            ),
            patch.object(self.window.preview, "render_plot"),
        ):
            self.window.open_project_path(candidate)

        self.assertEqual(self.window.current_project_path, candidate.resolve())
        self.assertEqual(
            self.window._current_project_revision,
            project_path_revision(candidate),
        )
        self.assertFalse(self.window._is_modified)

    def test_display_request_uses_preview_cache_and_cell_edit_invalidates_it(self) -> None:
        self.window.new_graph()

        preview = self.window._render_request(preview=True)
        full = self.window._render_request()

        self.assertTrue(preview.render_options.preview)
        self.assertIs(
            preview.render_options.numeric_cache,
            self.window.preview_numeric_cache,
        )
        self.assertFalse(full.render_options.preview)

        self.window.preview_numeric_cache.bind(
            preview.dataframe,
            source=self.window.df,
        ).numeric("Col 1")
        self.assertTrue(self.window.preview_numeric_cache._numeric)

        self.window.table.item(2, 0).setText("42")
        self.app.processEvents()

        self.assertFalse(self.window.preview_numeric_cache._numeric)

    def test_paste_preserves_current_cell_and_role_decorated_header(self) -> None:
        self.window.new_graph()
        self.window.table.setCurrentCell(2, 0)

        self.window.handle_table_paste(2, 0, "1.25")

        self.assertEqual(
            (self.window.table.currentRow(), self.window.table.currentColumn()),
            (2, 0),
        )
        self.assertEqual(
            self.window.table.table_model.headerData(
                0,
                Qt.Horizontal,
                Qt.DisplayRole,
            ),
            "Col 1 (X)",
        )
        self.assertEqual(self.window.table.horizontalHeaderItem(0).text(), "Col 1")

    def test_paste_into_name_row_keeps_a_spaced_name_in_one_cell(self) -> None:
        self.window.new_graph()

        self.window.handle_table_paste(NAME_ROW, 0, "1st cycle")

        self.assertEqual(self.window.table.item(NAME_ROW, 0).text(), "1st cycle")
        self.assertEqual(self.window.table.item(NAME_ROW, 1).text(), "")
        self.assertEqual(self.window.df.iat[NAME_ROW, 0], "1st cycle")
        self.assertEqual(self.window.df.iat[NAME_ROW, 1], "")

    def test_expanding_paste_preserves_next_paste_start_cell(self) -> None:
        self.window.new_graph()
        self.assertEqual(
            (self.window.table.rowCount(), self.window.table.columnCount()),
            (22, 4),
        )
        self.window.table.setCurrentCell(21, 3)

        self.window.handle_table_paste(21, 3, "a\tb\nc\td")

        self.assertEqual(
            (self.window.table.rowCount(), self.window.table.columnCount()),
            (23, 5),
        )
        self.assertEqual(
            (self.window.table.currentRow(), self.window.table.currentColumn()),
            (21, 3),
        )

        paste_events: list[tuple[int, int, str]] = []
        self.window.table.pasted.connect(
            lambda row, column, text: paste_events.append((row, column, text))
        )
        QApplication.clipboard().setText("next")
        shortcut = QKeySequence(QKeySequence.Paste)[0]
        event = QKeyEvent(
            QEvent.KeyPress,
            shortcut.key(),
            shortcut.keyboardModifiers(),
        )
        self.window.table.keyPressEvent(event)

        self.assertEqual(paste_events, [(21, 3, "next")])
        self.assertEqual(self.window.table.item(21, 3).text(), "next")
        self.assertEqual(self.window.table.item(0, 0).text(), "X")
        QApplication.clipboard().clear()

    def test_add_column_uses_next_available_sequential_name(self) -> None:
        self.window.new_graph()
        existing_columns = list(map(str, self.window.df.columns))
        next_name = f"Col {len(existing_columns) + 1}"
        self.window.table.setCurrentCell(2, len(existing_columns) - 1)

        self.window.add_column()

        self.assertEqual(
            list(map(str, self.window.df.columns)),
            [*existing_columns, next_name],
        )
        self.assertNotIn(f"Col {len(existing_columns) + 2}", self.window.df.columns)

    def test_bulk_clear_and_delete_selections_preserve_dataframe_integrity(
        self,
    ) -> None:
        self.window.new_graph()
        grid = [
            [f"r{row}c{column}" for column in range(4)]
            for row in range(6)
        ]
        self.window.table.set_block(2, 0, grid)
        expected = self.window.table.dataframe_copy()

        self.window.table.clearSelection()
        self.window.table.setRangeSelected(
            TableSelectionRange(3, 1, 4, 2),
            True,
        )
        self.window.clear_selected_cells()
        expected.iloc[3:5, 1:3] = ""
        assert_frame_equal(self.window.df, expected)

        self.window.table.clearSelection()
        self.window.table.setRangeSelected(
            TableSelectionRange(0, 0, 3, 3),
            True,
        )
        self.window.delete_selected_rows()
        expected = pd.concat(
            [expected.iloc[:2], expected.iloc[4:]],
            ignore_index=True,
        )
        assert_frame_equal(self.window.df, expected)

        self.window.table.clearSelection()
        self.window.table.setRangeSelected(
            TableSelectionRange(0, 1, len(expected) - 1, 2),
            True,
        )
        self.window.delete_selected_columns()
        expected = pd.concat(
            [expected.iloc[:, :1], expected.iloc[:, 3:]],
            axis=1,
        )
        assert_frame_equal(self.window.df, expected)
        self.assertEqual(list(self.window.df.columns), ["Col 1", "Col 4"])

    def test_clear_without_a_selection_does_not_create_an_undo_entry(self) -> None:
        self.window.new_graph()
        self.window.table.clearSelection()
        self.window.table.setCurrentIndex(QModelIndex())
        self.window.undo_history.reset()
        self.window._set_modified(False)

        self.window.clear_selected_cells()

        self.assertFalse(self.window._is_modified)
        self.assertEqual(self.window.undo_history.undo_count, 0)

    def test_center_actions_keep_y_label_gap_fixed_while_margins_move(self) -> None:
        self.window.new_graph()
        self.window.handle_table_paste(2, 0, "0\t0\n1\t1\n2\t4")
        self.window.y_label_edit.setText("Y axis title")
        self.window.y_label_offset_spin.setValue(12.0)
        self.window.plot_width_spin.setValue(70.0)
        self.window.plot_height_spin.setValue(45.0)
        self.window.set_plot_margins(5.0, 14.0, 11.0, 11.0)

        def measured_gap_mm() -> float:
            self.window.canvas.draw()
            figure = self.window.current_figure
            renderer = self.window.canvas.get_renderer()
            y_label = next(
                text for text in figure.texts if text.get_text() == "Y axis title"
            )
            return (
                figure.axes[0].bbox.x0
                - y_label.get_window_extent(renderer).x1
            ) / figure.dpi * 25.4

        self.window.center_plot_box()
        plot_box_left = self.window.plot_margin_left_spin.value()
        plot_box_gap = measured_gap_mm()

        self.window.center_content()
        content_left = self.window.plot_margin_left_spin.value()
        content_gap = measured_gap_mm()

        self.assertNotEqual(plot_box_left, content_left)
        self.assertAlmostEqual(plot_box_gap, 12.0, delta=0.05)
        self.assertAlmostEqual(content_gap, 12.0, delta=0.05)

    def test_independent_legend_mapping_does_not_rename_series_data(self) -> None:
        self.window.new_graph()
        self.window.handle_table_paste(2, 0, "0\t1\t3\n1\t2\t2\n2\t3\t1")
        self.window.table.item(0, 2).setText("Y")
        self.window.table.item(1, 0).setText("X values")
        self.window.table.item(1, 1).setText("Series A")
        self.window.table.item(1, 2).setText("Series B")
        self.window._set_checked_y_columns(["Col 2", "Col 3"])
        self.window.refresh_series_configs()
        self.window.update_style_targets()
        self.window.series_by_y["Col 2"].color = "#ff0000"
        self.window.series_by_y["Col 2"].marker = "o"
        self.window.series_by_y["Col 2"].plot_type = "line+marker"
        self.window.series_by_y["Col 3"].color = "#00aa00"
        self.window.series_by_y["Col 3"].marker = "s"
        self.window.series_by_y["Col 3"].plot_type = "line+marker"
        original_names = self.window.df.iloc[1].copy()
        original_labels = {
            key: series.label
            for key, series in self.window.series_by_y.items()
        }
        self.window.undo_history.reset()

        changed = self.window.apply_legend_entry_config(
            [
                LegendEntryConfig(source_y="Col 3", label="Control"),
                LegendEntryConfig(source_y="Col 2", label="Sample A"),
            ],
            [2],
            self.window.selected_series_configs(),
        )
        self.window.update_undo_baseline()
        self.window.render_plot()

        self.assertTrue(changed)
        self.assertEqual(
            self.window.plot_config.legend_entries,
            [
                LegendEntryConfig(source_y="Col 3", label="Control"),
                LegendEntryConfig(source_y="Col 2", label="Sample A"),
            ],
        )
        self.assertEqual(
            [text.get_text() for text in self.window.legend_artist.get_texts()],
            ["Control", "Sample A"],
        )
        self.assertEqual(
            [handle.get_marker() for handle in self.window.legend_artist.legend_handles],
            ["s", "o"],
        )
        self.assertEqual(self.window.legend_artist._ncols, 2)
        self.assertEqual(self.window.df.iloc[1].tolist(), original_names.tolist())
        self.assertEqual(
            {
                key: series.label
                for key, series in self.window.series_by_y.items()
            },
            original_labels,
        )

        self.window.save_active_state()
        payload = self.window.project_payload()
        _sheets, restored_graphs, _tree, _active = (
            self.window.load_payload_into_model(payload, "legend-round-trip")
        )
        restored_graph = next(iter(restored_graphs.values()))
        self.assertEqual(
            restored_graph.plot_config.legend_entries,
            [
                LegendEntryConfig(source_y="Col 3", label="Control"),
                LegendEntryConfig(source_y="Col 2", label="Sample A"),
            ],
        )
        self.assertEqual(restored_graph.plot_config.legend_row_lengths, [2])

        self.window.undo_workspace()
        self.assertIsNone(self.window.plot_config.legend_entries)
        self.window.redo_workspace()
        self.assertEqual(
            [entry.source_y for entry in self.window.plot_config.legend_entries],
            ["Col 3", "Col 2"],
        )

    def test_legend_editor_keeps_source_name_pairs_and_row_layout(self) -> None:
        dialog = LegendEditorDialog(
            [
                ("Col 2", "Series A", True),
                ("Col 3", "Series B", True),
            ],
            [
                LegendEntryConfig(source_y="Col 3", label="Control"),
                LegendEntryConfig(source_y="Col 2", label="Sample"),
            ],
            [2],
            automatic=False,
            parent=self.window,
        )
        try:
            entries, row_lengths = dialog.result_config()

            self.assertEqual(
                entries,
                [
                    LegendEntryConfig(source_y="Col 3", label="Control"),
                    LegendEntryConfig(source_y="Col 2", label="Sample"),
                ],
            )
            self.assertEqual(row_lengths, [2])

            dialog.reset_automatic()
            entries, row_lengths = dialog.result_config()
            self.assertIsNone(entries)
            self.assertEqual(row_lengths, [])
        finally:
            dialog.close()

    def test_manual_legend_is_authoritative_without_mutating_auto_visibility(self) -> None:
        self.window.table.item(0, 2).setText("Y")
        self.window.new_graph()
        self.window._set_checked_y_columns(["Col 2", "Col 3"])
        self.window.refresh_series_configs()
        self.window.series_by_y["Col 2"].show_in_legend = True
        self.window.series_by_y["Col 3"].show_in_legend = False
        self.window.plot_config.legend_entries = [
            LegendEntryConfig(label="Manual heading")
        ]
        series_configs = self.window.selected_series_configs()
        candidates = self.window.legend_editor_candidates(series_configs)

        self.assertEqual(
            self.window.legend_editor_entries(candidates),
            [LegendEntryConfig(label="Manual heading")],
        )
        changed = self.window.apply_legend_entry_config(
            [
                LegendEntryConfig(
                    label="Manual heading",
                    font_size=11.0,
                    font_bold=True,
                    text_color="#123456",
                ),
                LegendEntryConfig(source_y="Col 3", label="Control"),
            ],
            [2],
            series_configs,
        )

        self.assertTrue(changed)
        self.assertTrue(self.window.series_by_y["Col 2"].show_in_legend)
        self.assertFalse(self.window.series_by_y["Col 3"].show_in_legend)
        self.assertEqual(
            self.window.plot_config.legend_entries,
            [
                LegendEntryConfig(
                    label="Manual heading",
                    font_size=11.0,
                    font_bold=True,
                    text_color="#123456",
                ),
                LegendEntryConfig(source_y="Col 3", label="Control"),
            ],
        )
        self.assertEqual(self.window.plot_config.legend_row_lengths, [2])

    def test_shared_sheet_column_rename_and_delete_update_every_graph(self) -> None:
        self.window.table.item(0, 2).setText("Y")
        self.window.new_graph()
        first_graph_id = self.window.active_graph_id
        sheet_id = self.window.active_sheet_id
        self.window._set_checked_y_columns(["Col 2"])
        self.window.plot_config.legend_entries = [
            LegendEntryConfig(label="Measurements"),
            LegendEntryConfig(source_y="Col 2", label="First graph"),
        ]
        self.window.save_active_state()

        self.window.new_graph()
        second_graph_id = self.window.active_graph_id
        self.assertNotEqual(first_graph_id, second_graph_id)
        self.window._set_checked_y_columns(["Col 2"])
        self.window.plot_config.legend_entries = [
            LegendEntryConfig(label="Measurements"),
            LegendEntryConfig(source_y="Col 2", label="Second graph"),
        ]
        self.window.save_active_state()

        self.window.table.setCurrentCell(2, 1)
        with patch(
            "app.QInputDialog.getText",
            return_value=("Renamed Y", True),
        ):
            self.window.rename_current_column()

        self.assertEqual(self.window.checked_y_columns(), ["Renamed Y"])
        self.assertIn("Renamed Y", self.window.series_by_y)
        self.assertNotIn("Col 2", self.window.series_by_y)
        self.assertEqual(
            [entry.source_y for entry in self.window.plot_config.legend_entries],
            ["", "Renamed Y"],
        )
        for graph_id in (first_graph_id, second_graph_id):
            graph = self.window.graphs[graph_id]
            self.assertEqual(graph.checked_y, ["Renamed Y"])
            self.assertIn("Renamed Y", graph.series_by_y)
            self.assertNotIn("Col 2", graph.series_by_y)
            self.assertEqual(
                [entry.source_y for entry in graph.plot_config.legend_entries],
                ["", "Renamed Y"],
            )
        self.assertIn("Renamed Y", self.window.sheets[sheet_id].df.columns)
        self.assertNotIn("Col 2", self.window.sheets[sheet_id].df.columns)

        self.window.table.clearSelection()
        renamed_column = list(self.window.df.columns).index("Renamed Y")
        self.window.table.setCurrentCell(2, renamed_column)
        self.window.delete_selected_columns()

        self.assertEqual(self.window.checked_y_columns(), [])
        self.assertNotIn("Renamed Y", self.window.series_by_y)
        self.assertEqual(
            self.window.plot_config.legend_entries,
            [LegendEntryConfig(label="Measurements")],
        )
        for graph_id in (first_graph_id, second_graph_id):
            graph = self.window.graphs[graph_id]
            self.assertEqual(graph.checked_y, [])
            self.assertNotIn("Renamed Y", graph.series_by_y)
            self.assertEqual(
                graph.plot_config.legend_entries,
                [LegendEntryConfig(label="Measurements")],
            )
        self.assertNotIn("Renamed Y", self.window.sheets[sheet_id].df.columns)

    def test_legend_editor_uses_text_order_and_tabs_for_row_boundaries(
        self,
    ) -> None:
        candidates = [
            ("A", "A", True),
            ("B", "B", True),
            ("C", "C", True),
            ("D", "D", True),
        ]
        entries = [
            LegendEntryConfig(source_y=source, label=source)
            for source in ("A", "B", "C", "D")
        ]

        dialog = LegendEditorDialog(
            candidates,
            entries,
            [2, 2],
            automatic=False,
            parent=self.window,
        )
        try:
            dialog.text_edit.setPlainText(
                "\\L(4) Fourth\t\\L(1) First\nHeading\n\\L(3) Third"
            )
            edited_entries, edited_row_lengths = dialog.result_config()

            self.assertEqual(
                [entry.source_y for entry in edited_entries],
                ["D", "A", "", "C"],
            )
            self.assertEqual(
                [entry.label for entry in edited_entries],
                ["Fourth", "First", "Heading", "Third"],
            )
            self.assertEqual(edited_row_lengths, [2, 1, 1])
        finally:
            dialog.close()

    def test_controls_follow_selected_project_context(self) -> None:
        self.assertIsNone(self.window.active_graph_id)
        self.assertTrue(self.window.table.isEnabled())
        self.assertFalse(self.window.figure_box.isEnabled())
        self.assertFalse(self.window.edit_legend_btn.isEnabled())

        self.window.new_graph()
        self.assertIsNotNone(self.window.active_graph_id)
        self.assertEqual(self.window.checked_y_columns(), ["Col 2"])
        self.assertTrue(self.window.figure_box.isEnabled())
        self.assertTrue(self.window.edit_legend_btn.isEnabled())
        self.assertTrue(self.window.export_btn.isEnabled())

        self.window.new_folder()
        self.assertIsNone(self.window.active_graph_id)
        self.assertFalse(self.window.table.isEnabled())
        self.assertFalse(self.window.figure_box.isEnabled())
        self.assertFalse(self.window.edit_legend_btn.isEnabled())
        self.assertFalse(self.window.export_figure_action.isEnabled())

    def test_axis_line_width_control_updates_config_and_render(self) -> None:
        self.window.new_graph()
        self.window.table.set_block(2, 0, [[0, 1], [1, 2], [2, 3]])
        self.window.axis_line_width_spin.setValue(2.25)
        self.window.render_timer.stop()

        config = self.window.collect_plot_config()
        self.window.render_plot()

        self.assertEqual(config.axis_line_width, 2.25)
        self.assertTrue(
            all(
                spine.get_linewidth() == 2.25
                for spine in self.window.current_figure.axes[0].spines.values()
            )
        )

        self.window.plot_config.axis_line_width = 1.75
        self.window._load_config_into_widgets()
        self.assertEqual(self.window.axis_line_width_spin.value(), 1.75)

    def test_tick_mark_control_keeps_axis_lines_and_labels(self) -> None:
        self.window.new_graph()
        self.window.table.set_block(2, 0, [[0, 1], [1, 2], [2, 3]])
        self.window.show_tick_marks_check.setChecked(False)
        self.window.render_timer.stop()

        config = self.window.collect_plot_config()
        self.window.render_plot()
        axis = self.window.current_figure.axes[0]

        self.assertFalse(config.show_tick_marks)
        self.assertTrue(all(spine.get_visible() for spine in axis.spines.values()))
        for tick in axis.xaxis.get_major_ticks() + axis.xaxis.get_minor_ticks():
            self.assertFalse(tick.tick1line.get_visible())
            self.assertFalse(tick.tick2line.get_visible())
        for tick in axis.yaxis.get_major_ticks() + axis.yaxis.get_minor_ticks():
            self.assertFalse(tick.tick1line.get_visible())
            self.assertFalse(tick.tick2line.get_visible())
        self.assertTrue(any(label.get_visible() for label in axis.get_xticklabels()))
        self.assertTrue(any(label.get_visible() for label in axis.get_yticklabels()))

        self.window.plot_config.show_tick_marks = True
        self.window._load_config_into_widgets()
        self.assertTrue(self.window.show_tick_marks_check.isChecked())

    def test_axis_arrow_control_updates_config_and_render(self) -> None:
        self.window.new_graph()
        self.window.table.set_block(2, 0, [[0, 1], [1, 2], [2, 3]])
        self.window.show_top_axis_check.setChecked(False)
        self.window.show_right_axis_check.setChecked(False)
        self.window.show_axis_arrows_check.setChecked(True)
        self.window.render_timer.stop()

        config = self.window.collect_plot_config()
        self.window.render_plot()
        arrows = [
            patch
            for axis in self.window.current_figure.axes
            for patch in axis.patches
            if (patch.get_gid() or "").startswith("pubfig_axis_arrow_")
        ]

        self.assertTrue(config.show_axis_arrows)
        self.assertEqual(
            {arrow.get_gid() for arrow in arrows},
            {"pubfig_axis_arrow_bottom", "pubfig_axis_arrow_left"},
        )

        self.window.plot_config.show_axis_arrows = False
        self.window._load_config_into_widgets()
        self.assertFalse(self.window.show_axis_arrows_check.isChecked())

    def test_loading_graph_renders_once_without_queued_duplicate(self) -> None:
        self.window.new_graph()
        calls = 0
        original_render = self.window.preview.render_plot

        def counted_render() -> None:
            nonlocal calls
            calls += 1
            original_render()

        self.window.preview.render_plot = counted_render
        node = self.window.active_node()
        self.window.load_node(node)

        self.assertEqual(calls, 1)
        self.assertFalse(self.window.render_timer.isActive())

    def test_switching_to_another_sheet_keeps_the_previous_graph_state(self) -> None:
        self.window.new_graph()
        graph_id = self.window.active_graph_id
        graph_node_id = self.window.active_node_id
        self.window.title_edit.setText("Original graph")
        self.window.annotations.append(AnnotationConfig(text="Keep annotation"))
        self.window.save_active_state()
        original = self.window.graphs[graph_id]
        original_series = set(original.series_by_y)

        self.window.project_tree.new_sheet()
        self.window.table.item(0, 1).setText("")
        self.window.table.item(2, 0).setText("Other sheet data")

        self.assertEqual(original.plot_config.title, "Original graph")
        self.assertEqual(set(original.series_by_y), original_series)
        self.assertEqual(original.annotations[0].text, "Keep annotation")
        self.window.project_tree._select_node(graph_node_id)
        self.assertIs(self.window.session.graph, original)
        self.assertIs(self.window.plot_config, original.plot_config)
        self.assertIs(self.window.annotations, original.annotations)
        self.assertIs(self.window.series_by_y, original.series_by_y)
        self.assertIs(self.window.df, self.window.session.sheet.df)
        self.assertIs(self.window.table.dataframe(), self.window.session.sheet.df)

    def test_undo_restores_canonical_graph_annotations_and_config_together(self) -> None:
        self.window.new_graph()
        self.window.annotations.append(AnnotationConfig(text="Before"))
        self.window.undo_history.reset()
        self.window.push_current_undo_state()
        self.window.annotations = [AnnotationConfig(text="After")]
        self.window.update_undo_baseline()

        self.window.workspace.undo_workspace()
        graph = self.window.session.graph
        self.assertEqual(graph.annotations[0].text, "Before")
        self.assertIs(self.window.annotations, graph.plot_config.annotations)
        self.window.workspace.redo_workspace()
        graph = self.window.session.graph
        self.assertEqual(graph.annotations[0].text, "After")
        self.assertIs(self.window.annotations, graph.annotations)

    def test_loading_flag_is_restored_when_graph_loading_fails(self) -> None:
        self.window.new_graph()
        node = self.window.active_node()

        with patch.object(
            self.window.workspace,
            "load_graph",
            side_effect=RuntimeError("invalid graph state"),
        ):
            with self.assertRaisesRegex(RuntimeError, "invalid graph state"):
                self.window.load_node(node)

        self.assertFalse(self.window.loading_project_figure)

    def test_preset_dependent_values_are_one_undoable_change(self) -> None:
        self.window.new_graph()
        self.window.undo_history.reset()

        with patch.object(self.window.preview, "schedule_render") as schedule_render:
            self.window.preset_combo.setCurrentText("ACS 1-col")
            self.assertEqual(schedule_render.call_count, 1)
            self.window.title_edit.setText("Title after preset")

        self.window.undo_workspace()

        self.assertEqual(self.window.preset_combo.currentText(), "ACS 1-col")
        self.assertAlmostEqual(self.window.width_spin.value(), 84.7)
        self.assertAlmostEqual(
            self.window.plot_width_spin.value(),
            84.7
            - self.window.plot_margin_left_spin.value()
            - self.window.plot_margin_right_spin.value(),
        )

    def test_locked_dimension_and_ratio_changes_keep_complete_undo_baselines(
        self,
    ) -> None:
        self.window.new_graph()
        controls = (
            self.window.plot_width_spin,
            self.window.plot_height_spin,
            self.window.plot_ratio_lock_check,
            self.window.plot_ratio_preset_combo,
        )
        for widget in controls:
            widget.blockSignals(True)
        try:
            self.window.plot_width_spin.setValue(50.0)
            self.window.plot_height_spin.setValue(50.0)
            self.window.plot_ratio_lock_check.setChecked(True)
            self.window.plot_ratio_preset_combo.setCurrentText("Current")
        finally:
            for widget in controls:
                widget.blockSignals(False)
        self.window.collect_plot_config()
        self.window.undo_history.reset()

        self.window.plot_width_spin.setValue(60.0)
        self.assertEqual(
            (
                self.window.plot_width_spin.value(),
                self.window.plot_height_spin.value(),
            ),
            (60.0, 60.0),
        )
        self.window.title_edit.setText("Title after width")
        self.window.undo_workspace()
        self.assertEqual(
            (
                self.window.plot_width_spin.value(),
                self.window.plot_height_spin.value(),
            ),
            (60.0, 60.0),
        )

        self.window.undo_history.reset()
        self.window.plot_ratio_preset_combo.setCurrentText("2:1")
        self.assertEqual(
            (
                self.window.plot_width_spin.value(),
                self.window.plot_height_spin.value(),
            ),
            (60.0, 30.0),
        )
        self.window.title_edit.setText("Title after ratio")
        self.window.undo_workspace()
        self.assertEqual(
            (
                self.window.plot_width_spin.value(),
                self.window.plot_height_spin.value(),
            ),
            (60.0, 30.0),
        )

    def test_large_plot_width_survives_fit_and_png_export(self) -> None:
        self.window.handle_table_paste(2, 0, "0\t1\n1\t2\n2\t0")
        self.window.new_graph()
        panel = self.window.figure_settings
        panel.dpi_spin.setValue(100)
        panel.plot_height_spin.setValue(50)
        panel.fixed_plot_area_check.setChecked(True)
        panel.plot_ratio_lock_check.setChecked(False)

        for trim in (False, True):
            with self.subTest(trim=trim):
                self.window.trim_check.setChecked(trim)
                sizes = []
                for width in (400, 600):
                    panel.plot_width_spin.setValue(width)
                    panel.fit_canvas_btn.click()
                    self.assertEqual(panel.plot_width_spin.value(), width)
                    self.assertGreater(panel.width_spin.value(), width)
                    path = Path(self.temp_directory.name) / f"{width}_{trim}.png"
                    with patch(
                        "pubfig.ui.preview_controller.QFileDialog.getSaveFileName",
                        return_value=(str(path), "PNG (*.png)"),
                    ):
                        self.window.preview.export_current_figure()
                    with Image.open(path) as image:
                        sizes.append(image.size)
                        self.assertAlmostEqual(image.info["dpi"][0], 100, places=2)
                        if not trim:
                            self.assertAlmostEqual(
                                image.width, panel.width_spin.value() / 25.4 * 100,
                                delta=1,
                            )
                # Check saved pixels, independently of any preview/viewer zoom.
                self.assertAlmostEqual(
                    sizes[1][0] - sizes[0][0], 200 / 25.4 * 100, delta=2,
                )
                self.assertEqual(sizes[1][1], sizes[0][1])

    def test_ratio_update_preserves_an_existing_signal_block(self) -> None:
        self.window.plot_height_spin.blockSignals(True)
        try:
            self.window.apply_plot_ratio_from_width(2.0)
            self.assertTrue(self.window.plot_height_spin.signalsBlocked())
        finally:
            self.window.plot_height_spin.blockSignals(False)

    def test_temporary_render_failures_are_reported_without_escaping(self) -> None:
        self.window.new_graph()

        with patch.object(
            self.window.rendering,
            "render_bytes",
            side_effect=ValueError("bad clipboard render"),
        ):
            self.window.copy_figure_to_clipboard()
        self.assertIn("Clipboard copy failed", self.window.statusBar().currentMessage())

        for action, expected in (
            (self.window.center_content, "Center content failed"),
            (self.window.fit_canvas_to_content, "Fit canvas failed"),
        ):
            with self.subTest(action=action.__name__):
                with patch.object(
                    self.window.rendering,
                    "measure",
                    side_effect=ValueError("bad measurement"),
                ):
                    action()
                self.assertIn(expected, self.window.statusBar().currentMessage())

    def test_switching_graphs_on_same_sheet_keeps_live_table(self) -> None:
        self.window.new_graph()
        first_graph_id = self.window.active_graph_id
        self.window.new_graph()
        second_graph_id = self.window.active_graph_id
        self.assertNotEqual(first_graph_id, second_graph_id)

        live_dataframe = self.window.table.table_model._df
        first_node = next(
            node
            for node in self.window._sheet_node_for(self.window.active_sheet_id).children
            if node.ref_id == first_graph_id
        )
        self.window.active_node_id = first_node.id
        self.window.load_node(first_node)

        self.assertIs(self.window.table.table_model._df, live_dataframe)

    def test_canvas_is_cleanly_replaced_between_renders(self) -> None:
        self.window.new_graph()
        self.window.render_plot()
        original_canvas = self.window.canvas
        original_toolbar = self.window.toolbar

        self.window.title_edit.setText("Updated title")
        self.window.render_timer.stop()
        self.window.render_plot()

        self.assertIsNot(self.window.canvas, original_canvas)
        self.assertIsNot(self.window.toolbar, original_toolbar)
        self.assertFalse(original_canvas.isVisible())
        self.assertFalse(original_toolbar.isVisible())
        self.assertEqual(self.window.plot_layout.indexOf(original_canvas), -1)
        self.assertEqual(self.window.plot_layout.indexOf(original_toolbar), -1)

    def test_close_stops_timers_and_clears_the_current_figure(self) -> None:
        self.window.new_graph()
        self.window.render_plot()
        figure = self.window.current_figure
        self.window._set_modified(False)

        self.window.close()

        self.assertFalse(self.window.render_timer.isActive())
        self.assertFalse(self.window.autosave_timer.isActive())
        self.assertIsNone(self.window.current_figure)
        self.assertEqual(figure.axes, [])

    def test_close_unregisters_application_event_filter_once(self) -> None:
        self.assertTrue(self.window._application_event_filter_installed)
        self.window._set_modified(False)

        self.window.close()

        self.assertFalse(self.window._application_event_filter_installed)
        # A second close must remain a harmless no-op for the global filter.
        self.window.close()
        self.assertFalse(self.window._application_event_filter_installed)

    def test_preview_dpi_accounts_for_retina_pixel_ratio(self) -> None:
        self.window.new_graph()
        self.window.render_plot()
        self.window.canvas._set_device_pixel_ratio(2.0)

        self.window.update_canvas_size()

        figure = self.window.current_figure
        self.assertAlmostEqual(figure.dpi, figure._original_dpi * 2.0)
        expected_logical_width = figure.get_figwidth() * figure._original_dpi
        self.assertAlmostEqual(expected_logical_width, self.window.canvas.width(), delta=1.0)

    def test_interface_theme_switches_and_persists(self) -> None:
        self.window.set_interface_theme("dark")

        self.assertEqual(self.window.interface_theme, "dark")
        self.assertEqual(self.app.property("pubfigTheme"), "dark")
        self.assertEqual(self.window.settings.value("interface_theme"), "dark")
        self.assertTrue(self.window.theme_actions["dark"].isChecked())

        self.window.set_interface_theme("light")
        self.assertEqual(self.window.interface_theme, "light")
        self.assertTrue(self.window.theme_actions["light"].isChecked())

    def test_replacing_annotated_figure_discards_stale_handles(self) -> None:
        self.window.new_graph()
        self.window.annotations = [AnnotationConfig(kind="text", text="sample", x=0.5, y=0.5)]
        self.window.plot_config.annotations = self.window.annotations
        self.window.refresh_annotation_list()
        self.window.annotation_list.setCurrentRow(0)
        self.window.render_plot()
        self.assertGreater(len(self.window.annotation_handle_artists), 0)

        self.window.render_plot()
        self.assertGreater(len(self.window.annotation_handle_artists), 0)

    def test_annotation_can_be_dragged_before_data_is_plottable(self) -> None:
        self.window.new_graph()
        annotation = AnnotationConfig(
            kind="text",
            text="drag me",
            x=0.4,
            y=0.4,
            width=0.15,
            height=0.15,
        )
        self.window.annotations = [annotation]
        self.window.plot_config.annotations = self.window.annotations
        self.window.refresh_annotation_list()
        self.window.annotation_list.setCurrentRow(0)
        self.window.render_plot()
        self.window.canvas.draw()

        self.assertEqual(len(self.window.annotation_artists), 1)
        axis = self.window.annotation_axes()
        start_x, start_y = axis.transAxes.transform((0.4, 0.4))
        end_x, end_y = axis.transAxes.transform((0.55, 0.55))
        press = MouseEvent(
            "button_press_event",
            self.window.canvas,
            start_x,
            start_y,
            button=1,
        )
        motion = MouseEvent(
            "motion_notify_event",
            self.window.canvas,
            end_x,
            end_y,
            button=1,
        )
        release = MouseEvent(
            "button_release_event",
            self.window.canvas,
            end_x,
            end_y,
            button=1,
        )

        self.window.canvas.callbacks.process("button_press_event", press)
        self.assertEqual(self.window.drag_annotation_index, 0)
        self.window.canvas.callbacks.process("motion_notify_event", motion)
        self.window.canvas.callbacks.process("button_release_event", release)

        self.assertAlmostEqual(annotation.x, 0.55, delta=0.01)
        self.assertAlmostEqual(annotation.y, 0.55, delta=0.01)
        self.assertIsNone(self.window.drag_annotation_index)

    def test_named_style_persists_and_applies_in_another_window(self) -> None:
        self.window.new_graph()
        self.window.title_edit.setText("Copied title")
        self.window.title_size_spin.setValue(17)
        self.window.axis_size_spin.setValue(11)
        self.window.line_width_spin.setValue(2.75)
        self.window.annotations = [
            AnnotationConfig(kind="text", text="saved annotation", x=0.25, y=0.75)
        ]
        self.window.plot_config.annotations = self.window.annotations
        self.window.include_annotations_style_check.setChecked(True)

        self.assertTrue(self.window.save_named_style("Nature main"))
        self.assertEqual(self.window.saved_style_combo.currentText(), "Nature main")

        original_restore = GraphDrawerWindow.maybe_restore_autosave
        GraphDrawerWindow.maybe_restore_autosave = lambda _window: None
        try:
            other = GraphDrawerWindow()
        finally:
            GraphDrawerWindow.maybe_restore_autosave = original_restore
        try:
            other.autosave_timer.stop()
            other.autosave_path = Path(self.temp_directory.name) / "other-autosave.json"
            other.settings = QSettings(str(self.settings_path), QSettings.IniFormat)
            other.refresh_saved_style_combo()
            other.new_graph()
            other.title_edit.setText("Keep this title")
            other.title_size_spin.setValue(6)
            other.axis_size_spin.setValue(6)
            other.line_width_spin.setValue(0.5)
            other.include_annotations_style_check.setChecked(True)

            self.assertEqual(other.saved_style_combo.currentText(), "Nature main")
            self.assertTrue(other.apply_named_style("Nature main"))
            self.assertEqual(other.title_edit.text(), "Copied title")
            self.assertEqual(other.title_size_spin.value(), 17)
            self.assertEqual(other.axis_size_spin.value(), 11)
            self.assertAlmostEqual(other.line_width_spin.value(), 2.75)
            self.assertEqual([item.text for item in other.annotations], ["saved annotation"])
        finally:
            other._set_modified(False)
            other.close()
            self.app.processEvents()

        self.assertTrue(self.window.delete_named_style("Nature main", confirm=False))
        self.assertEqual(self.window.saved_style_payloads(), {})

    def test_copy_and_apply_style_copies_title_text_and_format(self) -> None:
        self.window.new_graph()
        self.window.title_edit.setText("Source title")
        self.window.title_size_spin.setValue(18)
        self.window.axis_size_spin.setValue(12)
        self.window.copy_current_style()

        self.window.title_edit.setText("Target title")
        self.window.title_size_spin.setValue(5)
        self.window.axis_size_spin.setValue(6)
        self.window.apply_copied_style()

        self.assertEqual(self.window.title_edit.text(), "Source title")
        self.assertEqual(self.window.title_size_spin.value(), 18)
        self.assertEqual(self.window.axis_size_spin.value(), 12)

    def test_style_application_preserves_legend_mapping_and_copies_legend_format(self) -> None:
        self.window.table.item(0, 2).setText("Y")
        self.window.new_graph()
        self.window._set_checked_y_columns(["Col 2", "Col 3"])
        self.window.refresh_series_configs()
        self.window.series_by_y["Col 2"].show_in_legend = True
        self.window.series_by_y["Col 3"].show_in_legend = False
        self.window.plot_config.legend_entries = [
            LegendEntryConfig(source_y="Col 2", label="Only this entry"),
            LegendEntryConfig(source_y="Col 3", label="Second entry"),
        ]
        self.window.plot_config.legend_row_lengths = [1, 1]

        bundle = self.window.capture_current_style()
        bundle["series_templates"][0].show_in_legend = False
        bundle["series_templates"][1].show_in_legend = True
        bundle["plot_config"].legend_entries = [
            LegendEntryConfig(
                source_y="Source column",
                label="Source legend text",
                font_family="DejaVu Sans",
                font_size=13.0,
                font_bold=True,
                font_italic=True,
                text_color="#123456",
            )
        ]
        bundle["plot_config"].legend_row_lengths = [2]
        self.window.apply_style_bundle(bundle, "test style")

        self.assertFalse(self.window.series_by_y["Col 2"].show_in_legend)
        self.assertTrue(self.window.series_by_y["Col 3"].show_in_legend)
        self.assertEqual(
            [
                (entry.source_y, entry.label)
                for entry in self.window.plot_config.legend_entries
            ],
            [("Col 2", "Only this entry"), ("Col 3", "Second entry")],
        )
        for entry in self.window.plot_config.legend_entries:
            self.assertEqual(entry.font_family, "DejaVu Sans")
            self.assertEqual(entry.font_size, 13.0)
            self.assertTrue(entry.font_bold)
            self.assertTrue(entry.font_italic)
            self.assertEqual(entry.text_color, "#123456")
        self.assertEqual(self.window.plot_config.legend_row_lengths, [2])


if __name__ == "__main__":
    unittest.main()
