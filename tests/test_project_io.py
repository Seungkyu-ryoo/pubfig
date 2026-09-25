from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from pubfig.model import Graph, ProjectDocument, Sheet, TreeNode
from pubfig.plot_config import (
    AnnotationConfig,
    LegendEntryConfig,
    PlotConfig,
    SeriesConfig,
    series_config_from_payload,
)
from pubfig.project_io import (
    ProjectConflictError,
    ProjectFormatError,
    UnsupportedProjectVersion,
    dataframe_from_payload,
    dataframe_to_payload,
    document_from_payload,
    document_to_payload,
    project_path_revision,
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
    def test_dataframe_codec_vectorizes_nulls_and_trims_only_empty_tail(self) -> None:
        frame = pd.DataFrame(
            [
                ["X", "Y", "Y", "Y"],
                ["Time", "A", "B", "C"],
                [0, 1.5, float("inf"), pd.NA],
                ["", "", "", ""],
                [2, float("nan"), float("-inf"), None],
                ["", None, pd.NA, float("nan")],
                [None, "", "", None],
            ],
            columns=["Value", "Value", 7, ""],
            dtype=object,
        )

        payload = dataframe_to_payload(frame)

        self.assertEqual(payload["columns"], ["Value", "Value_2", "7", "Col 4"])
        self.assertEqual(len(payload["rows"]), 5)
        self.assertEqual(payload["rows"][2], [0, 1.5, None, None])
        self.assertEqual(payload["rows"][3], ["", "", "", ""])
        self.assertEqual(payload["rows"][4], [2, None, None, None])
        self.assertEqual(frame.shape, (7, 4))

    def test_dataframe_load_trims_tail_but_keeps_blank_metadata_rows(self) -> None:
        frame = dataframe_from_payload(
            {
                "columns": ["x", "y"],
                "rows": [["", ""], ["", ""], [1, 2], ["", None]],
            }
        )

        self.assertEqual(frame.values.tolist(), [["", ""], ["", ""], [1, 2]])

    def test_numpy_backed_numeric_frames_are_stdlib_json_compatible(self) -> None:
        integer_frame = pd.DataFrame(
            np.array([[1, 2], [3, 4], [5, 6]], dtype=np.int64),
            columns=["a", "b"],
        )
        float_frame = pd.DataFrame(
            np.array(
                [[1.25, 2.5], [3.75, 4.0], [np.inf, np.nan]],
                dtype=np.float64,
            ),
            columns=["a", "b"],
        )

        integer_json = json.dumps(
            dataframe_to_payload(integer_frame),
            allow_nan=False,
        )
        float_json = json.dumps(
            dataframe_to_payload(float_frame),
            allow_nan=False,
        )

        self.assertEqual(json.loads(integer_json)["rows"][-1], [5, 6])
        self.assertEqual(json.loads(float_json)["rows"][-1], [None, None])

    def test_unsupported_numpy_dtypes_are_rejected_without_coercion(self) -> None:
        frames = {
            "datetime": pd.DataFrame(
                np.array(
                    ["2024-01-01", "2024-01-02", "2024-01-03"],
                    dtype="datetime64[ns]",
                )
            ),
            "timedelta": pd.DataFrame(
                np.array([1, 2, 3], dtype="timedelta64[ns]")
            ),
            "complex": pd.DataFrame(
                np.array([1 + 2j, 3 + 4j, 5 + 6j], dtype=np.complex128)
            ),
            "bytes": pd.DataFrame(
                np.array([b"a", b"b", b"c"], dtype="S1")
            ),
            "void": pd.DataFrame(
                np.array([b"aa", b"bb", b"cc"], dtype="V2")
            ),
        }

        for label, frame in frames.items():
            with self.subTest(label=label):
                with self.assertRaisesRegex(TypeError, "Unsupported DataFrame dtype"):
                    dataframe_to_payload(frame)

    def test_object_numpy_scalars_normalize_only_when_lossless(self) -> None:
        frame = pd.DataFrame(
            [
                [np.int64(1), np.float32(1.25), np.bool_(True), np.str_("a")],
                [np.uint64(2), np.float64(2.5), np.bool_(False), np.str_("b")],
                [np.int32(3), np.float32(np.inf), np.bool_(True), np.str_("c")],
            ],
            columns=["i", "f", "b", "s"],
            dtype=object,
        )

        payload = dataframe_to_payload(frame)
        encoded = json.dumps(payload, allow_nan=False)

        self.assertEqual(
            json.loads(encoded)["rows"],
            [[1, 1.25, True, "a"], [2, 2.5, False, "b"], [3, None, True, "c"]],
        )

        unsupported_objects = pd.DataFrame(
            [[np.longdouble("1.25")], [np.longdouble("2.5")]],
            dtype=object,
        )
        with self.assertRaisesRegex(TypeError, "Unsupported NumPy precision"):
            dataframe_to_payload(unsupported_objects)

    def test_writer_synchronizes_object_only_renames_into_tree_payload(self) -> None:
        sheet = Sheet("sh1", "Renamed sheet", pd.DataFrame())
        graph = Graph("gr1", "Renamed graph", sheet.id)
        graph_node = TreeNode("ng", "graph", "old graph", graph.id)
        sheet_node = TreeNode(
            "ns", "sheet", "old sheet", sheet.id, children=[graph_node]
        )
        document = ProjectDocument(
            sheets={sheet.id: sheet},
            graphs={graph.id: graph},
            tree_root=TreeNode("root", "folder", "Project", children=[sheet_node]),
        )

        payload = document_to_payload(document)

        self.assertEqual(payload["tree"]["children"][0]["name"], "Renamed sheet")
        self.assertEqual(
            payload["tree"]["children"][0]["children"][0]["name"],
            "Renamed graph",
        )
        self.assertEqual(sheet_node.name, "Renamed sheet")
        self.assertEqual(graph_node.name, "Renamed graph")

    def test_v3_round_trip_keeps_shape_annotations_and_empty_selection(self) -> None:
        sheet = Sheet(
            "sh1",
            "Measurements (µA)",
            pd.DataFrame(
                [["X", "Y"], ["Time", "Signal"], [0, 1]],
                columns=["X", "Y"],
            ),
        )
        annotation = AnnotationConfig(kind="text", text="Annotation (α)", x=0.5, y=1.0)
        series = SeriesConfig(
            x="X",
            y="Y",
            label="Signal",
            marker="o",
            marker_fill_style="left",
            error_column="E",
            error_cap_size=4.0,
        )
        graph = Graph(
            "gr1",
            "Graph (α)",
            sheet.id,
            PlotConfig(
                title="Results (Δ)",
                legend_entries=[
                    LegendEntryConfig(
                        label="Group A",
                        font_family="Arial",
                        font_size=10.5,
                        font_bold=True,
                        font_italic=True,
                        text_color="#123456",
                    ),
                    LegendEntryConfig(source_y="Y", label="%(1) — fitted"),
                ],
                legend_row_lengths=[2],
            ),
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
        restored_series = restored.graphs[graph.id].series_by_y["Y"]

        self.assertEqual(
            list(payload),
            ["schema_version", "active_node_id", "sheets", "graphs", "tree"],
        )
        self.assertEqual(
            list(payload["graphs"][0]),
            ["id", "name", "sheet_id", "plot_config", "series_config", "checked_y"],
        )
        self.assertNotIn("annotations", payload["graphs"][0])
        self.assertEqual(restored_series.error_column, "E")
        self.assertEqual(restored_series.error_cap_size, 4.0)
        self.assertEqual(restored_series.marker, "o")
        self.assertEqual(restored_series.marker_fill_style, "left")
        self.assertEqual(payload["graphs"][0]["plot_config"]["annotations"][0]["text"], "Annotation (α)")
        restored_graph = restored.graphs[graph.id]
        self.assertEqual(restored_graph.checked_y, [])
        self.assertIsInstance(restored_graph.annotations[0], AnnotationConfig)
        self.assertEqual(restored_graph.annotations[0].text, "Annotation (α)")
        self.assertEqual(
            restored_graph.plot_config.legend_entries,
            [
                LegendEntryConfig(
                    label="Group A",
                    font_family="Arial",
                    font_size=10.5,
                    font_bold=True,
                    font_italic=True,
                    text_color="#123456",
                ),
                LegendEntryConfig(source_y="Y", label="%(1) — fitted"),
            ],
        )
        self.assertEqual(restored_graph.plot_config.legend_row_lengths, [2])
        self.assertEqual(restored.active_node_id, graph_node.id)

    def test_legacy_series_without_marker_fill_defaults_to_filled(self) -> None:
        restored = series_config_from_payload({"x": "X", "y": "Y", "marker": "v"})

        self.assertEqual(restored.marker, "v")
        self.assertEqual(restored.marker_fill_style, "full")

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

    @unittest.skipIf(os.name == "nt", "POSIX permission bits")
    def test_atomic_write_is_private_by_default_and_preserves_target_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project.json"
            write_json_atomic(target, {"value": 1})
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

            target.chmod(0o640)
            write_json_atomic(target, {"value": 2})
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)

    def test_expected_revision_rejects_external_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project.json"
            write_json_atomic(target, {"value": "opened"})
            expected = project_path_revision(target)
            write_json_atomic(target, {"value": "external generation"})
            external_bytes = target.read_bytes()

            with self.assertRaisesRegex(ProjectConflictError, "changed externally"):
                write_json_atomic(
                    target,
                    {"value": "must not replace external"},
                    expected_revision=expected,
                )

            self.assertEqual(target.read_bytes(), external_bytes)

    def test_expected_revision_is_checked_again_before_atomic_replace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project.json"
            write_json_atomic(target, {"value": "opened"})
            expected = project_path_revision(target)
            real_fsync = os.fsync
            changed = False

            def replace_target_during_save(descriptor: int) -> None:
                nonlocal changed
                real_fsync(descriptor)
                if not changed:
                    changed = True
                    target.write_text(
                        '{"value":"external generation written during save"}',
                        encoding="utf-8",
                    )

            with (
                patch(
                    "pubfig.project_io.os.fsync",
                    side_effect=replace_target_during_save,
                ),
                self.assertRaisesRegex(ProjectConflictError, "during save"),
            ):
                write_json_atomic(
                    target,
                    {"value": "must not win"},
                    expected_revision=expected,
                )

            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8"))["value"],
                "external generation written during save",
            )
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
            saved_path = write_project(Path(directory) / "Results (Δ)", legacy)
            loaded = read_project(saved_path)

            self.assertEqual(saved_path.suffix, ".json")
            raw = json.loads(saved_path.read_text(encoding="utf-8"))
            self.assertEqual(raw["schema_version"], 3)
            self.assertEqual(len(loaded.sheets), 1)
            self.assertEqual(loaded.source_path, saved_path.resolve())
            self.assertEqual(
                loaded.source_revision,
                project_path_revision(saved_path),
            )
            self.assertNotIn("\n", saved_path.read_text(encoding="utf-8"))
            self.assertNotIn(": ", saved_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
