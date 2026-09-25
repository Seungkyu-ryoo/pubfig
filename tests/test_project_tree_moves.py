from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication, QAbstractItemView, QTreeWidgetItem

from pubfig.model import TreeNode
from pubfig.ui.main_window import GraphDrawerWindow
from pubfig.ui.widgets import ProjectTreeWidget


class DropEvent:
    def position(self):
        return QPointF()

    def acceptProposedAction(self):
        pass


class ProjectTreeMoveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        with patch.object(GraphDrawerWindow, "maybe_restore_autosave"):
            self.window = GraphDrawerWindow()
        self.window.autosave_timer.stop()
        self.window.autosave_path = Path(self.temp.name) / "autosave.json"
        self.window.new_graph()
        self.controller = self.window.project_tree
        self.graph = self.controller.active_node()
        self.sheet = self.controller._find_parent(self.graph.id)
        self.folder = TreeNode("destination", "folder", "Destination", expanded=False)
        self.window.session.tree_root.children.append(self.folder)
        self.controller.refresh_tree()
        self.window.workspace.undo_history.reset()

    def tearDown(self):
        self.window.workspace._set_modified(False)
        self.window.close()
        self.app.processEvents()
        self.temp.cleanup()

    def test_moves_immediately_reveal_selection_and_keep_editor_in_sync(self):
        for method in ("menu", "drop"):
            for kind in ("sheet", "graph"):
                with self.subTest(method=method, kind=kind):
                    node = self.graph if kind == "graph" else self.sheet
                    self.controller._select_node(node.id)
                    if method == "menu":
                        self.controller._move_node_to_folder(node, self.folder)
                    else:
                        self.controller.handle_tree_drop(node.id, self.folder.id, "on")

                    self.assertEqual(self.folder.children, [self.sheet])
                    self.assertEqual(self.sheet.children, [self.graph])
                    self.assertEqual(self.window.session.active_node_id, node.id)
                    item = self.window.figure_tree.currentItem()
                    self.assertEqual(item.data(0, Qt.UserRole), node.id)
                    parent = item.parent()
                    while parent is not None:
                        self.assertTrue(parent.isExpanded())
                        parent = parent.parent()
                    self.assertEqual(
                        self.window.session.active_graph_id,
                        self.graph.ref_id if kind == "graph" else None,
                    )
                    self.assertTrue(self.window.session.modified)
                    self.window.workspace.undo_workspace()
                    self.assertEqual(self.controller._find_parent(self.sheet.id).id,
                                     self.window.session.tree_root.id)
                    self.window.workspace.redo_workspace()
                    self.assertEqual(self.controller._find_parent(self.sheet.id).id,
                                     self.folder.id)
                    self.window.workspace.undo_workspace()
                    self.graph = self.controller._find_node(self.graph.id)
                    self.sheet = self.controller._find_node(self.sheet.id)
                    self.folder = self.controller._find_node(self.folder.id)

    def test_native_drag_applies_move_after_exit_without_processing_more_events(self):
        tree = self.window.figure_tree
        target = self.controller._tree_items[self.folder.id]

        def native_drag(*_args):
            tree.dropEvent(DropEvent())
            # The native drag may itself pump events; rebuilding here would
            # invalidate the items Qt is still using for the drag.
            self.app.processEvents()
            self.assertEqual(self.folder.children, [])
            return Qt.MoveAction

        with patch("pubfig.ui.widgets.QDrag") as drag_type, patch.object(
            tree, "itemAt", return_value=target
        ), patch.object(
            tree, "dropIndicatorPosition", return_value=QAbstractItemView.OnItem
        ):
            drag_type.return_value.exec.side_effect = native_drag
            tree.startDrag(Qt.MoveAction)

        self.assertEqual(self.folder.children, [self.sheet])
        self.assertEqual(tree.currentItem().data(0, Qt.UserRole), self.graph.id)
        self.assertEqual(tree.currentItem().parent().parent().data(0, Qt.UserRole),
                         self.folder.id)
        self.assertEqual(self.window.workspace.undo_history.undo_count, 1)

    def test_move_from_menu_loads_new_selection_when_another_node_is_active(self):
        self.controller._select_node(self.folder.id)
        self.controller._move_node_to_folder(self.graph, self.folder)
        self.assertEqual(self.window.session.active_node_id, self.graph.id)
        self.assertEqual(self.window.session.active_graph_id, self.graph.ref_id)
        self.assertEqual(self.window.session.active_sheet_id, self.sheet.ref_id)

    def test_rejected_folder_drop_keeps_tree_and_history_intact(self):
        self.controller.handle_tree_drop(self.folder.id, self.folder.id, "on")
        self.assertEqual(self.folder.children, [])
        self.assertEqual(self.window.workspace.undo_history.undo_count, 0)
        self.assertIn(self.folder.id, self.controller._tree_items)


class ProjectTreeDropTimingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_drop_dispatch_does_not_wait_for_another_event(self):
        tree = ProjectTreeWidget()
        source = QTreeWidgetItem(tree, ["Sheet"])
        source.setData(0, Qt.UserRole, "sheet")
        target = QTreeWidgetItem(tree, ["Folder"])
        target.setData(0, Qt.UserRole, "folder")
        tree.setCurrentItem(source)
        moves = []
        tree.node_dropped.connect(lambda *args: moves.append(args))
        with patch.object(tree, "itemAt", return_value=target), patch.object(
            tree, "dropIndicatorPosition", return_value=QAbstractItemView.OnItem
        ):
            tree.dropEvent(DropEvent())
        self.assertEqual(moves, [("sheet", "folder", "on")])

    def test_cancelled_drag_keeps_source_and_does_not_replay_previous_move(self):
        tree = ProjectTreeWidget()
        source = QTreeWidgetItem(tree, ["Sheet"])
        source.setData(0, Qt.UserRole, "sheet")
        tree.setCurrentItem(source)
        moves = []
        tree.node_dropped.connect(lambda *args: moves.append(args))
        with patch("pubfig.ui.widgets.QDrag") as drag_type:
            drag_type.return_value.exec.return_value = Qt.IgnoreAction
            tree.startDrag(Qt.MoveAction)
            tree.startDrag(Qt.MoveAction)
        self.assertEqual(moves, [])
        self.assertIs(tree.currentItem(), source)
        self.assertEqual(tree.topLevelItemCount(), 1)


if __name__ == "__main__":
    unittest.main()
