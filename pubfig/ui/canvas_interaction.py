"""Canvas gestures, annotation handles, hit testing, and dragging."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from matplotlib.patches import Ellipse, Rectangle
from matplotlib.transforms import Affine2D, IdentityTransform
from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QApplication

from ..annotation_geometry import rotated_endpoint
from ..plot_config import AnnotationConfig

if TYPE_CHECKING:
    from .main_window import GraphDrawerWindow


class CanvasInteractionController(QObject):
    """Canvas gestures, annotation handles, hit testing, and dragging."""

    def __init__(self, window: GraphDrawerWindow) -> None:
        super().__init__(window)
        self.window = window
        self.session = window.session
        self.annotation_handle_artists = []

        self.drag_annotation_index: int | None = None

        self.dragging_legend = False

        self.dragging_plot_box = False

        self.drag_legend_start: tuple[float, float] | None = None

        self.drag_legend_original: tuple[float, float] | None = None

        self.drag_plot_start_pixels: tuple[float, float] | None = None

        self.drag_plot_original_margins: tuple[float, float, float, float] | None = None

        self.drag_start: tuple[float, float] | None = None

        self.drag_mode: str | None = None

        self.drag_original: (
            tuple[float, float, float, float, float, float, float] | None
        ) = None

        self.drag_group_original: (
            dict[int, tuple[float, float, float, float]] | None
        ) = None

        self._hover_hint = ""

    def nudge_selected_annotations_from_key(self, event) -> bool:
        deltas = {
            Qt.Key_Left: (-1.0, 0.0),
            Qt.Key_Right: (1.0, 0.0),
            Qt.Key_Up: (0.0, 1.0),
            Qt.Key_Down: (0.0, -1.0),
        }
        delta = deltas.get(event.key())
        if delta is None or self.window.focus_widget_uses_text_shortcuts():
            return False
        indices = self.window.annotations_editor.selected_annotation_indices()
        if not indices:
            return False
        modifiers = event.modifiers()
        step = 0.005
        if modifiers & Qt.ShiftModifier:
            step = 0.02
        elif modifiers & Qt.ControlModifier:
            step = 0.001
        self.window.workspace.push_undo_baseline()
        dx = delta[0] * step
        dy = delta[1] * step
        for idx in indices:
            annotation = self.session.annotations[idx]
            annotation.x += dx
            annotation.y += dy
            annotation.x2 = annotation.x + annotation.width
            annotation.y2 = annotation.y + annotation.height
            if idx < len(self.window.preview.annotation_artists):
                self.update_annotation_artist(
                    annotation, self.window.preview.annotation_artists[idx]
                )
            self.window.annotations_editor.update_annotation_list_item(idx)
        current = self.window.annotation_settings.list_widget.currentRow()
        if current in indices:
            self.window.annotations_editor.update_annotation_inputs(current)
        self.draw_annotation_handles(redraw=False)
        if self.window.preview.canvas is not None:
            self.window.preview.canvas.draw_idle()
        self.window.workspace.update_undo_baseline()
        return True

    def handle_canvas_press(self, event) -> None:
        if event.button != 1 or event.x is None or event.y is None:
            return
        if self.window.preview.toolbar is not None and getattr(
            self.window.preview.toolbar, "mode", ""
        ):
            return
        if self.window.preview.legend_artist is not None:
            contains, _ = self.window.preview.legend_artist.contains(event)
            if contains and getattr(event, "dblclick", False):
                self.window.legend_editor.edit_legend_text()
                return
            if contains and self.start_legend_drag(event):
                return
        handle_hit = self.annotation_handle_hit_test(event.x, event.y)
        if handle_hit is not None:
            idx, mode = handle_hit
            self.start_annotation_drag(idx, event, mode)
            return
        idx = self.best_annotation_hit(event)
        if idx is not None:
            self.start_annotation_drag(idx, event, "move")
            return
        if self.start_plot_box_drag(event):
            return

    def annotation_hit_order(self) -> list[int]:
        indices = list(range(len(self.window.preview.annotation_artists) - 1, -1, -1))
        current = self.window.annotation_settings.list_widget.currentRow()
        if 0 <= current < len(self.window.preview.annotation_artists):
            indices = [current] + [idx for idx in indices if idx != current]
        return indices

    def best_annotation_hit(self, event) -> int | None:
        best_idx: int | None = None
        best_distance: float | None = None
        for idx in self.annotation_hit_order():
            distance = self.annotation_hit_distance(idx, event)
            if distance is None:
                continue
            if (
                best_distance is None
                or distance < best_distance - 1e-9
                or (abs(distance - best_distance) <= 1e-9 and idx > (best_idx or -1))
            ):
                best_idx = idx
                best_distance = distance
        return best_idx

    def start_plot_box_drag(self, event) -> bool:
        config = self.window.figure_editor.collect_plot_config()
        ax = (
            self.window.preview.current_figure.axes[0]
            if self.window.preview.current_figure is not None
            and self.window.preview.current_figure.axes
            else None
        )
        if not self.plot_box_drag_requested():
            return False
        if (
            not config.fixed_plot_area
            or ax is None
            or event.inaxes not in self.window.preview.current_figure.axes
        ):
            return False
        if event.x is None or event.y is None:
            return False
        remaining_width = config.width_mm - config.plot_width_mm
        remaining_height = config.height_mm - config.plot_height_mm
        if remaining_width < 0 or remaining_height < 0:
            self.window.set_status(
                "Plot box is larger than the canvas; cannot drag inside canvas."
            )
            return False
        self.window.workspace.push_current_undo_state()
        self.dragging_plot_box = True
        self.drag_plot_start_pixels = (float(event.x), float(event.y))
        self.drag_plot_original_margins = (
            config.plot_margin_left_mm,
            config.plot_margin_right_mm,
            config.plot_margin_top_mm,
            config.plot_margin_bottom_mm,
        )
        self.window.set_status("Dragging plot box.")
        return True

    def plot_box_drag_requested(self) -> bool:
        return bool(QApplication.keyboardModifiers() & Qt.AltModifier)

    def start_annotation_drag(self, idx: int, event, mode: str) -> None:
        self.drag_start = self.event_to_axes_fraction(event)
        if self.drag_start is None:
            return
        self.window.workspace.push_current_undo_state()
        selected = self.window.annotations_editor.selected_annotation_indices()
        group_move = (
            mode == "move"
            and self.annotation_group_drag_requested()
            and idx in selected
            and len(selected) > 1
        )
        annotation = self.session.annotations[idx]
        self.drag_annotation_index = idx
        self.drag_mode = mode
        self.drag_original = (
            annotation.x,
            annotation.y,
            annotation.x2,
            annotation.y2,
            annotation.width,
            annotation.height,
            annotation.angle,
        )
        if group_move:
            self.drag_group_original = {
                row: (
                    self.session.annotations[row].x,
                    self.session.annotations[row].y,
                    self.session.annotations[row].x2,
                    self.session.annotations[row].y2,
                )
                for row in selected
            }
            self.window.set_status(f"Dragging {len(selected)} annotations.")
        else:
            self.drag_group_original = None
            self.select_single_annotation(idx)
            self.window.set_status("Dragging annotation.")

    def annotation_group_drag_requested(self) -> bool:
        modifiers = QApplication.keyboardModifiers()
        return bool(modifiers & (Qt.ShiftModifier | Qt.ControlModifier))

    def select_single_annotation(self, idx: int) -> None:
        self.window.annotation_settings.list_widget.blockSignals(True)
        self.window.annotation_settings.list_widget.clearSelection()
        if 0 <= idx < self.window.annotation_settings.list_widget.count():
            self.window.annotation_settings.list_widget.setCurrentRow(idx)
            self.window.annotation_settings.list_widget.item(idx).setSelected(True)
        self.window.annotation_settings.list_widget.blockSignals(False)
        self.window.annotations_editor.update_annotation_inputs(idx)

    def start_legend_drag(self, event) -> bool:
        start = self.event_to_axes_fraction(
            event, self.window.annotations_editor.legend_axes()
        )
        anchor = self.legend_anchor_axes_fraction()
        if start is None or anchor is None:
            return False
        self.window.workspace.push_current_undo_state()
        self.dragging_legend = True
        self.drag_legend_start = start
        self.drag_legend_original = anchor
        self.window.set_status("Dragging legend.")
        return True

    def legend_anchor_axes_fraction(self) -> tuple[float, float] | None:
        ax = self.window.annotations_editor.legend_axes()
        if (
            ax is None
            or self.window.preview.legend_artist is None
            or self.window.preview.canvas is None
        ):
            return None
        renderer = self.window.preview.canvas.get_renderer()
        bbox = self.window.preview.legend_artist.get_window_extent(renderer=renderer)
        x, y = ax.transAxes.inverted().transform((bbox.x0, bbox.y1))
        return float(x), float(y)

    def annotation_hit_distance(self, index: int, event) -> float | None:
        if event.x is None or event.y is None:
            return None
        return self.annotation_hit_distance_for_point(
            index, float(event.x), float(event.y)
        )

    def annotation_hit_distance_for_point(
        self, index: int, x_px: float, y_px: float
    ) -> float | None:
        if index < 0 or index >= len(self.session.annotations):
            return None
        annotation = self.session.annotations[index]
        if annotation.kind in {"text", "textbox"}:
            return self.text_annotation_hit_distance(index, x_px, y_px)
        if annotation.kind in {"line", "arrow"}:
            return self.line_annotation_hit_distance(annotation, x_px, y_px)
        if annotation.kind == "circle":
            return self.circle_annotation_hit_distance(annotation, x_px, y_px)
        if annotation.kind == "box":
            return self.box_annotation_hit_distance(annotation, x_px, y_px)
        return None

    def text_annotation_hit_distance(
        self, index: int, x_px: float, y_px: float
    ) -> float | None:
        if (
            index >= len(self.window.preview.annotation_artists)
            or self.window.preview.canvas is None
        ):
            return None
        artist = self.window.preview.annotation_artists[index]
        renderer = self.window.preview.canvas.get_renderer()
        bbox_patch = getattr(artist, "get_bbox_patch", lambda: None)()
        bbox = (
            bbox_patch.get_window_extent(renderer=renderer)
            if bbox_patch is not None
            else artist.get_window_extent(renderer=renderer)
        )
        padding = 4.0
        if (
            bbox.x0 - padding <= x_px <= bbox.x1 + padding
            and bbox.y0 - padding <= y_px <= bbox.y1 + padding
        ):
            if bbox.x0 <= x_px <= bbox.x1 and bbox.y0 <= y_px <= bbox.y1:
                return 0.0
            dx = max(bbox.x0 - x_px, 0.0, x_px - bbox.x1)
            dy = max(bbox.y0 - y_px, 0.0, y_px - bbox.y1)
            return math.hypot(dx, dy)
        return None

    def line_annotation_hit_distance(
        self, annotation: AnnotationConfig, x_px: float, y_px: float
    ) -> float | None:
        start_x, start_y, width_px, height_px = (
            self.window.annotations_editor._annotation_display_geometry(annotation)
        )
        end_x, end_y = self._rotated_endpoint(
            start_x, start_y, width_px, height_px, annotation.angle
        )
        distance = self.point_to_segment_distance(
            x_px, y_px, start_x, start_y, end_x, end_y
        )
        return distance if distance <= 8.0 else None

    def box_annotation_hit_distance(
        self, annotation: AnnotationConfig, x_px: float, y_px: float
    ) -> float | None:
        start_x, start_y, width_px, height_px = (
            self.window.annotations_editor._annotation_display_geometry(annotation)
        )
        local_x, local_y = self.annotation_local_point(
            x_px, y_px, start_x, start_y, annotation.angle
        )
        left, right = sorted((0.0, width_px))
        bottom, top = sorted((0.0, height_px))
        inside = left <= local_x <= right and bottom <= local_y <= top
        border_distance = self.rectangle_border_distance(
            local_x, local_y, left, right, bottom, top
        )
        tolerance = 7.0
        if annotation.fill and inside:
            return 0.0
        if border_distance <= tolerance:
            return border_distance
        return None

    def circle_annotation_hit_distance(
        self, annotation: AnnotationConfig, x_px: float, y_px: float
    ) -> float | None:
        start_x, start_y, width_px, height_px = (
            self.window.annotations_editor._annotation_display_geometry(annotation)
        )
        center_x = start_x + width_px / 2
        center_y = start_y + height_px / 2
        local_x, local_y = self.annotation_local_point(
            x_px, y_px, center_x, center_y, annotation.angle
        )
        rx = abs(width_px) / 2
        ry = abs(height_px) / 2
        if rx <= 1e-9 or ry <= 1e-9:
            return None
        normalized = math.hypot(local_x / rx, local_y / ry)
        boundary_distance = abs(normalized - 1.0) * min(rx, ry)
        if annotation.fill and normalized <= 1.0:
            return 0.0
        return boundary_distance if boundary_distance <= 7.0 else None

    def annotation_local_point(
        self, x_px: float, y_px: float, origin_x: float, origin_y: float, angle: float
    ) -> tuple[float, float]:
        dx = x_px - origin_x
        dy = y_px - origin_y
        if not angle:
            return dx, dy
        radians = math.radians(-angle)
        cos_a = math.cos(radians)
        sin_a = math.sin(radians)
        return dx * cos_a - dy * sin_a, dx * sin_a + dy * cos_a

    @staticmethod
    def point_to_segment_distance(
        px: float, py: float, x1: float, y1: float, x2: float, y2: float
    ) -> float:
        dx = x2 - x1
        dy = y2 - y1
        length_sq = dx * dx + dy * dy
        if length_sq <= 1e-12:
            return math.hypot(px - x1, py - y1)
        t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length_sq))
        nearest_x = x1 + t * dx
        nearest_y = y1 + t * dy
        return math.hypot(px - nearest_x, py - nearest_y)

    @staticmethod
    def rectangle_border_distance(
        x: float, y: float, left: float, right: float, bottom: float, top: float
    ) -> float:
        if left <= x <= right and bottom <= y <= top:
            return min(abs(x - left), abs(x - right), abs(y - bottom), abs(y - top))
        nearest_x = max(left, min(right, x))
        nearest_y = max(bottom, min(top, y))
        return math.hypot(x - nearest_x, y - nearest_y)

    def annotation_handle_hit_test(
        self, x_px: float, y_px: float
    ) -> tuple[int, str] | None:
        idx = self.window.annotation_settings.list_widget.currentRow()
        if idx < 0 or idx >= len(self.session.annotations):
            return None
        handles = self.annotation_handle_points(self.session.annotations[idx])
        for mode, point in handles.items():
            if abs(x_px - point[0]) <= 8 and abs(y_px - point[1]) <= 8:
                return idx, mode
        return None

    def handle_canvas_motion(self, event) -> None:
        if self.dragging_legend:
            self.handle_legend_motion(event)
            return
        if self.dragging_plot_box:
            self.handle_plot_box_motion(event)
            return
        if (
            self.drag_annotation_index is None
            or self.drag_start is None
            or self.drag_original is None
        ):
            self._update_hover_hint(event)
            return
        current = self.event_to_axes_fraction(event)
        if current is None:
            return
        idx = self.drag_annotation_index
        if idx >= len(self.session.annotations) or idx >= len(
            self.window.preview.annotation_artists
        ):
            return
        x0, y0, x20, y20, w0, h0, angle0 = self.drag_original
        annotation = self.session.annotations[idx]
        artist = self.window.preview.annotation_artists[idx]
        dx = current[0] - self.drag_start[0]
        dy = current[1] - self.drag_start[1]
        if self.ctrl_is_pressed():
            if abs(dx) >= abs(dy):
                dy = 0.0
            else:
                dx = 0.0

        if self.drag_group_original and self.drag_mode == "move":
            for row, (gx, gy, gx2, gy2) in self.drag_group_original.items():
                if row >= len(self.session.annotations) or row >= len(
                    self.window.preview.annotation_artists
                ):
                    continue
                group_annotation = self.session.annotations[row]
                group_annotation.x = gx + dx
                group_annotation.y = gy + dy
                group_annotation.x2 = gx2 + dx
                group_annotation.y2 = gy2 + dy
                self.update_annotation_artist(
                    group_annotation, self.window.preview.annotation_artists[row]
                )
            self.draw_annotation_handles(redraw=False)
            self.window.preview.canvas.draw_idle()
            return

        if self.drag_mode == "resize":
            annotation.x = x0
            annotation.y = y0
            annotation.angle = angle0
            self.resize_annotation_from_event(
                annotation, event, angle0, self.ctrl_is_pressed()
            )
        elif self.drag_mode == "rotate":
            annotation.x = x0
            annotation.y = y0
            annotation.width = w0
            annotation.height = h0
            annotation.angle = self.rotate_annotation_from_event(
                annotation, event, w0, h0, self.ctrl_is_pressed()
            )
        else:
            annotation.x = x0 + dx
            annotation.y = y0 + dy
            annotation.x2 = x20 + dx
            annotation.y2 = y20 + dy
        annotation.x2 = annotation.x + annotation.width
        annotation.y2 = annotation.y + annotation.height
        self.update_annotation_artist(annotation, artist)
        self.draw_annotation_handles(redraw=False)
        self.window.preview.canvas.draw_idle()

    def _update_hover_hint(self, event) -> None:
        hint = ""
        if event.x is not None and event.y is not None:
            if self.window.preview.legend_artist is not None:
                contains, _ = self.window.preview.legend_artist.contains(event)
                if contains:
                    hint = "Legend: drag to move, double-click to edit entries, Ctrl constrains the drag."
            if (
                not hint
                and self.annotation_handle_hit_test(event.x, event.y) is not None
            ):
                hint = "Handle: drag to resize/rotate the selected annotation, Ctrl constrains."
            if not hint and self.best_annotation_hit(event) is not None:
                hint = "Annotation: drag to move, arrow keys nudge, Shift/Ctrl+drag moves the whole selection."
        if hint != self._hover_hint:
            self._hover_hint = hint
            if hint:
                self.window.set_status(hint)

    def handle_plot_box_motion(self, event) -> None:
        if (
            self.drag_plot_start_pixels is None
            or self.drag_plot_original_margins is None
            or self.window.preview.current_figure is None
            or self.window.preview.canvas is None
            or event.x is None
            or event.y is None
        ):
            return
        width_px = max(float(self.window.preview.canvas.width()), 1.0)
        height_px = max(float(self.window.preview.canvas.height()), 1.0)
        figure_width_mm = self.window.preview.current_figure.get_figwidth() * 25.4
        figure_height_mm = self.window.preview.current_figure.get_figheight() * 25.4
        dx_mm = (
            (float(event.x) - self.drag_plot_start_pixels[0])
            / width_px
            * figure_width_mm
        )
        dy_mm = (
            (float(event.y) - self.drag_plot_start_pixels[1])
            / height_px
            * figure_height_mm
        )
        left0, _, _, bottom0 = self.drag_plot_original_margins
        available_width = (
            self.session.plot_config.width_mm - self.session.plot_config.plot_width_mm
        )
        available_height = (
            self.session.plot_config.height_mm - self.session.plot_config.plot_height_mm
        )
        if available_width < 0 or available_height < 0:
            return
        left = max(0.0, min(available_width, left0 + dx_mm))
        bottom = max(0.0, min(available_height, bottom0 + dy_mm))
        right = available_width - left
        top = available_height - bottom
        self.window.preview.set_plot_margins(left, right, top, bottom)
        self.apply_current_plot_box_layout()
        self.refresh_annotation_artists()
        self.window.preview.canvas.draw_idle()

    def apply_current_plot_box_layout(self) -> None:
        if self.window.preview.current_figure is None:
            return
        total_width = max(
            self.session.plot_config.width_mm
            + self.session.plot_config.pad_left_mm
            + self.session.plot_config.pad_right_mm,
            1e-9,
        )
        total_height = max(
            self.session.plot_config.height_mm
            + self.session.plot_config.pad_top_mm
            + self.session.plot_config.pad_bottom_mm,
            1e-9,
        )
        left = (
            self.session.plot_config.pad_left_mm
            + self.session.plot_config.plot_margin_left_mm
        ) / total_width
        right = left + self.session.plot_config.plot_width_mm / total_width
        bottom = (
            self.session.plot_config.pad_bottom_mm
            + self.session.plot_config.plot_margin_bottom_mm
        ) / total_height
        top = bottom + self.session.plot_config.plot_height_mm / total_height
        if left < right and bottom < top:
            self.window.preview.current_figure.subplots_adjust(
                left=left, right=right, bottom=bottom, top=top
            )

    def refresh_annotation_artists(self) -> None:
        for annotation, artist in zip(
            self.session.annotations, self.window.preview.annotation_artists
        ):
            self.update_annotation_artist(annotation, artist)
        self.draw_annotation_handles(redraw=False)

    def handle_legend_motion(self, event) -> None:
        if (
            self.drag_legend_start is None
            or self.drag_legend_original is None
            or self.window.preview.legend_artist is None
        ):
            return
        ax = self.window.annotations_editor.legend_axes()
        current = self.event_to_axes_fraction(event, ax)
        if current is None or ax is None or self.window.preview.canvas is None:
            return
        dx = current[0] - self.drag_legend_start[0]
        dy = current[1] - self.drag_legend_start[1]
        if self.ctrl_is_pressed():
            if abs(dx) >= abs(dy):
                dy = 0.0
            else:
                dx = 0.0
        x = self.drag_legend_original[0] + dx
        y = self.drag_legend_original[1] + dy
        self.session.plot_config.legend_anchor_x = x
        self.session.plot_config.legend_anchor_y = y
        self.window.preview.legend_artist._loc = 2
        self.window.preview.legend_artist.set_bbox_to_anchor(
            (x, y), transform=ax.transAxes
        )
        self.window.preview.canvas.draw_idle()

    def event_to_axes_fraction(self, event, ax=None) -> tuple[float, float] | None:
        if ax is None:
            ax = self.window.annotations_editor.annotation_axes()
        if ax is None or event.x is None or event.y is None:
            return None
        x, y = ax.transAxes.inverted().transform((event.x, event.y))
        return float(x), float(y)

    def _rotated_endpoint(
        self,
        start_x: float,
        start_y: float,
        width_px: float,
        height_px: float,
        angle: float,
    ) -> tuple[float, float]:
        return rotated_endpoint(start_x, start_y, width_px, height_px, angle)

    def ctrl_is_pressed(self) -> bool:
        return bool(QApplication.keyboardModifiers() & Qt.ControlModifier)

    def resize_annotation_from_event(
        self, annotation: AnnotationConfig, event, angle: float, constrain: bool
    ) -> None:
        ax = self.window.annotations_editor.annotation_axes()
        if ax is None or event.x is None or event.y is None:
            return
        axes_bbox = ax.get_window_extent()
        unit_px = max(min(axes_bbox.width, axes_bbox.height), 1.0)
        start_x, start_y, _, _ = (
            self.window.annotations_editor._annotation_display_geometry(annotation)
        )
        vector = (float(event.x) - start_x, float(event.y) - start_y)
        if angle:
            vector = Affine2D().rotate_deg_around(0.0, 0.0, -angle).transform(vector)
        if constrain:
            vector = (
                (vector[0], 0.0)
                if abs(vector[0]) >= abs(vector[1])
                else (0.0, vector[1])
            )
        annotation.width = float(vector[0]) / unit_px
        annotation.height = float(vector[1]) / unit_px

    def rotate_annotation_from_event(
        self,
        annotation: AnnotationConfig,
        event,
        width: float,
        height: float,
        constrain: bool,
    ) -> float:
        if event.x is None or event.y is None:
            return annotation.angle
        start_x, start_y, _, _ = (
            self.window.annotations_editor._annotation_display_geometry(annotation)
        )
        target_angle = math.degrees(
            math.atan2(float(event.y) - start_y, float(event.x) - start_x)
        )
        base_angle = math.degrees(math.atan2(height, width)) if width or height else 0.0
        angle = target_angle - base_angle
        if constrain:
            angle = round(angle / 90.0) * 90.0
        return angle

    def update_annotation_artist(self, annotation: AnnotationConfig, artist) -> None:
        if annotation.kind == "arrow":
            start_x, start_y, width_px, height_px = (
                self.window.annotations_editor._annotation_display_geometry(annotation)
            )
            end_x, end_y = self._rotated_endpoint(
                start_x, start_y, width_px, height_px, annotation.angle
            )
            artist.xy = (end_x, end_y)
            artist.set_position((start_x, start_y))
        elif annotation.kind == "line":
            start_x, start_y, width_px, height_px = (
                self.window.annotations_editor._annotation_display_geometry(annotation)
            )
            end_x, end_y = self._rotated_endpoint(
                start_x, start_y, width_px, height_px, annotation.angle
            )
            artist.set_positions((start_x, start_y), (end_x, end_y))
        elif annotation.kind == "box":
            start_x, start_y, width_px, height_px = (
                self.window.annotations_editor._annotation_display_geometry(annotation)
            )
            artist.set_xy((start_x, start_y))
            artist.set_width(width_px)
            artist.set_height(height_px)
            artist.set_transform(
                Affine2D().rotate_deg_around(start_x, start_y, annotation.angle)
                + IdentityTransform()
            )
        elif annotation.kind == "circle":
            start_x, start_y, width_px, height_px = (
                self.window.annotations_editor._annotation_display_geometry(annotation)
            )
            artist.center = (start_x + width_px / 2, start_y + height_px / 2)
            artist.width = abs(width_px)
            artist.height = abs(height_px)
            artist.angle = annotation.angle
        else:
            artist.set_position((annotation.x, annotation.y))
            artist.set_rotation(annotation.angle)

    def annotation_handle_points(
        self, annotation: AnnotationConfig
    ) -> dict[str, tuple[float, float]]:
        start_x, start_y, width_px, height_px = (
            self.window.annotations_editor._annotation_display_geometry(annotation)
        )
        resize_x, resize_y = self._rotated_endpoint(
            start_x, start_y, width_px, height_px, annotation.angle
        )
        top_x, top_y = self._rotated_endpoint(
            start_x, start_y, width_px * 0.5, height_px, annotation.angle
        )
        return {"resize": (resize_x, resize_y), "rotate": (top_x, top_y + 24)}

    def draw_annotation_handles(self, redraw: bool = True) -> None:
        for handle in self.annotation_handle_artists:
            try:
                handle.remove()
            except (NotImplementedError, ValueError):
                # The owning Figure may already have been replaced.
                pass
        self.annotation_handle_artists = []
        ax = self.window.annotations_editor.annotation_axes()
        idx = self.window.annotation_settings.list_widget.currentRow()
        if ax is None or idx < 0 or idx >= len(self.session.annotations):
            if redraw and self.window.preview.canvas is not None:
                self.window.preview.canvas.draw_idle()
            return
        annotation = self.session.annotations[idx]
        start_x, start_y, width_px, height_px = (
            self.window.annotations_editor._annotation_display_geometry(annotation)
        )
        if annotation.kind == "circle":
            outline = Ellipse(
                (start_x + width_px / 2, start_y + height_px / 2),
                width=abs(width_px),
                height=abs(height_px),
                angle=annotation.angle,
                linewidth=0.8,
                edgecolor="#222222",
                facecolor="none",
                linestyle="--",
                transform=IdentityTransform(),
            )
        else:
            outline = Rectangle(
                (start_x, start_y),
                width_px,
                height_px,
                linewidth=0.8,
                edgecolor="#222222",
                facecolor="none",
                linestyle="--",
                transform=Affine2D().rotate_deg_around(
                    start_x, start_y, annotation.angle
                )
                + IdentityTransform(),
            )
        self.annotation_handle_artists.append(outline)
        ax.add_artist(outline)
        for mode, (x_px, y_px) in self.annotation_handle_points(annotation).items():
            size = 8 if mode == "resize" else 7
            handle = Rectangle(
                (x_px - size / 2, y_px - size / 2),
                size,
                size,
                linewidth=0.8,
                edgecolor="#111111",
                facecolor="white",
                transform=IdentityTransform(),
            )
            self.annotation_handle_artists.append(handle)
            ax.add_artist(handle)
        for handle in self.annotation_handle_artists:
            handle.set_clip_on(False)
            handle.set_in_layout(False)
        if redraw and self.window.preview.canvas is not None:
            self.window.preview.canvas.draw_idle()

    def handle_canvas_scroll(self, event) -> None:
        zoom_delta = self.preview_zoom_delta(event)
        if zoom_delta == 0:
            return
        if not self.ctrl_is_pressed():
            return
        if self.window.fit_preview_check.isChecked():
            self.window.fit_preview_check.setChecked(False)
        new_zoom = self.window.preview_zoom_spin.value() + zoom_delta
        self.window.preview_zoom_spin.setValue(
            max(
                self.window.preview_zoom_spin.minimum(),
                min(self.window.preview_zoom_spin.maximum(), new_zoom),
            )
        )
        self.window.set_status(
            f"Preview zoom: {self.window.preview_zoom_spin.value()}%"
        )

    def preview_zoom_delta(self, event) -> int:
        gui_event = getattr(event, "guiEvent", None)
        if gui_event is not None and hasattr(gui_event, "angleDelta"):
            delta = gui_event.angleDelta().y()
            if delta:
                return 10 if delta > 0 else -10

        button = str(getattr(event, "button", "")).lower()
        if button == "up":
            return 10
        if button == "down":
            return -10

        step = getattr(event, "step", 0)
        if step > 0:
            return 10
        if step < 0:
            return -10
        return 0

    def handle_canvas_release(self, event) -> None:
        if self.dragging_legend:
            self.dragging_legend = False
            self.drag_legend_start = None
            self.drag_legend_original = None
            self.window.workspace.update_undo_baseline()
            self.window.set_status("Moved legend.")
            return
        if self.dragging_plot_box:
            self.dragging_plot_box = False
            self.drag_plot_start_pixels = None
            self.drag_plot_original_margins = None
            self.window.preview.render_plot()
            self.window.workspace.update_undo_baseline()
            self.window.set_status("Moved plot box.")
            return
        if self.drag_annotation_index is None:
            return
        self.window.annotations_editor.update_annotation_inputs(
            self.drag_annotation_index
        )
        self.drag_annotation_index = None
        self.drag_start = None
        self.drag_mode = None
        self.drag_original = None
        self.drag_group_original = None
        self.window.workspace.update_undo_baseline()
        self.window.set_status("Moved annotation.")
