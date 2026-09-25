from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractItemView

from pubfig.model import TreeNode
from pubfig.ui.main_window import GraphDrawerWindow


class SelectedExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.directory = Path(self.temp.name)
        with patch.object(GraphDrawerWindow, "maybe_restore_autosave"):
            self.window = GraphDrawerWindow()
        self.window.autosave_timer.stop()
        self.window.autosave_path = self.directory / "autosave.json"
        self.window.settings = QSettings(str(self.directory / "settings.ini"), QSettings.IniFormat)
        self.controller = self.window.project_tree
        self.graph_nodes = []
        self.window.handle_table_paste(2, 0, "0\t1\n1\t2")
        for _ in range(4):
            self.window.new_graph()
            self.graph_nodes.append(self.controller.active_node())
        self.tree = self.window.figure_tree
        self.app.processEvents()

    def tearDown(self):
        self.window.workspace._set_modified(False)
        self.window.close()
        self.app.processEvents()
        self.temp.cleanup()

    def select(self, *indices):
        self.tree.clearSelection()
        for index in indices:
            self.controller._tree_items[self.graph_nodes[index].id].setSelected(True)

    def test_shift_selection_exports_only_highlighted_graphs(self):
        self.assertEqual(self.tree.selectionMode(), QAbstractItemView.ExtendedSelection)
        self.tree.setCurrentItem(self.controller._tree_items[self.graph_nodes[0].id])
        QTest.keyClick(self.tree, Qt.Key_Down, Qt.ShiftModifier)
        QTest.keyClick(self.tree, Qt.Key_Down, Qt.ShiftModifier)
        selected = self.window.preview._selected_graphs_with_folder()
        self.assertEqual([graph.id for graph, _ in selected],
                         [node.ref_id for node in self.graph_nodes[:3]])
        self.assertEqual(self.window.export_selected_btn.text(), "Export selected figures (3)")
        self.assertTrue(self.window.export_selected_action.isEnabled())
        # Flush the current graph's latest controls before capturing export jobs.
        self.window.figure_settings.title_edit.setText("Latest title")
        with patch("pubfig.ui.preview_controller.QFileDialog.getExistingDirectory",
                   return_value=str(self.directory)), patch.object(
            self.window.preview.rendering, "export_many",
            wraps=self.window.preview.rendering.export_many,
        ) as export:
            self.window.export_selected_btn.click()
        jobs = export.call_args.args[0]
        self.assertEqual(len(jobs), 3)
        self.assertEqual(jobs[2].request.config.title, "Latest title")
        self.assertEqual(len(list(self.directory.glob("*.png"))), 3)
        for graph, _ in selected:
            self.assertTrue((self.directory / f"{graph.name}.png").exists())
        self.assertFalse((self.directory / "Graph 4.png").exists())

    def test_disjoint_selection_survives_refresh_and_excludes_sheet_children(self):
        self.select(0, 2)
        sheet = self.controller._find_parent(self.graph_nodes[0].id)
        self.controller._tree_items[sheet.id].setSelected(True)
        self.controller.refresh_tree()
        self.assertEqual([graph.id for graph, _ in self.window.preview._selected_graphs_with_folder()],
                         [self.graph_nodes[i].ref_id for i in (0, 2)])
        self.assertEqual(self.window.export_selected_btn.text(), "Export selected figures (2)")
        self.tree.clearSelection()
        self.assertFalse(self.window.export_selected_btn.isEnabled())
        self.assertFalse(self.window.export_selected_action.isEnabled())
        self.controller._tree_items[sheet.id].setSelected(True)
        self.assertFalse(self.window.export_selected_btn.isEnabled())

    def test_export_preserves_folder_names_and_existing_files(self):
        sheet = self.controller._find_parent(self.graph_nodes[0].id)
        root = self.window.session.tree_root
        root.children.remove(sheet)
        root.children.append(TreeNode("folder", "folder", "Folder", children=[sheet]))
        for node in self.graph_nodes[:2]:
            self.window.session.graphs[node.ref_id].name = "Same"
        self.controller.refresh_tree()
        self.select(0, 1)
        existing = self.directory / "Folder_Same.png"
        existing.write_bytes(b"existing file")
        with patch("pubfig.ui.preview_controller.QFileDialog.getExistingDirectory",
                   return_value=str(self.directory)):
            self.window.export_selected_action.trigger()
        self.assertEqual(existing.read_bytes(), b"existing file")
        self.assertTrue((self.directory / "Folder_Same_2.png").exists())
        self.assertTrue((self.directory / "Folder_Same_3.png").exists())
        self.assertEqual(len(list(self.directory.glob("*.png"))), 3)

    def test_cancel_and_empty_selection_do_not_start_export(self):
        self.select(1, 3)
        with patch("pubfig.ui.preview_controller.QFileDialog.getExistingDirectory", return_value=""), \
                patch.object(self.window.preview.rendering, "export_many") as export:
            self.window.preview.export_selected_figures()
            export.assert_not_called()
        self.tree.clearSelection()
        with patch("pubfig.ui.preview_controller.QMessageBox.information") as message, \
                patch("pubfig.ui.preview_controller.QFileDialog.getExistingDirectory") as dialog:
            self.window.preview.export_selected_figures()
            message.assert_called_once()
            dialog.assert_not_called()

    def test_export_all_remains_independent_of_selection(self):
        self.select(1)
        with patch("pubfig.ui.preview_controller.QFileDialog.getExistingDirectory",
                   return_value=str(self.directory)):
            self.window.preview.export_all_figures()
        self.assertEqual(len(list(self.directory.glob("*.png"))), 4)


if __name__ == "__main__":
    unittest.main()
