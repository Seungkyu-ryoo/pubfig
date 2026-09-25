"""Preview rendering, canvas ownership, measurements, and export."""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.transforms import Bbox
from PySide6.QtCore import QObject, QSize, Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QSizePolicy

from ..model import Graph, TreeNode
from ..plot_config import PlotConfig, SeriesConfig, default_series
from ..rendering import (
    ExportJob,
    NumericColumnCache,
    RenderCoordinator,
    RenderOptions,
    RenderRequest,
)
from ..sheet_data import column_name, nearest_left_x, plot_dataframe
from .constants import PREVIEW_DPI

if TYPE_CHECKING:
    from .main_window import GraphDrawerWindow


class PreviewController(QObject):
    """Preview rendering, canvas ownership, measurements, and export."""

    def __init__(self, window: GraphDrawerWindow) -> None:
        super().__init__(window)
        self.window = window
        self.session = window.session
        self.rendering = RenderCoordinator()

        self.preview_numeric_cache = NumericColumnCache()

        self.current_figure: Figure | None = None

        self.annotation_artists = []

        self.legend_artist = None

        self.linear_fit_results_by_y = {}

        self.render_timer = QTimer(window)
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(200)
        self.render_timer.timeout.connect(self.render_plot)

    def copy_figure_to_clipboard(self) -> None:
        self.render_timer.stop()
        config = self.window.figure_editor.collect_plot_config()
        try:
            encoded = self.rendering.render_bytes(
                self._render_request(config),
                format="png",
            )
        except Exception as exc:
            message = f"Clipboard copy failed: {exc}"
            self.update_render_issues([message])
            self.window.set_status(message)
            return
        image = QImage.fromData(encoded.data, "PNG")
        QApplication.clipboard().setImage(image)
        self.window.set_status(
            f"Copied figure to clipboard ({image.width()} x {image.height()} px at {config.dpi} DPI)."
        )

    def _render_request(
        self,
        config: PlotConfig | None = None,
        *,
        label: str = "",
        preview: bool = False,
    ) -> RenderRequest:
        render_options = (
            RenderOptions.for_preview(
                numeric_cache=self.preview_numeric_cache,
                cache_source=self.session.df,
            )
            if preview
            else RenderOptions()
        )
        return RenderRequest(
            dataframe=self.window.table_editor._plot_dataframe(),
            config=config or self.window.figure_editor.collect_plot_config(),
            series_configs=tuple(self.window.figure_editor.selected_series_configs()),
            label=label,
            render_options=render_options,
        )

    def _adopt_render_result(self, result) -> None:
        self.current_figure = result.figure
        self.annotation_artists = result.annotation_artists
        self.legend_artist = result.legend_artist
        self.linear_fit_results_by_y = dict(getattr(result, "linear_fit_results", {}))
        self._replace_canvas(result.figure)
        self.window.interaction.draw_annotation_handles()
        self.update_render_issues(result.warnings)
        self.update_linear_fit_result_display()

    def schedule_render(self) -> None:
        if self.session.loading_project_figure:
            return
        self.linear_fit_results_by_y.clear()
        self.update_linear_fit_result_display(pending=True)
        self.render_timer.start()

    def update_linear_fit_result_display(self, *, pending: bool = False) -> None:
        target = self.window.series_settings.target_combo.currentText()
        series = self.session.series_by_y.get(target)
        if series is None:
            return
        self.window.series_settings.set_linear_fit_result(
            self.linear_fit_results_by_y.get(target),
            enabled=series.linear_fit_enabled,
            pending=pending and series.linear_fit_enabled,
        )

    def render_plot(self) -> None:
        config = self.window.figure_editor.collect_plot_config()
        request = self._render_request(config, preview=True)
        try:
            result = self.rendering.render_for_display(
                request,
                self._adopt_render_result,
            )
        except Exception as exc:
            message = f"Preview failed: {exc}"
            self.update_render_issues([message])
            self.linear_fit_results_by_y.clear()
            self.update_linear_fit_result_display()
            self.window.set_status(message)
            return
        if result.warnings:
            self.window.set_status(" | ".join(result.warnings[:2]))
        elif not self.session.df.empty:
            figure_width = result.figure.get_figwidth() * 25.4
            figure_height = result.figure.get_figheight() * 25.4
            self.window.set_status(
                f"Rendered {len(request.series_configs)} series at {figure_width:g} x {figure_height:g} mm"
            )

    def update_render_issues(self, warnings: list[str]) -> None:
        if not warnings:
            self.window.preview_issues.clear()
            self.window.preview_issues.hide()
            return
        visible = warnings[:5]
        text = "Figure issues:\n" + "\n".join(f"• {warning}" for warning in visible)
        if len(warnings) > len(visible):
            text += f"\n• …and {len(warnings) - len(visible)} more"
        self.window.preview_issues.setText(text)
        self.window.preview_issues.show()

    def center_plot_box(self) -> None:
        config = self.window.figure_editor.collect_plot_config()
        if not config.fixed_plot_area:
            self.window.set_status(
                "Enable Lock plot box size before centering the plot box."
            )
            return
        total_width = config.width_mm + config.pad_left_mm + config.pad_right_mm
        total_height = config.height_mm + config.pad_top_mm + config.pad_bottom_mm
        left = (total_width - config.plot_width_mm) / 2 - config.pad_left_mm
        bottom = (total_height - config.plot_height_mm) / 2 - config.pad_bottom_mm
        right = config.width_mm - config.plot_width_mm - left
        top = config.height_mm - config.plot_height_mm - bottom
        if min(left, right, top, bottom) < 0:
            self.window.set_status(
                "Plot box is larger than the canvas; reduce plot size or increase canvas size."
            )
            return
        self.window.workspace.push_current_undo_state()
        self.set_plot_margins(left, right, top, bottom)
        self.render_plot()
        self.window.workspace.update_undo_baseline()
        self.window.set_status("Centered plot box in canvas.")

    def center_content(self) -> None:
        config = deepcopy(self.window.figure_editor.collect_plot_config())
        if not config.fixed_plot_area:
            self.window.set_status(
                "Enable Lock plot box size before centering content."
            )
            return

        try:
            with self.rendering.measure(self._render_request(config)) as measured:
                result = measured.result
                bbox = self.figure_content_bbox(
                    result.figure,
                    result.annotation_artists,
                    result.legend_artist,
                    measured.renderer,
                )
                if bbox is None or not result.figure.axes:
                    self.window.set_status("No visible content to center.")
                    return

                px_per_mm = result.figure.dpi / 25.4
                figure_width_px = result.figure.get_figwidth() * result.figure.dpi
                figure_height_px = result.figure.get_figheight() * result.figure.dpi
                dx_mm = (figure_width_px / 2 - bbox.x0 - bbox.width / 2) / px_per_mm
                dy_mm = (figure_height_px / 2 - bbox.y0 - bbox.height / 2) / px_per_mm
        except Exception as exc:
            message = f"Center content failed: {exc}"
            self.update_render_issues([message])
            self.window.set_status(message)
            return

        left = config.plot_margin_left_mm + dx_mm
        bottom = config.plot_margin_bottom_mm + dy_mm
        available_width = config.width_mm - config.plot_width_mm
        available_height = config.height_mm - config.plot_height_mm
        if available_width < 0 or available_height < 0:
            self.window.set_status(
                "Plot box is larger than the canvas; reduce plot size or increase canvas size."
            )
            return
        left = max(0.0, min(available_width, left))
        bottom = max(0.0, min(available_height, bottom))
        right = available_width - left
        top = available_height - bottom

        self.window.workspace.push_current_undo_state()
        self.set_plot_margins(left, right, top, bottom)
        self.render_plot()
        self.window.workspace.update_undo_baseline()
        self.window.set_status("Centered visible content in canvas.")

    def fit_canvas_to_content(self) -> None:
        config = deepcopy(self.window.figure_editor.collect_plot_config())
        if not config.fixed_plot_area:
            self.window.set_status(
                "Enable Lock plot box size before fitting the canvas."
            )
            return

        self.render_timer.stop()
        try:
            with self.rendering.measure(self._render_request(config)) as measured:
                result = measured.result
                figure = result.figure
                bbox = self.figure_content_bbox(
                    figure,
                    result.annotation_artists,
                    result.legend_artist,
                    measured.renderer,
                )
                if bbox is None:
                    self.window.set_status("No visible content to fit.")
                    return

                pad_mm = 2.0
                px_per_mm = figure.dpi / 25.4
                pad_px = pad_mm * px_per_mm
                plot_bbox = self.plot_area_bbox(figure)
                if plot_bbox is None:
                    self.window.set_status("No plot box to fit.")
                    return
                new_width_mm = max(
                    (bbox.width + 2 * pad_px) / px_per_mm
                    - config.pad_left_mm
                    - config.pad_right_mm,
                    config.plot_width_mm,
                )
                new_height_mm = max(
                    (bbox.height + 2 * pad_px) / px_per_mm
                    - config.pad_top_mm
                    - config.pad_bottom_mm,
                    config.plot_height_mm,
                )
                new_width_mm = max(
                    self.window.figure_settings.width_spin.minimum(),
                    min(self.window.figure_settings.width_spin.maximum(), new_width_mm),
                )
                new_height_mm = max(
                    self.window.figure_settings.height_spin.minimum(),
                    min(
                        self.window.figure_settings.height_spin.maximum(), new_height_mm
                    ),
                )
                new_left_mm = max(
                    0.0,
                    (plot_bbox.x0 - bbox.x0 + pad_px) / px_per_mm - config.pad_left_mm,
                )
                new_bottom_mm = max(
                    0.0,
                    (plot_bbox.y0 - bbox.y0 + pad_px) / px_per_mm
                    - config.pad_bottom_mm,
                )
                new_right_mm = max(
                    0.0,
                    new_width_mm - config.plot_width_mm - new_left_mm,
                )
                new_top_mm = max(
                    0.0,
                    new_height_mm - config.plot_height_mm - new_bottom_mm,
                )
        except Exception as exc:
            message = f"Fit canvas failed: {exc}"
            self.update_render_issues([message])
            self.window.set_status(message)
            return

        self.window.workspace.push_current_undo_state()
        self.set_canvas_size(new_width_mm, new_height_mm)
        self.set_plot_margins(new_left_mm, new_right_mm, new_top_mm, new_bottom_mm)
        self.render_plot()
        self.window.workspace.update_undo_baseline()
        self.window.set_status(
            f"Fit canvas to content: {new_width_mm:g} x {new_height_mm:g} mm."
        )

    def figure_content_bbox(
        self, figure: Figure, annotation_artists: list, legend_artist, renderer
    ) -> Bbox | None:
        bboxes = []
        for axis in figure.axes:
            tight_bbox = axis.get_tightbbox(renderer)
            if tight_bbox is not None:
                bboxes.append(tight_bbox)
        for text in figure.texts:
            bbox = self.artist_window_extent(text, renderer)
            if bbox is not None:
                bboxes.append(bbox)
        if legend_artist is not None and legend_artist.get_visible():
            bboxes.append(legend_artist.get_window_extent(renderer))
        for artist in annotation_artists:
            bbox = self.artist_window_extent(artist, renderer)
            if bbox is not None:
                bboxes.append(bbox)
        valid = [
            bbox
            for bbox in bboxes
            if bbox is not None and bbox.width > 0 and bbox.height > 0
        ]
        return Bbox.union(valid) if valid else None

    def plot_area_bbox(self, figure: Figure) -> Bbox | None:
        bboxes = [axis.bbox for axis in figure.axes if axis.get_visible()]
        return Bbox.union(bboxes) if bboxes else None

    def artist_window_extent(self, artist, renderer) -> Bbox | None:
        pieces = []
        for attr in ("shaft_artist", "head_artist"):
            child = getattr(artist, attr, None)
            if child is not None:
                bbox = self.artist_window_extent(child, renderer)
                if bbox is not None:
                    pieces.append(bbox)
        if hasattr(artist, "get_window_extent"):
            try:
                bbox = artist.get_window_extent(renderer)
                if bbox is not None and bbox.width > 0 and bbox.height > 0:
                    pieces.append(bbox)
            except Exception:
                pass
        return Bbox.union(pieces) if pieces else None

    def set_canvas_size(self, width: float, height: float) -> None:
        width = max(
            self.window.figure_settings.width_spin.minimum(),
            min(self.window.figure_settings.width_spin.maximum(), width),
        )
        height = max(
            self.window.figure_settings.height_spin.minimum(),
            min(self.window.figure_settings.height_spin.maximum(), height),
        )
        for widget in (
            self.window.figure_settings.width_spin,
            self.window.figure_settings.height_spin,
        ):
            widget.blockSignals(True)
        self.window.figure_settings.width_spin.setValue(width)
        self.window.figure_settings.height_spin.setValue(height)
        for widget in (
            self.window.figure_settings.width_spin,
            self.window.figure_settings.height_spin,
        ):
            widget.blockSignals(False)
        self.session.plot_config.width_mm = width
        self.session.plot_config.height_mm = height

    def set_plot_margins(
        self, left: float, right: float, top: float, bottom: float
    ) -> None:
        left = max(0.0, left)
        right = max(0.0, right)
        top = max(0.0, top)
        bottom = max(0.0, bottom)
        widgets = (
            self.window.figure_settings.plot_margin_left_spin,
            self.window.figure_settings.plot_margin_right_spin,
            self.window.figure_settings.plot_margin_top_spin,
            self.window.figure_settings.plot_margin_bottom_spin,
        )
        for widget in widgets:
            widget.blockSignals(True)
        self.window.figure_settings.plot_margin_left_spin.setValue(left)
        self.window.figure_settings.plot_margin_right_spin.setValue(right)
        self.window.figure_settings.plot_margin_top_spin.setValue(top)
        self.window.figure_settings.plot_margin_bottom_spin.setValue(bottom)
        for widget in widgets:
            widget.blockSignals(False)
        self.session.plot_config.plot_margin_left_mm = left
        self.session.plot_config.plot_margin_right_mm = right
        self.session.plot_config.plot_margin_top_mm = top
        self.session.plot_config.plot_margin_bottom_mm = bottom

    def _replace_canvas(self, figure: Figure) -> None:
        self.preview_figure_width_in = figure.get_figwidth()
        self.preview_figure_height_in = figure.get_figheight()
        # FigureCanvasQT derives Retina backing-store DPI from _original_dpi.
        # Keep that logical DPI separate from the device-pixel ratio so the
        # preview preserves publication point sizes on both standard and HiDPI
        # displays.
        figure._original_dpi = PREVIEW_DPI
        figure.set_dpi(PREVIEW_DPI)
        figure.set_size_inches(
            self.preview_figure_width_in, self.preview_figure_height_in, forward=False
        )

        creating_canvas = self.canvas is None
        old_canvas = self.canvas
        old_toolbar = self.toolbar
        old_figure = old_canvas.figure if old_canvas is not None else None

        # Build the replacement while the previous Qt widget is still intact.
        # Deleting/reparenting the old canvas can dispatch pending Qt events.
        new_canvas = FigureCanvasQTAgg(figure)

        # FigureCanvasQTAgg does not expose a supported API for replacing its
        # Figure. Assigning canvas.figure leaves the Agg backing store and Qt
        # paint state tied to the previous Figure, which can paint a large
        # "ghost" plot behind a newly-sized preview. Replace both widgets so
        # each Figure starts with a clean renderer and toolbar.
        self.window.interaction.annotation_handle_artists = []
        if old_toolbar is not None:
            old_toolbar.hide()
            self.window.plot_layout.removeWidget(old_toolbar)
            old_toolbar.setParent(None)
            old_toolbar.deleteLater()
        if old_canvas is not None:
            old_canvas.hide()
            self.window.plot_layout.removeWidget(old_canvas)
            old_canvas.setParent(None)
            old_canvas.deleteLater()
        if old_figure is not None and old_figure is not figure:
            old_figure.clear()

        self.canvas = new_canvas
        self.canvas.setObjectName("figurePreview")
        self.canvas.setAccessibleName("Figure preview")
        self.canvas.installEventFilter(self.window)
        self.toolbar = NavigationToolbar2QT(self.canvas, self.window)
        self.toolbar.setIconSize(QSize(20, 20))
        self.canvas.mpl_connect(
            "button_press_event", self.window.interaction.handle_canvas_press
        )
        self.canvas.mpl_connect(
            "motion_notify_event", self.window.interaction.handle_canvas_motion
        )
        self.canvas.mpl_connect(
            "button_release_event", self.window.interaction.handle_canvas_release
        )
        self.canvas.mpl_connect(
            "scroll_event", self.window.interaction.handle_canvas_scroll
        )
        self.canvas.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.window.plot_layout.addWidget(self.toolbar)
        self.window.plot_layout.addWidget(self.canvas, 0, Qt.AlignCenter)
        # Widgets created while the main window is already running can retain
        # Qt's explicit hidden state after being adopted by the layout.
        self.toolbar.show()
        self.canvas.show()

        self.preview_base_width_px = max(
            80, int(self.preview_figure_width_in * PREVIEW_DPI)
        )
        self.preview_base_height_px = max(
            60, int(self.preview_figure_height_in * PREVIEW_DPI)
        )
        self.update_canvas_size(center=creating_canvas)
        self.window.plot_host.update()
        self.canvas.draw_idle()

    def update_canvas_size(self, *args, center: bool = False) -> None:
        if self.canvas is None or self.current_figure is None:
            return
        viewport = self.window.plot_scroll.viewport().size()
        old_width = max(self.window.plot_host.width(), 1)
        old_height = max(self.window.plot_host.height(), 1)
        hbar = self.window.plot_scroll.horizontalScrollBar()
        vbar = self.window.plot_scroll.verticalScrollBar()
        center_x_ratio = (hbar.value() + viewport.width() / 2) / old_width
        center_y_ratio = (vbar.value() + viewport.height() / 2) / old_height

        base_w = max(80, self.preview_base_width_px)
        base_h = max(60, self.preview_base_height_px)
        toolbar_hint = (
            self.toolbar.sizeHint()
            if self.toolbar is not None
            else self.canvas.sizeHint()
        )
        margins = self.window.plot_layout.contentsMargins()
        spacing = self.window.plot_layout.spacing()
        usable_w = max(1, viewport.width() - margins.left() - margins.right() - 24)
        usable_h = max(
            1,
            viewport.height()
            - toolbar_hint.height()
            - spacing
            - margins.top()
            - margins.bottom()
            - 24,
        )
        if self.window.fit_preview_check.isChecked():
            scale = min(usable_w / base_w, usable_h / base_h, 4.0)
            scale = max(scale, 0.1)
        else:
            scale = self.window.preview_zoom_spin.value() / 100.0
        self.window.preview_zoom_spin.setEnabled(
            not self.window.fit_preview_check.isChecked()
        )
        # Size from the exact figure dimensions. ``preview_base_*`` is integer
        # cached for fit calculations, but using it again here can compound
        # truncation enough to create a visible HiDPI pixel mismatch.
        canvas_w = max(80, round(self.preview_figure_width_in * PREVIEW_DPI * scale))
        canvas_h = max(60, round(self.preview_figure_height_in * PREVIEW_DPI * scale))
        logical_dpi = PREVIEW_DPI * scale
        device_pixel_ratio = max(float(self.canvas.device_pixel_ratio), 1.0)
        self.current_figure._original_dpi = logical_dpi
        self.current_figure.set_dpi(logical_dpi * device_pixel_ratio)
        self.current_figure.set_size_inches(
            self.preview_figure_width_in, self.preview_figure_height_in, forward=False
        )
        self.canvas.setFixedSize(canvas_w, canvas_h)
        self.window.interaction.refresh_annotation_artists()
        host_w = max(
            viewport.width(),
            canvas_w + margins.left() + margins.right(),
            toolbar_hint.width() + margins.left() + margins.right(),
        )
        host_h = max(
            viewport.height(),
            canvas_h
            + toolbar_hint.height()
            + spacing
            + margins.top()
            + margins.bottom(),
        )
        self.window.plot_host.setFixedSize(host_w, host_h)

        if center:
            QTimer.singleShot(0, self.center_preview)
        else:
            QTimer.singleShot(
                0, lambda: self.restore_preview_center(center_x_ratio, center_y_ratio)
            )
        self.canvas.draw_idle()

    def center_preview(self) -> None:
        hbar = self.window.plot_scroll.horizontalScrollBar()
        vbar = self.window.plot_scroll.verticalScrollBar()
        hbar.setValue((hbar.maximum() + hbar.minimum()) // 2)
        vbar.setValue((vbar.maximum() + vbar.minimum()) // 2)

    def restore_preview_center(self, x_ratio: float, y_ratio: float) -> None:
        viewport = self.window.plot_scroll.viewport().size()
        hbar = self.window.plot_scroll.horizontalScrollBar()
        vbar = self.window.plot_scroll.verticalScrollBar()
        x = int(self.window.plot_host.width() * x_ratio - viewport.width() / 2)
        y = int(self.window.plot_host.height() * y_ratio - viewport.height() / 2)
        hbar.setValue(max(hbar.minimum(), min(hbar.maximum(), x)))
        vbar.setValue(max(vbar.minimum(), min(vbar.maximum(), y)))

    def export_current_figure(self) -> None:
        filters = (
            "PNG (*.png);;PDF (*.pdf);;SVG (*.svg);;TIFF (*.tif *.tiff);;EPS (*.eps)"
        )
        path, selected_filter = QFileDialog.getSaveFileName(
            self.window,
            "Export figure",
            str(self.window.files.last_folder / "figure.png"),
            filters,
        )
        if not path:
            return
        path_obj = Path(path)
        if not path_obj.suffix:
            suffix_by_filter = {
                "PNG": ".png",
                "PDF": ".pdf",
                "SVG": ".svg",
                "TIFF": ".tiff",
                "EPS": ".eps",
            }
            suffix = next(
                (
                    value
                    for name, value in suffix_by_filter.items()
                    if selected_filter.startswith(name)
                ),
                ".png",
            )
            path_obj = path_obj.with_suffix(suffix)
        self.window.files.last_folder = path_obj.parent
        self.render_timer.stop()
        config = self.window.figure_editor.collect_plot_config()
        try:
            self.rendering.export_one(
                ExportJob(self._render_request(config, label=path_obj.stem), path_obj),
                retain_figure=True,
                accept=self._adopt_render_result,
            )
        except Exception as exc:
            message = f"Could not export {path_obj}:\n{exc}"
            QMessageBox.warning(self.window, "Export figure", message)
            self.window.set_status(f"Export failed: {exc}")
            return
        self.window.set_status(f"Exported figure: {path_obj}")

    def _ordered_graphs_with_folder(self) -> list[tuple[Graph, str]]:
        """Graphs in project-explorer (tree) order, each paired with the name of
        its nearest ancestor folder ("" when the graph sits under the root)."""
        ordered: list[tuple[Graph, str]] = []

        def walk(node: TreeNode, folder_name: str) -> None:
            for child in node.children:
                if child.type == "graph" and child.ref_id in self.session.graphs:
                    ordered.append((self.session.graphs[child.ref_id], folder_name))
                child_folder = child.name if child.type == "folder" else folder_name
                walk(child, child_folder)

        walk(self.session.tree_root, "")
        return ordered

    def export_all_figures(self) -> None:
        self.window.workspace.save_active_state()
        self._export_graphs(self._ordered_graphs_with_folder(), "Export all figures")

    def _selected_graphs_with_folder(self) -> list[tuple[Graph, str]]:
        selected_ids = set()
        for item in self.window.figure_tree.selectedItems():
            node = self.session.document.find_node(item.data(0, Qt.UserRole))
            if node is not None and node.type == "graph":
                selected_ids.add(node.ref_id)
        return [(graph, folder) for graph, folder in self._ordered_graphs_with_folder()
                if graph.id in selected_ids]

    def update_export_selection(self) -> None:
        count = len(self._selected_graphs_with_folder())
        self.window.export_selected_btn.setText(f"Export selected figures ({count})")
        self.window.export_selected_btn.setEnabled(count > 0)
        self.window.export_selected_action.setEnabled(count > 0)

    def export_selected_figures(self) -> None:
        self.window.workspace.save_active_state()
        self._export_graphs(self._selected_graphs_with_folder(), "Export selected figures")

    def _export_graphs(self, graphs: list[tuple[Graph, str]], title: str) -> None:
        if not graphs:
            QMessageBox.information(
                self.window, title, "No graphs to export. Select Graph entries in Project Explorer."
            )
            return

        folder = QFileDialog.getExistingDirectory(
            self.window, title, str(self.window.files.last_folder)
        )
        if not folder:
            return

        output_dir = Path(folder)
        self.window.files.last_folder = output_dir
        self.render_timer.stop()

        jobs: list[ExportJob] = []
        reserved_paths: set[Path] = set()
        for graph, folder_name in graphs:
            sheet = self.session.sheets.get(graph.sheet_id)
            if sheet is None:
                continue
            config = deepcopy(graph.plot_config)
            config.annotations = deepcopy(graph.annotations)
            graph_part = self._safe_export_filename(graph.name)
            if folder_name:
                base_name = f"{self._safe_export_filename(folder_name)}_{graph_part}"
            else:
                base_name = graph_part
            path = self._unique_export_path(
                output_dir / f"{base_name}.png", reserved_paths
            )
            reserved_paths.add(path)
            jobs.append(
                ExportJob(
                    RenderRequest(
                        dataframe=self._project_plot_dataframe(sheet.df),
                        config=config,
                        series_configs=tuple(
                            self._project_series_configs(graph, sheet.df)
                        ),
                        label=graph.name,
                    ),
                    path,
                )
            )

        report = self.rendering.export_many(jobs)
        warnings = list(report.warnings)
        warnings.extend(
            f"{failure.label}: export failed ({failure.error})"
            for failure in report.failed
        )
        message = f"Exported {report.succeeded} figures to {output_dir}"
        if warnings:
            message += f" | {warnings[0]}"
        self.window.set_status(message)

    def _project_plot_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        return plot_dataframe(df)

    def _project_column_name(self, df: pd.DataFrame, column: str) -> str:
        return column_name(df, column)

    def _project_nearest_left_x(self, df: pd.DataFrame, y_column: str) -> str:
        return nearest_left_x(df, y_column)

    def _project_series_configs(
        self, graph: Graph, df: pd.DataFrame
    ) -> list[SeriesConfig]:
        series_configs: list[SeriesConfig] = []
        for idx, y_column in enumerate(graph.checked_y):
            if y_column not in df.columns:
                continue
            x_column = self._project_nearest_left_x(df, y_column)
            if not x_column:
                continue
            series = deepcopy(
                graph.series_by_y.get(y_column)
                or default_series(x_column, y_column, idx)
            )
            series.x = x_column
            series.y = y_column
            series.label = self._project_column_name(df, y_column)
            series_configs.append(series)
        return series_configs

    def _safe_export_filename(self, name: str) -> str:
        safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" ._")
        return safe_name or "figure"

    def _unique_export_path(self, path: Path, reserved_paths: set[Path]) -> Path:
        candidate = path
        suffix = 2
        while candidate.exists() or candidate in reserved_paths:
            candidate = path.with_name(f"{path.stem}_{suffix}{path.suffix}")
            suffix += 1
        return candidate

    def close(self) -> None:
        """Stop scheduled renders and release the displayed figure."""
        self.render_timer.stop()
        if self.current_figure is not None:
            self.current_figure.clear()
            self.current_figure = None
