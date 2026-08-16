from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from pubfig.model import Graph, ProjectDocument, Sheet, TreeNode
from pubfig.plot_config import AnnotationConfig, PlotConfig, SeriesConfig
from pubfig.project_io import (
    ProjectFormatError,
    UnsupportedProjectVersion,
    document_from_payload,
    document_to_payload,
    read_project,
    write_json_atomic,
    write_project,
)


def _data_payload() -> dict:
    return {
        "columns": ["X", "Y"],
        "rows": [["X", "Y"], ["Time", "Signal"], [0, 1], [1, 2]],
    }


class ProjectIoTests(unittest.TestCase):
    def test_v3_round_trip_keeps_shape_annotations_and_empty_selection(self) -> None:
        sheet = Sheet(
            "sh1",
            "측정값",
            pd.DataFrame(
                [["X", "Y"], ["Time", "Signal"], [0, 1]],
                columns=["X", "Y"],
            ),
        )
        annotation = AnnotationConfig(kind="text", text="주석", x=0.5, y=1.0)
        series = SeriesConfig(x="X", y="Y", label="Signal")
        graph = Graph(
            "gr1",
            "그래프",
            sheet.id,
            PlotConfig(title="결과"),
            {series.y: series},
            [],
            [annotation],
        )
        graph_node = TreeNode("ng", "graph", graph.name, graph.id)
        sheet_node = TreeNode("ns", "sheet", sheet.name, sheet.id, [graph_node])
        document = ProjectDocument(
            {sheet.id: sheet},
            {graph.id: graph},
            TreeNode("root", "folder", "Project", children=[sheet_node]),
            graph_node.id,
        )

        payload = document_to_payload(document)
        restored = document_from_payload(payload)

        self.assertEqual(
            list(payload),
            ["schema_version", "active_node_id", "sheets", "graphs", "tree"],
        )
        self.assertEqual(
            list(payload["graphs"][0]),
            ["id", "name", "sheet_id", "plot_config", "series_config", "checked_y"],
        )
        self.assertNotIn("annotations", payload["graphs"][0])
        self.assertEqual(payload["graphs"][0]["plot_config"]["annotations"][0]["text"], "주석")
        restored_graph = restored.graphs[graph.id]
        self.assertEqual(restored_graph.checked_y, [])
        self.assertIsInstance(restored_graph.annotations[0], AnnotationConfig)
        self.assertEqual(restored_graph.annotations[0].text, "주석")
        self.assertEqual(restored.active_node_id, graph_node.id)

    def test_v1_single_figure_migrates(self) -> None:
        payload = {
            "schema_version": 1,
            "data": _data_payload(),
            "plot_config": {
                "title": "Legacy",
                "annotations": [{"kind": "text", "text": "old"}],
            },
            "series_config": [{"x": "X", "y": "Y", "label": "Signal"}],
        }

        document = document_from_payload(payload, "old-file")

        self.assertEqual([sheet.name for sheet in document.sheets.values()], ["old-file"])
        graph = next(iter(document.graphs.values()))
        self.assertEqual(graph.name, "old-file")
        self.assertEqual(graph.checked_y, ["Y"])
        self.assertEqual(graph.annotations[0].text, "old")
        self.assertEqual(document.find_node(document.active_node_id).type, "graph")

    def test_v2_figures_migrate_and_preserve_active_figure(self) -> None:
        figure = {
            "data": _data_payload(),
            "plot_config": {},
            "series_config": [{"x": "X", "y": "Y"}],
            "checked_y": ["Y"],
        }
        payload = {
            "schema_version": 2,
            "active_figure_index": 1,
            "figures": [dict(figure, name="First"), dict(figure, name="Second")],
        }

        document = document_from_payload(payload)

        self.assertEqual([sheet.name for sheet in document.sheets.values()], ["First", "Second"])
        self.assertEqual(len(document.graphs), 2)
        active = document.find_node(document.active_node_id)
        self.assertEqual(active.name, "Second")

    def test_v3_load_normalizes_duplicate_columns_and_repairs_nested_bad_leaf(self) -> None:
        payload = {
            "schema_version": 3,
            "active_node_id": "bad",
            "sheets": [
                {
                    "id": "sh1",
                    "name": "Sheet",
                    "data": {
                        "columns": ["Value", "Value"],
                        "rows": [["X", "Y"], ["", ""]],
                    },
                }
            ],
            "graphs": [],
            "tree": {
                "id": "root",
                "type": "folder",
                "name": "Project",
                "ref_id": None,
                "expanded": True,
                "children": [
                    {
                        "id": "sheet-node",
                        "type": "sheet",
                        "name": "Sheet",
                        "ref_id": "sh1",
                        "expanded": True,
                        "children": [
                            {
                                "id": "bad",
                                "type": "graph",
                                "name": "Broken",
                                "ref_id": "missing",
                                "expanded": True,
                                "children": [],
                            }
                        ],
                    }
                ],
            },
        }

        document = document_from_payload(payload)

        self.assertEqual(list(document.sheets["sh1"].df.columns), ["Value", "Value_2"])
        self.assertIsNone(document.find_node("bad"))
        self.assertEqual(document.active_node_id, "sheet-node")

    def test_schema_version_is_strict(self) -> None:
        invalid_payloads = [
            {},
            {"schema_version": "3", "sheets": []},
            {"schema_version": True, "data": {}},
            {"schema_version": 999, "sheets": []},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(UnsupportedProjectVersion):
                    document_from_payload(payload)

        with self.assertRaises(ProjectFormatError):
            document_from_payload({"schema_version": 2, "sheets": []})
        with self.assertRaises(ProjectFormatError):
            document_from_payload({"schema_version": 3, "figures": []})

    def test_atomic_write_preserves_existing_target_when_encoding_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project.json"
            target.write_text("original", encoding="utf-8")

            with self.assertRaises(TypeError):
                write_json_atomic(target, {"bad": object()})

            self.assertEqual(target.read_text(encoding="utf-8"), "original")
            self.assertEqual(list(target.parent.glob(f".{target.name}.*.tmp")), [])

    def test_write_project_adds_suffix_and_read_project_uses_file_stem(self) -> None:
        payload = {
            "schema_version": 1,
            "data": _data_payload(),
            "plot_config": {},
            "series_config": [],
        }
        legacy = document_from_payload(payload, "Legacy")
        with tempfile.TemporaryDirectory() as directory:
            saved_path = write_project(Path(directory) / "결과", legacy)
            loaded = read_project(saved_path)

            self.assertEqual(saved_path.suffix, ".json")
            raw = json.loads(saved_path.read_text(encoding="utf-8"))
            self.assertEqual(raw["schema_version"], 3)
            self.assertEqual(len(loaded.sheets), 1)


if __name__ == "__main__":
    unittest.main()
