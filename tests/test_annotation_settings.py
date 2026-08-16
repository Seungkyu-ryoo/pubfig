from __future__ import annotations

from dataclasses import fields
import os
from types import SimpleNamespace
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from pubfig.plot_config import AnnotationConfig
from pubfig.ui.annotation_settings import (
    ANNOTATION_WIDGET_ALIASES,
    AnnotationSettingsPanel,
)


def sample_annotation(**overrides) -> AnnotationConfig:
    annotation = AnnotationConfig(
        kind="arrow",
        text="Peak position",
        x=0.1234,
        y=0.6789,
        width=0.4321,
        height=-0.2345,
        angle=17.5,
        color="#123456",
        line_style="loosely dashed",
        alpha=0.65,
        fill=True,
        font_size=17,
        arrow_head_size=14.5,
    )
    annotation.x2 = annotation.x + annotation.width
    annotation.y2 = annotation.y + annotation.height
    for name, value in overrides.items():
        setattr(annotation, name, value)
    return annotation


class AnnotationSettingsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.panel = AnnotationSettingsPanel()

    def tearDown(self) -> None:
        self.panel.deleteLater()

    def test_bindings_cover_every_annotation_config_field(self) -> None:
        expected = {field.name for field in fields(AnnotationConfig)}
        self.assertEqual(set(self.panel.bindings.fields), expected)
        self.assertEqual(len(self.panel.bindings), len(expected))

    def test_load_and_update_round_trip_all_fields_without_emitting(self) -> None:
        source = sample_annotation()
        changes = []
        self.panel.connect_changed(changes.append)

        self.panel.load(source)

        self.assertEqual(changes, [])
        self.assertEqual(self.panel.kind_combo.currentText(), source.kind)
        self.assertEqual(self.panel.text_edit.text(), source.text)
        self.assertEqual(self.panel.color_button.text(), source.color)
        self.assertEqual(self.panel.line_style_combo.currentText(), source.line_style)

        target = AnnotationConfig()
        result = self.panel.update(target)
        self.assertIs(result, target)
        for field in fields(AnnotationConfig):
            self.assertEqual(getattr(target, field.name), getattr(source, field.name), field.name)

    def test_user_edits_report_source_but_commit_only_when_requested(self) -> None:
        annotation = sample_annotation(text="Before")
        self.panel.set_annotations([annotation], preserve_selection=False)
        sources = []
        self.panel.connect_changed(sources.append)

        self.panel.text_edit.setText("After")
        self.panel.x_spin.setValue(0.25)
        self.panel.width_spin.setValue(0.5)
        self.panel.set_color("#abcdef")

        self.assertEqual(annotation.text, "Before")
        self.assertEqual(
            sources,
            [self.panel.text_edit, self.panel.x_spin, self.panel.width_spin],
        )

        result = self.panel.update_current()
        self.assertIs(result, annotation)
        self.assertEqual(annotation.text, "After")
        self.assertEqual(annotation.color, "#abcdef")
        self.assertEqual(annotation.x2, 0.75)
        self.assertEqual(annotation.y2, annotation.y + annotation.height)
        self.assertEqual(self.panel.x2_spin.value(), annotation.x2)
        self.assertAlmostEqual(self.panel.y2_spin.value(), annotation.y2, places=4)
        self.assertIn("After", self.panel.list_widget.item(0).text())
        self.assertIn("Text: After", self.panel.list_widget.item(0).toolTip())

    def test_selection_loads_inputs_and_preserves_extended_selection_on_refresh(self) -> None:
        annotations = [
            sample_annotation(text="First", x=0.1),
            sample_annotation(text="Second", x=0.2),
            sample_annotation(text="Third", x=0.3),
        ]
        for annotation in annotations:
            annotation.x2 = annotation.x + annotation.width
        self.panel.set_annotations(annotations, preserve_selection=False)
        selection_events = []
        changes = []
        self.panel.selection_changed.connect(selection_events.append)
        self.panel.connect_changed(changes.append)

        current = self.panel.set_selection([0, 2], current=2)
        self.assertEqual(current, 2)
        self.assertEqual(self.panel.selected_indices(), [0, 2])
        self.assertIs(self.panel.current_annotation(), annotations[2])
        self.assertEqual(self.panel.text_edit.text(), "Third")
        self.assertEqual(selection_events, [2])
        self.assertEqual(changes, [])

        annotations[0].text = "First refreshed"
        self.panel.refresh_annotation_list()
        self.assertEqual(self.panel.selected_indices(), [0, 2])
        self.assertEqual(self.panel.list_widget.currentRow(), 2)
        self.assertIn("First refreshed", self.panel.list_widget.item(0).text())
        self.assertEqual(selection_events, [2])

    def test_set_annotations_shares_list_and_clamps_preserved_selection(self) -> None:
        first = [sample_annotation(text=str(index)) for index in range(3)]
        self.panel.set_annotations(first, preserve_selection=False)
        self.panel.set_selection([0, 2], current=2, emit=False)

        replacement = [sample_annotation(text="a"), sample_annotation(text="b")]
        self.panel.set_annotations(replacement)

        self.assertIs(self.panel.annotations, replacement)
        self.assertEqual(self.panel.list_widget.currentRow(), 1)
        self.assertEqual(self.panel.selected_indices(), [0, 1])
        replacement.append(sample_annotation(text="c"))
        self.panel.refresh_annotation_list()
        self.assertEqual(self.panel.list_widget.count(), 3)

    def test_action_buttons_emit_requests_without_mutating_model(self) -> None:
        annotations = [sample_annotation()]
        self.panel.set_annotations(annotations, preserve_selection=False)
        requests = []
        self.panel.add_requested.connect(lambda: requests.append("add"))
        self.panel.remove_requested.connect(lambda: requests.append("remove"))
        self.panel.color_requested.connect(lambda: requests.append("color"))

        self.panel.add_button.click()
        self.panel.remove_button.click()
        self.panel.color_button.click()

        self.assertEqual(requests, ["add", "remove", "color"])
        self.assertEqual(self.panel.annotations, annotations)
        self.assertEqual(len(annotations), 1)

    def test_load_restores_signal_state_and_loading_guard_after_writer_error(self) -> None:
        failing = self.panel.bindings["arrow_head_size"]
        original_write = failing.write
        already_blocked = self.panel.text_edit
        already_blocked.blockSignals(True)

        def fail(_value) -> None:
            raise RuntimeError("annotation writer failed")

        failing.write = fail
        try:
            with self.assertRaisesRegex(RuntimeError, "annotation writer failed"):
                self.panel.load(sample_annotation())
        finally:
            failing.write = original_write

        self.assertFalse(self.panel.loading_annotation_inputs)
        self.assertTrue(already_blocked.signalsBlocked())
        for widget in self.panel.bindings.widgets:
            if widget is not already_blocked:
                self.assertFalse(widget.signalsBlocked(), type(widget).__name__)
        already_blocked.blockSignals(False)

    def test_unknown_kind_and_line_style_have_stable_fallbacks(self) -> None:
        annotation = sample_annotation(kind="future kind", line_style="future style")
        self.panel.kind_combo.setCurrentText("box")
        self.panel.line_style_combo.setCurrentText("dotted")

        self.panel.load(annotation)

        self.assertEqual(self.panel.kind_combo.currentText(), "text")
        self.assertEqual(self.panel.line_style_combo.currentText(), "solid")
        self.panel.update(annotation)
        self.assertEqual(annotation.kind, "text")
        self.assertEqual(annotation.line_style, "solid")

    def test_compatibility_aliases_expose_panel_owned_widgets(self) -> None:
        host = SimpleNamespace()
        self.panel.install_compatibility_aliases(host)

        self.assertEqual(
            set(self.panel.compatibility_widgets),
            set(ANNOTATION_WIDGET_ALIASES),
        )
        for old_name, panel_name in ANNOTATION_WIDGET_ALIASES.items():
            widget = getattr(self.panel, panel_name)
            self.assertIs(getattr(host, old_name), widget)
            self.assertTrue(self.panel.isAncestorOf(widget), old_name)

    def test_invalid_selection_and_type_errors_are_safe(self) -> None:
        self.panel.set_annotations([sample_annotation()], preserve_selection=False)
        self.assertEqual(self.panel.set_current_index(100, emit=False), -1)
        self.assertEqual(self.panel.selected_indices(), [])
        self.assertIsNone(self.panel.current_annotation())
        self.assertIsNone(self.panel.update_current())

        self.panel.set_annotations(None)
        self.assertEqual(self.panel.annotations, [])

        with self.assertRaisesRegex(TypeError, "AnnotationConfig"):
            self.panel.load(object())
        with self.assertRaisesRegex(TypeError, "AnnotationConfig"):
            self.panel.update(object())
        with self.assertRaisesRegex(TypeError, "AnnotationConfig"):
            self.panel.set_annotations([object()])


if __name__ == "__main__":
    unittest.main()
