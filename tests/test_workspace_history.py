from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

try:
    import pyarrow as pa
except ImportError:  # pragma: no cover - optional pandas storage backend
    pa = None

from pubfig.model import Graph, ProjectDocument, Sheet
from pubfig.workspace_history import (
    clone_project_document,
    project_snapshot_metadata_weight,
    project_snapshot_resources,
)


class WorkspaceHistoryTests(unittest.TestCase):
    def make_document(self) -> ProjectDocument:
        dataframe = pd.DataFrame(
            {
                "X": ["1", "2", "3"],
                "Y": ["4", "5", "6"],
            }
        )
        return ProjectDocument(
            sheets={"sheet": Sheet("sheet", "Data", dataframe)},
            graphs={"graph": Graph("graph", "Figure", "sheet")},
        )

    def test_clone_detaches_metadata_and_uses_copy_on_write_frames(self) -> None:
        document = self.make_document()
        snapshot = clone_project_document(document)

        self.assertIsNot(snapshot, document)
        self.assertIsNot(snapshot.graphs["graph"], document.graphs["graph"])
        self.assertIsNot(snapshot.sheets["sheet"].df, document.sheets["sheet"].df)
        self.assertEqual(
            project_snapshot_resources(snapshot),
            project_snapshot_resources(document),
        )

        document.graphs["graph"].name = "Changed"
        document.sheets["sheet"].df.iat[0, 0] = "99"
        self.assertEqual(snapshot.graphs["graph"].name, "Figure")
        self.assertEqual(snapshot.sheets["sheet"].df.iat[0, 0], "1")

        snapshot.sheets["sheet"].df.iat[1, 1] = "88"
        self.assertEqual(document.sheets["sheet"].df.iat[1, 1], "5")

    def test_clone_preserves_intentional_dataframe_aliases(self) -> None:
        document = self.make_document()
        shared = document.sheets["sheet"].df
        document.sheets["sheet-2"] = Sheet("sheet-2", "Same data", shared)

        snapshot = clone_project_document(document)

        self.assertIs(
            snapshot.sheets["sheet"].df,
            snapshot.sheets["sheet-2"].df,
        )
        self.assertIsNot(snapshot.sheets["sheet"].df, shared)

    def test_metadata_weight_never_deep_scans_dataframe_cells(self) -> None:
        document = self.make_document()
        with patch.object(
            pd.DataFrame,
            "memory_usage",
            side_effect=AssertionError("cell scan attempted"),
        ):
            weight = project_snapshot_metadata_weight(document)

        self.assertGreater(weight, 0)

    def test_resource_map_tracks_only_new_copy_on_write_storage(self) -> None:
        document = self.make_document()
        snapshot = clone_project_document(document)
        old_resources = project_snapshot_resources(snapshot)

        document.sheets["sheet"].df.iat[0, 0] = "changed"
        live_resources = project_snapshot_resources(document)

        self.assertTrue(set(old_resources) & set(live_resources))
        self.assertTrue(set(live_resources) - set(old_resources))
        self.assertEqual(snapshot.sheets["sheet"].df.iat[0, 0], "1")

    @unittest.skipIf(pa is None, "pyarrow is not installed")
    def test_arrow_string_snapshots_track_shared_physical_buffers(self) -> None:
        x_values = pd.arrays.ArrowStringArray(
            pa.chunked_array(
                [
                    pa.array(["first", "second"]),
                    pa.array(["third"]),
                ]
            )
        )
        dataframe = pd.DataFrame(
            {
                "X": x_values,
                "Y": pd.array(["4", "5", "6"], dtype="string[pyarrow]"),
            }
        )
        document = ProjectDocument(
            sheets={"sheet": Sheet("sheet", "Arrow data", dataframe)}
        )
        snapshot = clone_project_document(document)

        snapshot_resources = project_snapshot_resources(snapshot)
        live_resources = project_snapshot_resources(document)
        self.assertEqual(snapshot_resources, live_resources)
        self.assertTrue(snapshot_resources)
        self.assertTrue(
            all(token[0] == "arrow" for token in snapshot_resources)
        )

        document.sheets["sheet"].df.iat[0, 0] = "changed"
        changed_resources = project_snapshot_resources(document)
        self.assertTrue(set(snapshot_resources) & set(changed_resources))
        self.assertTrue(set(snapshot_resources) - set(changed_resources))
        self.assertTrue(set(changed_resources) - set(snapshot_resources))
        self.assertEqual(snapshot.sheets["sheet"].df.iat[0, 0], "first")

    def test_nullable_masked_arrays_track_shared_data_and_mask_buffers(self) -> None:
        cases = (
            ("Int64", [1, None, 3], 9),
            ("Float64", [1.5, None, 3.5], 9.5),
            ("boolean", [True, None, False], False),
        )
        for dtype, values, replacement in cases:
            with self.subTest(dtype=dtype):
                dataframe = pd.DataFrame(
                    {"value": pd.array(values, dtype=dtype)}
                )
                document = ProjectDocument(
                    sheets={"sheet": Sheet("sheet", dtype, dataframe)}
                )
                snapshot = clone_project_document(document)
                snapshot_resources = project_snapshot_resources(snapshot)

                self.assertEqual(
                    snapshot_resources,
                    project_snapshot_resources(document),
                )
                self.assertEqual(len(snapshot_resources), 2)
                self.assertTrue(
                    all(token[0] == "numpy" for token in snapshot_resources)
                )

                document.sheets["sheet"].df.iat[0, 0] = replacement
                self.assertNotEqual(
                    snapshot_resources,
                    project_snapshot_resources(document),
                )
                self.assertEqual(
                    snapshot.sheets["sheet"].df.iat[0, 0],
                    values[0],
                )

    def test_categorical_and_sparse_snapshots_track_physical_storage(self) -> None:
        cases = (
            ("categorical", pd.Categorical(["a", "b", "a"])),
            ("sparse", pd.arrays.SparseArray([0, 1, 0, 2])),
        )
        for name, values in cases:
            with self.subTest(kind=name):
                document = ProjectDocument(
                    sheets={
                        "sheet": Sheet(
                            "sheet",
                            name,
                            pd.DataFrame({"value": values}),
                        )
                    }
                )
                snapshot = clone_project_document(document)
                snapshot_resources = project_snapshot_resources(snapshot)

                self.assertTrue(snapshot_resources)
                self.assertEqual(
                    snapshot_resources,
                    project_snapshot_resources(document),
                )
                self.assertNotIn(
                    "extension",
                    {token[0] for token in snapshot_resources},
                )

        categorical = cases[0][1]
        document = ProjectDocument(
            sheets={
                "sheet": Sheet(
                    "sheet",
                    "categorical",
                    pd.DataFrame({"value": categorical}),
                )
            }
        )
        snapshot = clone_project_document(document)
        category_resources = project_snapshot_resources(snapshot)
        document.sheets["sheet"].df.iat[0, 0] = "b"
        changed_resources = project_snapshot_resources(document)
        self.assertNotEqual(category_resources, changed_resources)
        self.assertTrue(set(category_resources) & set(changed_resources))
        self.assertEqual(snapshot.sheets["sheet"].df.iat[0, 0], "a")


if __name__ == "__main__":
    unittest.main()
