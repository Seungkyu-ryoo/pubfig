"""Reusable figure styles and the named style library."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict, replace
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMessageBox

from ..plot_config import (
    LegendEntryConfig,
    annotation_config_from_payload,
    default_series,
    plot_config_from_payload,
    series_config_from_payload,
)
from ..style_colors import sample_series_color_recipe, series_color_recipe_matches
from .constants import SAVED_STYLES_SCHEMA_VERSION, SAVED_STYLES_SETTINGS_KEY

if TYPE_CHECKING:
    from .main_window import GraphDrawerWindow


class StyleController(QObject):
    """Reusable figure styles and the named style library."""

    def __init__(self, window: GraphDrawerWindow) -> None:
        super().__init__(window)
        self.window = window
        self.session = window.session
        self.style_clipboard: dict | None = None

    def capture_current_style(self) -> dict:
        config = deepcopy(self.window.figure_editor.collect_plot_config())
        series_templates = deepcopy(self.window.figure_editor.selected_series_configs())
        if not series_color_recipe_matches(
            config.series_color_recipe,
            series_templates,
        ):
            config.series_color_recipe = None
        include_annotations = self.window.include_annotations_style_check.isChecked()
        config.annotations = []
        return {
            "plot_config": config,
            "series_templates": series_templates,
            "annotations": deepcopy(self.session.annotations)
            if include_annotations
            else [],
            "has_annotations": include_annotations,
        }

    @staticmethod
    def style_bundle_to_payload(bundle: dict) -> dict:
        plot_config = asdict(bundle["plot_config"])
        plot_config.pop("annotations", None)
        series_templates = []
        for series in bundle.get("series_templates", []):
            template = asdict(series)
            # Whether/where to fit is graph-specific analysis intent rather
            # than reusable visual style. Fit line appearance remains reusable.
            for field in (
                "linear_fit_enabled",
                "linear_fit_x_min",
                "linear_fit_x_max",
            ):
                template.pop(field, None)
            series_templates.append(template)
        return {
            "plot_config": plot_config,
            "series_templates": series_templates,
            "annotations": [
                asdict(annotation) for annotation in bundle.get("annotations", [])
            ],
            "has_annotations": bool(bundle.get("has_annotations")),
        }

    @staticmethod
    def style_bundle_from_payload(payload: dict) -> dict | None:
        if not isinstance(payload, dict) or not isinstance(
            payload.get("plot_config"), dict
        ):
            return None
        try:
            plot_payload = dict(payload["plot_config"])
            plot_payload.pop("annotations", None)
            return {
                "plot_config": plot_config_from_payload(plot_payload),
                "series_templates": [
                    series_config_from_payload(item)
                    for item in payload.get("series_templates", [])
                    if isinstance(item, dict)
                ],
                "annotations": [
                    annotation_config_from_payload(item)
                    for item in payload.get("annotations", [])
                    if isinstance(item, dict)
                ],
                "has_annotations": bool(payload.get("has_annotations")),
            }
        except (TypeError, ValueError):
            return None

    def saved_style_payloads(self) -> dict[str, dict]:
        raw = self.window.settings.value(SAVED_STYLES_SETTINGS_KEY, "")
        try:
            envelope = json.loads(raw) if isinstance(raw, str) and raw else raw
        except (TypeError, json.JSONDecodeError):
            return {}
        if not isinstance(envelope, dict):
            return {}
        styles = envelope.get("styles", {})
        if not isinstance(styles, dict):
            return {}
        return {
            name.strip(): payload
            for name, payload in styles.items()
            if isinstance(name, str)
            and name.strip()
            and isinstance(payload, dict)
            and self.style_bundle_from_payload(payload) is not None
        }

    def write_saved_style_payloads(self, styles: dict[str, dict]) -> None:
        envelope = {
            "schema_version": SAVED_STYLES_SCHEMA_VERSION,
            "styles": styles,
        }
        self.window.settings.setValue(
            SAVED_STYLES_SETTINGS_KEY,
            json.dumps(envelope, ensure_ascii=False, separators=(",", ":")),
        )
        self.window.settings.sync()

    def refresh_saved_style_combo(self, preferred_name: str = "") -> None:
        styles = self.saved_style_payloads()
        previous = preferred_name or self.window.saved_style_combo.currentText()
        names = sorted(styles, key=str.casefold)
        self.window.saved_style_combo.blockSignals(True)
        self.window.saved_style_combo.clear()
        self.window.saved_style_combo.addItems(names)
        if names:
            match = next(
                (name for name in names if name.casefold() == previous.casefold()),
                names[0],
            )
            self.window.saved_style_combo.setCurrentText(match)
        else:
            self.window.saved_style_combo.setCurrentIndex(-1)
        self.window.saved_style_combo.blockSignals(False)
        has_styles = bool(names)
        self.window.apply_named_style_btn.setEnabled(has_styles)
        self.window.delete_named_style_btn.setEnabled(has_styles)
        self.update_named_style_save_button()

    def update_named_style_save_button(self, *_args) -> None:
        name = self.window.saved_style_name_edit.text().strip()
        if name:
            display_name = name if len(name) <= 24 else name[:21] + "..."
            self.window.save_named_style_btn.setText(f'Save to "{display_name}"')
        else:
            self.window.save_named_style_btn.setText("Save named style")
        self.window.save_named_style_btn.setEnabled(bool(name))

    @staticmethod
    def matching_saved_style_name(
        styles: dict[str, dict], requested_name: str
    ) -> str | None:
        requested_key = requested_name.casefold()
        return next((name for name in styles if name.casefold() == requested_key), None)

    def save_named_style(
        self, name: str | None = None, *, overwrite: bool | None = None
    ) -> bool:
        requested_name = (
            name if name is not None else self.window.saved_style_name_edit.text()
        ).strip()
        if not requested_name:
            self.window.set_status("Enter a style name first.")
            return False
        styles = self.saved_style_payloads()
        existing_name = self.matching_saved_style_name(styles, requested_name)
        if existing_name is not None:
            requested_name = existing_name
            if overwrite is None:
                reply = QMessageBox.question(
                    self.window,
                    "Replace saved style",
                    f'Replace the saved style "{requested_name}"?',
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return False
            elif not overwrite:
                return False
        bundle = self.capture_current_style()
        styles[requested_name] = self.style_bundle_to_payload(bundle)
        self.write_saved_style_payloads(styles)
        self.window.saved_style_name_edit.setText(requested_name)
        self.refresh_saved_style_combo(requested_name)
        note = " with annotations" if bundle.get("has_annotations") else ""
        self.window.set_status(f'Saved style "{requested_name}"{note}.')
        return True

    def delete_named_style(
        self, name: str | None = None, *, confirm: bool = True
    ) -> bool:
        requested_name = (
            name if name is not None else self.window.saved_style_combo.currentText()
        ).strip()
        styles = self.saved_style_payloads()
        stored_name = self.matching_saved_style_name(styles, requested_name)
        if stored_name is None:
            self.window.set_status("Select a saved style first.")
            return False
        if confirm:
            reply = QMessageBox.question(
                self.window,
                "Delete saved style",
                f'Delete the saved style "{stored_name}"?',
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return False
        del styles[stored_name]
        self.write_saved_style_payloads(styles)
        if (
            self.window.saved_style_name_edit.text().strip().casefold()
            == stored_name.casefold()
        ):
            self.window.saved_style_name_edit.clear()
        self.refresh_saved_style_combo()
        self.window.set_status(f'Deleted saved style "{stored_name}".')
        return True

    def copy_current_style(self) -> None:
        self.style_clipboard = self.capture_current_style()
        include_annotations = self.style_clipboard.get("has_annotations")
        note = " with annotations" if include_annotations else ""
        self.window.set_status(f"Copied current style{note}.")

    def apply_copied_style(self) -> None:
        if not self.style_clipboard:
            self.window.set_status("Copy a style first.")
            return
        self.apply_style_bundle(self.style_clipboard, "copied style")

    def apply_named_style(self, name: str | None = None) -> bool:
        requested_name = (
            name if name is not None else self.window.saved_style_combo.currentText()
        ).strip()
        styles = self.saved_style_payloads()
        stored_name = self.matching_saved_style_name(styles, requested_name)
        if stored_name is None:
            self.window.set_status("Select a saved style first.")
            return False
        bundle = self.style_bundle_from_payload(styles[stored_name])
        if bundle is None:
            self.window.set_status(f'Could not read saved style "{stored_name}".')
            return False
        self.apply_style_bundle(bundle, f'saved style "{stored_name}"')
        return True

    def apply_style_bundle(self, bundle: dict, source_label: str) -> None:
        self.window.workspace.push_current_undo_state()
        current = deepcopy(self.window.figure_editor.collect_plot_config())
        style_config = deepcopy(bundle["plot_config"])

        # Legend entry sources and labels are graph-specific data mappings, so
        # retain that content while applying the source layout and per-entry
        # text formatting by position. Plot text such as the title and axis
        # labels is copied with the requested format.
        target_legend_entries = deepcopy(current.legend_entries)
        if target_legend_entries is not None:
            source_legend_entries = list(style_config.legend_entries or [])
            default_legend_format = LegendEntryConfig()
            formatted_entries: list[LegendEntryConfig] = []
            for index, target_entry in enumerate(target_legend_entries):
                template = (
                    source_legend_entries[min(index, len(source_legend_entries) - 1)]
                    if source_legend_entries
                    else default_legend_format
                )
                formatted_entries.append(
                    replace(
                        target_entry,
                        font_family=template.font_family,
                        font_size=template.font_size,
                        font_bold=template.font_bold,
                        font_italic=template.font_italic,
                        text_color=template.text_color,
                    )
                )
            style_config.legend_entries = formatted_entries
        else:
            style_config.legend_entries = None

        include_annotations = (
            self.window.include_annotations_style_check.isChecked()
            and bundle.get("has_annotations")
        )
        self.session.annotations = (
            deepcopy(bundle["annotations"])
            if include_annotations
            else self.session.annotations
        )
        style_config.annotations = self.session.annotations

        templates = bundle.get("series_templates", [])
        checked = self.window.table_editor.checked_y_columns()
        source_color_recipe = deepcopy(style_config.series_color_recipe)
        if not series_color_recipe_matches(source_color_recipe, templates):
            source_color_recipe = None
        adaptive_colors = sample_series_color_recipe(source_color_recipe, len(checked))
        style_config.series_color_recipe = (
            replace(source_color_recipe, series_count=len(checked))
            if source_color_recipe is not None and adaptive_colors
            else None
        )

        self.session.plot_config = style_config
        self.window.figure_editor._load_config_into_widgets()

        for idx, y_column in enumerate(checked):
            if not templates:
                break
            template = templates[min(idx, len(templates) - 1)]
            x_column = self.window.table_editor._nearest_left_x(y_column)
            series = self.session.series_by_y.get(y_column) or default_series(
                x_column, y_column, idx
            )
            series.color = template.color
            series.plot_type = template.plot_type
            series.y_axis = template.y_axis
            series.marker = template.marker
            series.marker_fill_style = template.marker_fill_style
            series.line_style = template.line_style
            series.line_width = template.line_width
            series.marker_size = template.marker_size
            series.y_offset = template.y_offset
            series.alpha = template.alpha
            series.force_opaque = template.force_opaque
            if adaptive_colors:
                series.color = adaptive_colors[idx]
                series.alpha = 1.0
                series.force_opaque = True
            series.show_in_legend = template.show_in_legend
            series.error_cap_size = template.error_cap_size
            series.linear_fit_line_style = template.linear_fit_line_style
            series.linear_fit_line_width = template.linear_fit_line_width
            series.x = x_column
            series.y = y_column
            series.label = self.window.table_editor._column_name(y_column)
            self.session.series_by_y[y_column] = series

        self.window.annotations_editor.refresh_annotation_list()
        self.window.series_editor.update_style_targets()
        self.window.preview.render_plot()
        self.window.workspace.update_undo_baseline()
        note = " and annotations" if include_annotations else ""
        color_note = (
            f"; resampled {source_color_recipe.name} across {len(checked)} series"
            if adaptive_colors and source_color_recipe is not None
            else ""
        )
        self.window.set_status(f"Applied {source_label}{note}{color_note}.")
