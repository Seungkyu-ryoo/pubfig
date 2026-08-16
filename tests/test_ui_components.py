from __future__ import annotations

import os
from dataclasses import dataclass
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QKeyEvent, QTextCursor
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QSpinBox

from pubfig.plot_config import LegendEntryConfig
from pubfig.ui.binding import BindingRegistry, FieldBinding, signals_blocked
from pubfig.ui.widgets import (
    AnnotationTextEdit,
    GradientEditorWidget,
    LegendEditorDialog,
    NoWheelComboBox,
    NoWheelDoubleSpinBox,
    NoWheelSpinBox,
    ProjectTreeWidget,
)


class _IgnoredEvent:
    def __init__(self) -> None:
        self.ignored = False

    def ignore(self) -> None:
        self.ignored = True


class _DropEvent(_IgnoredEvent):
    class _Position:
        @staticmethod
        def toPoint():
            return QPoint()

    @staticmethod
    def position():
        return _DropEvent._Position()


class StandaloneWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_no_wheel_widgets_ignore_wheel_events(self) -> None:
        for widget_type in (NoWheelComboBox, NoWheelDoubleSpinBox, NoWheelSpinBox):
            event = _IgnoredEvent()
            widget_type().wheelEvent(event)
            self.assertTrue(event.ignored)

    def test_annotation_text_edit_compatibility_and_shift_enter(self) -> None:
        editor = AnnotationTextEdit()
        editor.setText("alpha")
        editor.moveCursor(QTextCursor.End)
        editor.insert(" beta")
        self.assertEqual(editor.text(), "alpha beta")

        event = QKeyEvent(
            QKeyEvent.KeyPress,
            Qt.Key_Return,
            Qt.ShiftModifier,
        )
        editor.keyPressEvent(event)
        self.assertEqual(editor.text(), "alpha beta\n")
        self.assertEqual(editor.maximumHeight(), 70)
        self.assertTrue(editor.tabChangesFocus())

    def test_project_tree_ignores_drop_without_a_source(self) -> None:
        tree = ProjectTreeWidget()
        event = _DropEvent()
        tree.dropEvent(event)
        self.assertTrue(event.ignored)

    def test_legend_rows_round_trip_and_preserve_layout(self) -> None:
        candidates = [
            ("a", "Alpha", True),
            ("b", "Beta", True),
            ("c", "Gamma", False),
        ]
        entries = [
            LegendEntryConfig(source_y="a", label="A"),
            LegendEntryConfig(source_y="b", label="B"),
            LegendEntryConfig(source_y="c", label="C"),
        ]
        dialog = LegendEditorDialog(
            candidates,
            entries,
            [2, 1],
            automatic=False,
        )

        self.assertEqual(
            dialog.rows_payload(),
            [("a", "A", True), ("b", "B", False), ("c", "C", True)],
        )
        result_entries, row_lengths = dialog.result_config()
        self.assertEqual(result_entries, entries)
        self.assertEqual(row_lengths, [2, 1])

        dialog.table.selectRow(0)
        dialog.remove_selected_entry()
        self.assertEqual(
            dialog.rows_payload(),
            [("b", "B", True), ("c", "C", True)],
        )

        dialog.reset_automatic()
        self.assertEqual(dialog.rows_payload(), [("a", "Alpha", True), ("b", "Beta", True)])
        self.assertEqual(dialog.result_config(), (None, []))

    def test_legend_add_uses_first_unused_candidate(self) -> None:
        dialog = LegendEditorDialog(
            [("a", "Alpha", True), ("b", "Beta", True)],
            [LegendEntryConfig(source_y="a", label="Alpha")],
            [],
            automatic=False,
        )
        dialog.add_entry()
        self.assertEqual(
            dialog.rows_payload(),
            [("a", "Alpha", True), ("b", "Beta", True)],
        )

    def test_gradient_validation_sorting_and_interpolation(self) -> None:
        editor = GradientEditorWidget()
        changes: list[None] = []
        editor.changed.connect(lambda: changes.append(None))

        editor.set_stops(
            [
                [2, "#ffffff", 2],
                [-1, "#000000", -1],
                [0.25, "not-a-color", 0.5],
                ["bad"],
            ]
        )

        self.assertEqual(
            editor.stops_payload(),
            [[1.0, "#ffffff", 1.0], [0.0, "#000000", 0.0]],
        )
        self.assertEqual(editor.sorted_stops()[0][0], 0.0)
        color, alpha = editor.sample(0.5)
        self.assertEqual(color, "#808080")
        self.assertAlmostEqual(alpha, 0.5)
        self.assertEqual(changes, [])

        before = editor.stops_payload()
        editor.set_stops([[0.5, "invalid", 0.5]])
        self.assertEqual(editor.stops_payload(), before)


class BindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_signals_blocked_supports_many_objects_and_restores_state(self) -> None:
        first = QSpinBox()
        second = QSpinBox()
        emissions: list[tuple[str, int]] = []
        first.valueChanged.connect(lambda value: emissions.append(("first", value)))
        second.valueChanged.connect(lambda value: emissions.append(("second", value)))
        second.blockSignals(True)

        with self.assertRaisesRegex(RuntimeError, "writer failed"):
            with signals_blocked([first, second, first]):
                self.assertTrue(first.signalsBlocked())
                self.assertTrue(second.signalsBlocked())
                first.setValue(1)
                second.setValue(2)
                raise RuntimeError("writer failed")

        self.assertFalse(first.signalsBlocked())
        self.assertTrue(second.signalsBlocked())
        self.assertEqual(emissions, [])
        second.blockSignals(False)

    def test_registry_uses_custom_conversions_for_optional_values_and_color(self) -> None:
        @dataclass
        class Config:
            optional_size: float | None
            color: str

        optional_edit = QLineEdit()
        color_button = QPushButton()

        def read_optional() -> float | None:
            text = optional_edit.text().strip()
            if not text:
                return None
            value = float(text)
            if value <= 0:
                raise ValueError("Size must be positive")
            return value

        def write_optional(value: float | None) -> None:
            optional_edit.setText("" if value is None else str(value))

        def read_color() -> str:
            return str(color_button.property("color") or "")

        def write_color(value: str) -> None:
            color_button.setProperty("color", value)
            color_button.setText(value)

        registry = BindingRegistry(
            [
                FieldBinding(
                    "optional_size",
                    optional_edit,
                    read_optional,
                    write_optional,
                    optional_edit.textChanged,
                ),
                FieldBinding(
                    "color",
                    color_button,
                    read_color,
                    write_color,
                    color_button.clicked,
                ),
            ]
        )
        callbacks: list[None] = []
        registry.connect(lambda: callbacks.append(None))

        config = Config(optional_size=2.5, color="#112233")
        registry.load(config)
        self.assertEqual(optional_edit.text(), "2.5")
        self.assertEqual(color_button.text(), "#112233")
        self.assertEqual(callbacks, [])

        optional_edit.setText("4.75")
        write_color("#abcdef")
        color_button.click()
        self.assertEqual(len(callbacks), 2)
        self.assertEqual(
            registry.values(),
            {"optional_size": 4.75, "color": "#abcdef"},
        )
        self.assertIs(registry.update(config), config)
        self.assertEqual(config, Config(optional_size=4.75, color="#abcdef"))

        mapping: dict[str, object] = {"optional_size": None, "color": "#000000"}
        registry.load(mapping)
        self.assertEqual(optional_edit.text(), "")
        self.assertEqual(callbacks, [None, None])
        optional_edit.setText("1.25")
        registry.update(mapping)
        self.assertEqual(mapping["optional_size"], 1.25)

        registry.disconnect()
        optional_edit.setText("2.0")
        self.assertEqual(len(callbacks), 3)

    def test_registry_load_unblocks_every_widget_after_writer_exception(self) -> None:
        first = QLineEdit()
        second = QLineEdit()

        def fail(_value: str) -> None:
            raise RuntimeError("cannot display value")

        registry = BindingRegistry(
            [
                FieldBinding("first", first, first.text, first.setText),
                FieldBinding("second", second, second.text, fail),
            ]
        )

        with self.assertRaisesRegex(RuntimeError, "cannot display value"):
            registry.load({"first": "loaded", "second": "failure"})

        self.assertEqual(first.text(), "loaded")
        self.assertFalse(first.signalsBlocked())
        self.assertFalse(second.signalsBlocked())

    def test_registry_rejects_duplicate_fields(self) -> None:
        widget = QLineEdit()
        binding = FieldBinding("name", widget, widget.text, widget.setText)
        with self.assertRaisesRegex(ValueError, "Duplicate field"):
            BindingRegistry([binding, binding])


if __name__ == "__main__":
    unittest.main()
