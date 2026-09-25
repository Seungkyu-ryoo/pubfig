from __future__ import annotations

from copy import deepcopy
import unittest

import pandas as pd

from pubfig.undo import UndoManager


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class MutableWorkspace:
    def __init__(self, value: int = 0) -> None:
        self.state = {"value": value}
        self.capture_count = 0

    def capture(self) -> dict:
        self.capture_count += 1
        return deepcopy(self.state)

    def restore(self, snapshot: dict) -> None:
        # Deliberately install the object supplied by UndoManager.  Its
        # clone-for-restore boundary must keep internal history isolated.
        self.state = snapshot


class UndoManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = MutableWorkspace()
        self.clock = FakeClock()
        self.history = UndoManager(
            self.workspace.capture,
            self.workspace.restore,
            clock=self.clock,
        )
        self.history.reset()

    def set_value(self, value: int) -> None:
        self.workspace.state["value"] = value

    def test_checkpoint_current_supports_undo_redo_and_new_branch(self) -> None:
        self.history.checkpoint_current()
        self.set_value(1)
        self.history.refresh_baseline()

        self.assertTrue(self.history.undo())
        self.assertEqual(self.workspace.state["value"], 0)
        self.assertEqual((self.history.undo_count, self.history.redo_count), (0, 1))

        self.assertTrue(self.history.redo())
        self.assertEqual(self.workspace.state["value"], 1)
        self.assertEqual((self.history.undo_count, self.history.redo_count), (1, 0))

        self.assertTrue(self.history.undo())
        self.history.checkpoint_current()
        self.set_value(2)
        self.history.refresh_baseline()
        self.assertFalse(self.history.can_redo)

    def test_same_key_coalesces_until_timeout_or_explicit_boundary(self) -> None:
        self.history.perform_change(lambda: self.set_value(1), coalesce_key="title")
        self.clock.advance(0.2)
        self.history.perform_change(lambda: self.set_value(2), coalesce_key="title")
        self.assertEqual(self.history.undo_count, 1)

        self.clock.advance(0.7)
        self.history.perform_change(lambda: self.set_value(3), coalesce_key="title")
        self.assertEqual(self.history.undo_count, 2)

        self.history.end_coalescing()
        self.history.perform_change(lambda: self.set_value(4), coalesce_key="title")
        self.assertEqual(self.history.undo_count, 3)

        self.assertTrue(self.history.undo())
        self.assertEqual(self.workspace.state["value"], 3)
        self.assertTrue(self.history.undo())
        self.assertEqual(self.workspace.state["value"], 2)
        self.assertTrue(self.history.undo())
        self.assertEqual(self.workspace.state["value"], 0)

    def test_different_or_none_key_starts_a_new_entry(self) -> None:
        self.history.perform_change(lambda: self.set_value(1), coalesce_key="title")
        self.history.perform_change(lambda: self.set_value(2), coalesce_key="width")
        self.history.perform_change(lambda: self.set_value(3))
        self.history.perform_change(lambda: self.set_value(4))

        self.assertEqual(self.history.undo_count, 4)
        expected = [3, 2, 1, 0]
        for value in expected:
            self.assertTrue(self.history.undo())
            self.assertEqual(self.workspace.state["value"], value)

    def test_history_limit_matches_legacy_fifty_entry_limit(self) -> None:
        history = UndoManager(
            self.workspace.capture,
            self.workspace.restore,
            limit=50,
            clock=self.clock,
        )
        history.reset()
        for value in range(1, 56):
            history.checkpoint_current()
            self.set_value(value)
            history.refresh_baseline()

        self.assertEqual(history.undo_count, 50)
        for _ in range(50):
            self.assertTrue(history.undo())
        self.assertEqual(self.workspace.state["value"], 5)
        self.assertFalse(history.undo())
        self.assertEqual(history.redo_count, 50)

    def test_weight_limit_keeps_the_newest_contiguous_undo_entries(self) -> None:
        self.workspace.state["weight"] = 4
        history = UndoManager(
            self.workspace.capture,
            self.workspace.restore,
            snapshot_weight=lambda snapshot: snapshot["weight"],
            max_stack_weight=8,
            clock=self.clock,
        )
        history.reset()

        for value in (1, 2, 3):
            self.assertTrue(history.checkpoint_current())
            self.set_value(value)
            history.refresh_baseline()

        self.assertEqual(history.baseline_weight, 4)
        self.assertEqual((history.undo_count, history.undo_weight), (2, 8))
        self.assertTrue(history.undo())
        self.assertEqual(self.workspace.state["value"], 2)
        self.assertTrue(history.undo())
        self.assertEqual(self.workspace.state["value"], 1)
        self.assertFalse(history.undo())
        self.assertEqual((history.redo_count, history.redo_weight), (2, 8))

    def test_live_shared_resource_is_not_charged_per_style_snapshot(self) -> None:
        self.workspace.state["resource"] = "large-live-frame"
        history = UndoManager(
            self.workspace.capture,
            self.workspace.restore,
            snapshot_resources=lambda snapshot: {snapshot["resource"]: 1_000},
            max_stack_weight=10,
            clock=self.clock,
        )
        history.reset()

        self.assertTrue(history.checkpoint_current())
        self.set_value(1)
        history.refresh_baseline()

        self.assertEqual((history.undo_count, history.undo_weight), (1, 0))
        self.assertTrue(history.undo())
        self.assertEqual(self.workspace.state["value"], 0)

    def test_history_only_resources_are_deduplicated_and_budgeted(self) -> None:
        self.workspace.state["resource"] = "frame-a"
        history = UndoManager(
            self.workspace.capture,
            self.workspace.restore,
            snapshot_resources=lambda snapshot: {snapshot["resource"]: 10},
            max_stack_weight=15,
            clock=self.clock,
        )
        history.reset()

        history.checkpoint_current()
        self.workspace.state.update(value=1, resource="frame-b")
        history.refresh_baseline()
        self.assertEqual((history.undo_count, history.undo_weight), (1, 10))

        history.checkpoint_current()
        self.workspace.state.update(value=2, resource="frame-c")
        history.refresh_baseline()

        # Retaining both old buffers would exceed the limit, so the oldest
        # state is removed while the newest contiguous undo remains valid.
        self.assertEqual((history.undo_count, history.undo_weight), (1, 10))
        self.assertTrue(history.undo())
        self.assertEqual(self.workspace.state["value"], 1)

    def test_oversized_checkpoint_clears_history_instead_of_skipping_state(self) -> None:
        self.workspace.state["weight"] = 4
        history = UndoManager(
            self.workspace.capture,
            self.workspace.restore,
            snapshot_weight=lambda snapshot: snapshot["weight"],
            max_stack_weight=8,
            clock=self.clock,
        )
        history.reset()
        self.assertTrue(history.checkpoint_current())
        self.set_value(1)
        history.refresh_baseline()

        self.workspace.state["weight"] = 9
        self.assertFalse(history.checkpoint_current())
        self.assertEqual((history.undo_count, history.undo_weight), (0, 0))
        self.assertEqual((history.redo_count, history.redo_weight), (0, 0))
        self.assertFalse(history.undo())

    def test_undo_succeeds_but_drops_oversized_outgoing_redo_state(self) -> None:
        self.workspace.state["weight"] = 4
        history = UndoManager(
            self.workspace.capture,
            self.workspace.restore,
            snapshot_weight=lambda snapshot: snapshot["weight"],
            max_stack_weight=8,
            clock=self.clock,
        )
        history.reset()
        self.assertTrue(history.checkpoint_current())
        self.set_value(1)
        self.workspace.state["weight"] = 9
        history.refresh_baseline()

        self.assertTrue(history.undo())
        self.assertEqual(self.workspace.state["value"], 0)
        self.assertFalse(history.can_redo)
        self.assertEqual(history.redo_weight, 0)

    def test_redo_succeeds_but_drops_oversized_outgoing_undo_state(self) -> None:
        self.workspace.state["weight"] = 4
        history = UndoManager(
            self.workspace.capture,
            self.workspace.restore,
            snapshot_weight=lambda snapshot: snapshot["weight"],
            max_stack_weight=8,
            clock=self.clock,
        )
        history.reset()
        history.checkpoint_current()
        self.set_value(1)
        history.refresh_baseline()
        self.assertTrue(history.undo())

        self.workspace.state["weight"] = 9
        self.assertTrue(history.redo())
        self.assertEqual(self.workspace.state["value"], 1)
        self.assertFalse(history.can_undo)
        self.assertEqual(history.undo_weight, 0)

    def test_restore_failure_keeps_weighted_stacks_unchanged(self) -> None:
        self.workspace.state["weight"] = 4

        def restore(_snapshot: dict) -> None:
            raise RuntimeError("restore failed")

        history = UndoManager(
            self.workspace.capture,
            restore,
            snapshot_weight=lambda snapshot: snapshot["weight"],
            max_stack_weight=8,
            clock=self.clock,
        )
        history.reset()
        history.checkpoint_current()
        self.set_value(1)
        history.refresh_baseline()

        with self.assertRaisesRegex(RuntimeError, "restore failed"):
            history.undo()
        self.assertEqual(self.workspace.state["value"], 1)
        self.assertEqual((history.undo_count, history.undo_weight), (1, 4))
        self.assertEqual((history.redo_count, history.redo_weight), (0, 0))

    def test_weight_callback_failure_does_not_mutate_history_or_workspace(self) -> None:
        self.workspace.state["weight"] = 4
        fail = False

        def snapshot_weight(snapshot: dict) -> int:
            if fail:
                raise RuntimeError("weight failed")
            return snapshot["weight"]

        history = UndoManager(
            self.workspace.capture,
            self.workspace.restore,
            snapshot_weight=snapshot_weight,
            max_stack_weight=8,
            clock=self.clock,
        )
        history.reset()
        history.checkpoint_current()
        self.set_value(1)
        history.refresh_baseline()
        fail = True

        with self.assertRaisesRegex(RuntimeError, "weight failed"):
            history.undo()
        self.assertEqual(self.workspace.state["value"], 1)
        self.assertEqual((history.undo_count, history.undo_weight), (1, 4))
        self.assertEqual((history.redo_count, history.redo_weight), (0, 0))

    def test_suspension_and_restore_callbacks_do_not_record_reentrantly(self) -> None:
        reentrant_results: list[bool] = []

        def restore(snapshot: dict) -> None:
            self.workspace.restore(snapshot)
            reentrant_results.append(history.checkpoint_current())

        history = UndoManager(self.workspace.capture, restore, clock=self.clock)
        history.reset()
        history.checkpoint_current()
        self.set_value(1)
        history.refresh_baseline()

        self.assertTrue(history.undo())
        self.assertEqual(reentrant_results, [False])
        self.assertEqual(history.undo_count, 0)

        with history.suspended():
            self.assertFalse(history.checkpoint_current())
            self.set_value(9)
        self.assertEqual(history.redo_count, 1)

    def test_undo_reuses_target_as_baseline_without_recapturing_workspace(self) -> None:
        self.assertEqual(self.workspace.capture_count, 1)
        self.history.perform_change(lambda: self.set_value(1), coalesce_key="field")
        self.assertEqual(self.workspace.capture_count, 2)

        self.assertTrue(self.history.undo())
        # Undo captures the outgoing state once for redo.  The restored target
        # remains the protected baseline, so no fourth full-workspace capture
        # is necessary.
        self.assertEqual(self.workspace.capture_count, 3)
        self.assertTrue(self.history.has_baseline)

    def test_dataframe_snapshots_stay_isolated_after_restore_and_edit(self) -> None:
        live = {
            "frame": pd.DataFrame({"x": [1.0, 2.0], "label": ["a", "b"]}),
        }
        clone_calls = 0

        def capture() -> dict:
            return deepcopy(live)

        def clone_for_restore(snapshot: dict) -> dict:
            nonlocal clone_calls
            clone_calls += 1
            return deepcopy(snapshot)

        def restore(snapshot: dict) -> None:
            nonlocal live
            live = snapshot

        def set_x(values: list[float]) -> None:
            live["frame"].loc[:, "x"] = values

        history = UndoManager(
            capture,
            restore,
            clone_for_restore=clone_for_restore,
            clock=self.clock,
        )
        history.reset()
        history.perform_change(
            lambda: set_x([10.0, 20.0]),
            coalesce_key="table",
        )

        self.assertEqual(clone_calls, 0, "moving a baseline onto the stack needs no copy")
        self.assertTrue(history.undo())
        self.assertEqual(clone_calls, 1)
        self.assertEqual(live["frame"]["x"].tolist(), [1.0, 2.0])

        # If restore shared the stored baseline, this edit would corrupt the
        # undo snapshot and the next undo would incorrectly return 99 below.
        history.perform_change(
            lambda: set_x([99.0, 99.0]),
            coalesce_key="table",
        )
        self.assertTrue(history.undo())
        self.assertEqual(live["frame"]["x"].tolist(), [1.0, 2.0])

    def test_reset_can_leave_history_without_a_baseline(self) -> None:
        self.history.reset(capture_baseline=False)
        self.assertFalse(self.history.has_baseline)
        self.history.perform_change(lambda: self.set_value(1), coalesce_key="field")
        self.assertEqual(self.history.undo_count, 0)
        self.assertTrue(self.history.has_baseline)

    def test_invalid_configuration_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            UndoManager(self.workspace.capture, self.workspace.restore, limit=0)
        with self.assertRaises(ValueError):
            UndoManager(
                self.workspace.capture,
                self.workspace.restore,
                coalesce_interval=-0.1,
            )
        with self.assertRaises(ValueError):
            UndoManager(
                self.workspace.capture,
                self.workspace.restore,
                snapshot_weight=lambda _snapshot: 1,
            )
        with self.assertRaises(ValueError):
            UndoManager(
                self.workspace.capture,
                self.workspace.restore,
                max_stack_weight=8,
            )
        with self.assertRaises(ValueError):
            UndoManager(
                self.workspace.capture,
                self.workspace.restore,
                snapshot_resources=lambda _snapshot: {},
            )
        for invalid in (0, -1, True, 1.5):
            with self.subTest(max_stack_weight=invalid):
                with self.assertRaises(ValueError):
                    UndoManager(
                        self.workspace.capture,
                        self.workspace.restore,
                        snapshot_weight=lambda _snapshot: 1,
                        max_stack_weight=invalid,
                    )

        for invalid_weight in (-1, True, 1.5):
            with self.subTest(snapshot_weight=invalid_weight):
                history = UndoManager(
                    self.workspace.capture,
                    self.workspace.restore,
                    snapshot_weight=lambda _snapshot, value=invalid_weight: value,
                    max_stack_weight=8,
                )
                with self.assertRaises(ValueError):
                    history.reset()

        invalid_resources = (
            [],
            {"buffer": -1},
            {"buffer": True},
            {"buffer": 1.5},
        )
        for resources in invalid_resources:
            with self.subTest(snapshot_resources=resources):
                history = UndoManager(
                    self.workspace.capture,
                    self.workspace.restore,
                    snapshot_resources=lambda _snapshot, value=resources: value,
                    max_stack_weight=8,
                )
                with self.assertRaises(ValueError):
                    history.reset()


if __name__ == "__main__":
    unittest.main()
