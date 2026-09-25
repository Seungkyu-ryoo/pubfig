from __future__ import annotations

from concurrent.futures import CancelledError
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, get_ident
import unittest
from unittest.mock import patch

import pandas as pd

from pubfig.model import ProjectDocument, Sheet
from pubfig.save_worker import BackgroundProjectWriter


class BackgroundProjectWriterTests(unittest.TestCase):
    def test_payload_write_runs_off_caller_thread(self) -> None:
        started = Event()
        release = Event()
        worker_thread_ids: list[int] = []

        def blocked_write(path, payload, *, indent=None):
            worker_thread_ids.append(get_ident())
            started.set()
            self.assertTrue(release.wait(2))
            Path(path).write_text(json.dumps(payload), encoding="utf-8")

        with TemporaryDirectory() as directory, patch(
            "pubfig.save_worker.write_json_atomic",
            side_effect=blocked_write,
        ):
            writer = BackgroundProjectWriter()
            try:
                future = writer.submit_payload(
                    Path(directory) / "project.json",
                    {"schema_version": 3},
                )
                self.assertTrue(started.wait(2))
                self.assertFalse(future.done())
                self.assertNotEqual(worker_thread_ids, [get_ident()])
                release.set()
                result = future.result(timeout=2)
            finally:
                release.set()
                writer.shutdown()

        self.assertEqual(result.path.name, "project.json")
        self.assertFalse(result.autosave)

    def test_document_snapshot_is_independent_and_supports_autosave_metadata(self) -> None:
        frame = pd.DataFrame(
            [
                ["X", "Y"],
                ["Time", "Signal"],
                [0, 1],
                ["", ""],
            ],
            columns=["x", "y"],
        )
        document = ProjectDocument(
            sheets={"sh1": Sheet("sh1", "Original", frame)},
        )

        with TemporaryDirectory() as directory:
            target = Path(directory) / "autosave.json"
            with BackgroundProjectWriter() as writer:
                future = writer.submit_document(
                    target,
                    document,
                    extra_payload={"autosave_origin": "/tmp/source.json"},
                    autosave=True,
                )
                document.sheets["sh1"].name = "Changed after submit"
                document.sheets["sh1"].df.iat[2, 1] = 999
                result = future.result(timeout=2)

            payload = json.loads(target.read_text(encoding="utf-8"))
            saved_text = target.read_text(encoding="utf-8")

        self.assertTrue(result.autosave)
        self.assertEqual(payload["autosave_origin"], "/tmp/source.json")
        self.assertEqual(payload["sheets"][0]["name"], "Original")
        self.assertEqual(payload["sheets"][0]["data"]["rows"][-1], [0, 1])
        self.assertNotIn("\n", saved_text)

    def test_queued_autosaves_to_same_path_are_coalesced(self) -> None:
        blocker_started = Event()
        release_blocker = Event()
        written_values: list[int] = []

        def controlled_write(_path, payload, *, indent=None):
            if payload.get("block"):
                blocker_started.set()
                self.assertTrue(release_blocker.wait(2))
            elif "value" in payload:
                written_values.append(payload["value"])

        with patch(
            "pubfig.save_worker.write_json_atomic",
            side_effect=controlled_write,
        ):
            writer = BackgroundProjectWriter()
            try:
                blocker = writer.submit_payload("manual.json", {"block": True})
                self.assertTrue(blocker_started.wait(2))
                old = writer.submit_payload(
                    "autosave.json",
                    {"value": 1},
                    autosave=True,
                )
                latest = writer.submit_payload(
                    "autosave.json",
                    {"value": 2},
                    autosave=True,
                )
                self.assertTrue(old.cancelled())
                with self.assertRaises(CancelledError):
                    old.result()
                release_blocker.set()
                blocker.result(timeout=2)
                latest.result(timeout=2)
            finally:
                release_blocker.set()
                writer.shutdown()

        self.assertEqual(written_values, [2])

    def test_discard_autosave_is_ordered_after_running_write(self) -> None:
        write_started = Event()
        release_write = Event()

        def controlled_write(path, payload, *, indent=None):
            if payload["value"] == 1:
                write_started.set()
                self.assertTrue(release_write.wait(2))
            Path(path).write_text(str(payload["value"]), encoding="utf-8")

        with TemporaryDirectory() as directory, patch(
            "pubfig.save_worker.write_json_atomic",
            side_effect=controlled_write,
        ):
            target = Path(directory) / "autosave.json"
            writer = BackgroundProjectWriter()
            try:
                running = writer.submit_payload(
                    target,
                    {"value": 1},
                    autosave=True,
                )
                self.assertTrue(write_started.wait(2))
                queued = writer.submit_payload(
                    target,
                    {"value": 2},
                    autosave=True,
                )

                removal = writer.discard_autosave(target)

                self.assertTrue(queued.cancelled())
                release_write.set()
                running.result(timeout=2)
                removal_result = removal.result(timeout=2)
            finally:
                release_write.set()
                writer.shutdown()

            self.assertTrue(removal_result.removed)
            self.assertFalse(target.exists())

    def test_submit_removal_of_missing_file_is_successful_noop(self) -> None:
        with TemporaryDirectory() as directory:
            target = Path(directory) / "missing.json"
            with BackgroundProjectWriter() as writer:
                result = writer.submit_removal(target).result(timeout=2)

        self.assertEqual(result.path, target)
        self.assertFalse(result.removed)

    def test_shutdown_rejects_new_work(self) -> None:
        writer = BackgroundProjectWriter()
        writer.shutdown()

        with self.assertRaisesRegex(RuntimeError, "shut down"):
            writer.submit_payload("late.json", {})

    def test_encoding_failure_reaches_future_and_preserves_target(self) -> None:
        with TemporaryDirectory() as directory:
            target = Path(directory) / "project.json"
            target.write_text("original", encoding="utf-8")
            with BackgroundProjectWriter() as writer:
                future = writer.submit_payload(target, {"bad": object()})
                with self.assertRaises(TypeError):
                    future.result(timeout=2)

            self.assertEqual(target.read_text(encoding="utf-8"), "original")


if __name__ == "__main__":
    unittest.main()
