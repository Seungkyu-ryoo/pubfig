"""PySide6 application for Graph_drawer."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import re

import matplotlib as mpl
from matplotlib.colors import to_hex
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.patches import Ellipse, Rectangle
from matplotlib.transforms import Affine2D, Bbox, IdentityTransform
import pandas as pd
from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from data_parser import ParsedTable, parse_clipboard_grid, parse_table_text
from plot_config import (
    ANNOTATION_LINE_STYLES,
    MARKERS,
    PLOT_TYPES,
    PRESETS,
    RECOMMENDED_PALETTES,
    AnnotationConfig,
    PlotConfig,
    SeriesConfig,
    apply_preset,
    dataframe_from_payload,
    dataframe_to_payload,
    default_series,
    project_from_payload,
)
from renderer import export_figure, render_figure


ROLE_ROW = 0
NAME_ROW = 1
DATA_START_ROW = 2
PREVIEW_DPI = 100
PLOT_RATIO_PRESETS = {
    "Current": None,
    "1:1": 1.0,
    "4:3": 4 / 3,
    "3:4": 3 / 4,
    "3:2": 3 / 2,
    "2:3": 2 / 3,
    "16:9": 16 / 9,
    "9:16": 9 / 16,
    "2:1": 2.0,
    "1:2": 0.5,
    "Golden 1.618:1": 1.618,
    "Golden 1:1.618": 1 / 1.618,
    "A-series sqrt2:1": math.sqrt(2),
    "A-series 1:sqrt2": 1 / math.sqrt(2),
}

PLOT_TYPE_HELP = {
    "line": "Continuous line for ordered X-Y data.",
    "scatter": "Markers only; useful for point distributions.",
    "line+marker": "Line with markers; common for publication curves.",
    "bar": "Bars for discrete or categorical comparisons.",
    "step": "Stair-step trace for binned or piecewise-constant data.",
    "area": "Filled area under a curve; useful for cumulative or contribution plots.",
    "stem": "Vertical sticks from baseline; useful for peaks or impulse-like data.",
}


@dataclass
class ProjectFigure:
    name: str
    df: pd.DataFrame
    plot_config: PlotConfig
    series_by_y: dict[str, SeriesConfig]
    checked_y: list[str]
    annotations: list[AnnotationConfig]


class SpreadsheetTableWidget(QTableWidget):
    pasted = Signal(int, int, str)
    copied = Signal(str)
    delete_requested = Signal()

    def keyPressEvent(self, event):
        if event.modifiers() & Qt.ControlModifier and event.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            self.move_to_data_edge(event.key())
            return
        if event.matches(QKeySequence.Paste):
            text = QApplication.clipboard().text()
            if text:
                self.pasted.emit(max(self.currentRow(), 0), max(self.currentColumn(), 0), text)
            return
        if event.matches(QKeySequence.Copy):
            QApplication.clipboard().setText(self.selected_text())
            self.copied.emit("Copied selected cells.")
            return
        if event.matches(QKeySequence.Cut):
            QApplication.clipboard().setText(self.selected_text())
            self.delete_requested.emit()
            self.copied.emit("Cut selected cells.")
            return
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_requested.emit()
            return
        super().keyPressEvent(event)

    def selected_text(self) -> str:
        ranges = self.selectedRanges()
        if not ranges:
            return ""
        selected = ranges[0]
        rows: list[str] = []
        for row in range(selected.topRow(), selected.bottomRow() + 1):
            cells: list[str] = []
            for col in range(selected.leftColumn(), selected.rightColumn() + 1):
                item = self.item(row, col)
                cells.append("" if item is None else item.text())
            rows.append("\t".join(cells))
        return "\n".join(rows)

    def move_to_data_edge(self, key: int) -> None:
        if self.rowCount() <= 0 or self.columnCount() <= 0:
            return
        row = max(0, self.currentRow())
        col = max(0, self.currentColumn())
        if row < 0 or col < 0:
            return

        directions = {
            Qt.Key_Left: (0, -1),
            Qt.Key_Right: (0, 1),
            Qt.Key_Up: (-1, 0),
            Qt.Key_Down: (1, 0),
        }
        dr, dc = directions[key]
        target_row, target_col = self._data_edge_cell(row, col, dr, dc)
        self.setCurrentCell(target_row, target_col)
        item = self.item(target_row, target_col)
        if item is not None:
            self.scrollToItem(item)

    def _data_edge_cell(self, row: int, col: int, dr: int, dc: int) -> tuple[int, int]:
        last_row = self.rowCount() - 1
        last_col = self.columnCount() - 1

        def in_bounds(r: int, c: int) -> bool:
            return 0 <= r <= last_row and 0 <= c <= last_col

        def filled(r: int, c: int) -> bool:
            item = self.item(r, c)
            return item is not None and bool(item.text().strip())

        next_row = row + dr
        next_col = col + dc
        if not in_bounds(next_row, next_col):
            return row, col

        current_filled = filled(row, col)
        next_filled = filled(next_row, next_col)

        if current_filled and next_filled:
            while in_bounds(next_row + dr, next_col + dc) and filled(next_row + dr, next_col + dc):
                next_row += dr
                next_col += dc
            return next_row, next_col

        while in_bounds(next_row, next_col) and not filled(next_row, next_col):
            edge_row = next_row
            edge_col = next_col
            next_row += dr
            next_col += dc
        if in_bounds(next_row, next_col):
            return next_row, next_col
        return edge_row, edge_col


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event) -> None:
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event) -> None:
        event.ignore()


class NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event) -> None:
        event.ignore()


class AnnotationTextEdit(QPlainTextEdit):
    def __init__(self) -> None:
        super().__init__()
        self.setMaximumHeight(70)
        self.setTabChangesFocus(True)

    def text(self) -> str:
        return self.toPlainText()

    def setText(self, value: str) -> None:
        self.setPlainText(value)

    def insert(self, value: str) -> None:
        self.insertPlainText(value)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ShiftModifier:
            self.insertPlainText("\n")
            return
        super().keyPressEvent(event)


class GraphDrawerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Graph_drawer")
        self.resize(1500, 900)

        self.df = pd.DataFrame()
        self.plot_config = PlotConfig()
        self.annotations: list[AnnotationConfig] = []
        self.annotation_artists = []
        self.annotation_handle_artists = []
        self.legend_artist = None
        self.drag_annotation_index: int | None = None
        self.dragging_legend = False
        self.dragging_plot_box = False
        self.drag_legend_start: tuple[float, float] | None = None
        self.drag_legend_original: tuple[float, float] | None = None
        self.drag_plot_start_pixels: tuple[float, float] | None = None
        self.drag_plot_original_margins: tuple[float, float, float, float] | None = None
        self.drag_start: tuple[float, float] | None = None
        self.drag_start_pixels: tuple[float, float] | None = None
        self.drag_mode: str | None = None
        self.drag_original: tuple[float, float, float, float, float, float, float] | None = None
        self.drag_group_original: dict[int, tuple[float, float, float, float]] | None = None
        self.loading_annotation_inputs = False
        self.annotation_clipboard: dict | None = None
        self.project_figures: list[ProjectFigure] = []
        self.active_figure_index = -1
        self.loading_project_figure = False
        self.style_clipboard: dict | None = None
        self.special_text_target: QWidget | None = None
        self.undo_stack: list[dict] = []
        self.undo_baseline: dict | None = None
        self.restoring_undo = False
        self.updating_plot_ratio = False
        self.series_by_y: dict[str, SeriesConfig] = {}
        self.current_figure: Figure | None = None
        self.last_folder = Path.cwd()
        self.current_project_path: Path | None = None
        self.fit_preview = True
        self.preview_zoom = 100
        self._is_modified = False

        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(200)
        self.render_timer.timeout.connect(self.render_plot)

        self._build_ui()
        self._connect_signals()
        self.create_blank_sheet()
        self.project_figures = [self.capture_current_figure("Figure 1")]
        self.active_figure_index = 0
        self.refresh_figure_list()
        self.undo_baseline = self.current_workspace_snapshot()
        QApplication.instance().installEventFilter(self)

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.Save):
            self.save_project()
            return
        if event.matches(QKeySequence.Undo):
            self.undo_workspace()
            return
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.delete_selected_annotation_from_key():
            return
        if self.focus_widget_uses_text_shortcuts():
            super().keyPressEvent(event)
            return
        if event.matches(QKeySequence.Copy) and self.copy_selected_annotation():
            return
        if event.matches(QKeySequence.Paste) and self.paste_annotation():
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.FocusIn and watched in self.special_text_fields():
            self.special_text_target = watched
        if event.type() == QEvent.KeyPress:
            if event.matches(QKeySequence.Save):
                self.save_project()
                return True
            if event.matches(QKeySequence.Undo):
                self.undo_workspace()
                return True
            if event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.delete_selected_annotation_from_key():
                return True
        if event.type() == QEvent.KeyPress and watched in (getattr(self, "annotation_list", None), getattr(self, "canvas", None)):
            if event.matches(QKeySequence.Copy) and self.copy_selected_annotation():
                return True
            if event.matches(QKeySequence.Paste) and self.paste_annotation():
                return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event) -> None:
        if self._is_modified:
            reply = QMessageBox.question(
                self,
                "저장하시겠습니까?",
                "저장되지 않은 변경사항이 있습니다. 저장하시겠습니까?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            )
            if reply == QMessageBox.Save:
                self.save_project()
                if self._is_modified:
                    event.ignore()
                    return
            elif reply == QMessageBox.Cancel:
                event.ignore()
                return
        event.accept()

    def focus_widget_uses_text_shortcuts(self) -> bool:
        focus = QApplication.focusWidget()
        return isinstance(focus, (QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, SpreadsheetTableWidget))

    def delete_selected_annotation_from_key(self) -> bool:
        if self.focus_widget_uses_text_shortcuts():
            return False
        if not self.selected_annotation_indices():
            return False
        self.remove_selected_annotation()
        return True

    def special_text_fields(self) -> tuple[QWidget, ...]:
        fields = []
        for name in ("annotation_text_edit", "title_edit", "x_label_edit", "y_label_edit", "y2_label_edit", "series_label_edit"):
            field = getattr(self, name, None)
            if field is not None:
                fields.append(field)
        return tuple(fields)

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(8, 8, 8, 8)
        self.setCentralWidget(root)

        splitter = QSplitter(Qt.Horizontal)
        root_layout.addWidget(splitter, 1)

        splitter.addWidget(self._build_data_panel())
        splitter.addWidget(self._build_plot_panel())
        splitter.addWidget(self._build_settings_panel())
        splitter.setSizes([360, 820, 360])

        self.statusBar().showMessage("Ready")

    def _build_data_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        project_box = QGroupBox("Project Explorer")
        project_layout = QVBoxLayout(project_box)
        self.figure_list = QListWidget()
        self.figure_list.setMinimumHeight(80)
        project_layout.addWidget(self.figure_list)
        project_buttons = QHBoxLayout()
        self.new_figure_btn = QPushButton("New")
        self.duplicate_figure_btn = QPushButton("Duplicate")
        self.rename_figure_btn = QPushButton("Rename")
        self.delete_figure_btn = QPushButton("Delete")
        project_buttons.addWidget(self.new_figure_btn)
        project_buttons.addWidget(self.duplicate_figure_btn)
        project_buttons.addWidget(self.rename_figure_btn)
        project_buttons.addWidget(self.delete_figure_btn)
        project_layout.addLayout(project_buttons)
        project_move_buttons = QHBoxLayout()
        self.move_figure_up_btn = QPushButton("Up")
        self.move_figure_down_btn = QPushButton("Down")
        project_move_buttons.addWidget(self.move_figure_up_btn)
        project_move_buttons.addWidget(self.move_figure_down_btn)
        project_layout.addLayout(project_move_buttons)

        self.table = SpreadsheetTableWidget()
        self.table.pasted.connect(self.handle_table_paste)
        self.table.copied.connect(self.set_status)
        self.table.delete_requested.connect(self.clear_selected_cells)
        self.table.itemChanged.connect(self.handle_table_item_changed)
        self.table.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.AnyKeyPressed
        )
        self.table.setSelectionBehavior(QTableWidget.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_table_menu)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setMinimumSectionSize(20)
        self.table.verticalHeader().setVisible(True)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.verticalHeader().setMinimumSectionSize(18)

        self.data_splitter = QSplitter(Qt.Vertical)
        self.data_splitter.addWidget(project_box)
        self.data_splitter.addWidget(self.table)
        self.data_splitter.setChildrenCollapsible(False)
        self.data_splitter.setStretchFactor(0, 0)
        self.data_splitter.setStretchFactor(1, 1)
        self.data_splitter.setSizes([260, 520])
        layout.addWidget(self.data_splitter, 1)

        self.data_info = QLabel("No data")
        layout.addWidget(self.data_info)
        return panel

    def _build_plot_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 0, 8, 0)

        self.plot_scroll = QScrollArea()
        self.plot_scroll.setWidgetResizable(False)
        self.plot_host = QWidget()
        self.plot_host.setStyleSheet("background-color: #e8e8e8;")
        self.plot_layout = QVBoxLayout(self.plot_host)
        self.plot_layout.setContentsMargins(8, 8, 8, 8)
        self.plot_layout.setAlignment(Qt.AlignCenter)
        self.plot_scroll.setWidget(self.plot_host)
        layout.addWidget(self.plot_scroll, 1)

        self.canvas: FigureCanvasQTAgg | None = None
        self.toolbar: NavigationToolbar2QT | None = None
        self.preview_base_width_px = 0
        self.preview_base_height_px = 0
        self.preview_plot_height_px = 0
        self.preview_figure_width_in = 4.0
        self.preview_figure_height_in = 3.0
        preview_row = QHBoxLayout()
        self.fit_preview_check = QCheckBox("Fit preview")
        self.fit_preview_check.setChecked(True)
        self.preview_zoom_spin = self._int_spin(100, 25, 400)
        preview_row.addWidget(self.fit_preview_check)
        preview_row.addWidget(QLabel("Zoom %"))
        preview_row.addWidget(self.preview_zoom_spin)
        self.center_preview_btn = QPushButton("Center")
        preview_row.addWidget(self.center_preview_btn)
        preview_row.addStretch(1)
        layout.addLayout(preview_row)
        self._replace_canvas(Figure(figsize=(4, 3), dpi=100))
        return panel

    def _build_settings_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(self._build_column_box())
        layout.addWidget(self._build_series_box())
        layout.addWidget(self._build_annotation_box())
        layout.addWidget(self._build_figure_box())
        layout.addWidget(self._build_special_chars_box())
        layout.addWidget(self._build_style_copy_box())
        layout.addWidget(self._build_export_box())
        layout.addStretch(1)
        scroll.setWidget(panel)
        return scroll

    def _build_column_box(self) -> QGroupBox:
        box = QGroupBox("Origin-style columns")
        layout = QFormLayout(box)
        self.y_list = QListWidget()
        self.y_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.y_list.setMaximumHeight(140)
        hint = QLabel("Set table Role row to X/Y. Each Y uses the nearest X on its left.")
        hint.setWordWrap(True)
        layout.addRow(hint)
        layout.addRow("Plot Y", self.y_list)
        return box

    def _build_series_box(self) -> QGroupBox:
        box = QGroupBox("Series style")
        layout = QVBoxLayout(box)

        self.style_target_combo = NoWheelComboBox()
        self.series_label_edit = QLineEdit()
        self.y_axis_combo = NoWheelComboBox()
        self.y_axis_combo.addItems(["left", "right"])
        self.plot_type_combo = NoWheelComboBox()
        self.plot_type_combo.addItems(PLOT_TYPES)
        self.plot_type_help = QLabel(PLOT_TYPE_HELP["line"])
        self.plot_type_help.setWordWrap(True)
        self.marker_combo = NoWheelComboBox()
        self.marker_combo.addItems(MARKERS)
        self.line_width_spin = self._double_spin(1.0, 0.1, 10.0, 2)
        self.marker_size_spin = self._double_spin(4.0, 0.0, 30.0, 1)
        self.series_y_offset_spin = self._double_spin(0.0, -1e9, 1e9, 4)
        self.color_btn = QPushButton("#4477AA")
        self.color_btn.setStyleSheet("background-color: #4477AA; color: white;")
        self.series_alpha_spin = self._double_spin(1.0, 0.0, 1.0, 2)
        self.show_in_legend_check = QCheckBox("Show in legend")
        self.show_in_legend_check.setChecked(True)
        self.cmap_combo = NoWheelComboBox()
        self.cmap_combo.addItems(["viridis", "plasma", "inferno", "magma", "cividis", "turbo", "coolwarm", "Spectral", "rainbow"])
        self.cmap_start_spin = self._double_spin(0.05, 0.0, 1.0, 3)
        self.cmap_end_spin = self._double_spin(0.95, 0.0, 1.0, 3)
        self.apply_cmap_btn = QPushButton("Apply colormap to plotted series")
        self.palette_combo = NoWheelComboBox()
        self.palette_combo.addItems(list(RECOMMENDED_PALETTES.keys()))
        self.apply_palette_btn = QPushButton("Apply recommended palette")

        tabs = QTabWidget()
        layout.addWidget(tabs)

        style_tab = QWidget()
        style_form = QFormLayout(style_tab)
        style_form.addRow("Edit series", self.style_target_combo)
        style_form.addRow("Label", self.series_label_edit)
        style_form.addRow("Y axis", self.y_axis_combo)
        style_form.addRow("Type", self.plot_type_combo)
        style_form.addRow(self.plot_type_help)
        style_form.addRow("Marker", self.marker_combo)
        style_form.addRow("Line width", self.line_width_spin)
        style_form.addRow("Marker size", self.marker_size_spin)
        style_form.addRow("Y offset", self.series_y_offset_spin)
        style_form.addRow("Color", self.color_btn)
        style_form.addRow("Alpha", self.series_alpha_spin)
        style_form.addRow(self.show_in_legend_check)
        tabs.addTab(style_tab, "Style")

        cmap_tab = QWidget()
        cmap_layout = QVBoxLayout(cmap_tab)
        cmap_layout.setContentsMargins(4, 4, 4, 4)

        self.cmap_column_list = QListWidget()
        self.cmap_column_list.setMaximumHeight(80)
        sel_row = QHBoxLayout()
        self.cmap_select_all_btn = QPushButton("All")
        self.cmap_select_none_btn = QPushButton("None")
        sel_row.addWidget(self.cmap_select_all_btn)
        sel_row.addWidget(self.cmap_select_none_btn)

        self.cmap_alpha_only_check = QCheckBox("Alpha only (same color, vary alpha)")

        self.cmap_alpha_section = QWidget()
        alpha_form = QFormLayout(self.cmap_alpha_section)
        alpha_form.setContentsMargins(0, 0, 0, 0)
        self.cmap_base_color_btn = QPushButton("#4477AA")
        self.cmap_base_color_btn.setStyleSheet("background-color: #4477AA; color: white;")
        self.cmap_alpha_start_spin = self._double_spin(0.2, 0.0, 1.0, 2)
        self.cmap_alpha_end_spin = self._double_spin(1.0, 0.0, 1.0, 2)
        self.cmap_alpha_dist_combo = NoWheelComboBox()
        self.cmap_alpha_dist_combo.addItems(["Linear", "Geometric", "Sqrt", "Power²", "Log"])
        self.cmap_alpha_dist_combo.setCurrentText("Geometric")
        alpha_form.addRow("Base color", self.cmap_base_color_btn)
        alpha_form.addRow("Alpha start", self.cmap_alpha_start_spin)
        alpha_form.addRow("Alpha end", self.cmap_alpha_end_spin)
        alpha_form.addRow("Distribution", self.cmap_alpha_dist_combo)

        self.cmap_color_section = QWidget()
        color_form = QFormLayout(self.cmap_color_section)
        color_form.setContentsMargins(0, 0, 0, 0)
        color_form.addRow("Colormap", self.cmap_combo)
        color_form.addRow("Stretch start", self.cmap_start_spin)
        color_form.addRow("Stretch end", self.cmap_end_spin)

        cmap_layout.addWidget(QLabel("Apply to:"))
        cmap_layout.addWidget(self.cmap_column_list)
        cmap_layout.addLayout(sel_row)
        cmap_layout.addWidget(self.cmap_alpha_only_check)
        cmap_layout.addWidget(self.cmap_alpha_section)
        cmap_layout.addWidget(self.cmap_color_section)
        cmap_layout.addWidget(self.apply_cmap_btn)
        palette_form = QFormLayout()
        palette_form.setContentsMargins(0, 0, 0, 0)
        palette_form.addRow("Recommended palette", self.palette_combo)
        cmap_layout.addLayout(palette_form)
        cmap_layout.addWidget(self.apply_palette_btn)
        self.cmap_alpha_section.setVisible(False)
        tabs.addTab(cmap_tab, "Colormap")
        return box

    def _build_annotation_box(self) -> QGroupBox:
        box = QGroupBox("Annotations")
        layout = QVBoxLayout(box)

        self.annotation_list = QListWidget()
        self.annotation_list.setMinimumHeight(120)
        self.annotation_list.setMaximumHeight(180)
        self.annotation_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.annotation_list.installEventFilter(self)
        self.annotation_kind_combo = NoWheelComboBox()
        self.annotation_kind_combo.addItems(["text", "line", "arrow", "box", "circle", "textbox"])
        self.annotation_text_edit = AnnotationTextEdit()
        self.annotation_x_spin = self._double_spin(0.0, -1e12, 1e12, 4)
        self.annotation_y_spin = self._double_spin(0.0, -1e12, 1e12, 4)
        self.annotation_x2_spin = self._double_spin(0.0, -1e12, 1e12, 4)
        self.annotation_y2_spin = self._double_spin(0.0, -1e12, 1e12, 4)
        self.annotation_w_spin = self._double_spin(0.15, -10.0, 10.0, 4)
        self.annotation_h_spin = self._double_spin(0.15, -10.0, 10.0, 4)
        self.annotation_angle_spin = self._double_spin(0.0, -360.0, 360.0, 1)
        self.annotation_font_spin = self._int_spin(8, 4, 60)
        self.annotation_arrow_head_spin = self._double_spin(12.0, 1.0, 80.0, 1)
        self.annotation_line_style_combo = NoWheelComboBox()
        self.annotation_line_style_combo.addItems(ANNOTATION_LINE_STYLES)
        self.annotation_alpha_spin = self._double_spin(1.0, 0.0, 1.0, 2)
        self.annotation_fill_check = QCheckBox("Fill shape")
        self.annotation_color_btn = QPushButton("#000000")
        self.annotation_color_btn.setStyleSheet("background-color: #000000; color: white;")
        self.add_annotation_btn = QPushButton("Add annotation")
        self.remove_annotation_btn = QPushButton("Remove selected")

        layout.addWidget(self.annotation_list)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        text_tab = QWidget()
        text_form = QFormLayout(text_tab)
        text_form.addRow("Type", self.annotation_kind_combo)
        text_form.addRow("Text", self.annotation_text_edit)
        text_form.addRow(self.add_annotation_btn)
        text_form.addRow(self.remove_annotation_btn)
        tabs.addTab(text_tab, "Text")

        position_tab = QWidget()
        position_form = QFormLayout(position_tab)
        position_form.addRow("X (axes 0-1)", self.annotation_x_spin)
        position_form.addRow("Y (axes 0-1)", self.annotation_y_spin)
        position_form.addRow("Width", self.annotation_w_spin)
        position_form.addRow("Height", self.annotation_h_spin)
        position_form.addRow("Angle", self.annotation_angle_spin)
        tabs.addTab(position_tab, "Position")

        style_tab = QWidget()
        style_form = QFormLayout(style_tab)
        style_form.addRow("Font size", self.annotation_font_spin)
        style_form.addRow("Arrow head size", self.annotation_arrow_head_spin)
        style_form.addRow("Line style", self.annotation_line_style_combo)
        style_form.addRow("Alpha", self.annotation_alpha_spin)
        style_form.addRow(self.annotation_fill_check)
        style_form.addRow("Color", self.annotation_color_btn)
        tabs.addTab(style_tab, "Style")
        return box

    def _build_figure_box(self) -> QGroupBox:
        box = QGroupBox("Figure")
        layout = QVBoxLayout(box)

        self.preset_combo = NoWheelComboBox()
        self.preset_combo.addItems(list(PRESETS.keys()) + ["Custom"])
        self.preset_combo.setCurrentText(self.plot_config.preset)

        self.title_edit = QLineEdit()
        self.x_label_edit = QLineEdit()
        self.y_label_edit = QLineEdit()
        self.y2_label_edit = QLineEdit()
        self.width_spin = self._double_spin(self.plot_config.width_mm, 20.0, 300.0, 1)
        self.height_spin = self._double_spin(self.plot_config.height_mm, 20.0, 300.0, 1)
        self.plot_width_spin = self._double_spin(self.plot_config.plot_width_mm, 1.0, 300.0, 1)
        self.plot_height_spin = self._double_spin(self.plot_config.plot_height_mm, 1.0, 300.0, 1)
        self.plot_ratio_lock_check = QCheckBox("Lock plot ratio")
        self.plot_ratio_lock_check.setChecked(self.plot_config.plot_ratio_locked)
        self.plot_ratio_preset_combo = NoWheelComboBox()
        self.plot_ratio_preset_combo.addItems(list(PLOT_RATIO_PRESETS.keys()))
        self.plot_ratio_preset_combo.setCurrentText(self.plot_config.plot_ratio_preset)
        self.dpi_spin = self._int_spin(self.plot_config.dpi, 72, 1200)
        self.title_size_spin = self._int_spin(self.plot_config.title_size, 4, 40)
        self.axis_size_spin = self._int_spin(self.plot_config.axis_size, 4, 40)
        self.tick_size_spin = self._int_spin(self.plot_config.tick_size, 4, 40)
        self.legend_size_spin = self._int_spin(self.plot_config.legend_size, 4, 40)
        self.y_label_offset_spin = self._double_spin(self.plot_config.y_label_offset_mm, 0.0, 50.0, 1)
        self.y_axis_color_btn = QPushButton(self.plot_config.y_axis_color)
        self.y_axis_color_btn.setStyleSheet("background-color: #000000; color: white;")
        self.y2_axis_color_btn = QPushButton(self.plot_config.y2_axis_color)
        self.y2_axis_color_btn.setStyleSheet("background-color: #000000; color: white;")
        self.pad_left_spin = self._double_spin(self.plot_config.pad_left_mm, 0.0, 100.0, 1)
        self.pad_right_spin = self._double_spin(self.plot_config.pad_right_mm, 0.0, 100.0, 1)
        self.pad_top_spin = self._double_spin(self.plot_config.pad_top_mm, 0.0, 100.0, 1)
        self.pad_bottom_spin = self._double_spin(self.plot_config.pad_bottom_mm, 0.0, 100.0, 1)
        self.fixed_plot_area_check = QCheckBox("Lock plot box size")
        self.fixed_plot_area_check.setChecked(self.plot_config.fixed_plot_area)
        self.plot_margin_left_spin = self._double_spin(self.plot_config.plot_margin_left_mm, 0.0, 200.0, 1)
        self.plot_margin_right_spin = self._double_spin(self.plot_config.plot_margin_right_mm, 0.0, 200.0, 1)
        self.plot_margin_top_spin = self._double_spin(self.plot_config.plot_margin_top_mm, 0.0, 200.0, 1)
        self.plot_margin_bottom_spin = self._double_spin(self.plot_config.plot_margin_bottom_mm, 0.0, 200.0, 1)
        self.center_plot_box_btn = QPushButton("Center plot box")
        self.center_content_btn = QPushButton("Center content")
        self.fit_canvas_btn = QPushButton("Fit canvas to content")
        self.x_scale_combo = NoWheelComboBox()
        self.x_scale_combo.addItems(["linear", "log"])
        self.y_scale_combo = NoWheelComboBox()
        self.y_scale_combo.addItems(["linear", "log"])
        self.y2_scale_combo = NoWheelComboBox()
        self.y2_scale_combo.addItems(["linear", "log"])
        self.x_scale_divisor_edit = QLineEdit(str(self.plot_config.x_scale_divisor))
        self.y_scale_divisor_edit = QLineEdit(str(self.plot_config.y_scale_divisor))
        self.y2_scale_divisor_edit = QLineEdit(str(self.plot_config.y2_scale_divisor))
        self.x_min_edit = QLineEdit()
        self.x_max_edit = QLineEdit()
        self.y_min_edit = QLineEdit()
        self.y_max_edit = QLineEdit()
        self.y2_min_edit = QLineEdit()
        self.y2_max_edit = QLineEdit()
        self.x_tick_interval_edit = QLineEdit()
        self.y_tick_interval_edit = QLineEdit()
        self.y2_tick_interval_edit = QLineEdit()
        self.x_minor_divisions_spin = self._int_spin(self.plot_config.x_minor_divisions, 0, 20)
        self.y_minor_divisions_spin = self._int_spin(self.plot_config.y_minor_divisions, 0, 20)
        self.y2_minor_divisions_spin = self._int_spin(self.plot_config.y2_minor_divisions, 0, 20)
        self.x_break_check = QCheckBox("X broken axis")
        self.x_break_check.setChecked(self.plot_config.x_break_enabled)
        self.x_break_left_min_edit = QLineEdit()
        self.x_break_left_max_edit = QLineEdit()
        self.x_break_right_min_edit = QLineEdit()
        self.x_break_right_max_edit = QLineEdit()
        self.x_break_gap_spin = self._double_spin(self.plot_config.x_break_gap, 0.01, 0.5, 3)
        self.y_break_check = QCheckBox("Y broken axis")
        self.y_break_check.setChecked(self.plot_config.y_break_enabled)
        self.y_break_lower_min_edit = QLineEdit()
        self.y_break_lower_max_edit = QLineEdit()
        self.y_break_upper_min_edit = QLineEdit()
        self.y_break_upper_max_edit = QLineEdit()
        self.y_break_gap_spin = self._double_spin(self.plot_config.y_break_gap, 0.01, 0.5, 3)
        for edit in (
            self.x_min_edit,
            self.x_max_edit,
            self.y2_min_edit,
            self.y2_max_edit,
            self.x_tick_interval_edit,
            self.y_tick_interval_edit,
            self.y2_tick_interval_edit,
            self.x_break_left_min_edit,
            self.x_break_left_max_edit,
            self.x_break_right_min_edit,
            self.x_break_right_max_edit,
            self.y_break_lower_min_edit,
            self.y_break_lower_max_edit,
            self.y_break_upper_min_edit,
            self.y_break_upper_max_edit,
        ):
            edit.setPlaceholderText("auto")
        self.y_min_edit.setPlaceholderText("auto; log: exponent")
        self.y_max_edit.setPlaceholderText("auto; log: exponent")
        self.y2_min_edit.setPlaceholderText("auto; log: exponent")
        self.y2_max_edit.setPlaceholderText("auto; log: exponent")
        for edit in (self.x_tick_interval_edit, self.y_tick_interval_edit, self.y2_tick_interval_edit):
            edit.setPlaceholderText("auto; log: decades")
            edit.setToolTip("Linear scale: data-unit interval. Log scale: decade interval, e.g. 1 for 10^n ticks or 0.5 for half-decades.")
        for spin in (self.x_minor_divisions_spin, self.y_minor_divisions_spin, self.y2_minor_divisions_spin):
            spin.setToolTip("Linear scale: minor subdivisions. Log scale: 0 hides minor ticks; any positive value shows standard 2-9 minor ticks per decade.")
        for edit in (self.x_scale_divisor_edit, self.y_scale_divisor_edit, self.y2_scale_divisor_edit):
            edit.setPlaceholderText("1")
            edit.setToolTip("Plot values are divided by this positive number. Example: 1e-6 converts seconds to microseconds.")
        self.y_offset_spin = self._double_spin(self.plot_config.y_offset_step, -1e9, 1e9, 4)
        self.show_x_tick_labels_check = QCheckBox("Show X tick labels")
        self.show_x_tick_labels_check.setChecked(True)
        self.show_y_tick_labels_check = QCheckBox("Show Y tick labels")
        self.show_y_tick_labels_check.setChecked(True)
        self.show_y2_tick_labels_check = QCheckBox("Show Y2 tick labels")
        self.show_y2_tick_labels_check.setChecked(True)
        self.grid_check = QCheckBox("Grid")
        self.grid_check.setChecked(self.plot_config.grid)
        self.legend_check = QCheckBox("Legend")
        self.legend_check.setChecked(True)

        tabs = QTabWidget()
        layout.addWidget(tabs)

        size_tab = QWidget()
        size_form = QFormLayout(size_tab)
        size_form.addRow("Preset", self.preset_combo)
        size_form.addRow("Canvas width (mm)", self.width_spin)
        size_form.addRow("Canvas height (mm)", self.height_spin)
        size_form.addRow("Plot width (mm)", self.plot_width_spin)
        size_form.addRow("Plot height (mm)", self.plot_height_spin)
        size_form.addRow(self.plot_ratio_lock_check)
        size_form.addRow("Plot ratio preset", self.plot_ratio_preset_combo)
        size_form.addRow("DPI", self.dpi_spin)
        tabs.addTab(size_tab, "Size")

        padding_tab = QWidget()
        padding_form = QFormLayout(padding_tab)
        padding_form.addRow("Left padding (mm)", self.pad_left_spin)
        padding_form.addRow("Right padding (mm)", self.pad_right_spin)
        padding_form.addRow("Top padding (mm)", self.pad_top_spin)
        padding_form.addRow("Bottom padding (mm)", self.pad_bottom_spin)
        tabs.addTab(padding_tab, "Padding")

        layout_tab = QWidget()
        layout_form = QFormLayout(layout_tab)
        layout_form.addRow(self.fixed_plot_area_check)
        layout_form.addRow("Plot left margin (mm)", self.plot_margin_left_spin)
        layout_form.addRow("Plot right margin (mm)", self.plot_margin_right_spin)
        layout_form.addRow("Plot top margin (mm)", self.plot_margin_top_spin)
        layout_form.addRow("Plot bottom margin (mm)", self.plot_margin_bottom_spin)
        layout_form.addRow(self.center_plot_box_btn)
        layout_form.addRow(self.center_content_btn)
        layout_form.addRow(self.fit_canvas_btn)
        tabs.addTab(layout_tab, "Layout")

        labels_tab = QWidget()
        labels_form = QFormLayout(labels_tab)
        labels_form.addRow("Title", self.title_edit)
        labels_form.addRow("X label", self.x_label_edit)
        labels_form.addRow("Y label", self.y_label_edit)
        labels_form.addRow("Y2 label", self.y2_label_edit)
        labels_form.addRow("Y label gap (mm)", self.y_label_offset_spin)
        tabs.addTab(labels_tab, "Labels")

        axes_tab = QWidget()
        axes_form = QFormLayout(axes_tab)
        axes_form.addRow("X scale", self.x_scale_combo)
        axes_form.addRow("Y scale", self.y_scale_combo)
        axes_form.addRow("Y2 scale", self.y2_scale_combo)
        axes_form.addRow("X divisor", self.x_scale_divisor_edit)
        axes_form.addRow("Y divisor", self.y_scale_divisor_edit)
        axes_form.addRow("Y2 divisor", self.y2_scale_divisor_edit)
        axes_form.addRow("X min", self.x_min_edit)
        axes_form.addRow("X max", self.x_max_edit)
        axes_form.addRow("Y min", self.y_min_edit)
        axes_form.addRow("Y max", self.y_max_edit)
        axes_form.addRow("Y2 min", self.y2_min_edit)
        axes_form.addRow("Y2 max", self.y2_max_edit)
        axes_form.addRow("X tick interval", self.x_tick_interval_edit)
        axes_form.addRow("X minor divisions", self.x_minor_divisions_spin)
        axes_form.addRow("Y tick interval", self.y_tick_interval_edit)
        axes_form.addRow("Y minor divisions", self.y_minor_divisions_spin)
        axes_form.addRow("Y2 tick interval", self.y2_tick_interval_edit)
        axes_form.addRow("Y2 minor divisions", self.y2_minor_divisions_spin)
        axes_form.addRow(self.x_break_check)
        axes_form.addRow("X break left min", self.x_break_left_min_edit)
        axes_form.addRow("X break left max", self.x_break_left_max_edit)
        axes_form.addRow("X break right min", self.x_break_right_min_edit)
        axes_form.addRow("X break right max", self.x_break_right_max_edit)
        axes_form.addRow("X break gap", self.x_break_gap_spin)
        axes_form.addRow(self.y_break_check)
        axes_form.addRow("Y break lower min", self.y_break_lower_min_edit)
        axes_form.addRow("Y break lower max", self.y_break_lower_max_edit)
        axes_form.addRow("Y break upper min", self.y_break_upper_min_edit)
        axes_form.addRow("Y break upper max", self.y_break_upper_max_edit)
        axes_form.addRow("Y break gap", self.y_break_gap_spin)
        axes_form.addRow(self.show_x_tick_labels_check)
        axes_form.addRow(self.show_y_tick_labels_check)
        axes_form.addRow(self.show_y2_tick_labels_check)
        tabs.addTab(axes_tab, "Axes")

        style_tab = QWidget()
        style_form = QFormLayout(style_tab)
        style_form.addRow("Title size", self.title_size_spin)
        style_form.addRow("Axis size", self.axis_size_spin)
        style_form.addRow("Tick size", self.tick_size_spin)
        style_form.addRow("Legend size", self.legend_size_spin)
        style_form.addRow("Y1 axis color", self.y_axis_color_btn)
        style_form.addRow("Y2 axis color", self.y2_axis_color_btn)
        style_form.addRow("Y offset", self.y_offset_spin)
        style_form.addRow(self.grid_check)
        style_form.addRow(self.legend_check)
        tabs.addTab(style_tab, "Style")
        return box

    def _build_style_copy_box(self) -> QGroupBox:
        box = QGroupBox("Style copy")
        layout = QVBoxLayout(box)
        self.include_annotations_style_check = QCheckBox("Include annotations")
        self.include_annotations_style_check.setChecked(True)
        self.copy_style_btn = QPushButton("Copy current style")
        self.apply_style_btn = QPushButton("Apply style")
        layout.addWidget(self.include_annotations_style_check)
        layout.addWidget(self.copy_style_btn)
        layout.addWidget(self.apply_style_btn)
        return box

    def _build_special_chars_box(self) -> QGroupBox:
        box = QGroupBox("Special characters")
        layout = QVBoxLayout(box)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        groups = {
            "Greek": [
                "\u03b1", "\u03b2", "\u03b3", "\u03b4", "\u03b5", "\u03b8", "\u03bb", "\u03bc",
                "\u03c0", "\u03c3", "\u03c4", "\u03c6", "\u03c7", "\u03c9", "\u0394", "\u03a9",
            ],
            "Math": [
                "\u00b0", "\u00b1", "\u00d7", "\u00f7", "\u00b7", "\u2212", "\u2264", "\u2265",
                "\u2248", "\u2260", "\u221e", "\u221a", "\u2206", "\u2202", "\u222b", "\u2211",
            ],
            "Units": [
                "\u00b5", "\u212b", "\u00b0C", "\u03a9", "\u03a9\u00b7cm", "cm^2", "m^2", "10^{-3}",
                "^-1", "_2", "_3", "_{0.5}", "bar{1}", "bar{2}", "bar{3}", "bar{0}",
            ],
            "Arrows": [
                "\u2190", "\u2192", "\u2191", "\u2193", "\u2194", "\u21d0", "\u21d2", "\u21d4",
                "\u21b5", "\u21c4", "\u25b2", "\u25bc", "\u25c0", "\u25b6", "\u25b3", "\u25bd",
            ],
            "Shapes": [
                "\u2b1b", "\u2b1c", "\u25fc", "\u25fb", "\u25aa", "\u25ab", "\u25cf", "\u25cb",
                "\u25c6", "\u25c7",
                "\u25b2", "\u25b3", "\u25bc", "\u25bd", "\u25c0", "\u25c1", "\u25b6", "\u25b7",
                "\u2605", "\u2606", "\u2713", "\u2717", "\u25ac", "\u25ad", "\u25ef", "\u25cc",
            ],
        }

        for label, chars in groups.items():
            tabs.addTab(self._build_special_char_tab(chars), label)
        for field in self.special_text_fields():
            field.installEventFilter(self)
        self.special_text_target = self.annotation_text_edit
        return box

    def _build_special_char_tab(self, chars: list[str]) -> QWidget:
        tab = QWidget()
        grid = QGridLayout(tab)
        grid.setContentsMargins(4, 4, 4, 4)
        grid.setSpacing(4)
        for idx, char in enumerate(chars):
            button = QPushButton(char)
            button.setFocusPolicy(Qt.NoFocus)
            button.setFixedWidth(42)
            button.setToolTip(f"Insert {char}")
            button.clicked.connect(lambda checked=False, value=char: self.insert_special_character(value))
            grid.addWidget(button, idx // 4, idx % 4)
        return tab

    def insert_special_character(self, value: str) -> None:
        focus = QApplication.focusWidget()
        if focus in self.special_text_fields():
            target = focus
        else:
            if self.insert_special_character_into_table_name(value, focus):
                return
            target = self.special_text_target
        if target is None:
            self.set_status("Select an annotation, label, title, series text field, or column Name cell first.")
            return
        target.insert(value)
        target.setFocus()
        self.special_text_target = target

    def insert_special_character_into_table_name(self, value: str, focus: QWidget | None) -> bool:
        if isinstance(focus, QLineEdit) and self.widget_has_ancestor(focus, self.table):
            focus.insert(value)
            return True
        item = self.table.currentItem()
        if item is not None and item.row() == NAME_ROW:
            item.setText(item.text() + value)
            self.table.setFocus()
            return True
        if focus is not self.table and not self.widget_has_ancestor(focus, self.table):
            return False
        return False

    def widget_has_ancestor(self, widget: QWidget | None, ancestor: QWidget) -> bool:
        while widget is not None:
            if widget is ancestor:
                return True
            widget = widget.parentWidget()
        return False

    def _build_export_box(self) -> QGroupBox:
        box = QGroupBox("Export / Project")
        layout = QVBoxLayout(box)

        self.trim_check = QCheckBox("Trim whitespace")
        self.transparent_check = QCheckBox("Transparent background")
        self.export_btn = QPushButton("Export figure")
        self.export_all_btn = QPushButton("Export all figures")
        self.new_project_btn = QPushButton("New project")
        self.save_project_btn = QPushButton("Save project")
        self.load_project_btn = QPushButton("Load project")

        layout.addWidget(self.trim_check)
        layout.addWidget(self.transparent_check)
        layout.addWidget(self.export_btn)
        layout.addWidget(self.export_all_btn)
        layout.addWidget(self.new_project_btn)
        layout.addWidget(self.save_project_btn)
        layout.addWidget(self.load_project_btn)
        return box

    def _connect_signals(self) -> None:
        self.figure_list.currentRowChanged.connect(self.switch_project_figure)
        self.new_figure_btn.clicked.connect(self.new_project_figure)
        self.duplicate_figure_btn.clicked.connect(self.duplicate_project_figure)
        self.rename_figure_btn.clicked.connect(self.rename_project_figure)
        self.delete_figure_btn.clicked.connect(self.delete_project_figure)
        self.move_figure_up_btn.clicked.connect(lambda: self.move_project_figure(-1))
        self.move_figure_down_btn.clicked.connect(lambda: self.move_project_figure(1))
        self.fit_preview_check.toggled.connect(self.update_canvas_size)
        self.preview_zoom_spin.valueChanged.connect(self.update_canvas_size)
        self.center_preview_btn.clicked.connect(self.center_preview)
        self.center_plot_box_btn.clicked.connect(self.center_plot_box)
        self.center_content_btn.clicked.connect(self.center_content)
        self.fit_canvas_btn.clicked.connect(self.fit_canvas_to_content)
        self.y_list.itemChanged.connect(self.handle_plot_y_changed)
        self.y_list.itemClicked.connect(self.handle_plot_y_clicked)
        self.style_target_combo.currentTextChanged.connect(self.handle_style_target_changed)
        self.plot_type_combo.currentTextChanged.connect(self.update_plot_type_help)
        self.preset_combo.currentTextChanged.connect(self.handle_preset_changed)
        self.plot_width_spin.valueChanged.connect(lambda *_: self.handle_plot_dimension_changed("width"))
        self.plot_height_spin.valueChanged.connect(lambda *_: self.handle_plot_dimension_changed("height"))
        self.plot_ratio_lock_check.toggled.connect(self.handle_plot_ratio_lock_toggled)
        self.plot_ratio_preset_combo.currentTextChanged.connect(self.handle_plot_ratio_preset_changed)
        self.color_btn.clicked.connect(self.choose_series_color)
        self.apply_cmap_btn.clicked.connect(self.apply_colormap_to_plotted_series)
        self.cmap_select_all_btn.clicked.connect(self._cmap_select_all)
        self.cmap_select_none_btn.clicked.connect(self._cmap_select_none)
        self.cmap_alpha_only_check.toggled.connect(self.update_cmap_mode)
        self.cmap_base_color_btn.clicked.connect(self.choose_cmap_base_color)
        self.annotation_color_btn.clicked.connect(self.choose_annotation_color)
        self.y_axis_color_btn.clicked.connect(self.choose_y_axis_color)
        self.y2_axis_color_btn.clicked.connect(self.choose_y2_axis_color)
        self.add_annotation_btn.clicked.connect(self.add_annotation)
        self.remove_annotation_btn.clicked.connect(self.remove_selected_annotation)
        self.apply_palette_btn.clicked.connect(self.apply_recommended_palette_to_series)
        self.annotation_list.currentRowChanged.connect(self.update_annotation_inputs)
        for widget in (
            self.annotation_kind_combo,
            self.annotation_text_edit,
            self.annotation_x_spin,
            self.annotation_y_spin,
            self.annotation_x2_spin,
            self.annotation_y2_spin,
            self.annotation_w_spin,
            self.annotation_h_spin,
            self.annotation_angle_spin,
            self.annotation_font_spin,
            self.annotation_arrow_head_spin,
            self.annotation_line_style_combo,
            self.annotation_alpha_spin,
            self.annotation_fill_check,
        ):
            self._connect_change(widget, self.update_selected_annotation)
        self.export_btn.clicked.connect(self.export_current_figure)
        self.export_all_btn.clicked.connect(self.export_all_figures)
        self.new_project_btn.clicked.connect(self.new_project)
        self.save_project_btn.clicked.connect(self.save_project)
        self.load_project_btn.clicked.connect(self.load_project)
        self.copy_style_btn.clicked.connect(self.copy_current_style)
        self.apply_style_btn.clicked.connect(self.apply_copied_style)

        for widget in (
            self.series_label_edit,
            self.y_axis_combo,
            self.plot_type_combo,
            self.marker_combo,
            self.line_width_spin,
            self.marker_size_spin,
            self.series_y_offset_spin,
            self.series_alpha_spin,
            self.show_in_legend_check,
        ):
            self._connect_change(widget, self.apply_series_widget_state)

        for widget in (
            self.title_edit,
            self.x_label_edit,
            self.y_label_edit,
            self.y2_label_edit,
            self.width_spin,
            self.height_spin,
            self.plot_width_spin,
            self.plot_height_spin,
            self.plot_ratio_lock_check,
            self.plot_ratio_preset_combo,
            self.dpi_spin,
            self.title_size_spin,
            self.axis_size_spin,
            self.tick_size_spin,
            self.legend_size_spin,
            self.y_label_offset_spin,
            self.y_axis_color_btn,
            self.y2_axis_color_btn,
            self.pad_left_spin,
            self.pad_right_spin,
            self.pad_top_spin,
            self.pad_bottom_spin,
            self.fixed_plot_area_check,
            self.plot_margin_left_spin,
            self.plot_margin_right_spin,
            self.plot_margin_top_spin,
            self.plot_margin_bottom_spin,
            self.x_scale_combo,
            self.y_scale_combo,
            self.y2_scale_combo,
            self.x_scale_divisor_edit,
            self.y_scale_divisor_edit,
            self.y2_scale_divisor_edit,
            self.x_min_edit,
            self.x_max_edit,
            self.y_min_edit,
            self.y_max_edit,
            self.y2_min_edit,
            self.y2_max_edit,
            self.x_tick_interval_edit,
            self.x_minor_divisions_spin,
            self.y_tick_interval_edit,
            self.y_minor_divisions_spin,
            self.y2_tick_interval_edit,
            self.y2_minor_divisions_spin,
            self.x_break_check,
            self.x_break_left_min_edit,
            self.x_break_left_max_edit,
            self.x_break_right_min_edit,
            self.x_break_right_max_edit,
            self.x_break_gap_spin,
            self.y_break_check,
            self.y_break_lower_min_edit,
            self.y_break_lower_max_edit,
            self.y_break_upper_min_edit,
            self.y_break_upper_max_edit,
            self.y_break_gap_spin,
            self.show_x_tick_labels_check,
            self.show_y_tick_labels_check,
            self.show_y2_tick_labels_check,
            self.y_offset_spin,
            self.grid_check,
            self.legend_check,
            self.trim_check,
            self.transparent_check,
        ):
            self._connect_change(widget, self.schedule_render)

    def _connect_change(self, widget, callback) -> None:
        for signal_name in ("textChanged", "currentTextChanged", "valueChanged", "toggled"):
            signal = getattr(widget, signal_name, None)
            if signal is not None:
                signal.connect(lambda *args, cb=callback: self.handle_undoable_widget_change(cb))
                return

    def handle_undoable_widget_change(self, callback) -> None:
        self.push_undo_baseline()
        callback()
        self.update_undo_baseline()

    def update_plot_type_help(self, plot_type: str) -> None:
        self.plot_type_help.setText(PLOT_TYPE_HELP.get(plot_type, ""))

    def handle_table_paste(self, row: int, col: int, text: str) -> None:
        if self._table_is_blank() and row == 0 and col == 0:
            self.replace_table_from_text(text)
            return
        grid = parse_clipboard_grid(text)
        if not grid:
            return
        self.push_current_undo_state()
        self._paste_grid(row, col, grid)
        self.sync_dataframe_after_paste(row, col, grid)
        self.update_undo_baseline()
        self.set_status(f"Pasted {len(grid)} rows x {max(len(values) for values in grid)} cols.")

    def replace_table_from_text(self, text: str) -> None:
        try:
            parsed = parse_table_text(text)
        except Exception as exc:
            QMessageBox.warning(self, "Paste data", str(exc))
            self.set_status(f"Paste failed: {exc}")
            return
        self.push_current_undo_state()
        self.set_dataframe(parsed)
        self.update_undo_baseline()

    def set_dataframe(self, parsed: ParsedTable) -> None:
        self.df = self._with_metadata_rows(parsed.dataframe)
        self.series_by_y.clear()
        self.populate_table()
        self.populate_columns()
        self.set_status(
            f"Loaded {len(parsed.dataframe)} data rows x {len(self.df.columns)} cols; delimiter={parsed.delimiter}; header={parsed.has_header}"
        )
        self.schedule_render()

    def create_blank_sheet(self, rows: int = 20, columns: int = 4) -> None:
        self.df = self.blank_dataframe(rows, columns)
        self.series_by_y.clear()
        self.annotations = []
        self.plot_config = PlotConfig()
        self.populate_table()
        self.populate_columns()
        self._load_config_into_widgets()
        self.refresh_annotation_list()
        self.set_status("Ready. Paste or type data into the table.")

    def blank_dataframe(self, rows: int = 20, columns: int = 4) -> pd.DataFrame:
        df = pd.DataFrame("", index=range(rows + DATA_START_ROW), columns=[f"Col {idx + 1}" for idx in range(columns)])
        df.iloc[ROLE_ROW, 0] = "X"
        if columns > 1:
            df.iloc[ROLE_ROW, 1] = "Y"
        return df

    def capture_current_figure(self, name: str | None = None) -> ProjectFigure:
        config = deepcopy(self.collect_plot_config())
        annotations = deepcopy(self.annotations)
        config.annotations = annotations
        return ProjectFigure(
            name=name or self.current_project_figure_name(),
            df=self.df.copy(deep=True),
            plot_config=config,
            series_by_y=deepcopy(self.series_by_y),
            checked_y=self.checked_y_columns(),
            annotations=annotations,
        )

    def current_workspace_snapshot(self) -> dict:
        figures = deepcopy(self.project_figures)
        if 0 <= self.active_figure_index < len(figures):
            figures[self.active_figure_index] = self.capture_current_figure(figures[self.active_figure_index].name)
        return {"figures": figures, "active": self.active_figure_index}

    def push_current_undo_state(self) -> None:
        if self.restoring_undo or self.loading_project_figure:
            return
        self._is_modified = True
        self.undo_stack.append(self.current_workspace_snapshot())
        self.undo_stack = self.undo_stack[-50:]

    def push_undo_baseline(self) -> None:
        if self.restoring_undo or self.loading_project_figure or self.undo_baseline is None:
            return
        self._is_modified = True
        self.undo_stack.append(deepcopy(self.undo_baseline))
        self.undo_stack = self.undo_stack[-50:]

    def update_undo_baseline(self) -> None:
        if self.restoring_undo or self.loading_project_figure:
            return
        self.undo_baseline = self.current_workspace_snapshot()

    def undo_workspace(self) -> None:
        if not self.undo_stack:
            self.set_status("Nothing to undo.")
            return
        snapshot = self.undo_stack.pop()
        self.restoring_undo = True
        self.project_figures = deepcopy(snapshot["figures"])
        self.active_figure_index = max(0, min(snapshot["active"], len(self.project_figures) - 1))
        self.refresh_figure_list()
        self.load_project_figure(self.project_figures[self.active_figure_index])
        self.restoring_undo = False
        self.undo_baseline = self.current_workspace_snapshot()
        self.set_status("Undid last change.")

    def current_project_figure_name(self) -> str:
        if 0 <= self.active_figure_index < len(self.project_figures):
            return self.project_figures[self.active_figure_index].name
        return f"Figure {len(self.project_figures) + 1}"

    def save_active_figure_state(self) -> None:
        if self.loading_project_figure:
            return
        if 0 <= self.active_figure_index < len(self.project_figures):
            name = self.project_figures[self.active_figure_index].name
            self.project_figures[self.active_figure_index] = self.capture_current_figure(name)

    def load_project_figure(self, figure: ProjectFigure) -> None:
        self.loading_project_figure = True
        self.df = figure.df.copy(deep=True)
        self.plot_config = deepcopy(figure.plot_config)
        self.annotations = deepcopy(figure.annotations)
        self.plot_config.annotations = self.annotations
        self.series_by_y = deepcopy(figure.series_by_y)
        self.populate_table()
        self._load_config_into_widgets()
        self.populate_columns()
        self._set_checked_y_columns([column for column in figure.checked_y if column in list(map(str, self.df.columns))])
        self.refresh_series_configs()
        self.update_style_targets()
        self.refresh_cmap_column_list()
        self.refresh_annotation_list()
        self.loading_project_figure = False
        self.render_plot()

    def refresh_figure_list(self) -> None:
        self.figure_list.blockSignals(True)
        self.figure_list.clear()
        for figure in self.project_figures:
            self.figure_list.addItem(figure.name)
        if self.project_figures:
            self.figure_list.setCurrentRow(max(0, min(self.active_figure_index, len(self.project_figures) - 1)))
        self.figure_list.blockSignals(False)
        self.update_project_move_buttons()

    def update_project_move_buttons(self) -> None:
        row = self.figure_list.currentRow()
        count = len(self.project_figures)
        self.move_figure_up_btn.setEnabled(count > 1 and row > 0)
        self.move_figure_down_btn.setEnabled(count > 1 and 0 <= row < count - 1)

    def switch_project_figure(self, row: int) -> None:
        if self.loading_project_figure or row < 0 or row >= len(self.project_figures) or row == self.active_figure_index:
            self.update_project_move_buttons()
            return
        self.save_active_figure_state()
        self.active_figure_index = row
        self.load_project_figure(self.project_figures[row])
        self.update_project_move_buttons()
        self.set_status(f"Switched to {self.project_figures[row].name}.")

    def new_project_figure(self) -> None:
        self.push_current_undo_state()
        self.save_active_figure_state()
        figure = ProjectFigure(
            name=self.next_figure_name(),
            df=self.blank_dataframe(),
            plot_config=PlotConfig(),
            series_by_y={},
            checked_y=[],
            annotations=[],
        )
        self.project_figures.append(figure)
        self.active_figure_index = len(self.project_figures) - 1
        self.refresh_figure_list()
        self.load_project_figure(figure)
        self.update_undo_baseline()
        self.set_status(f"Created {figure.name}.")

    def duplicate_project_figure(self) -> None:
        if not self.project_figures:
            return
        self.push_current_undo_state()
        self.save_active_figure_state()
        source = self.project_figures[self.active_figure_index]
        figure = deepcopy(source)
        figure.name = self.next_figure_name(source.name)
        self.project_figures.append(figure)
        self.active_figure_index = len(self.project_figures) - 1
        self.refresh_figure_list()
        self.load_project_figure(figure)
        self.update_undo_baseline()
        self.set_status(f"Duplicated as {figure.name}.")

    def rename_project_figure(self) -> None:
        if not (0 <= self.active_figure_index < len(self.project_figures)):
            return
        current = self.project_figures[self.active_figure_index].name
        name, ok = QInputDialog.getText(self, "Rename figure", "Figure name", text=current)
        name = name.strip()
        if not ok or not name:
            return
        if any(idx != self.active_figure_index and figure.name == name for idx, figure in enumerate(self.project_figures)):
            QMessageBox.warning(self, "Rename figure", f"Figure already exists: {name}")
            return
        self.push_current_undo_state()
        self.project_figures[self.active_figure_index].name = name
        self.refresh_figure_list()
        self.update_undo_baseline()
        self.set_status(f"Renamed figure to {name}.")

    def delete_project_figure(self) -> None:
        self.push_current_undo_state()
        if len(self.project_figures) <= 1:
            self.create_blank_sheet()
            self.project_figures = [self.capture_current_figure("Figure 1")]
            self.active_figure_index = 0
            self.refresh_figure_list()
            self.render_plot()
            self.update_undo_baseline()
            self.set_status("Reset the only figure.")
            return
        row = self.active_figure_index
        removed = self.project_figures.pop(row)
        self.active_figure_index = min(row, len(self.project_figures) - 1)
        self.refresh_figure_list()
        self.load_project_figure(self.project_figures[self.active_figure_index])
        self.update_undo_baseline()
        self.set_status(f"Deleted {removed.name}.")

    def move_project_figure(self, offset: int) -> None:
        if len(self.project_figures) <= 1:
            return
        row = self.figure_list.currentRow()
        if row < 0:
            row = self.active_figure_index
        target = row + offset
        if row < 0 or row >= len(self.project_figures) or target < 0 or target >= len(self.project_figures):
            self.update_project_move_buttons()
            return

        self.push_current_undo_state()
        self.save_active_figure_state()
        self.project_figures[row], self.project_figures[target] = self.project_figures[target], self.project_figures[row]
        self.active_figure_index = target
        self.refresh_figure_list()
        self.update_undo_baseline()
        self.set_status(f"Moved {self.project_figures[target].name} {'up' if offset < 0 else 'down'}.")

    def next_figure_name(self, base: str = "Figure") -> str:
        existing = {figure.name for figure in self.project_figures}
        root = base
        if root.startswith("Figure "):
            root = "Figure"
        idx = len(existing) + 1
        while f"{root} {idx}" in existing:
            idx += 1
        return f"{root} {idx}"

    def _with_metadata_rows(self, data_df: pd.DataFrame) -> pd.DataFrame:
        columns = list(map(str, data_df.columns))
        roles = [""] * len(columns)
        if columns:
            roles[0] = "X"
        for idx in range(1, len(columns)):
            roles[idx] = "Y"
        names = columns.copy()
        metadata = pd.DataFrame([roles, names], columns=columns)
        return pd.concat([metadata, data_df.astype(str)], ignore_index=True)

    def populate_table(self) -> None:
        self.table.blockSignals(True)
        self.table.clear()
        self.table.setRowCount(len(self.df))
        self.table.setColumnCount(len(self.df.columns))
        self.table.setHorizontalHeaderLabels(list(map(str, self.df.columns)))
        self.table.setVerticalHeaderLabels(self._row_labels(len(self.df)))

        for row_idx, (_, row) in enumerate(self.df.iterrows()):
            for col_idx, value in enumerate(row.tolist()):
                item = QTableWidgetItem("" if pd.isna(value) else str(value))
                self.table.setItem(row_idx, col_idx, item)
        if len(self.df) * max(len(self.df.columns), 1) <= 2000:
            self.table.resizeColumnsToContents()
        self._update_column_headers()
        self.data_info.setText(f"{max(len(self.df) - DATA_START_ROW, 0)} data rows, {len(self.df.columns)} columns")
        self.table.blockSignals(False)

    def populate_columns(self) -> None:
        columns = list(map(str, self.df.columns))
        previous_y = self.checked_y_columns()
        self.series_by_y = {key: value for key, value in self.series_by_y.items() if key in columns}

        self.y_list.blockSignals(True)
        self.y_list.clear()

        y_columns = [column for column in columns if self._column_role(column) == "Y" and self._nearest_left_x(column)]
        for column in y_columns:
            item = QListWidgetItem(column)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.y_list.addItem(item)

        kept_y = [column for column in previous_y if column in y_columns]
        if kept_y:
            self._set_checked_y_columns(kept_y)
        else:
            self._set_checked_y_columns(y_columns)

        self.y_list.blockSignals(False)
        self.refresh_series_configs()
        self.update_style_targets()

    def handle_table_item_changed(self, item: QTableWidgetItem) -> None:
        row = item.row()
        col = item.column()
        if row < 0 or col < 0 or col >= len(self.df.columns):
            return
        while row >= len(self.df):
            self.df.loc[len(self.df)] = [""] * len(self.df.columns)
        self.df.iat[row, col] = item.text()
        if row == ROLE_ROW:
            self._normalize_role_cell(item)
            self.df.iat[row, col] = item.text()
            self._update_column_headers()
        self.data_info.setText(f"{max(len(self.df) - DATA_START_ROW, 0)} data rows, {len(self.df.columns)} columns")
        if row in (ROLE_ROW, NAME_ROW):
            self.populate_columns()
        self.schedule_render()

    def sync_dataframe_from_table(self) -> None:
        columns = [self._header_text(col) for col in range(self.table.columnCount())]
        rows: list[list[str]] = []
        for row in range(self.table.rowCount()):
            values: list[str] = []
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                values.append("" if item is None else item.text())
            rows.append(values)
        self.df = pd.DataFrame(rows, columns=columns)
        self.table.setVerticalHeaderLabels(self._row_labels(self.table.rowCount()))
        self.populate_columns()
        self._update_column_headers()
        self.data_info.setText(f"{max(len(self.df) - DATA_START_ROW, 0)} data rows, {len(self.df.columns)} columns")
        self.schedule_render()

    def show_table_menu(self, position) -> None:
        menu = QMenu(self)
        add_row_action = QAction("Add row", self)
        add_col_action = QAction("Add column", self)
        rename_col_action = QAction("Rename column", self)
        set_x_action = QAction("Set selected columns as X", self)
        set_y_action = QAction("Set selected columns as Y", self)
        clear_role_action = QAction("Clear selected column roles", self)
        clear_cells_action = QAction("Clear selected cells", self)
        delete_rows_action = QAction("Delete selected rows", self)
        delete_cols_action = QAction("Delete selected columns", self)

        add_row_action.triggered.connect(self.add_row)
        add_col_action.triggered.connect(self.add_column)
        rename_col_action.triggered.connect(self.rename_current_column)
        set_x_action.triggered.connect(lambda: self.set_selected_column_roles("X"))
        set_y_action.triggered.connect(lambda: self.set_selected_column_roles("Y"))
        clear_role_action.triggered.connect(lambda: self.set_selected_column_roles(""))
        clear_cells_action.triggered.connect(self.clear_selected_cells)
        delete_rows_action.triggered.connect(self.delete_selected_rows)
        delete_cols_action.triggered.connect(self.delete_selected_columns)

        menu.addAction(add_row_action)
        menu.addAction(add_col_action)
        menu.addAction(rename_col_action)
        menu.addSeparator()
        menu.addAction(set_x_action)
        menu.addAction(set_y_action)
        menu.addAction(clear_role_action)
        menu.addSeparator()
        menu.addAction(clear_cells_action)
        menu.addAction(delete_rows_action)
        menu.addAction(delete_cols_action)
        menu.exec(self.table.viewport().mapToGlobal(position))

    def add_row(self) -> None:
        self.push_current_undo_state()
        row = self.table.currentRow()
        if row < DATA_START_ROW:
            insert_at = DATA_START_ROW
        else:
            insert_at = self.table.rowCount() if row < 0 else row + 1
        self.table.blockSignals(True)
        self.table.insertRow(insert_at)
        for col in range(self.table.columnCount()):
            self.table.setItem(insert_at, col, QTableWidgetItem(""))
        self.table.blockSignals(False)
        self.sync_dataframe_from_table()
        self.update_undo_baseline()
        self.set_status(f"Added row {insert_at + 1}.")

    def add_column(self) -> None:
        self.push_current_undo_state()
        col = self.table.currentColumn()
        insert_at = self.table.columnCount() if col < 0 else col + 1
        self.table.blockSignals(True)
        self.table.insertColumn(insert_at)
        self.table.setHorizontalHeaderItem(insert_at, QTableWidgetItem(self._next_column_name()))
        for row in range(self.table.rowCount()):
            self.table.setItem(row, insert_at, QTableWidgetItem(""))
        self.table.blockSignals(False)
        self.sync_dataframe_from_table()
        self.update_undo_baseline()
        self.set_status(f"Added column {insert_at + 1}.")

    def rename_current_column(self) -> None:
        col = self.table.currentColumn()
        if col < 0 and self.table.columnCount() > 0:
            col = 0
        if col < 0:
            return
        old_name = self._header_text(col)
        new_name, ok = QInputDialog.getText(self, "Rename column", "Column name", text=old_name)
        new_name = new_name.strip()
        if not ok or not new_name:
            return
        existing = [self._header_text(idx) for idx in range(self.table.columnCount()) if idx != col]
        if new_name in existing:
            QMessageBox.warning(self, "Rename column", f"Column already exists: {new_name}")
            return
        self.push_current_undo_state()
        self.table.setHorizontalHeaderItem(col, QTableWidgetItem(new_name))
        if old_name in self.series_by_y:
            self.series_by_y[new_name] = self.series_by_y.pop(old_name)
            self.series_by_y[new_name].y = new_name
            self.series_by_y[new_name].label = new_name
        self.sync_dataframe_from_table()
        self.update_undo_baseline()
        self.set_status(f"Renamed column {old_name} to {new_name}.")

    def set_selected_column_roles(self, role: str) -> None:
        cols = self._selected_columns()
        if not cols and self.table.currentColumn() >= 0:
            cols = [self.table.currentColumn()]
        self.push_current_undo_state()
        self._ensure_metadata_rows()
        self.table.blockSignals(True)
        for col in cols:
            item = self.table.item(ROLE_ROW, col)
            if item is None:
                item = QTableWidgetItem("")
                self.table.setItem(ROLE_ROW, col, item)
            item.setText(role)
        self.table.blockSignals(False)
        self.sync_dataframe_from_table()
        self.update_undo_baseline()
        self.set_status(f"Set {len(cols)} column role(s) to {role or 'blank'}.")

    def clear_selected_cells(self) -> None:
        self.push_current_undo_state()
        self.table.blockSignals(True)
        for item in self.table.selectedItems():
            item.setText("")
        self.table.blockSignals(False)
        self.sync_dataframe_from_table()
        self.update_undo_baseline()
        self.set_status("Cleared selected cells.")

    def delete_selected_rows(self) -> None:
        rows = sorted({item.row() for item in self.table.selectedItems() if item.row() >= DATA_START_ROW}, reverse=True)
        if not rows and self.table.currentRow() >= 0:
            rows = [self.table.currentRow()] if self.table.currentRow() >= DATA_START_ROW else []
        if not rows:
            self.set_status("Role/Name rows are kept.")
            return
        self.push_current_undo_state()
        self.table.blockSignals(True)
        for row in rows:
            self.table.removeRow(row)
        if self.table.rowCount() == 0:
            self.table.setRowCount(1)
        self.table.blockSignals(False)
        self.sync_dataframe_from_table()
        self.update_undo_baseline()
        self.set_status(f"Deleted {len(rows)} row(s).")

    def delete_selected_columns(self) -> None:
        cols = sorted({item.column() for item in self.table.selectedItems()}, reverse=True)
        if not cols and self.table.currentColumn() >= 0:
            cols = [self.table.currentColumn()]
        removed_names = [self._header_text(col) for col in cols]
        self.push_current_undo_state()
        self.table.blockSignals(True)
        for col in cols:
            self.table.removeColumn(col)
        if self.table.columnCount() == 0:
            self.table.setColumnCount(1)
            self.table.setHorizontalHeaderItem(0, QTableWidgetItem("Col 1"))
        self.table.blockSignals(False)
        for name in removed_names:
            self.series_by_y.pop(name, None)
        self.sync_dataframe_from_table()
        self.update_undo_baseline()
        self.set_status(f"Deleted {len(cols)} column(s).")

    def _selected_columns(self) -> list[int]:
        cols = sorted({item.column() for item in self.table.selectedItems()})
        if not cols:
            ranges = self.table.selectedRanges()
            for selected_range in ranges:
                cols.extend(range(selected_range.leftColumn(), selected_range.rightColumn() + 1))
        return sorted(set(cols))

    def _paste_grid(self, start_row: int, start_col: int, grid: list[list[str]]) -> None:
        row_count = start_row + len(grid)
        col_count = start_col + max(len(row) for row in grid)
        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)
        try:
            self._ensure_table_size(row_count, col_count)

            for row_offset, row_values in enumerate(grid):
                for col_offset, value in enumerate(row_values):
                    row = start_row + row_offset
                    col = start_col + col_offset
                    item = self.table.item(row, col)
                    if item is None:
                        if value == "":
                            continue
                        item = QTableWidgetItem("")
                        self.table.setItem(row, col, item)
                    item.setText(value)
                    if row == ROLE_ROW:
                        self._normalize_role_cell(item)
        finally:
            self.table.blockSignals(False)
            self.table.setUpdatesEnabled(True)

    def _ensure_table_size(self, rows: int, columns: int) -> None:
        if self.table.rowCount() < rows:
            self.table.setRowCount(rows)
        while self.table.columnCount() < columns:
            col = self.table.columnCount()
            name = self._next_column_name()
            self.table.setColumnCount(col + 1)
            self.table.setHorizontalHeaderItem(col, QTableWidgetItem(name))
        self._ensure_metadata_rows()

    def sync_dataframe_after_paste(self, start_row: int, start_col: int, grid: list[list[str]]) -> None:
        columns = [self._header_text(col) for col in range(self.table.columnCount())]
        if len(self.df) != self.table.rowCount() or list(map(str, self.df.columns)) != columns:
            old_values = self.df.fillna("").astype(str).values.tolist()
            rows = [[""] * len(columns) for _ in range(self.table.rowCount())]
            for row_idx in range(min(len(old_values), len(rows))):
                for col_idx in range(min(len(old_values[row_idx]), len(columns))):
                    rows[row_idx][col_idx] = old_values[row_idx][col_idx]
            self.df = pd.DataFrame(rows, columns=columns)

        while len(self.df) < self.table.rowCount():
            self.df.loc[len(self.df)] = [""] * len(self.df.columns)

        for row_offset, row_values in enumerate(grid):
            row = start_row + row_offset
            if row >= len(self.df):
                break
            for col_offset, value in enumerate(row_values):
                col = start_col + col_offset
                if col >= len(self.df.columns):
                    break
                if row == ROLE_ROW:
                    value = self._normalized_role_text(value)
                self.df.iat[row, col] = value

        self.table.setVerticalHeaderLabels(self._row_labels(self.table.rowCount()))
        metadata_touched = start_row <= NAME_ROW < start_row + len(grid) or start_row <= ROLE_ROW < start_row + len(grid)
        if metadata_touched:
            self.populate_columns()
            self._update_column_headers()
        self.data_info.setText(f"{max(len(self.df) - DATA_START_ROW, 0)} data rows, {len(self.df.columns)} columns")
        self.schedule_render()

    def _table_is_blank(self) -> bool:
        for row in range(self.table.rowCount()):
            if row == ROLE_ROW:
                continue
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                if item is not None and item.text().strip():
                    return False
        return True

    def _header_text(self, col: int) -> str:
        item = self.table.horizontalHeaderItem(col)
        text = item.text() if item is not None and item.text().strip() else f"Col {col + 1}"
        return text.split(" (", 1)[0]

    def _next_column_name(self) -> str:
        existing = {self._header_text(col) for col in range(self.table.columnCount())}
        idx = 1
        while f"Col {idx}" in existing:
            idx += 1
        return f"Col {idx}"

    def _ensure_metadata_rows(self) -> None:
        while self.table.rowCount() < DATA_START_ROW:
            self.table.insertRow(self.table.rowCount())
        self.table.setVerticalHeaderLabels(self._row_labels(self.table.rowCount()))

    def _row_labels(self, row_count: int) -> list[str]:
        labels: list[str] = []
        for row in range(row_count):
            if row == ROLE_ROW:
                labels.append("Role")
            elif row == NAME_ROW:
                labels.append("Name")
            else:
                labels.append(str(row - DATA_START_ROW + 1))
        return labels

    def _column_role(self, column: str) -> str:
        if column not in self.df.columns or len(self.df) <= ROLE_ROW:
            return ""
        value = str(self.df.at[ROLE_ROW, column]).strip().upper()
        if value.startswith("X"):
            return "X"
        if value.startswith("Y"):
            return "Y"
        return ""

    def _nearest_left_x(self, y_column: str) -> str:
        columns = list(map(str, self.df.columns))
        if y_column not in columns:
            return ""
        y_idx = columns.index(y_column)
        for idx in range(y_idx - 1, -1, -1):
            column = columns[idx]
            if self._column_role(column) == "X":
                return column
        return ""

    def _column_name(self, column: str) -> str:
        if column in self.df.columns and len(self.df) > NAME_ROW:
            value = str(self.df.at[NAME_ROW, column]).strip()
            if value:
                return value
        return column

    def _plot_dataframe(self) -> pd.DataFrame:
        if len(self.df) <= DATA_START_ROW:
            return pd.DataFrame(columns=self.df.columns)
        return self.df.iloc[DATA_START_ROW:].reset_index(drop=True)

    def _normalize_role_cell(self, item: QTableWidgetItem) -> None:
        item.setText(self._normalized_role_text(item.text()))

    def _normalized_role_text(self, value: str) -> str:
        text = str(value).strip().upper()
        if text.startswith("X"):
            return "X"
        if text.startswith("Y"):
            return "Y"
        if text in ("", "NONE", "-"):
            return ""
        return str(value)

    def _update_column_headers(self) -> None:
        for col in range(self.table.columnCount()):
            name = self._header_text(col).split(" (", 1)[0]
            role = ""
            if col < len(self.df.columns):
                role = self._column_role(str(self.df.columns[col]))
            label = f"{name} ({role})" if role else name
            item = self.table.horizontalHeaderItem(col)
            if item is None:
                item = QTableWidgetItem(label)
                self.table.setHorizontalHeaderItem(col, item)
            else:
                item.setText(label)

    def _set_checked_y_columns(self, columns: list[str]) -> None:
        column_set = set(columns)
        for idx in range(self.y_list.count()):
            item = self.y_list.item(idx)
            item.setCheckState(Qt.Checked if item.text() in column_set else Qt.Unchecked)

    def checked_y_columns(self) -> list[str]:
        columns: list[str] = []
        for idx in range(self.y_list.count()):
            item = self.y_list.item(idx)
            if item.checkState() == Qt.Checked:
                columns.append(item.text())
        return columns

    def refresh_series_configs(self) -> None:
        for y_column in self.checked_y_columns():
            x_column = self._nearest_left_x(y_column)
            if not x_column:
                continue
            if y_column not in self.series_by_y:
                self.series_by_y[y_column] = default_series(x_column, y_column, len(self.series_by_y))
            self.series_by_y[y_column].x = x_column
            self.series_by_y[y_column].label = self._column_name(y_column)
        self.schedule_render()

    def handle_plot_y_changed(self) -> None:
        self.refresh_series_configs()
        self.update_style_targets()
        self.refresh_cmap_column_list()
        self.schedule_render()

    def handle_plot_y_clicked(self, item: QListWidgetItem) -> None:
        if item is None:
            return
        QTimer.singleShot(0, lambda column=item.text(): self.select_series_for_y_column(column))

    def select_series_for_y_column(self, y_column: str) -> None:
        if not y_column or y_column not in self.checked_y_columns():
            self.set_status("Check a Y column to plot and edit its series.")
            return
        if y_column not in self.series_by_y:
            x_column = self._nearest_left_x(y_column)
            if not x_column:
                return
            self.series_by_y[y_column] = default_series(x_column, y_column, len(self.series_by_y))
        self.style_target_combo.setCurrentText(y_column)
        self._load_series_into_widgets(self.series_by_y[y_column])
        self.set_status(f"Editing series: {y_column}")

    def update_style_targets(self) -> None:
        current = self.style_target_combo.currentText()
        plotted = self.checked_y_columns()
        self.style_target_combo.blockSignals(True)
        self.style_target_combo.clear()
        self.style_target_combo.addItems(plotted)
        if current in plotted:
            self.style_target_combo.setCurrentText(current)
        self.style_target_combo.blockSignals(False)
        self._set_series_widgets_enabled(bool(plotted))
        target = self.style_target_combo.currentText()
        if target and target in self.series_by_y:
            self._load_series_into_widgets(self.series_by_y[target])

    def handle_style_target_changed(self) -> None:
        target = self.style_target_combo.currentText()
        if target and target in self.series_by_y:
            self._load_series_into_widgets(self.series_by_y[target])

    def _load_series_into_widgets(self, series: SeriesConfig) -> None:
        widgets = [
            self.series_label_edit,
            self.y_axis_combo,
            self.plot_type_combo,
            self.marker_combo,
            self.line_width_spin,
            self.marker_size_spin,
            self.series_y_offset_spin,
            self.series_alpha_spin,
            self.show_in_legend_check,
        ]
        for widget in widgets:
            widget.blockSignals(True)
        self.series_label_edit.setText(series.label)
        self.y_axis_combo.setCurrentText(series.y_axis)
        self.plot_type_combo.setCurrentText(series.plot_type)
        self.marker_combo.setCurrentText(series.marker)
        self.line_width_spin.setValue(series.line_width)
        self.marker_size_spin.setValue(series.marker_size)
        self.series_y_offset_spin.setValue(series.y_offset)
        self.series_alpha_spin.setValue(series.alpha)
        self.show_in_legend_check.setChecked(series.show_in_legend)
        for widget in widgets:
            widget.blockSignals(False)
        self._set_color_button(series.color)

    def _set_series_widgets_enabled(self, enabled: bool) -> None:
        for widget in (
            self.series_label_edit,
            self.y_axis_combo,
            self.plot_type_combo,
            self.marker_combo,
            self.line_width_spin,
            self.marker_size_spin,
            self.series_y_offset_spin,
            self.color_btn,
            self.series_alpha_spin,
            self.show_in_legend_check,
        ):
            widget.setEnabled(enabled)

    def apply_series_widget_state(self) -> None:
        target = self.style_target_combo.currentText()
        if not target or target not in self.series_by_y:
            return
        label = self.series_label_edit.text().strip()
        self.table.blockSignals(True)
        series = self.series_by_y[target]
        series.label = label or target
        series.y_axis = self.y_axis_combo.currentText()
        series.plot_type = self.plot_type_combo.currentText()
        series.marker = self.marker_combo.currentText()
        series.line_width = self.line_width_spin.value()
        series.marker_size = self.marker_size_spin.value()
        series.y_offset = self.series_y_offset_spin.value()
        series.alpha = self.series_alpha_spin.value()
        series.show_in_legend = self.show_in_legend_check.isChecked()
        col = list(map(str, self.df.columns)).index(target)
        name_item = self.table.item(NAME_ROW, col)
        if name_item is None:
            name_item = QTableWidgetItem("")
            self.table.setItem(NAME_ROW, col, name_item)
        name_item.setText(label)
        self.df.iat[NAME_ROW, col] = label
        self.table.blockSignals(False)
        self.schedule_render()

    def choose_series_color(self) -> None:
        target = self.style_target_combo.currentText()
        if not target or target not in self.series_by_y:
            return
        current = self.series_by_y[target].color
        color = QColorDialog.getColor(QColor(current), self, "Choose series color")
        if not color.isValid():
            return
        self.push_current_undo_state()
        color_name = color.name()
        self.series_by_y[target].color = color_name
        self.series_by_y[target].force_opaque = False
        self._set_color_button(color_name)
        self.schedule_render()
        self.update_undo_baseline()

    def apply_colormap_to_plotted_series(self) -> None:
        selected = self.selected_cmap_columns()
        if not selected:
            self.set_status("Select at least one series in the column list.")
            return
        self.push_current_undo_state()
        count = len(selected)
        target = self.style_target_combo.currentText()
        if self.cmap_alpha_only_check.isChecked():
            base_color = self.cmap_base_color_btn.text()
            a_start = self.cmap_alpha_start_spin.value()
            a_end = self.cmap_alpha_end_spin.value()
            dist = self.cmap_alpha_dist_combo.currentText()
            for idx, y_column in enumerate(selected):
                t = 0.5 if count == 1 else idx / (count - 1)
                alpha_val = self._alpha_dist(t, a_start, a_end, dist)
                series = self.series_by_y.get(y_column)
                if series is None:
                    x_column = self._nearest_left_x(y_column)
                    series = default_series(x_column, y_column, idx)
                    self.series_by_y[y_column] = series
                series.color = base_color
                series.alpha = round(alpha_val, 4)
                series.force_opaque = False
            if target in self.series_by_y:
                self._load_series_into_widgets(self.series_by_y[target])
            self.set_status(f"Applied alpha ({dist}, {a_start:.2f}–{a_end:.2f}) to {count} series.")
        else:
            cmap = mpl.colormaps[self.cmap_combo.currentText()]
            start = self.cmap_start_spin.value()
            end = self.cmap_end_spin.value()
            for idx, y_column in enumerate(selected):
                position = (start + end) / 2 if count == 1 else start + (end - start) * idx / (count - 1)
                color = to_hex(cmap(position))
                series = self.series_by_y.get(y_column)
                if series is None:
                    x_column = self._nearest_left_x(y_column)
                    series = default_series(x_column, y_column, idx)
                    self.series_by_y[y_column] = series
                series.color = color
                series.alpha = 1.0
                series.force_opaque = True
            if target in self.series_by_y:
                self._load_series_into_widgets(self.series_by_y[target])
            self.set_status(f"Applied {self.cmap_combo.currentText()} colormap to {count} series.")
        self.schedule_render()
        self.update_undo_baseline()

    def apply_recommended_palette_to_series(self) -> None:
        selected = self.selected_cmap_columns()
        if not selected:
            self.set_status("Select at least one series in the column list.")
            return
        palette_name = self.palette_combo.currentText()
        colors = self.recommended_colors(palette_name, len(selected))
        if not colors:
            self.set_status("Choose a recommended palette first.")
            return
        self.push_current_undo_state()
        for idx, y_column in enumerate(selected):
            series = self.series_by_y.get(y_column)
            if series is None:
                x_column = self._nearest_left_x(y_column)
                series = default_series(x_column, y_column, idx)
                self.series_by_y[y_column] = series
            series.color = colors[idx]
            series.alpha = 1.0
            series.force_opaque = True
        target = self.style_target_combo.currentText()
        if target in self.series_by_y:
            self._load_series_into_widgets(self.series_by_y[target])
        self.schedule_render()
        self.update_undo_baseline()
        self.set_status(f"Applied {palette_name} palette to {len(selected)} series.")

    def selected_cmap_columns(self) -> list[str]:
        return [
            self.cmap_column_list.item(i).text()
            for i in range(self.cmap_column_list.count())
            if self.cmap_column_list.item(i).checkState() == Qt.Checked
        ]

    def recommended_colors(self, palette_name: str, count: int) -> list[str]:
        palette = RECOMMENDED_PALETTES.get(palette_name, [])
        if count <= 0 or not palette:
            return []
        if count <= len(palette):
            return palette[:count]
        return [palette[idx % len(palette)] for idx in range(count)]

    @staticmethod
    def _alpha_dist(t: float, a_start: float, a_end: float, dist: str) -> float:
        """Map t∈[0,1] to an alpha value using the chosen distribution."""
        if dist == "Sqrt":
            # faster start, slower end — bigger gaps near a_start
            mapped = math.sqrt(t)
        elif dist == "Power²":
            # slower start, faster end — bigger gaps near a_end
            mapped = t ** 2
        elif dist == "Log":
            # logarithmic: rapid climb, then plateaus — similar to sqrt but more extreme
            mapped = math.log(1 + t * 9) / math.log(10)
        elif dist == "Geometric":
            # equal ratio between consecutive values — perceptually most uniform
            # avoids the "0.8 and 1.0 look the same" problem
            if a_start <= 0 or a_end <= 0:
                mapped = t
            else:
                mapped = (a_end / a_start) ** t
                return float(a_start * mapped)
        else:
            mapped = t  # Linear
        return float(a_start + (a_end - a_start) * mapped)

    def update_cmap_mode(self, alpha_only: bool) -> None:
        self.cmap_alpha_section.setVisible(alpha_only)
        self.cmap_color_section.setVisible(not alpha_only)

    def refresh_cmap_column_list(self) -> None:
        plotted = self.checked_y_columns()
        prev_checked = {
            self.cmap_column_list.item(i).text()
            for i in range(self.cmap_column_list.count())
            if self.cmap_column_list.item(i).checkState() == Qt.Checked
        }
        self.cmap_column_list.blockSignals(True)
        self.cmap_column_list.clear()
        for col in plotted:
            item = QListWidgetItem(col)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            state = Qt.Checked if (not prev_checked or col in prev_checked) else Qt.Unchecked
            item.setCheckState(state)
            self.cmap_column_list.addItem(item)
        self.cmap_column_list.blockSignals(False)

    def _cmap_select_all(self) -> None:
        for i in range(self.cmap_column_list.count()):
            self.cmap_column_list.item(i).setCheckState(Qt.Checked)

    def _cmap_select_none(self) -> None:
        for i in range(self.cmap_column_list.count()):
            self.cmap_column_list.item(i).setCheckState(Qt.Unchecked)

    def choose_cmap_base_color(self) -> None:
        current = self.cmap_base_color_btn.text()
        color = QColorDialog.getColor(QColor(current), self, "Choose base color")
        if not color.isValid():
            return
        self._set_cmap_base_color_button(color.name())

    def _set_cmap_base_color_button(self, color: str) -> None:
        self.cmap_base_color_btn.setText(color)
        qcolor = QColor(color)
        light = (0.299 * qcolor.red() + 0.587 * qcolor.green() + 0.114 * qcolor.blue()) > 150
        text_color = "black" if light else "white"
        self.cmap_base_color_btn.setStyleSheet(f"background-color: {color}; color: {text_color};")

    def choose_annotation_color(self) -> None:
        current = self.annotation_color_btn.text()
        color = QColorDialog.getColor(QColor(current), self, "Choose annotation color")
        if not color.isValid():
            return
        self.push_current_undo_state()
        self._set_annotation_color_button(color.name())
        self.update_selected_annotation()
        self.update_undo_baseline()

    def choose_y2_axis_color(self) -> None:
        current = self.y2_axis_color_btn.text()
        color = QColorDialog.getColor(QColor(current), self, "Choose Y2 axis color")
        if not color.isValid():
            return
        self.push_current_undo_state()
        self._set_y2_axis_color_button(color.name())
        self.schedule_render()
        self.update_undo_baseline()

    def choose_y_axis_color(self) -> None:
        current = self.y_axis_color_btn.text()
        color = QColorDialog.getColor(QColor(current), self, "Choose Y1 axis color")
        if not color.isValid():
            return
        self.push_current_undo_state()
        self._set_y_axis_color_button(color.name())
        self.schedule_render()
        self.update_undo_baseline()

    def add_annotation(self) -> None:
        if self.render_timer.isActive():
            self.render_timer.stop()
            self.render_plot()
        self.push_current_undo_state()
        x, y, x2, y2, width, height = self.default_annotation_geometry()
        annotation = AnnotationConfig(
            kind=self.annotation_kind_combo.currentText(),
            text=self.annotation_text_edit.text(),
            x=x,
            y=y,
            x2=x2,
            y2=y2,
            width=width,
            height=height,
            angle=self.annotation_angle_spin.value(),
            color=self.annotation_color_btn.text(),
            line_style=self.annotation_line_style_combo.currentText(),
            alpha=self.annotation_alpha_spin.value(),
            fill=self.annotation_fill_check.isChecked(),
            font_size=self.annotation_font_spin.value(),
            arrow_head_size=self.annotation_arrow_head_spin.value(),
        )
        self.annotations.append(annotation)
        self.refresh_annotation_list()
        self.annotation_list.setCurrentRow(len(self.annotations) - 1)
        self.render_plot()
        self.update_undo_baseline()
        self.set_status(f"Added {annotation.kind} annotation.")

    def default_annotation_geometry(self) -> tuple[float, float, float, float, float, float]:
        values = (
            self.annotation_x_spin.value(),
            self.annotation_y_spin.value(),
            self.annotation_x2_spin.value(),
            self.annotation_y2_spin.value(),
            self.annotation_w_spin.value(),
            self.annotation_h_spin.value(),
        )
        if any(abs(value) > 1e-12 for value in values[:2]):
            x, y, _, _, width, height = values
            return x, y, x + width, y + height, width, height

        kind = self.annotation_kind_combo.currentText()
        if kind in {"line", "arrow"}:
            x, y, width, height = 0.35, 0.72, 0.18, -0.18
        elif kind in {"box", "circle"}:
            x, y, width, height = 0.38, 0.56, 0.16, 0.16
        else:
            x, y = 0.42, 0.68
            width = self.annotation_w_spin.value()
            height = self.annotation_h_spin.value()
        return x, y, x + width, y + height, width, height

    def _annotation_display_geometry(self, annotation: AnnotationConfig) -> tuple[float, float, float, float]:
        ax = self.annotation_axes()
        if ax is None:
            return 0.0, 0.0, 0.0, 0.0
        axes_bbox = ax.get_window_extent()
        unit_px = max(min(axes_bbox.width, axes_bbox.height), 1.0)
        start_x, start_y = ax.transAxes.transform((annotation.x, annotation.y))
        return start_x, start_y, annotation.width * unit_px, annotation.height * unit_px

    def annotation_axes(self):
        if self.current_figure is None or not self.current_figure.axes:
            return None
        if self.current_broken_y_active():
            return self.current_figure.axes[1]
        return self.current_figure.axes[0]

    def current_broken_y_active(self) -> bool:
        if self.current_figure is None or len(self.current_figure.axes) < 2:
            return False
        if not self.plot_config.y_break_enabled or self.plot_config.y_scale != "linear":
            return False
        if any(series.y_axis == "right" for series in self.selected_series_configs()):
            return False
        values = (
            self.plot_config.y_break_lower_min,
            self.plot_config.y_break_lower_max,
            self.plot_config.y_break_upper_min,
            self.plot_config.y_break_upper_max,
        )
        if any(value is None for value in values):
            return False
        lower_min, lower_max, upper_min, upper_max = values
        return lower_min < lower_max < upper_min < upper_max

    def legend_axes(self):
        if self.current_figure is None or not self.current_figure.axes:
            return None
        return self.current_figure.axes[0]

    def remove_selected_annotation(self) -> None:
        rows = self.selected_annotation_indices()
        if not rows:
            return
        self.push_current_undo_state()
        removed_count = len(rows)
        for row in sorted(rows, reverse=True):
            self.annotations.pop(row)
        self.refresh_annotation_list()
        if self.annotations:
            self.annotation_list.setCurrentRow(min(rows[0], len(self.annotations) - 1))
        self.render_plot()
        self.update_undo_baseline()
        self.set_status(f"Removed {removed_count} annotation(s).")

    def copy_selected_annotation(self) -> bool:
        row = self.annotation_list.currentRow()
        if row < 0 or row >= len(self.annotations):
            return False
        self.annotation_clipboard = asdict(self.annotations[row])
        QApplication.clipboard().setText("GRAPH_DRAWER_ANNOTATION\t" + json.dumps(self.annotation_clipboard))
        self.set_status("Copied annotation.")
        return True

    def paste_annotation(self) -> bool:
        payload = self.annotation_clipboard
        text = QApplication.clipboard().text()
        if text.startswith("GRAPH_DRAWER_ANNOTATION\t"):
            try:
                payload = json.loads(text.split("\t", 1)[1])
            except json.JSONDecodeError:
                payload = self.annotation_clipboard
        if not payload:
            return False
        self.push_current_undo_state()
        annotation = AnnotationConfig(**payload)
        annotation.x += 0.03
        annotation.y -= 0.03
        annotation.x2 = annotation.x + annotation.width
        annotation.y2 = annotation.y + annotation.height
        self.annotations.append(annotation)
        self.refresh_annotation_list()
        self.annotation_list.setCurrentRow(len(self.annotations) - 1)
        self.render_plot()
        self.update_undo_baseline()
        self.set_status("Pasted annotation.")
        return True

    def copy_current_style(self) -> None:
        config = deepcopy(self.collect_plot_config())
        include_annotations = self.include_annotations_style_check.isChecked()
        self.style_clipboard = {
            "plot_config": config,
            "series_templates": deepcopy(self.selected_series_configs()),
            "annotations": deepcopy(self.annotations) if include_annotations else [],
            "has_annotations": include_annotations,
        }
        note = " with annotations" if include_annotations else ""
        self.set_status(f"Copied current style{note}.")

    def apply_copied_style(self) -> None:
        if not self.style_clipboard:
            self.set_status("Copy a style first.")
            return
        self.push_current_undo_state()
        current = deepcopy(self.collect_plot_config())
        style_config = deepcopy(self.style_clipboard["plot_config"])

        for attr in ("title",):
            setattr(style_config, attr, getattr(current, attr))

        include_annotations = self.include_annotations_style_check.isChecked() and self.style_clipboard.get("has_annotations")
        self.annotations = deepcopy(self.style_clipboard["annotations"]) if include_annotations else self.annotations
        style_config.annotations = self.annotations
        self.plot_config = style_config
        self._load_config_into_widgets()

        templates = self.style_clipboard.get("series_templates", [])
        checked = self.checked_y_columns()
        for idx, y_column in enumerate(checked):
            if not templates:
                break
            template = templates[min(idx, len(templates) - 1)]
            x_column = self._nearest_left_x(y_column)
            series = self.series_by_y.get(y_column) or default_series(x_column, y_column, idx)
            series.color = template.color
            series.plot_type = template.plot_type
            series.y_axis = template.y_axis
            series.marker = template.marker
            series.line_width = template.line_width
            series.marker_size = template.marker_size
            series.y_offset = template.y_offset
            series.alpha = template.alpha
            series.show_in_legend = template.show_in_legend
            series.x = x_column
            series.y = y_column
            series.label = self._column_name(y_column)
            self.series_by_y[y_column] = series

        self.refresh_annotation_list()
        self.update_style_targets()
        self.render_plot()
        self.update_undo_baseline()
        note = " and annotations" if include_annotations else ""
        self.set_status(f"Applied copied style{note}.")

    def refresh_annotation_list(self) -> None:
        current = self.annotation_list.currentRow()
        selected = set(self.selected_annotation_indices())
        self.annotation_list.blockSignals(True)
        self.annotation_list.clear()
        for idx, annotation in enumerate(self.annotations):
            item = QListWidgetItem(self.annotation_list_label(annotation, idx))
            item.setToolTip(self.annotation_list_tooltip(annotation, idx))
            self.annotation_list.addItem(item)
        if self.annotations:
            self.annotation_list.setCurrentRow(min(max(current, 0), len(self.annotations) - 1))
            for idx in selected:
                if idx < self.annotation_list.count():
                    self.annotation_list.item(idx).setSelected(True)
        self.annotation_list.blockSignals(False)

    def annotation_list_label(self, annotation: AnnotationConfig, index: int | None = None) -> str:
        prefix = f"{index + 1}. " if index is not None else ""
        text = " ".join(annotation.text.strip().split()) or annotation.kind
        if len(text) > 28:
            text = text[:25] + "..."
        return f"{prefix}{annotation.kind} | {text} | x={annotation.x:.3f}, y={annotation.y:.3f}"

    def annotation_list_tooltip(self, annotation: AnnotationConfig, index: int | None = None) -> str:
        prefix = f"Annotation {index + 1}\n" if index is not None else ""
        text = annotation.text.strip() or "(no text)"
        return (
            f"{prefix}"
            f"Type: {annotation.kind}\n"
            f"Text: {text}\n"
            f"Position: x={annotation.x:.4f}, y={annotation.y:.4f}\n"
            f"Size: width={annotation.width:.4f}, height={annotation.height:.4f}"
        )

    def update_annotation_list_item(self, row: int) -> None:
        if 0 <= row < len(self.annotations) and row < self.annotation_list.count():
            item = self.annotation_list.item(row)
            item.setText(self.annotation_list_label(self.annotations[row], row))
            item.setToolTip(self.annotation_list_tooltip(self.annotations[row], row))

    def selected_annotation_indices(self) -> list[int]:
        rows = sorted({self.annotation_list.row(item) for item in self.annotation_list.selectedItems()})
        if rows:
            return [row for row in rows if 0 <= row < len(self.annotations)]
        row = self.annotation_list.currentRow()
        return [row] if 0 <= row < len(self.annotations) else []

    def update_selected_annotation(self) -> None:
        if self.loading_annotation_inputs:
            return
        row = self.annotation_list.currentRow()
        if row < 0 or row >= len(self.annotations):
            return
        annotation = self.annotations[row]
        annotation.kind = self.annotation_kind_combo.currentText()
        annotation.text = self.annotation_text_edit.text()
        annotation.x = self.annotation_x_spin.value()
        annotation.y = self.annotation_y_spin.value()
        annotation.width = self.annotation_w_spin.value()
        annotation.height = self.annotation_h_spin.value()
        annotation.x2 = annotation.x + annotation.width
        annotation.y2 = annotation.y + annotation.height
        annotation.angle = self.annotation_angle_spin.value()
        annotation.color = self.annotation_color_btn.text()
        annotation.line_style = self.annotation_line_style_combo.currentText()
        annotation.alpha = self.annotation_alpha_spin.value()
        annotation.fill = self.annotation_fill_check.isChecked()
        annotation.font_size = self.annotation_font_spin.value()
        annotation.arrow_head_size = self.annotation_arrow_head_spin.value()
        self.update_annotation_list_item(row)
        self.schedule_render()

    def _set_annotation_color_button(self, color: str) -> None:
        self.annotation_color_btn.setText(color)
        qcolor = QColor(color)
        light = (0.299 * qcolor.red() + 0.587 * qcolor.green() + 0.114 * qcolor.blue()) > 150
        text_color = "black" if light else "white"
        self.annotation_color_btn.setStyleSheet(f"background-color: {color}; color: {text_color};")

    def _set_y2_axis_color_button(self, color: str) -> None:
        self.plot_config.y2_axis_color = color
        self.y2_axis_color_btn.setText(color)
        qcolor = QColor(color)
        light = (0.299 * qcolor.red() + 0.587 * qcolor.green() + 0.114 * qcolor.blue()) > 150
        text_color = "black" if light else "white"
        self.y2_axis_color_btn.setStyleSheet(f"background-color: {color}; color: {text_color};")

    def _set_y_axis_color_button(self, color: str) -> None:
        self.plot_config.y_axis_color = color
        self.y_axis_color_btn.setText(color)
        qcolor = QColor(color)
        light = (0.299 * qcolor.red() + 0.587 * qcolor.green() + 0.114 * qcolor.blue()) > 150
        text_color = "black" if light else "white"
        self.y_axis_color_btn.setStyleSheet(f"background-color: {color}; color: {text_color};")

    def _set_color_button(self, color: str) -> None:
        self.color_btn.setText(color)
        qcolor = QColor(color)
        light = (0.299 * qcolor.red() + 0.587 * qcolor.green() + 0.114 * qcolor.blue()) > 150
        text_color = "black" if light else "white"
        self.color_btn.setStyleSheet(f"background-color: {color}; color: {text_color};")

    def handle_preset_changed(self, preset_name: str) -> None:
        if preset_name == "Custom":
            return
        apply_preset(self.plot_config, preset_name)
        for widget in (
            self.width_spin,
            self.height_spin,
            self.plot_width_spin,
            self.plot_height_spin,
            self.plot_ratio_lock_check,
            self.plot_ratio_preset_combo,
            self.dpi_spin,
            self.title_size_spin,
            self.axis_size_spin,
            self.tick_size_spin,
            self.legend_size_spin,
            self.y_label_offset_spin,
        ):
            widget.blockSignals(True)
        self.width_spin.setValue(self.plot_config.width_mm)
        self.height_spin.setValue(self.plot_config.height_mm)
        self.plot_width_spin.setValue(self.plot_config.plot_width_mm)
        self.plot_height_spin.setValue(self.plot_config.plot_height_mm)
        self.plot_ratio_lock_check.setChecked(self.plot_config.plot_ratio_locked)
        self.plot_ratio_preset_combo.setCurrentText(
            self.plot_config.plot_ratio_preset if self.plot_config.plot_ratio_preset in PLOT_RATIO_PRESETS else "Current"
        )
        self.dpi_spin.setValue(self.plot_config.dpi)
        self.title_size_spin.setValue(self.plot_config.title_size)
        self.axis_size_spin.setValue(self.plot_config.axis_size)
        self.tick_size_spin.setValue(self.plot_config.tick_size)
        self.legend_size_spin.setValue(self.plot_config.legend_size)
        self.y_label_offset_spin.setValue(self.plot_config.y_label_offset_mm)
        for widget in (
            self.width_spin,
            self.height_spin,
            self.plot_width_spin,
            self.plot_height_spin,
            self.dpi_spin,
            self.title_size_spin,
            self.axis_size_spin,
            self.tick_size_spin,
            self.legend_size_spin,
            self.y_label_offset_spin,
        ):
            widget.blockSignals(False)
        self.schedule_render()

    def handle_plot_ratio_lock_toggled(self, checked: bool) -> None:
        if checked:
            self.plot_config.plot_aspect_ratio = self.current_plot_ratio()

    def handle_plot_ratio_preset_changed(self, preset_name: str) -> None:
        ratio = self.plot_ratio_from_preset(preset_name)
        if ratio is None:
            self.plot_config.plot_aspect_ratio = self.current_plot_ratio()
            return
        self.plot_config.plot_aspect_ratio = ratio
        self.apply_plot_ratio_from_width(ratio)

    def handle_plot_dimension_changed(self, changed: str) -> None:
        if self.updating_plot_ratio or not self.plot_ratio_lock_check.isChecked():
            return
        ratio = self.active_plot_ratio()
        if ratio <= 0:
            return
        if changed == "width":
            self.apply_plot_ratio_from_width(ratio)
        else:
            self.apply_plot_ratio_from_height(ratio)

    def active_plot_ratio(self) -> float:
        ratio = self.plot_ratio_from_preset(self.plot_ratio_preset_combo.currentText())
        if ratio is None:
            ratio = self.plot_config.plot_aspect_ratio or self.current_plot_ratio()
        return max(ratio, 1e-9)

    def current_plot_ratio(self) -> float:
        height = max(self.plot_height_spin.value(), 1e-9)
        return max(self.plot_width_spin.value() / height, 1e-9)

    def plot_ratio_from_preset(self, preset_name: str) -> float | None:
        return PLOT_RATIO_PRESETS.get(preset_name)

    def apply_plot_ratio_from_width(self, ratio: float) -> None:
        self.updating_plot_ratio = True
        self.plot_height_spin.blockSignals(True)
        self.plot_height_spin.setValue(max(self.plot_height_spin.minimum(), min(self.plot_height_spin.maximum(), self.plot_width_spin.value() / ratio)))
        self.plot_height_spin.blockSignals(False)
        self.updating_plot_ratio = False

    def apply_plot_ratio_from_height(self, ratio: float) -> None:
        self.updating_plot_ratio = True
        self.plot_width_spin.blockSignals(True)
        self.plot_width_spin.setValue(max(self.plot_width_spin.minimum(), min(self.plot_width_spin.maximum(), self.plot_height_spin.value() * ratio)))
        self.plot_width_spin.blockSignals(False)
        self.updating_plot_ratio = False

    def collect_plot_config(self) -> PlotConfig:
        self.plot_config.preset = self.preset_combo.currentText()
        self.plot_config.title = self.title_edit.text()
        self.plot_config.x_label = self.x_label_edit.text()
        self.plot_config.y_label = self.y_label_edit.text()
        self.plot_config.y2_label = self.y2_label_edit.text()
        self.plot_config.width_mm = self.width_spin.value()
        self.plot_config.height_mm = self.height_spin.value()
        self.plot_config.plot_width_mm = self.plot_width_spin.value()
        self.plot_config.plot_height_mm = self.plot_height_spin.value()
        self.plot_config.plot_ratio_locked = self.plot_ratio_lock_check.isChecked()
        self.plot_config.plot_ratio_preset = self.plot_ratio_preset_combo.currentText()
        self.plot_config.plot_aspect_ratio = self.active_plot_ratio() if self.plot_config.plot_ratio_locked else self.current_plot_ratio()
        self.plot_config.dpi = self.dpi_spin.value()
        self.plot_config.title_size = self.title_size_spin.value()
        self.plot_config.axis_size = self.axis_size_spin.value()
        self.plot_config.tick_size = self.tick_size_spin.value()
        self.plot_config.legend_size = self.legend_size_spin.value()
        self.plot_config.y_label_offset_mm = self.y_label_offset_spin.value()
        self.plot_config.y_axis_color = self.y_axis_color_btn.text()
        self.plot_config.y2_axis_color = self.y2_axis_color_btn.text()
        self.plot_config.pad_left_mm = self.pad_left_spin.value()
        self.plot_config.pad_right_mm = self.pad_right_spin.value()
        self.plot_config.pad_top_mm = self.pad_top_spin.value()
        self.plot_config.pad_bottom_mm = self.pad_bottom_spin.value()
        self.plot_config.fixed_plot_area = self.fixed_plot_area_check.isChecked()
        self.plot_config.plot_margin_left_mm = self.plot_margin_left_spin.value()
        self.plot_config.plot_margin_right_mm = self.plot_margin_right_spin.value()
        self.plot_config.plot_margin_top_mm = self.plot_margin_top_spin.value()
        self.plot_config.plot_margin_bottom_mm = self.plot_margin_bottom_spin.value()
        self.plot_config.x_scale = self.x_scale_combo.currentText()
        self.plot_config.y_scale = self.y_scale_combo.currentText()
        self.plot_config.y2_scale = self.y2_scale_combo.currentText()
        self.plot_config.x_scale_divisor = self._positive_float_or_default(self.x_scale_divisor_edit, 1.0)
        self.plot_config.y_scale_divisor = self._positive_float_or_default(self.y_scale_divisor_edit, 1.0)
        self.plot_config.y2_scale_divisor = self._positive_float_or_default(self.y2_scale_divisor_edit, 1.0)
        self.plot_config.x_min = self._optional_float(self.x_min_edit)
        self.plot_config.x_max = self._optional_float(self.x_max_edit)
        self.plot_config.y_min = self._optional_float(self.y_min_edit)
        self.plot_config.y_max = self._optional_float(self.y_max_edit)
        self.plot_config.y2_min = self._optional_float(self.y2_min_edit)
        self.plot_config.y2_max = self._optional_float(self.y2_max_edit)
        self.plot_config.x_tick_interval = self._optional_float(self.x_tick_interval_edit)
        self.plot_config.y_tick_interval = self._optional_float(self.y_tick_interval_edit)
        self.plot_config.y2_tick_interval = self._optional_float(self.y2_tick_interval_edit)
        self.plot_config.x_minor_divisions = self.x_minor_divisions_spin.value()
        self.plot_config.y_minor_divisions = self.y_minor_divisions_spin.value()
        self.plot_config.y2_minor_divisions = self.y2_minor_divisions_spin.value()
        self.plot_config.x_break_enabled = self.x_break_check.isChecked()
        self.plot_config.x_break_left_min = self._optional_float(self.x_break_left_min_edit)
        self.plot_config.x_break_left_max = self._optional_float(self.x_break_left_max_edit)
        self.plot_config.x_break_right_min = self._optional_float(self.x_break_right_min_edit)
        self.plot_config.x_break_right_max = self._optional_float(self.x_break_right_max_edit)
        self.plot_config.x_break_gap = self.x_break_gap_spin.value()
        self.plot_config.y_break_enabled = self.y_break_check.isChecked()
        self.plot_config.y_break_lower_min = self._optional_float(self.y_break_lower_min_edit)
        self.plot_config.y_break_lower_max = self._optional_float(self.y_break_lower_max_edit)
        self.plot_config.y_break_upper_min = self._optional_float(self.y_break_upper_min_edit)
        self.plot_config.y_break_upper_max = self._optional_float(self.y_break_upper_max_edit)
        self.plot_config.y_break_gap = self.y_break_gap_spin.value()
        self.plot_config.y_offset_step = self.y_offset_spin.value()
        self.plot_config.show_x_tick_labels = self.show_x_tick_labels_check.isChecked()
        self.plot_config.show_y_tick_labels = self.show_y_tick_labels_check.isChecked()
        self.plot_config.show_y2_tick_labels = self.show_y2_tick_labels_check.isChecked()
        self.plot_config.annotations = self.annotations
        self.plot_config.grid = self.grid_check.isChecked()
        self.plot_config.legend = self.legend_check.isChecked()
        self.plot_config.trim_whitespace = self.trim_check.isChecked()
        self.plot_config.transparent = self.transparent_check.isChecked()
        return self.plot_config

    def selected_series_configs(self) -> list[SeriesConfig]:
        series_configs: list[SeriesConfig] = []
        for idx, y_column in enumerate(self.checked_y_columns()):
            x_column = self._nearest_left_x(y_column)
            if not x_column:
                continue
            series = self.series_by_y.get(y_column) or default_series(x_column, y_column, idx)
            series.x = x_column
            series.y = y_column
            series.label = self._column_name(y_column)
            series_configs.append(series)
        return series_configs

    def schedule_render(self) -> None:
        self.render_timer.start()

    def render_plot(self) -> None:
        config = self.collect_plot_config()
        result = render_figure(self._plot_dataframe(), config, self.selected_series_configs())
        self.current_figure = result.figure
        self.annotation_artists = result.annotation_artists
        self.legend_artist = result.legend_artist
        self._replace_canvas(result.figure)
        self.draw_annotation_handles()
        if result.warnings:
            self.set_status(" | ".join(result.warnings[:2]))
        elif not self.df.empty:
            figure_width = result.figure.get_figwidth() * 25.4
            figure_height = result.figure.get_figheight() * 25.4
            self.set_status(
                f"Rendered {len(self.selected_series_configs())} series at {figure_width:g} x {figure_height:g} mm"
            )

    def center_plot_box(self) -> None:
        config = self.collect_plot_config()
        if not config.fixed_plot_area:
            self.set_status("Enable Lock plot box size before centering the plot box.")
            return
        total_width = config.width_mm + config.pad_left_mm + config.pad_right_mm
        total_height = config.height_mm + config.pad_top_mm + config.pad_bottom_mm
        left = (total_width - config.plot_width_mm) / 2 - config.pad_left_mm
        bottom = (total_height - config.plot_height_mm) / 2 - config.pad_bottom_mm
        right = config.width_mm - config.plot_width_mm - left
        top = config.height_mm - config.plot_height_mm - bottom
        if min(left, right, top, bottom) < 0:
            self.set_status("Plot box is larger than the canvas; reduce plot size or increase canvas size.")
            return
        self.push_current_undo_state()
        self.set_plot_margins(left, right, top, bottom)
        self.render_plot()
        self.update_undo_baseline()
        self.set_status("Centered plot box in canvas.")

    def center_content(self) -> None:
        config = deepcopy(self.collect_plot_config())
        if not config.fixed_plot_area:
            self.set_status("Enable Lock plot box size before centering content.")
            return

        result, renderer = self.render_for_layout_measure(config)
        bbox = self.figure_content_bbox(result.figure, result.annotation_artists, result.legend_artist, renderer)
        if bbox is None or not result.figure.axes:
            self.set_status("No visible content to center.")
            return

        px_per_mm = result.figure.dpi / 25.4
        figure_width_px = result.figure.get_figwidth() * result.figure.dpi
        figure_height_px = result.figure.get_figheight() * result.figure.dpi
        dx_mm = (figure_width_px / 2 - bbox.x0 - bbox.width / 2) / px_per_mm
        dy_mm = (figure_height_px / 2 - bbox.y0 - bbox.height / 2) / px_per_mm

        left = config.plot_margin_left_mm + dx_mm
        bottom = config.plot_margin_bottom_mm + dy_mm
        available_width = config.width_mm - config.plot_width_mm
        available_height = config.height_mm - config.plot_height_mm
        if available_width < 0 or available_height < 0:
            self.set_status("Plot box is larger than the canvas; reduce plot size or increase canvas size.")
            return
        left = max(0.0, min(available_width, left))
        bottom = max(0.0, min(available_height, bottom))
        right = available_width - left
        top = available_height - bottom

        self.push_current_undo_state()
        self.set_plot_margins(left, right, top, bottom)
        self.render_plot()
        self.update_undo_baseline()
        self.set_status("Centered visible content in canvas.")

    def fit_canvas_to_content(self) -> None:
        config = deepcopy(self.collect_plot_config())
        if not config.fixed_plot_area:
            self.set_status("Enable Lock plot box size before fitting the canvas.")
            return

        self.render_timer.stop()
        result, renderer = self.render_for_layout_measure(config)
        figure = result.figure
        bbox = self.figure_content_bbox(figure, result.annotation_artists, result.legend_artist, renderer)
        if bbox is None:
            self.set_status("No visible content to fit.")
            return

        pad_mm = 2.0
        px_per_mm = figure.dpi / 25.4
        pad_px = pad_mm * px_per_mm
        plot_bbox = self.plot_area_bbox(figure)
        if plot_bbox is None:
            self.set_status("No plot box to fit.")
            return
        new_width_mm = max(
            (bbox.width + 2 * pad_px) / px_per_mm - config.pad_left_mm - config.pad_right_mm,
            config.plot_width_mm,
        )
        new_height_mm = max(
            (bbox.height + 2 * pad_px) / px_per_mm - config.pad_top_mm - config.pad_bottom_mm,
            config.plot_height_mm,
        )
        new_width_mm = max(self.width_spin.minimum(), min(self.width_spin.maximum(), new_width_mm))
        new_height_mm = max(self.height_spin.minimum(), min(self.height_spin.maximum(), new_height_mm))
        new_left_mm = max(0.0, (plot_bbox.x0 - bbox.x0 + pad_px) / px_per_mm - config.pad_left_mm)
        new_bottom_mm = max(0.0, (plot_bbox.y0 - bbox.y0 + pad_px) / px_per_mm - config.pad_bottom_mm)
        new_right_mm = max(0.0, new_width_mm - config.plot_width_mm - new_left_mm)
        new_top_mm = max(0.0, new_height_mm - config.plot_height_mm - new_bottom_mm)

        self.push_current_undo_state()
        self.set_canvas_size(new_width_mm, new_height_mm)
        self.set_plot_margins(new_left_mm, new_right_mm, new_top_mm, new_bottom_mm)
        self.render_plot()
        self.update_undo_baseline()
        self.set_status(f"Fit canvas to content: {new_width_mm:g} x {new_height_mm:g} mm.")

    def render_for_layout_measure(self, config: PlotConfig):
        result = render_figure(self._plot_dataframe(), config, self.selected_series_configs())
        canvas = FigureCanvasAgg(result.figure)
        canvas.draw()
        return result, canvas.get_renderer()

    def figure_content_bbox(self, figure: Figure, annotation_artists: list, legend_artist, renderer) -> Bbox | None:
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
        valid = [bbox for bbox in bboxes if bbox is not None and bbox.width > 0 and bbox.height > 0]
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
        width = max(self.width_spin.minimum(), min(self.width_spin.maximum(), width))
        height = max(self.height_spin.minimum(), min(self.height_spin.maximum(), height))
        for widget in (self.width_spin, self.height_spin):
            widget.blockSignals(True)
        self.width_spin.setValue(width)
        self.height_spin.setValue(height)
        for widget in (self.width_spin, self.height_spin):
            widget.blockSignals(False)
        self.plot_config.width_mm = width
        self.plot_config.height_mm = height

    def set_plot_margins(self, left: float, right: float, top: float, bottom: float) -> None:
        left = max(0.0, left)
        right = max(0.0, right)
        top = max(0.0, top)
        bottom = max(0.0, bottom)
        widgets = (
            self.plot_margin_left_spin,
            self.plot_margin_right_spin,
            self.plot_margin_top_spin,
            self.plot_margin_bottom_spin,
        )
        for widget in widgets:
            widget.blockSignals(True)
        self.plot_margin_left_spin.setValue(left)
        self.plot_margin_right_spin.setValue(right)
        self.plot_margin_top_spin.setValue(top)
        self.plot_margin_bottom_spin.setValue(bottom)
        for widget in widgets:
            widget.blockSignals(False)
        self.plot_config.plot_margin_left_mm = left
        self.plot_config.plot_margin_right_mm = right
        self.plot_config.plot_margin_top_mm = top
        self.plot_config.plot_margin_bottom_mm = bottom

    def _replace_canvas(self, figure: Figure) -> None:
        if self.toolbar is not None:
            self.plot_layout.removeWidget(self.toolbar)
            self.toolbar.setParent(None)
            self.toolbar.deleteLater()
        if self.canvas is not None:
            self.plot_layout.removeWidget(self.canvas)
            self.canvas.setParent(None)
            self.canvas.deleteLater()

        self.preview_figure_width_in = figure.get_figwidth()
        self.preview_figure_height_in = figure.get_figheight()
        figure.set_dpi(PREVIEW_DPI)
        figure.set_size_inches(self.preview_figure_width_in, self.preview_figure_height_in, forward=False)

        self.canvas = FigureCanvasQTAgg(figure)
        self.canvas.setStyleSheet("background-color: white; border: 1px solid #b8b8b8;")
        self.canvas.installEventFilter(self)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.canvas.mpl_connect("button_press_event", self.handle_canvas_press)
        self.canvas.mpl_connect("motion_notify_event", self.handle_canvas_motion)
        self.canvas.mpl_connect("button_release_event", self.handle_canvas_release)
        self.canvas.mpl_connect("scroll_event", self.handle_canvas_scroll)
        self.canvas.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.preview_base_width_px = max(80, int(self.preview_figure_width_in * PREVIEW_DPI))
        self.preview_base_height_px = max(60, int(self.preview_figure_height_in * PREVIEW_DPI))
        self.preview_plot_height_px = max(1, int(self.plot_config.plot_height_mm / 25.4 * PREVIEW_DPI))
        self.plot_layout.addWidget(self.toolbar)
        self.plot_layout.addWidget(self.canvas, 0, Qt.AlignCenter)
        self.update_canvas_size(center=True)
        self.canvas.draw_idle()

    def update_canvas_size(self, *args, center: bool = False) -> None:
        if self.canvas is None or self.current_figure is None:
            return
        viewport = self.plot_scroll.viewport().size()
        old_width = max(self.plot_host.width(), 1)
        old_height = max(self.plot_host.height(), 1)
        hbar = self.plot_scroll.horizontalScrollBar()
        vbar = self.plot_scroll.verticalScrollBar()
        center_x_ratio = (hbar.value() + viewport.width() / 2) / old_width
        center_y_ratio = (vbar.value() + viewport.height() / 2) / old_height

        base_w = max(80, self.preview_base_width_px)
        base_h = max(60, self.preview_base_height_px)
        toolbar_hint = self.toolbar.sizeHint() if self.toolbar is not None else self.canvas.sizeHint()
        margins = self.plot_layout.contentsMargins()
        spacing = self.plot_layout.spacing()
        usable_w = max(1, viewport.width() - margins.left() - margins.right() - 24)
        usable_h = max(1, viewport.height() - toolbar_hint.height() - spacing - margins.top() - margins.bottom() - 24)
        if self.fit_preview_check.isChecked():
            scale = min(usable_w / base_w, usable_h / base_h, 1.0)
            scale = max(scale, 0.1)
        else:
            scale = self.preview_zoom_spin.value() / 100.0
        canvas_w = max(80, int(base_w * scale))
        canvas_h = max(60, int(base_h * scale))
        self.current_figure.set_dpi(PREVIEW_DPI * scale)
        self.current_figure.set_size_inches(self.preview_figure_width_in, self.preview_figure_height_in, forward=False)
        self.canvas.setFixedSize(canvas_w, canvas_h)
        self.refresh_annotation_artists()
        host_w = max(viewport.width(), canvas_w + margins.left() + margins.right(), toolbar_hint.width() + margins.left() + margins.right())
        host_h = max(
            viewport.height(),
            canvas_h + toolbar_hint.height() + spacing + margins.top() + margins.bottom(),
        )
        self.plot_host.setFixedSize(host_w, host_h)

        if center:
            QTimer.singleShot(0, self.center_preview)
        else:
            QTimer.singleShot(0, lambda: self.restore_preview_center(center_x_ratio, center_y_ratio))
        self.canvas.draw_idle()

    def center_preview(self) -> None:
        hbar = self.plot_scroll.horizontalScrollBar()
        vbar = self.plot_scroll.verticalScrollBar()
        hbar.setValue((hbar.maximum() + hbar.minimum()) // 2)
        vbar.setValue((vbar.maximum() + vbar.minimum()) // 2)

    def restore_preview_center(self, x_ratio: float, y_ratio: float) -> None:
        viewport = self.plot_scroll.viewport().size()
        hbar = self.plot_scroll.horizontalScrollBar()
        vbar = self.plot_scroll.verticalScrollBar()
        x = int(self.plot_host.width() * x_ratio - viewport.width() / 2)
        y = int(self.plot_host.height() * y_ratio - viewport.height() / 2)
        hbar.setValue(max(hbar.minimum(), min(hbar.maximum(), x)))
        vbar.setValue(max(vbar.minimum(), min(vbar.maximum(), y)))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "fit_preview_check") and self.fit_preview_check.isChecked():
            self.update_canvas_size()

    def handle_canvas_press(self, event) -> None:
        if event.button != 1 or event.x is None or event.y is None:
            return
        if self.toolbar is not None and getattr(self.toolbar, "mode", ""):
            return
        if self.legend_artist is not None:
            contains, _ = self.legend_artist.contains(event)
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
        indices = list(range(len(self.annotation_artists) - 1, -1, -1))
        current = self.annotation_list.currentRow()
        if 0 <= current < len(self.annotation_artists):
            indices = [current] + [idx for idx in indices if idx != current]
        return indices

    def annotation_body_hit(self, idx: int, event) -> bool:
        return self.annotation_hit_distance(idx, event) is not None

    def best_annotation_hit(self, event) -> int | None:
        best_idx: int | None = None
        best_distance: float | None = None
        for idx in self.annotation_hit_order():
            distance = self.annotation_hit_distance(idx, event)
            if distance is None:
                continue
            if best_distance is None or distance < best_distance - 1e-9 or (abs(distance - best_distance) <= 1e-9 and idx > (best_idx or -1)):
                best_idx = idx
                best_distance = distance
        return best_idx

    def start_plot_box_drag(self, event) -> bool:
        config = self.collect_plot_config()
        ax = self.current_figure.axes[0] if self.current_figure is not None and self.current_figure.axes else None
        if not self.plot_box_drag_requested():
            return False
        if not config.fixed_plot_area or ax is None or event.inaxes not in self.current_figure.axes:
            return False
        if event.x is None or event.y is None:
            return False
        remaining_width = config.width_mm - config.plot_width_mm
        remaining_height = config.height_mm - config.plot_height_mm
        if remaining_width < 0 or remaining_height < 0:
            self.set_status("Plot box is larger than the canvas; cannot drag inside canvas.")
            return False
        self.push_current_undo_state()
        self.dragging_plot_box = True
        self.drag_plot_start_pixels = (float(event.x), float(event.y))
        self.drag_plot_original_margins = (
            config.plot_margin_left_mm,
            config.plot_margin_right_mm,
            config.plot_margin_top_mm,
            config.plot_margin_bottom_mm,
        )
        self.set_status("Dragging plot box.")
        return True

    def plot_box_drag_requested(self) -> bool:
        return bool(QApplication.keyboardModifiers() & Qt.AltModifier)

    def start_annotation_drag(self, idx: int, event, mode: str) -> None:
        self.drag_start = self.event_to_axes_fraction(event)
        if self.drag_start is None:
            return
        self.push_current_undo_state()
        selected = self.selected_annotation_indices()
        group_move = mode == "move" and self.annotation_group_drag_requested() and idx in selected and len(selected) > 1
        annotation = self.annotations[idx]
        self.drag_annotation_index = idx
        self.drag_start_pixels = (float(event.x), float(event.y))
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
                    self.annotations[row].x,
                    self.annotations[row].y,
                    self.annotations[row].x2,
                    self.annotations[row].y2,
                )
                for row in selected
            }
            self.set_status(f"Dragging {len(selected)} annotations.")
        else:
            self.drag_group_original = None
            self.select_single_annotation(idx)
            self.set_status("Dragging annotation.")

    def annotation_group_drag_requested(self) -> bool:
        modifiers = QApplication.keyboardModifiers()
        return bool(modifiers & (Qt.ShiftModifier | Qt.ControlModifier))

    def select_single_annotation(self, idx: int) -> None:
        self.annotation_list.blockSignals(True)
        self.annotation_list.clearSelection()
        if 0 <= idx < self.annotation_list.count():
            self.annotation_list.setCurrentRow(idx)
            self.annotation_list.item(idx).setSelected(True)
        self.annotation_list.blockSignals(False)
        self.update_annotation_inputs(idx)

    def start_legend_drag(self, event) -> bool:
        start = self.event_to_axes_fraction(event, self.legend_axes())
        anchor = self.legend_anchor_axes_fraction()
        if start is None or anchor is None:
            return False
        self.push_current_undo_state()
        self.dragging_legend = True
        self.drag_legend_start = start
        self.drag_legend_original = anchor
        self.set_status("Dragging legend.")
        return True

    def legend_anchor_axes_fraction(self) -> tuple[float, float] | None:
        ax = self.legend_axes()
        if ax is None or self.legend_artist is None or self.canvas is None:
            return None
        renderer = self.canvas.get_renderer()
        bbox = self.legend_artist.get_window_extent(renderer=renderer)
        x, y = ax.transAxes.inverted().transform((bbox.x0, bbox.y1))
        return float(x), float(y)

    def annotation_hit_test(self, index: int, x_px: float, y_px: float) -> bool:
        return self.annotation_hit_distance_for_point(index, x_px, y_px) is not None

    def annotation_hit_distance(self, index: int, event) -> float | None:
        if event.x is None or event.y is None:
            return None
        return self.annotation_hit_distance_for_point(index, float(event.x), float(event.y))

    def annotation_hit_distance_for_point(self, index: int, x_px: float, y_px: float) -> float | None:
        if index < 0 or index >= len(self.annotations):
            return None
        annotation = self.annotations[index]
        if annotation.kind in {"text", "textbox"}:
            return self.text_annotation_hit_distance(index, x_px, y_px)
        if annotation.kind in {"line", "arrow"}:
            return self.line_annotation_hit_distance(annotation, x_px, y_px)
        if annotation.kind == "circle":
            return self.circle_annotation_hit_distance(annotation, x_px, y_px)
        if annotation.kind == "box":
            return self.box_annotation_hit_distance(annotation, x_px, y_px)
        return None

    def text_annotation_hit_distance(self, index: int, x_px: float, y_px: float) -> float | None:
        if index >= len(self.annotation_artists) or self.canvas is None:
            return None
        artist = self.annotation_artists[index]
        renderer = self.canvas.get_renderer()
        bbox_patch = getattr(artist, "get_bbox_patch", lambda: None)()
        bbox = bbox_patch.get_window_extent(renderer=renderer) if bbox_patch is not None else artist.get_window_extent(renderer=renderer)
        padding = 4.0
        if bbox.x0 - padding <= x_px <= bbox.x1 + padding and bbox.y0 - padding <= y_px <= bbox.y1 + padding:
            if bbox.x0 <= x_px <= bbox.x1 and bbox.y0 <= y_px <= bbox.y1:
                return 0.0
            dx = max(bbox.x0 - x_px, 0.0, x_px - bbox.x1)
            dy = max(bbox.y0 - y_px, 0.0, y_px - bbox.y1)
            return math.hypot(dx, dy)
        return None

    def line_annotation_hit_distance(self, annotation: AnnotationConfig, x_px: float, y_px: float) -> float | None:
        start_x, start_y, width_px, height_px = self._annotation_display_geometry(annotation)
        end_x, end_y = self._rotated_endpoint(start_x, start_y, width_px, height_px, annotation.angle)
        distance = self.point_to_segment_distance(x_px, y_px, start_x, start_y, end_x, end_y)
        return distance if distance <= 8.0 else None

    def box_annotation_hit_distance(self, annotation: AnnotationConfig, x_px: float, y_px: float) -> float | None:
        start_x, start_y, width_px, height_px = self._annotation_display_geometry(annotation)
        local_x, local_y = self.annotation_local_point(x_px, y_px, start_x, start_y, annotation.angle)
        left, right = sorted((0.0, width_px))
        bottom, top = sorted((0.0, height_px))
        inside = left <= local_x <= right and bottom <= local_y <= top
        border_distance = self.rectangle_border_distance(local_x, local_y, left, right, bottom, top)
        tolerance = 7.0
        if annotation.fill and inside:
            return 0.0
        if border_distance <= tolerance:
            return border_distance
        return None

    def circle_annotation_hit_distance(self, annotation: AnnotationConfig, x_px: float, y_px: float) -> float | None:
        start_x, start_y, width_px, height_px = self._annotation_display_geometry(annotation)
        center_x = start_x + width_px / 2
        center_y = start_y + height_px / 2
        local_x, local_y = self.annotation_local_point(x_px, y_px, center_x, center_y, annotation.angle)
        rx = abs(width_px) / 2
        ry = abs(height_px) / 2
        if rx <= 1e-9 or ry <= 1e-9:
            return None
        normalized = math.hypot(local_x / rx, local_y / ry)
        boundary_distance = abs(normalized - 1.0) * min(rx, ry)
        if annotation.fill and normalized <= 1.0:
            return 0.0
        return boundary_distance if boundary_distance <= 7.0 else None

    def annotation_local_point(self, x_px: float, y_px: float, origin_x: float, origin_y: float, angle: float) -> tuple[float, float]:
        dx = x_px - origin_x
        dy = y_px - origin_y
        if not angle:
            return dx, dy
        radians = math.radians(-angle)
        cos_a = math.cos(radians)
        sin_a = math.sin(radians)
        return dx * cos_a - dy * sin_a, dx * sin_a + dy * cos_a

    @staticmethod
    def point_to_segment_distance(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
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
    def rectangle_border_distance(x: float, y: float, left: float, right: float, bottom: float, top: float) -> float:
        if left <= x <= right and bottom <= y <= top:
            return min(abs(x - left), abs(x - right), abs(y - bottom), abs(y - top))
        nearest_x = max(left, min(right, x))
        nearest_y = max(bottom, min(top, y))
        return math.hypot(x - nearest_x, y - nearest_y)

    def annotation_handle_hit_test(self, x_px: float, y_px: float) -> tuple[int, str] | None:
        idx = self.annotation_list.currentRow()
        if idx < 0 or idx >= len(self.annotations):
            return None
        handles = self.annotation_handle_points(self.annotations[idx])
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
        if self.drag_annotation_index is None or self.drag_start is None or self.drag_original is None:
            return
        current = self.event_to_axes_fraction(event)
        if current is None:
            return
        idx = self.drag_annotation_index
        if idx >= len(self.annotations) or idx >= len(self.annotation_artists):
            return
        x0, y0, x20, y20, w0, h0, angle0 = self.drag_original
        annotation = self.annotations[idx]
        artist = self.annotation_artists[idx]
        dx = current[0] - self.drag_start[0]
        dy = current[1] - self.drag_start[1]
        if self.ctrl_is_pressed():
            if abs(dx) >= abs(dy):
                dy = 0.0
            else:
                dx = 0.0

        if self.drag_group_original and self.drag_mode == "move":
            for row, (gx, gy, gx2, gy2) in self.drag_group_original.items():
                if row >= len(self.annotations) or row >= len(self.annotation_artists):
                    continue
                group_annotation = self.annotations[row]
                group_annotation.x = gx + dx
                group_annotation.y = gy + dy
                group_annotation.x2 = gx2 + dx
                group_annotation.y2 = gy2 + dy
                self.update_annotation_artist(group_annotation, self.annotation_artists[row])
            self.draw_annotation_handles(redraw=False)
            self.canvas.draw_idle()
            return

        if self.drag_mode == "resize":
            annotation.x = x0
            annotation.y = y0
            annotation.angle = angle0
            self.resize_annotation_from_event(annotation, event, angle0, self.ctrl_is_pressed())
        elif self.drag_mode == "rotate":
            annotation.x = x0
            annotation.y = y0
            annotation.width = w0
            annotation.height = h0
            annotation.angle = self.rotate_annotation_from_event(annotation, event, w0, h0, self.ctrl_is_pressed())
        else:
            annotation.x = x0 + dx
            annotation.y = y0 + dy
            annotation.x2 = x20 + dx
            annotation.y2 = y20 + dy
        annotation.x2 = annotation.x + annotation.width
        annotation.y2 = annotation.y + annotation.height
        self.update_annotation_artist(annotation, artist)
        self.draw_annotation_handles(redraw=False)
        self.canvas.draw_idle()

    def handle_plot_box_motion(self, event) -> None:
        if (
            self.drag_plot_start_pixels is None
            or self.drag_plot_original_margins is None
            or self.current_figure is None
            or self.canvas is None
            or event.x is None
            or event.y is None
        ):
            return
        width_px = max(float(self.canvas.width()), 1.0)
        height_px = max(float(self.canvas.height()), 1.0)
        figure_width_mm = self.current_figure.get_figwidth() * 25.4
        figure_height_mm = self.current_figure.get_figheight() * 25.4
        dx_mm = (float(event.x) - self.drag_plot_start_pixels[0]) / width_px * figure_width_mm
        dy_mm = (float(event.y) - self.drag_plot_start_pixels[1]) / height_px * figure_height_mm
        left0, _, _, bottom0 = self.drag_plot_original_margins
        available_width = self.plot_config.width_mm - self.plot_config.plot_width_mm
        available_height = self.plot_config.height_mm - self.plot_config.plot_height_mm
        if available_width < 0 or available_height < 0:
            return
        left = max(0.0, min(available_width, left0 + dx_mm))
        bottom = max(0.0, min(available_height, bottom0 + dy_mm))
        right = available_width - left
        top = available_height - bottom
        self.set_plot_margins(left, right, top, bottom)
        self.apply_current_plot_box_layout()
        self.refresh_annotation_artists()
        self.canvas.draw_idle()

    def apply_current_plot_box_layout(self) -> None:
        if self.current_figure is None:
            return
        total_width = max(self.plot_config.width_mm + self.plot_config.pad_left_mm + self.plot_config.pad_right_mm, 1e-9)
        total_height = max(self.plot_config.height_mm + self.plot_config.pad_top_mm + self.plot_config.pad_bottom_mm, 1e-9)
        left = (self.plot_config.pad_left_mm + self.plot_config.plot_margin_left_mm) / total_width
        right = left + self.plot_config.plot_width_mm / total_width
        bottom = (self.plot_config.pad_bottom_mm + self.plot_config.plot_margin_bottom_mm) / total_height
        top = bottom + self.plot_config.plot_height_mm / total_height
        if left < right and bottom < top:
            self.current_figure.subplots_adjust(left=left, right=right, bottom=bottom, top=top)

    def refresh_annotation_artists(self) -> None:
        for annotation, artist in zip(self.annotations, self.annotation_artists):
            self.update_annotation_artist(annotation, artist)
        self.draw_annotation_handles(redraw=False)

    def handle_legend_motion(self, event) -> None:
        if self.drag_legend_start is None or self.drag_legend_original is None or self.legend_artist is None:
            return
        ax = self.legend_axes()
        current = self.event_to_axes_fraction(event, ax)
        if current is None or ax is None or self.canvas is None:
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
        self.plot_config.legend_anchor_x = x
        self.plot_config.legend_anchor_y = y
        self.legend_artist._loc = 2
        self.legend_artist.set_bbox_to_anchor((x, y), transform=ax.transAxes)
        self.canvas.draw_idle()

    def event_to_axes_fraction(self, event, ax=None) -> tuple[float, float] | None:
        if ax is None:
            ax = self.annotation_axes()
        if ax is None or event.x is None or event.y is None:
            return None
        x, y = ax.transAxes.inverted().transform((event.x, event.y))
        return float(x), float(y)

    def _rotated_endpoint(self, start_x: float, start_y: float, width_px: float, height_px: float, angle: float) -> tuple[float, float]:
        if not angle:
            return start_x + width_px, start_y + height_px
        point = Affine2D().rotate_deg_around(0.0, 0.0, angle).transform((width_px, height_px))
        return start_x + float(point[0]), start_y + float(point[1])

    def ctrl_is_pressed(self) -> bool:
        return bool(QApplication.keyboardModifiers() & Qt.ControlModifier)

    def resize_annotation_from_event(self, annotation: AnnotationConfig, event, angle: float, constrain: bool) -> None:
        ax = self.annotation_axes()
        if ax is None or event.x is None or event.y is None:
            return
        axes_bbox = ax.get_window_extent()
        unit_px = max(min(axes_bbox.width, axes_bbox.height), 1.0)
        start_x, start_y, _, _ = self._annotation_display_geometry(annotation)
        vector = (float(event.x) - start_x, float(event.y) - start_y)
        if angle:
            vector = Affine2D().rotate_deg_around(0.0, 0.0, -angle).transform(vector)
        if constrain:
            vector = (vector[0], 0.0) if abs(vector[0]) >= abs(vector[1]) else (0.0, vector[1])
        annotation.width = float(vector[0]) / unit_px
        annotation.height = float(vector[1]) / unit_px

    def rotate_annotation_from_event(self, annotation: AnnotationConfig, event, width: float, height: float, constrain: bool) -> float:
        if event.x is None or event.y is None:
            return annotation.angle
        start_x, start_y, _, _ = self._annotation_display_geometry(annotation)
        target_angle = math.degrees(math.atan2(float(event.y) - start_y, float(event.x) - start_x))
        base_angle = math.degrees(math.atan2(height, width)) if width or height else 0.0
        angle = target_angle - base_angle
        if constrain:
            angle = round(angle / 90.0) * 90.0
        return angle

    def update_annotation_artist(self, annotation: AnnotationConfig, artist) -> None:
        if annotation.kind == "arrow":
            start_x, start_y, width_px, height_px = self._annotation_display_geometry(annotation)
            end_x, end_y = self._rotated_endpoint(start_x, start_y, width_px, height_px, annotation.angle)
            artist.xy = (end_x, end_y)
            artist.set_position((start_x, start_y))
        elif annotation.kind == "line":
            start_x, start_y, width_px, height_px = self._annotation_display_geometry(annotation)
            end_x, end_y = self._rotated_endpoint(start_x, start_y, width_px, height_px, annotation.angle)
            artist.set_positions((start_x, start_y), (end_x, end_y))
        elif annotation.kind == "box":
            start_x, start_y, width_px, height_px = self._annotation_display_geometry(annotation)
            artist.set_xy((start_x, start_y))
            artist.set_width(width_px)
            artist.set_height(height_px)
            artist.set_transform(Affine2D().rotate_deg_around(start_x, start_y, annotation.angle) + IdentityTransform())
        elif annotation.kind == "circle":
            start_x, start_y, width_px, height_px = self._annotation_display_geometry(annotation)
            artist.center = (start_x + width_px / 2, start_y + height_px / 2)
            artist.width = abs(width_px)
            artist.height = abs(height_px)
            artist.angle = annotation.angle
        else:
            artist.set_position((annotation.x, annotation.y))
            artist.set_rotation(annotation.angle)

    def annotation_handle_points(self, annotation: AnnotationConfig) -> dict[str, tuple[float, float]]:
        start_x, start_y, width_px, height_px = self._annotation_display_geometry(annotation)
        resize_x, resize_y = self._rotated_endpoint(start_x, start_y, width_px, height_px, annotation.angle)
        top_x, top_y = self._rotated_endpoint(start_x, start_y, width_px * 0.5, height_px, annotation.angle)
        return {"resize": (resize_x, resize_y), "rotate": (top_x, top_y + 24)}

    def draw_annotation_handles(self, redraw: bool = True) -> None:
        for handle in self.annotation_handle_artists:
            handle.remove()
        self.annotation_handle_artists = []
        ax = self.annotation_axes()
        idx = self.annotation_list.currentRow()
        if ax is None or idx < 0 or idx >= len(self.annotations):
            if redraw and self.canvas is not None:
                self.canvas.draw_idle()
            return
        annotation = self.annotations[idx]
        start_x, start_y, width_px, height_px = self._annotation_display_geometry(annotation)
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
                transform=Affine2D().rotate_deg_around(start_x, start_y, annotation.angle) + IdentityTransform(),
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
        if redraw and self.canvas is not None:
            self.canvas.draw_idle()

    def handle_canvas_scroll(self, event) -> None:
        zoom_delta = self.preview_zoom_delta(event)
        if zoom_delta == 0:
            return
        if not self.ctrl_is_pressed():
            return
        if self.fit_preview_check.isChecked():
            self.fit_preview_check.setChecked(False)
        new_zoom = self.preview_zoom_spin.value() + zoom_delta
        self.preview_zoom_spin.setValue(max(self.preview_zoom_spin.minimum(), min(self.preview_zoom_spin.maximum(), new_zoom)))
        self.set_status(f"Preview zoom: {self.preview_zoom_spin.value()}%")

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
            self.update_undo_baseline()
            self.set_status("Moved legend.")
            return
        if self.dragging_plot_box:
            self.dragging_plot_box = False
            self.drag_plot_start_pixels = None
            self.drag_plot_original_margins = None
            self.render_plot()
            self.update_undo_baseline()
            self.set_status("Moved plot box.")
            return
        if self.drag_annotation_index is None:
            return
        self.update_annotation_inputs(self.drag_annotation_index)
        self.drag_annotation_index = None
        self.drag_start = None
        self.drag_start_pixels = None
        self.drag_mode = None
        self.drag_original = None
        self.drag_group_original = None
        self.update_undo_baseline()
        self.set_status("Moved annotation.")

    def update_annotation_inputs(self, index: int) -> None:
        if index < 0 or index >= len(self.annotations):
            return
        annotation = self.annotations[index]
        self.loading_annotation_inputs = True
        widgets = (
            self.annotation_kind_combo,
            self.annotation_text_edit,
            self.annotation_x_spin,
            self.annotation_y_spin,
            self.annotation_x2_spin,
            self.annotation_y2_spin,
            self.annotation_w_spin,
            self.annotation_h_spin,
            self.annotation_angle_spin,
            self.annotation_font_spin,
            self.annotation_arrow_head_spin,
            self.annotation_line_style_combo,
            self.annotation_alpha_spin,
            self.annotation_fill_check,
        )
        for widget in widgets:
            widget.blockSignals(True)
        self.annotation_kind_combo.setCurrentText(annotation.kind)
        self.annotation_text_edit.setText(annotation.text)
        self.annotation_x_spin.setValue(annotation.x)
        self.annotation_y_spin.setValue(annotation.y)
        self.annotation_x2_spin.setValue(annotation.x2)
        self.annotation_y2_spin.setValue(annotation.y2)
        self.annotation_w_spin.setValue(annotation.width)
        self.annotation_h_spin.setValue(annotation.height)
        self.annotation_angle_spin.setValue(annotation.angle)
        self.annotation_font_spin.setValue(annotation.font_size)
        self.annotation_arrow_head_spin.setValue(annotation.arrow_head_size)
        self.annotation_line_style_combo.setCurrentText(annotation.line_style)
        self.annotation_alpha_spin.setValue(annotation.alpha)
        self.annotation_fill_check.setChecked(annotation.fill)
        for widget in widgets:
            widget.blockSignals(False)
        self._set_annotation_color_button(annotation.color)
        self.loading_annotation_inputs = False
        self.draw_annotation_handles()

    def export_current_figure(self) -> None:
        filters = "PNG (*.png);;PDF (*.pdf);;SVG (*.svg);;TIFF (*.tif *.tiff);;EPS (*.eps)"
        path, _ = QFileDialog.getSaveFileName(self, "Export figure", str(self.last_folder / "figure.png"), filters)
        if not path:
            return
        self.last_folder = Path(path).parent
        self.render_timer.stop()
        config = self.collect_plot_config()
        result = render_figure(self._plot_dataframe(), config, self.selected_series_configs())
        export_figure(result.figure, path, config)
        self.current_figure = result.figure
        self.annotation_artists = result.annotation_artists
        self.legend_artist = result.legend_artist
        self._replace_canvas(result.figure)
        self.draw_annotation_handles()
        self.set_status(f"Exported figure: {path}")

    def export_all_figures(self) -> None:
        self.save_active_figure_state()
        if not self.project_figures:
            QMessageBox.information(self, "Export all figures", "No figures to export.")
            return

        folder = QFileDialog.getExistingDirectory(self, "Export all figures", str(self.last_folder))
        if not folder:
            return

        output_dir = Path(folder)
        self.last_folder = output_dir
        self.render_timer.stop()

        exported_paths: list[Path] = []
        warnings: list[str] = []
        reserved_paths: set[Path] = set()
        for idx, figure in enumerate(self.project_figures, start=1):
            config = deepcopy(figure.plot_config)
            config.annotations = deepcopy(figure.annotations)
            result = render_figure(
                self._project_plot_dataframe(figure.df),
                config,
                self._project_series_configs(figure),
            )
            base_name = f"{idx:02d}_{self._safe_export_filename(figure.name)}"
            path = self._unique_export_path(output_dir / f"{base_name}.png", reserved_paths)
            reserved_paths.add(path)
            export_figure(result.figure, path, config)
            exported_paths.append(path)
            warnings.extend(f"{figure.name}: {warning}" for warning in result.warnings)

        message = f"Exported {len(exported_paths)} figures to {output_dir}"
        if warnings:
            message += f" | {warnings[0]}"
        self.set_status(message)

    def _project_plot_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        if len(df) <= DATA_START_ROW:
            return pd.DataFrame(columns=df.columns)
        return df.iloc[DATA_START_ROW:].reset_index(drop=True)

    def _project_column_role(self, df: pd.DataFrame, column: str) -> str:
        if column not in df.columns or len(df) <= ROLE_ROW:
            return ""
        value = str(df.at[ROLE_ROW, column]).strip().upper()
        if value.startswith("X"):
            return "X"
        if value.startswith("Y"):
            return "Y"
        return ""

    def _project_column_name(self, df: pd.DataFrame, column: str) -> str:
        if column in df.columns and len(df) > NAME_ROW:
            value = str(df.at[NAME_ROW, column]).strip()
            if value:
                return value
        return column

    def _project_nearest_left_x(self, df: pd.DataFrame, y_column: str) -> str:
        columns = list(map(str, df.columns))
        if y_column not in columns:
            return ""
        y_idx = columns.index(y_column)
        for idx in range(y_idx - 1, -1, -1):
            column = columns[idx]
            if self._project_column_role(df, column) == "X":
                return column
        return ""

    def _project_series_configs(self, figure: ProjectFigure) -> list[SeriesConfig]:
        series_configs: list[SeriesConfig] = []
        for idx, y_column in enumerate(figure.checked_y):
            if y_column not in figure.df.columns:
                continue
            x_column = self._project_nearest_left_x(figure.df, y_column)
            if not x_column:
                continue
            series = deepcopy(figure.series_by_y.get(y_column) or default_series(x_column, y_column, idx))
            series.x = x_column
            series.y = y_column
            series.label = self._project_column_name(figure.df, y_column)
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

    def set_annotation_handles_visible(self, visible: bool) -> None:
        for handle in self.annotation_handle_artists:
            handle.set_visible(visible)
        if self.canvas is not None:
            self.canvas.draw_idle()

    def save_project(self) -> None:
        self.save_active_figure_state()
        if not self.project_figures:
            QMessageBox.information(self, "Save project", "Paste or load data first.")
            return
        if self.current_project_path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Save project", str(self.last_folder / "graph_project.json"), "JSON (*.json)")
            if not path:
                return
            self.current_project_path = Path(path)
        self.write_project(self.current_project_path)
        self.last_folder = self.current_project_path.parent
        self.set_status(f"Saved project: {self.current_project_path}")

    def new_project(self) -> None:
        if self._is_modified:
            reply = QMessageBox.question(
                self,
                "New project",
                "Discard unsaved changes and start a new project?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        self.render_timer.stop()
        self.create_blank_sheet()
        self.project_figures = [self.capture_current_figure("Figure 1")]
        self.active_figure_index = 0
        self.current_project_path = None
        self.undo_stack.clear()
        self.refresh_figure_list()
        self.load_project_figure(self.project_figures[self.active_figure_index])
        self.undo_baseline = self.current_workspace_snapshot()
        self._is_modified = False
        self.set_status("Started a new project.")

    def write_project(self, path: Path) -> None:
        if path.suffix.lower() != ".json":
            path = path.with_suffix(".json")
            self.current_project_path = path
        payload = {
            "schema_version": 2,
            "active_figure_index": self.active_figure_index,
            "figures": [self.project_figure_to_payload(figure) for figure in self.project_figures],
        }
        with open(path, "w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, ensure_ascii=False)
        self._is_modified = False

    def save_project_as(self) -> None:
        self.save_active_figure_state()
        if not self.project_figures:
            QMessageBox.information(self, "Save project", "Paste or load data first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save project", str(self.last_folder / "graph_project.json"), "JSON (*.json)")
        if not path:
            return
        self.current_project_path = Path(path)
        self.write_project(self.current_project_path)
        self.last_folder = self.current_project_path.parent
        self.set_status(f"Saved project: {self.current_project_path}")

    def load_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load project", str(self.last_folder), "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as file:
                payload = json.load(file)
            figures, active_index = self.project_figures_from_payload(payload, Path(path).stem)
        except Exception as exc:
            QMessageBox.warning(self, "Load project", str(exc))
            return

        self.project_figures = figures
        self.active_figure_index = max(0, min(active_index, len(self.project_figures) - 1))
        self.refresh_figure_list()
        self.load_project_figure(self.project_figures[self.active_figure_index])
        self.last_folder = Path(path).parent
        self.current_project_path = Path(path)
        self.undo_stack.clear()
        self.undo_baseline = self.current_workspace_snapshot()
        self._is_modified = False
        self.set_status(f"Loaded project: {path}")

    def project_figure_to_payload(self, figure: ProjectFigure) -> dict:
        config = deepcopy(figure.plot_config)
        config.annotations = deepcopy(figure.annotations)
        return {
            "name": figure.name,
            "data": dataframe_to_payload(figure.df),
            "plot_config": asdict(config),
            "series_config": [asdict(series) for series in figure.series_by_y.values()],
            "checked_y": figure.checked_y,
        }

    def project_figures_from_payload(self, payload: dict, fallback_name: str) -> tuple[list[ProjectFigure], int]:
        if "figures" not in payload:
            df, plot_config, series_configs = project_from_payload(payload)
            annotations = plot_config.annotations or []
            return [
                ProjectFigure(
                    name=fallback_name or "Figure 1",
                    df=df,
                    plot_config=plot_config,
                    series_by_y={series.y: series for series in series_configs},
                    checked_y=[series.y for series in series_configs],
                    annotations=annotations,
                )
            ], 0

        figures: list[ProjectFigure] = []
        for idx, item in enumerate(payload.get("figures", [])):
            df = dataframe_from_payload(item.get("data", {}))
            plot_payload = item.get("plot_config", {}).copy()
            annotation_payload = plot_payload.pop("annotations", []) or []
            annotations = [AnnotationConfig(**annotation) for annotation in annotation_payload]
            plot_config = PlotConfig(**plot_payload)
            plot_config.annotations = annotations
            series_configs = [SeriesConfig(**series) for series in item.get("series_config", [])]
            figures.append(
                ProjectFigure(
                    name=item.get("name") or f"Figure {idx + 1}",
                    df=df,
                    plot_config=plot_config,
                    series_by_y={series.y: series for series in series_configs},
                    checked_y=item.get("checked_y") or [series.y for series in series_configs],
                    annotations=annotations,
                )
            )
        if not figures:
            figures.append(
                ProjectFigure(
                    name="Figure 1",
                    df=self.blank_dataframe(),
                    plot_config=PlotConfig(),
                    series_by_y={},
                    checked_y=[],
                    annotations=[],
                )
            )
        return figures, int(payload.get("active_figure_index", 0))

    def _load_config_into_widgets(self) -> None:
        widgets = (
            self.preset_combo,
            self.title_edit,
            self.x_label_edit,
            self.y_label_edit,
            self.y2_label_edit,
            self.width_spin,
            self.height_spin,
            self.plot_width_spin,
            self.plot_height_spin,
            self.dpi_spin,
            self.title_size_spin,
            self.axis_size_spin,
            self.tick_size_spin,
            self.legend_size_spin,
            self.y_label_offset_spin,
            self.y_axis_color_btn,
            self.y2_axis_color_btn,
            self.pad_left_spin,
            self.pad_right_spin,
            self.pad_top_spin,
            self.pad_bottom_spin,
            self.fixed_plot_area_check,
            self.plot_margin_left_spin,
            self.plot_margin_right_spin,
            self.plot_margin_top_spin,
            self.plot_margin_bottom_spin,
            self.x_scale_combo,
            self.y_scale_combo,
            self.y2_scale_combo,
            self.x_scale_divisor_edit,
            self.y_scale_divisor_edit,
            self.y2_scale_divisor_edit,
            self.x_min_edit,
            self.x_max_edit,
            self.y_min_edit,
            self.y_max_edit,
            self.y2_min_edit,
            self.y2_max_edit,
            self.x_tick_interval_edit,
            self.x_minor_divisions_spin,
            self.y_tick_interval_edit,
            self.y_minor_divisions_spin,
            self.y2_tick_interval_edit,
            self.y2_minor_divisions_spin,
            self.x_break_check,
            self.x_break_left_min_edit,
            self.x_break_left_max_edit,
            self.x_break_right_min_edit,
            self.x_break_right_max_edit,
            self.x_break_gap_spin,
            self.y_break_check,
            self.y_break_lower_min_edit,
            self.y_break_lower_max_edit,
            self.y_break_upper_min_edit,
            self.y_break_upper_max_edit,
            self.y_break_gap_spin,
            self.y_offset_spin,
            self.show_x_tick_labels_check,
            self.show_y_tick_labels_check,
            self.show_y2_tick_labels_check,
            self.grid_check,
            self.legend_check,
            self.trim_check,
            self.transparent_check,
        )
        for widget in widgets:
            widget.blockSignals(True)
        self.preset_combo.setCurrentText(self.plot_config.preset if self.plot_config.preset in PRESETS else "Custom")
        self.title_edit.setText(self.plot_config.title)
        self.x_label_edit.setText(self.plot_config.x_label)
        self.y_label_edit.setText(self.plot_config.y_label)
        self.y2_label_edit.setText(self.plot_config.y2_label)
        self.width_spin.setValue(self.plot_config.width_mm)
        self.height_spin.setValue(self.plot_config.height_mm)
        self.plot_width_spin.setValue(self.plot_config.plot_width_mm)
        self.plot_height_spin.setValue(self.plot_config.plot_height_mm)
        self.dpi_spin.setValue(self.plot_config.dpi)
        self.title_size_spin.setValue(self.plot_config.title_size)
        self.axis_size_spin.setValue(self.plot_config.axis_size)
        self.tick_size_spin.setValue(self.plot_config.tick_size)
        self.legend_size_spin.setValue(self.plot_config.legend_size)
        self.y_label_offset_spin.setValue(self.plot_config.y_label_offset_mm)
        self._set_y_axis_color_button(self.plot_config.y_axis_color)
        self._set_y2_axis_color_button(self.plot_config.y2_axis_color)
        self.pad_left_spin.setValue(self.plot_config.pad_left_mm)
        self.pad_right_spin.setValue(self.plot_config.pad_right_mm)
        self.pad_top_spin.setValue(self.plot_config.pad_top_mm)
        self.pad_bottom_spin.setValue(self.plot_config.pad_bottom_mm)
        self.fixed_plot_area_check.setChecked(self.plot_config.fixed_plot_area)
        self.plot_margin_left_spin.setValue(self.plot_config.plot_margin_left_mm)
        self.plot_margin_right_spin.setValue(self.plot_config.plot_margin_right_mm)
        self.plot_margin_top_spin.setValue(self.plot_config.plot_margin_top_mm)
        self.plot_margin_bottom_spin.setValue(self.plot_config.plot_margin_bottom_mm)
        self.x_scale_combo.setCurrentText(self.plot_config.x_scale)
        self.y_scale_combo.setCurrentText(self.plot_config.y_scale)
        self.y2_scale_combo.setCurrentText(self.plot_config.y2_scale)
        self.x_scale_divisor_edit.setText(str(self.plot_config.x_scale_divisor))
        self.y_scale_divisor_edit.setText(str(self.plot_config.y_scale_divisor))
        self.y2_scale_divisor_edit.setText(str(self.plot_config.y2_scale_divisor))
        self.x_min_edit.setText("" if self.plot_config.x_min is None else str(self.plot_config.x_min))
        self.x_max_edit.setText("" if self.plot_config.x_max is None else str(self.plot_config.x_max))
        self.y_min_edit.setText("" if self.plot_config.y_min is None else str(self.plot_config.y_min))
        self.y_max_edit.setText("" if self.plot_config.y_max is None else str(self.plot_config.y_max))
        self.y2_min_edit.setText("" if self.plot_config.y2_min is None else str(self.plot_config.y2_min))
        self.y2_max_edit.setText("" if self.plot_config.y2_max is None else str(self.plot_config.y2_max))
        self.x_tick_interval_edit.setText("" if self.plot_config.x_tick_interval is None else str(self.plot_config.x_tick_interval))
        self.x_minor_divisions_spin.setValue(self.plot_config.x_minor_divisions)
        self.y_tick_interval_edit.setText("" if self.plot_config.y_tick_interval is None else str(self.plot_config.y_tick_interval))
        self.y_minor_divisions_spin.setValue(self.plot_config.y_minor_divisions)
        self.y2_tick_interval_edit.setText("" if self.plot_config.y2_tick_interval is None else str(self.plot_config.y2_tick_interval))
        self.y2_minor_divisions_spin.setValue(self.plot_config.y2_minor_divisions)
        self.x_break_check.setChecked(self.plot_config.x_break_enabled)
        self.x_break_left_min_edit.setText("" if self.plot_config.x_break_left_min is None else str(self.plot_config.x_break_left_min))
        self.x_break_left_max_edit.setText("" if self.plot_config.x_break_left_max is None else str(self.plot_config.x_break_left_max))
        self.x_break_right_min_edit.setText("" if self.plot_config.x_break_right_min is None else str(self.plot_config.x_break_right_min))
        self.x_break_right_max_edit.setText("" if self.plot_config.x_break_right_max is None else str(self.plot_config.x_break_right_max))
        self.x_break_gap_spin.setValue(self.plot_config.x_break_gap)
        self.y_break_check.setChecked(self.plot_config.y_break_enabled)
        self.y_break_lower_min_edit.setText("" if self.plot_config.y_break_lower_min is None else str(self.plot_config.y_break_lower_min))
        self.y_break_lower_max_edit.setText("" if self.plot_config.y_break_lower_max is None else str(self.plot_config.y_break_lower_max))
        self.y_break_upper_min_edit.setText("" if self.plot_config.y_break_upper_min is None else str(self.plot_config.y_break_upper_min))
        self.y_break_upper_max_edit.setText("" if self.plot_config.y_break_upper_max is None else str(self.plot_config.y_break_upper_max))
        self.y_break_gap_spin.setValue(self.plot_config.y_break_gap)
        self.y_offset_spin.setValue(self.plot_config.y_offset_step)
        self.show_x_tick_labels_check.setChecked(self.plot_config.show_x_tick_labels)
        self.show_y_tick_labels_check.setChecked(self.plot_config.show_y_tick_labels)
        self.show_y2_tick_labels_check.setChecked(self.plot_config.show_y2_tick_labels)
        self.grid_check.setChecked(self.plot_config.grid)
        self.legend_check.setChecked(self.plot_config.legend)
        self.trim_check.setChecked(self.plot_config.trim_whitespace)
        self.transparent_check.setChecked(self.plot_config.transparent)
        for widget in widgets:
            widget.blockSignals(False)

    def set_status(self, message: str) -> None:
        self.statusBar().showMessage(message)

    def _optional_float(self, edit: QLineEdit) -> float | None:
        text = edit.text().strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            self.set_status(f"Ignoring invalid numeric limit: {text}")
            return None

    def _positive_float_or_default(self, edit: QLineEdit, default: float) -> float:
        text = edit.text().strip()
        if not text:
            return default
        try:
            value = float(text)
        except ValueError:
            self.set_status(f"Ignoring invalid divisor: {text}")
            return default
        if value <= 0:
            self.set_status(f"Ignoring non-positive divisor: {text}")
            return default
        return value

    def _double_spin(self, value: float, minimum: float, maximum: float, decimals: int) -> QDoubleSpinBox:
        spin = NoWheelDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setSingleStep(0.1 if decimals else 1.0)
        spin.setValue(value)
        return spin

    def _int_spin(self, value: int, minimum: int, maximum: int) -> QSpinBox:
        spin = NoWheelSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin
