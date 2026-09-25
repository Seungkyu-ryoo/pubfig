"""Workspace activation and undo transactions around the editor session."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QWidget

from ..model import Graph, ProjectDocument, Sheet, TreeNode, new_id
from ..project_io import document_to_payload
from ..sheet_data import blank_dataframe as make_blank_dataframe
from ..undo import UndoManager
from ..workspace_history import (
    clone_project_document,
    project_snapshot_metadata_weight,
    project_snapshot_resources,
)
from .binding import change_signal
from .constants import MAX_UNDO_STACK_WEIGHT_BYTES

if TYPE_CHECKING:
    from .main_window import GraphDrawerWindow


class WorkspaceController(QObject):
    """Workspace activation and undo transactions around the editor session."""

    def __init__(self, window: GraphDrawerWindow) -> None:
        super().__init__(window)
        self.window = window
        self.session = window.session
        self.undo_history = UndoManager(
            self.current_workspace_snapshot,
            self._restore_workspace_snapshot,
            clone_for_restore=clone_project_document,
            snapshot_weight=self.workspace_snapshot_weight,
            snapshot_resources=project_snapshot_resources,
            max_stack_weight=MAX_UNDO_STACK_WEIGHT_BYTES,
        )

    @property
    def restoring_undo(self) -> bool:
        return self.undo_history.is_restoring

    def _connect_change(self, widget, callback) -> None:
        signal = change_signal(widget)
        if signal is not None:
            signal.connect(
                lambda *args, cb=callback, source=widget: (
                    self.handle_undoable_widget_change(cb, source)
                )
            )

    def handle_undoable_widget_change(
        self, callback, source: QWidget | None = None
    ) -> None:
        """Apply a widget edit while merging rapid changes from one control.

        Text edits and spin-box repeats can emit on every keystroke/step. A
        single undo entry for the whole short editing burst is both more useful
        and avoids deep-copying a large workbook into the undo stack each time.
        """
        if self.session.loading_project_figure:
            callback()
            return
        self._set_modified(True)
        self.undo_history.perform_change(
            callback,
            coalesce_key=id(source) if source is not None else None,
        )

    def _reset_undo_coalescing(self) -> None:
        self.undo_history.end_coalescing()

    def blank_dataframe(self, rows: int = 20, columns: int = 4) -> pd.DataFrame:
        return make_blank_dataframe(rows, columns)

    def capture_active_graph(self) -> Graph | None:
        """Flush the active editor session into its canonical graph object."""
        if (
            self.session.active_graph_id is None
            or self.session.active_graph_id not in self.session.graphs
        ):
            return None
        graph = self.session.graphs[self.session.active_graph_id]
        graph.plot_config = self.window.figure_editor.collect_plot_config()
        graph.checked_y = self.window.table_editor.checked_y_columns()
        return graph

    def sync_active_sheet_df(self) -> None:
        """Flush the live table buffer back into the active sheet object."""
        if (
            self.session.active_sheet_id
            and self.session.active_sheet_id in self.session.sheets
        ):
            self.session.df = self.window.table.dataframe()

    def current_workspace_snapshot(self) -> ProjectDocument:
        # Flush live state into the model before taking a structural snapshot so
        # edits made since the last switch are not lost on undo. DataFrames use
        # Copy-on-Write views; graph/tree metadata remains fully detached.
        self.sync_active_sheet_df()
        self.capture_active_graph()
        return clone_project_document(self.session.document)

    @staticmethod
    def workspace_snapshot_weight(snapshot: ProjectDocument) -> int:
        """Estimate only metadata/wrapper memory owned by one snapshot."""

        return project_snapshot_metadata_weight(snapshot)

    def _set_modified(self, modified: bool) -> None:
        self.session.modified = modified
        self._update_window_title()

    def _update_window_title(self) -> None:
        name = (
            self.window.files.current_project_path.name
            if self.window.files.current_project_path
            else "Untitled project"
        )
        marker = "*" if self.session.modified else ""
        self.window.setWindowTitle(f"pubfig — {name}{marker}")

    def push_current_undo_state(self) -> None:
        if self.restoring_undo or self.session.loading_project_figure:
            return
        self._set_modified(True)
        self.undo_history.checkpoint_current()

    def push_undo_baseline(self) -> None:
        if (
            self.restoring_undo
            or self.session.loading_project_figure
            or not self.undo_history.has_baseline
        ):
            return
        self._set_modified(True)
        self.undo_history.checkpoint_baseline()

    def update_undo_baseline(self) -> None:
        if self.restoring_undo or self.session.loading_project_figure:
            return
        self.undo_history.refresh_baseline()

    def undo_workspace(self) -> None:
        if not self.undo_history.undo():
            self.window.set_status("Nothing to undo.")
            return
        self._set_modified(True)
        self.window.set_status(
            f"Undid last change ({self.undo_history.undo_count} undo, "
            f"{self.undo_history.redo_count} redo available)."
        )

    def redo_workspace(self) -> None:
        if not self.undo_history.redo():
            self.window.set_status("Nothing to redo.")
            return
        self._set_modified(True)
        self.window.set_status(
            f"Redid change ({self.undo_history.undo_count} undo, "
            f"{self.undo_history.redo_count} redo available)."
        )

    def _restore_workspace_snapshot(self, snapshot: ProjectDocument) -> None:
        self.window.preview.preview_numeric_cache.clear()
        # Undo restores project contents, not the file-generation token used by
        # Save.  Keep the latest acknowledged destination across history moves.
        snapshot.source_path = self.session.document.source_path
        snapshot.source_revision = self.session.document.source_revision
        self.session.replace_document(snapshot)
        self.window.project_tree.refresh_tree()
        node = (
            self.window.project_tree._find_node(self.session.active_node_id)
            or self.window.project_tree._first_leaf()
        )
        if node is not None:
            self.session.active_node_id = node.id
            self.load_node(node)

    def _init_blank_project(self) -> None:
        """Reset to a project with a single empty sheet (no graph yet)."""
        document = ProjectDocument()
        node_id = self._seed_blank_sheet(document.sheets, document.tree_root)
        self.set_model(
            document.sheets,
            document.graphs,
            document.tree_root,
            node_id,
        )

    def set_model(
        self,
        sheets: dict[str, Sheet],
        graphs: dict[str, Graph],
        tree_root: TreeNode,
        active_node_id: str | None,
        *,
        source_path: Path | None = None,
        source_revision: tuple[int, int, int, int] | None = None,
    ) -> None:
        """Install a freshly-loaded/migrated model and show its active node."""
        self.window.preview.preview_numeric_cache.clear()
        document = ProjectDocument(
            sheets=sheets,
            graphs=graphs,
            tree_root=tree_root,
            active_node_id=active_node_id,
            source_path=source_path,
            source_revision=source_revision,
        )
        self.session.replace_document(document)
        self.window.project_tree.refresh_tree()
        node = (
            self.window.project_tree._find_node(active_node_id)
            or self.window.project_tree._first_leaf()
        )
        if node is None:
            self.session.active_node_id = self._seed_blank_sheet(
                self.session.sheets, self.session.tree_root
            )
            self.window.project_tree.refresh_tree()
            node = self.window.project_tree._find_node(self.session.active_node_id)
        self.session.active_node_id = node.id
        self.load_node(node)

    def save_active_state(self) -> None:
        """Flush the live table+plot widgets into the active sheet/graph."""
        if self.session.loading_project_figure:
            return
        self.sync_active_sheet_df()
        self.capture_active_graph()

    def load_sheet_into_table(
        self, sheet: Sheet, *, populate_column_controls: bool = True
    ) -> None:
        self.session.df = sheet.df
        self.window.table_editor.populate_table()
        if populate_column_controls:
            self.window.table_editor.populate_columns()

    def load_graph(self, graph: Graph) -> None:
        """Load a graph's plot state. The graph's sheet must already be in
        self.df (load the sheet first)."""
        self.session.active_graph_id = graph.id
        self.window.figure_editor._load_config_into_widgets()
        self.window.table_editor.populate_columns()
        self.window.table_editor._set_checked_y_columns(
            [c for c in graph.checked_y if c in list(map(str, self.session.df.columns))]
        )
        self.window.table_editor.refresh_series_configs()
        self.window.series_editor._discard_invalid_series_color_recipe()
        self.window.series_editor.update_style_targets()
        self.window.series_editor.refresh_cmap_column_list()
        self.window.annotations_editor.refresh_annotation_list()
        self.window.figure_editor.update_axis_control_states()

    def load_node(self, node: TreeNode) -> None:
        """Show a tree node: load its sheet into the table (editable) and, if a
        graph, its plot state; then render."""
        # Discard a preview queued by the previous context. The selected graph
        # is rendered synchronously once its complete state has been loaded.
        self.window.preview.render_timer.stop()
        self.window.preview.linear_fit_results_by_y.clear()
        self.session.loading_project_figure = True
        try:
            sheet_id = self.window.project_tree.current_sheet_id()
            sheet = self.session.sheets.get(sheet_id) if sheet_id else None
            if sheet is not None:
                sheet_changed = sheet.id != self.session.active_sheet_id
                self.session.select_sheet(sheet.id)
                if sheet_changed:
                    # A graph loads its column controls after installing its
                    # own series state. Building them here did the work twice.
                    self.load_sheet_into_table(
                        sheet,
                        populate_column_controls=node.type != "graph",
                    )
            if node.type == "graph" and node.ref_id in self.session.graphs:
                self.session.active_graph_id = node.ref_id
                self.load_graph(self.session.graphs[node.ref_id])
            else:
                # Bare sheet or folder: no plot context.
                self.session.active_graph_id = None
                self.session.series_by_y = {}
                if sheet is not None:
                    self.window.table_editor._set_checked_y_columns([])
                    self.window.table_editor.refresh_series_configs()
                    self.window.series_editor.update_style_targets()
                    self.window.series_editor.refresh_cmap_column_list()
            self.set_editing_context(
                sheet is not None,
                self.session.active_graph_id is not None,
            )
        finally:
            self.session.loading_project_figure = False
        self.window.preview.render_plot()

    def set_editing_context(self, has_sheet: bool, has_graph: bool) -> None:
        """Keep controls aligned with the selected project node.

        Figure settings edited while a bare sheet/folder is selected have no
        graph model to receive them, so leaving those controls enabled creates
        changes that appear saveable but are silently discarded.
        """
        self.window.table.setEnabled(has_sheet)
        self.window.column_box.setEnabled(has_graph)
        self.window.series_box.setEnabled(has_graph)
        self.window.annotation_box.setEnabled(has_graph)
        self.window.figure_box.setEnabled(has_graph)
        self.window.style_copy_box.setEnabled(has_graph)
        self.window.special_chars_box.setEnabled(has_sheet)
        self.window.trim_check.setEnabled(has_graph)
        self.window.transparent_check.setEnabled(has_graph)
        self.window.export_btn.setEnabled(has_graph)
        self.window.export_figure_action.setEnabled(has_graph)
        self.window.copy_figure_action.setEnabled(has_graph)
        if not has_sheet:
            self.window.data_info.setText("Select a sheet to edit data.")

    def project_payload(self) -> dict:
        return document_to_payload(self.project_document())

    def project_document(self) -> ProjectDocument:
        return self.session.document

    def _seed_blank_sheet(self, sheets: dict[str, Sheet], tree_root: TreeNode) -> str:
        """Add one empty sheet + node to an otherwise-empty model; return its node id."""
        sheet = Sheet(id=new_id("sh"), name="Sheet 1", df=self.blank_dataframe())
        sheets[sheet.id] = sheet
        node = TreeNode(id=new_id("nd"), type="sheet", name=sheet.name, ref_id=sheet.id)
        tree_root.children.append(node)
        return node.id
