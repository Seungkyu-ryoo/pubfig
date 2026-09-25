from __future__ import annotations

import re
import unittest

import pandas as pd

from pubfig.model import (
    Graph,
    ProjectDocument,
    Sheet,
    TreeNode,
    find_node,
    find_parent,
    first_leaf,
    iter_tree,
    new_id,
    repair_document,
)


class ProjectModelTests(unittest.TestCase):
    def test_new_id_keeps_legacy_shape_and_is_unique(self) -> None:
        identifiers = {new_id("sh") for _ in range(100)}

        self.assertEqual(len(identifiers), 100)
        self.assertTrue(all(re.fullmatch(r"sh[0-9a-f]{8}", item) for item in identifiers))

    def test_tree_helpers_use_stable_preorder(self) -> None:
        graph = TreeNode("graph", "graph", "Graph", "gr1")
        sheet = TreeNode("sheet", "sheet", "Sheet", "sh1", [graph])
        nested = TreeNode("nested", "folder", "Nested", children=[sheet])
        sibling = TreeNode("sibling", "sheet", "Other", "sh2")
        root = TreeNode("root", "folder", "Project", children=[nested, sibling])

        self.assertEqual(
            [node.id for node in iter_tree(root)],
            ["root", "nested", "sheet", "graph", "sibling"],
        )
        self.assertIs(find_node(root, "graph"), graph)
        self.assertIs(find_parent(root, "graph"), sheet)
        self.assertIs(first_leaf(root), sheet)

    def test_repair_recurses_below_sheets_and_rehomes_valid_graphs(self) -> None:
        sheet = Sheet("sh1", "Measurements", pd.DataFrame(columns=["X", "Y"]))
        valid = Graph("gr1", "Valid", "sh1")
        missing_sheet = Graph("gr2", "Missing sheet", "does-not-exist")
        invalid_leaf = TreeNode("invalid", "graph", "Broken", "missing-graph")
        sheet_node = TreeNode(
            "duplicate-id",
            "sheet",
            "stale sheet name",
            "sh1",
            children=[invalid_leaf],
        )
        misplaced_graph = TreeNode(
            "duplicate-id", "graph", "stale graph name", "gr1"
        )
        root = TreeNode(
            "root",
            "folder",
            "Project",
            children=[sheet_node, misplaced_graph],
        )
        document = ProjectDocument(
            sheets={sheet.id: sheet},
            graphs={valid.id: valid, missing_sheet.id: missing_sheet},
            tree_root=root,
            active_node_id="not-present",
        )

        report = repair_document(document)

        self.assertEqual(report.dropped_graph_ids, ("gr2",))
        self.assertNotIn("gr2", document.graphs)
        self.assertEqual(sheet_node.name, "Measurements")
        self.assertEqual(sheet_node.children, [misplaced_graph])
        self.assertEqual(misplaced_graph.name, "Valid")
        self.assertEqual(misplaced_graph.children, [])
        self.assertEqual(document.active_node_id, sheet_node.id)
        node_ids = [node.id for node in document.iter_nodes()]
        self.assertEqual(len(node_ids), len(set(node_ids)))
        self.assertIs(document.find_parent(misplaced_graph.id), sheet_node)

    def test_repair_creates_nodes_for_unrepresented_objects(self) -> None:
        sheet = Sheet("sh1", "Sheet", pd.DataFrame())
        graph = Graph("gr1", "Graph", "sh1")
        document = ProjectDocument(
            sheets={sheet.id: sheet},
            graphs={graph.id: graph},
            tree_root=TreeNode("root", "folder", "Project"),
        )

        report = document.repair()

        self.assertEqual(report.added_tree_nodes, 2)
        sheet_node = next(
            node for node in document.iter_nodes() if node.ref_id == sheet.id
        )
        graph_node = next(
            node for node in document.iter_nodes() if node.ref_id == graph.id
        )
        self.assertIs(document.find_parent(graph_node.id), sheet_node)

    def test_rename_node_updates_canonical_objects_and_tree_labels_together(self) -> None:
        sheet = Sheet("sh1", "Sheet", pd.DataFrame())
        graph = Graph("gr1", "Graph", sheet.id)
        graph_node = TreeNode("ng", "graph", "stale graph", graph.id)
        sheet_node = TreeNode(
            "ns", "sheet", "stale sheet", sheet.id, children=[graph_node]
        )
        folder = TreeNode("nf", "folder", "Folder", children=[sheet_node])
        document = ProjectDocument(
            sheets={sheet.id: sheet},
            graphs={graph.id: graph},
            tree_root=TreeNode("root", "folder", "Project", children=[folder]),
        )

        self.assertTrue(document.rename_node(sheet_node.id, "Measurements"))
        self.assertTrue(document.rename_node(graph_node.id, "Polarization"))
        self.assertTrue(document.rename_node(folder.id, "4V pair"))
        self.assertTrue(document.rename_node(document.tree_root.id, "HfO2"))
        self.assertFalse(document.rename_node(sheet_node.id, "Measurements"))

        self.assertEqual(sheet.name, "Measurements")
        self.assertEqual(sheet_node.name, "Measurements")
        self.assertEqual(graph.name, "Polarization")
        self.assertEqual(graph_node.name, "Polarization")
        self.assertEqual(folder.name, "4V pair")
        self.assertEqual(document.tree_root.name, "HfO2")


if __name__ == "__main__":
    unittest.main()
