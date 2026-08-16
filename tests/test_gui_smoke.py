from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
from pandas.testing import assert_frame_equal
from PySide6.QtCore import QEvent, QModelIndex, QSettings, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import QApplication

from app import GraphDrawerWindow, LegendEditorDialog
from plot_config import AnnotationConfig, LegendEntryConfig
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

        self.window.undo_workspace()
        self.assertEqual(self.window.table.item(2, 0).text(), original)

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

    def test_shared_sheet_column_rename_and_delete_update_every_graph(self) -> None:
        self.window.table.item(0, 2).setText("Y")
        self.window.new_graph()
        first_graph_id = self.window.active_graph_id
        sheet_id = self.window.active_sheet_id
        self.window._set_checked_y_columns(["Col 2"])
        self.window.plot_config.legend_entries = [
            LegendEntryConfig(source_y="Col 2", label="First graph"),
        ]
        self.window.save_active_state()

        self.window.new_graph()
        second_graph_id = self.window.active_graph_id
        self.assertNotEqual(first_graph_id, second_graph_id)
        self.window._set_checked_y_columns(["Col 2"])
        self.window.plot_config.legend_entries = [
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
            ["Renamed Y"],
        )
        for graph_id in (first_graph_id, second_graph_id):
            graph = self.window.graphs[graph_id]
            self.assertEqual(graph.checked_y, ["Renamed Y"])
            self.assertIn("Renamed Y", graph.series_by_y)
            self.assertNotIn("Col 2", graph.series_by_y)
            self.assertEqual(
                [entry.source_y for entry in graph.plot_config.legend_entries],
                ["Renamed Y"],
            )
        self.assertIn("Renamed Y", self.window.sheets[sheet_id].df.columns)
        self.assertNotIn("Col 2", self.window.sheets[sheet_id].df.columns)

        self.window.table.clearSelection()
        renamed_column = list(self.window.df.columns).index("Renamed Y")
        self.window.table.setCurrentCell(2, renamed_column)
        self.window.delete_selected_columns()

        self.assertEqual(self.window.checked_y_columns(), [])
        self.assertNotIn("Renamed Y", self.window.series_by_y)
        self.assertEqual(self.window.plot_config.legend_entries, [])
        for graph_id in (first_graph_id, second_graph_id):
            graph = self.window.graphs[graph_id]
            self.assertEqual(graph.checked_y, [])
            self.assertNotIn("Renamed Y", graph.series_by_y)
            self.assertEqual(graph.plot_config.legend_entries, [])
        self.assertNotIn("Renamed Y", self.window.sheets[sheet_id].df.columns)

    def test_legend_editor_preserves_position_row_boundaries_on_delete_and_move(
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

        delete_dialog = LegendEditorDialog(
            candidates,
            entries,
            [2, 2],
            automatic=False,
            parent=self.window,
        )
        try:
            delete_dialog.table.selectRow(2)
            delete_dialog.remove_selected_entry()
            deleted_entries, deleted_row_lengths = delete_dialog.result_config()

            self.assertEqual(
                [entry.source_y for entry in deleted_entries],
                ["A", "B", "D"],
            )
            self.assertEqual(deleted_row_lengths, [2, 1])
        finally:
            delete_dialog.close()

        move_dialog = LegendEditorDialog(
            candidates,
            entries,
            [2, 2],
            automatic=False,
            parent=self.window,
        )
        try:
            move_dialog.table.selectRow(0)
            move_dialog.move_selected_entry(1)
            moved_entries, moved_row_lengths = move_dialog.result_config()

            self.assertEqual(
                [entry.source_y for entry in moved_entries],
                ["B", "A", "C", "D"],
            )
            self.assertEqual(moved_row_lengths, [2, 2])
        finally:
            move_dialog.close()

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
        original_render = self.window.render_plot

        def counted_render() -> None:
            nonlocal calls
            calls += 1
            original_render()

        self.window.render_plot = counted_render
        node = self.window.active_node()
        self.window.load_node(node)

        self.assertEqual(calls, 1)
        self.assertFalse(self.window.render_timer.isActive())

    def test_loading_flag_is_restored_when_graph_loading_fails(self) -> None:
        self.window.new_graph()
        node = self.window.active_node()

        with patch.object(
            self.window,
            "load_graph",
            side_effect=RuntimeError("invalid graph state"),
        ):
            with self.assertRaisesRegex(RuntimeError, "invalid graph state"):
                self.window.load_node(node)

        self.assertFalse(self.window.loading_project_figure)

    def test_preset_dependent_values_are_one_undoable_change(self) -> None:
        self.window.new_graph()
        self.window.undo_history.reset()

        with patch.object(self.window, "schedule_render") as schedule_render:
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

    def test_named_style_persists_and_applies_in_another_window(self) -> None:
        self.window.new_graph()
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
            other.axis_size_spin.setValue(6)
            other.line_width_spin.setValue(0.5)
            other.include_annotations_style_check.setChecked(True)

            self.assertEqual(other.saved_style_combo.currentText(), "Nature main")
            self.assertTrue(other.apply_named_style("Nature main"))
            self.assertEqual(other.title_edit.text(), "Keep this title")
            self.assertEqual(other.axis_size_spin.value(), 11)
            self.assertAlmostEqual(other.line_width_spin.value(), 2.75)
            self.assertEqual([item.text for item in other.annotations], ["saved annotation"])
        finally:
            other._set_modified(False)
            other.close()
            self.app.processEvents()

        self.assertTrue(self.window.delete_named_style("Nature main", confirm=False))
        self.assertEqual(self.window.saved_style_payloads(), {})

    def test_style_application_preserves_explicit_legend_visibility(self) -> None:
        self.window.table.item(0, 2).setText("Y")
        self.window.new_graph()
        self.window._set_checked_y_columns(["Col 2", "Col 3"])
        self.window.refresh_series_configs()
        self.window.series_by_y["Col 2"].show_in_legend = True
        self.window.series_by_y["Col 3"].show_in_legend = False
        self.window.plot_config.legend_entries = [
            LegendEntryConfig(source_y="Col 2", label="Only this entry"),
        ]

        bundle = self.window.capture_current_style()
        bundle["series_templates"][0].show_in_legend = False
        bundle["series_templates"][1].show_in_legend = True
        self.window.apply_style_bundle(bundle, "test style")

        self.assertTrue(self.window.series_by_y["Col 2"].show_in_legend)
        self.assertFalse(self.window.series_by_y["Col 3"].show_in_legend)
        self.assertEqual(
            self.window.plot_config.legend_entries,
            [LegendEntryConfig(source_y="Col 2", label="Only this entry")],
        )


if __name__ == "__main__":
    unittest.main()
