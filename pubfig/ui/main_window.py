"""PySide6 application for Graph_drawer."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
import math
from pathlib import Path
import re

import matplotlib as mpl
from matplotlib.colors import to_hex
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.patches import Ellipse, Rectangle
from matplotlib.transforms import Affine2D, Bbox, IdentityTransform
import pandas as pd
from PySide6.QtCore import QEvent, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QImage,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
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
    QTableWidgetItem,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..data_parser import ParsedTable, parse_clipboard_grid, parse_table_text
from ..annotation_geometry import annotation_display_geometry, rotated_endpoint
from ..legend_layout import legend_row_ids
from ..model import (
    Graph,
    ProjectDocument,
    Sheet,
    TreeNode,
    find_node,
    find_parent,
    first_leaf,
    new_id,
)
from ..plot_config import (
    RECOMMENDED_PALETTES,
    AnnotationConfig,
    LegendEntryConfig,
    PlotConfig,
    SeriesConfig,
    annotation_config_from_payload,
    apply_preset,
    default_series,
    plot_config_from_payload,
    series_config_from_payload,
)
from ..project_io import (
    document_to_payload,
    load_payload_into_model as decode_project_payload,
    read_project as read_project_document,
    write_json_atomic,
)
from ..rendering import ExportJob, RenderCoordinator, RenderRequest
from ..sheet_data import (
    DATA_START_ROW,
    NAME_ROW,
    ROLE_ROW,
    blank_dataframe as make_blank_dataframe,
    column_name,
    column_role,
    nearest_left_x,
    next_column_name,
    plot_dataframe,
    with_metadata_rows,
)
from ..table_view import SpreadsheetTableWidget, TableSelectionRange
from ..theme import THEME_NAMES, apply_theme, color_swatch_style, normalize_theme_name
from ..undo import UndoManager
from .annotation_settings import AnnotationSettingsPanel
from .binding import signals_blocked
from .figure_settings import FigureSettingsPanel, PLOT_RATIO_PRESETS
from .series_settings import SeriesSettingsPanel
from .widgets import (
    LegendEditorDialog,
    NoWheelSpinBox,
    ProjectTreeWidget,
)


PREVIEW_DPI = 100
RECENT_FILES_LIMIT = 10
SAVED_STYLES_SETTINGS_KEY = "saved_styles_v1"
SAVED_STYLES_SCHEMA_VERSION = 1
AUTOSAVE_INTERVAL_MS = 120_000
DATA_FILE_SUFFIXES = {".csv", ".txt", ".tsv", ".dat"}


class GraphDrawerWindow(QMainWindow):
    @property
    def sheets(self) -> dict[str, Sheet]:
        return self.document.sheets

    @sheets.setter
    def sheets(self, value: dict[str, Sheet]) -> None:
        self.document.sheets = value

    @property
    def graphs(self) -> dict[str, Graph]:
        return self.document.graphs

    @graphs.setter
    def graphs(self, value: dict[str, Graph]) -> None:
        self.document.graphs = value

    @property
    def tree_root(self) -> TreeNode:
        return self.document.tree_root

    @tree_root.setter
    def tree_root(self, value: TreeNode) -> None:
        self.document.tree_root = value

    @property
    def active_node_id(self) -> str | None:
        return self.document.active_node_id

    @active_node_id.setter
    def active_node_id(self, value: str | None) -> None:
        self.document.active_node_id = value

    @property
    def restoring_undo(self) -> bool:
        return self.undo_history.is_restoring

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("pubfig — Untitled project")
        self.resize(1500, 900)
        self.setAcceptDrops(True)

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
        self.drag_mode: str | None = None
        self.drag_original: tuple[float, float, float, float, float, float, float] | None = None
        self.drag_group_original: dict[int, tuple[float, float, float, float]] | None = None
        self.annotation_clipboard: dict | None = None
        self.document = ProjectDocument()
        self.rendering = RenderCoordinator()
        self.active_graph_id: str | None = None
        self.active_sheet_id: str | None = None
        self._tree_items: dict[str, "QTreeWidgetItem"] = {}
        self.loading_project_figure = False
        self.style_clipboard: dict | None = None
        self.special_text_target: QWidget | None = None
        self._hover_hint = ""
        self.settings = QSettings("pubfig", "pubfig")
        app = QApplication.instance()
        active_theme = app.property("pubfigTheme") if app is not None else None
        if active_theme is None:
            active_theme = self.settings.value("interface_theme", "light")
        self.interface_theme = normalize_theme_name(active_theme)
        self.autosave_path = Path.home() / ".pubfig_autosave.json"
        self.updating_plot_ratio = False
        self.series_by_y: dict[str, SeriesConfig] = {}
        self.current_figure: Figure | None = None
        self.last_folder = Path.cwd()
        self.current_project_path: Path | None = None
        self._is_modified = False

        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(200)
        self.render_timer.timeout.connect(self.render_plot)

        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(AUTOSAVE_INTERVAL_MS)
        self.autosave_timer.timeout.connect(self.autosave_project)

        self._build_ui()
        self._build_menus()
        self._connect_signals()
        self.refresh_saved_style_combo()
        self.undo_history = UndoManager(
            self.current_workspace_snapshot,
            self._restore_workspace_snapshot,
        )
        self._init_blank_project()
        self.undo_history.reset()
        QApplication.instance().installEventFilter(self)
        self.autosave_timer.start()
        QTimer.singleShot(0, self.maybe_restore_autosave)

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.Save):
            self.save_project()
            return
        if event.matches(QKeySequence.Redo):
            self.redo_workspace()
            return
        if event.matches(QKeySequence.Undo):
            self.undo_workspace()
            return
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.delete_selected_annotation_from_key():
            return
        if self.focus_widget_uses_text_shortcuts():
            super().keyPressEvent(event)
            return
        if self.nudge_selected_annotations_from_key(event):
            return
        if event.matches(QKeySequence.Copy) and self.copy_selected_annotation():
            return
        if event.matches(QKeySequence.Paste) and self.paste_annotation():
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Wheel and isinstance(watched, (QAbstractSpinBox, QComboBox)):
            # Never let the scroll wheel change numeric/combo values. Forward the
            # scroll to the enclosing scroll area so the panel still scrolls.
            scroller = watched.parentWidget()
            while scroller is not None and not isinstance(scroller, QAbstractScrollArea):
                scroller = scroller.parentWidget()
            if scroller is not None:
                QApplication.sendEvent(scroller.viewport(), event)
            return True
        if event.type() == QEvent.FocusIn and watched in self.special_text_fields():
            self.special_text_target = watched
        if event.type() == QEvent.KeyPress:
            if event.matches(QKeySequence.Save):
                self.save_project()
                return True
            if event.matches(QKeySequence.Redo):
                self.redo_workspace()
                return True
            if event.matches(QKeySequence.Undo):
                self.undo_workspace()
                return True
            if event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.delete_selected_annotation_from_key():
                return True
        if event.type() == QEvent.KeyPress and watched is getattr(self, "canvas", None):
            if self.nudge_selected_annotations_from_key(event):
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
                "Unsaved changes",
                "There are unsaved changes. Save before closing?",
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
        self._remove_autosave()
        self.render_timer.stop()
        self.autosave_timer.stop()
        if self.current_figure is not None:
            self.current_figure.clear()
            self.current_figure = None
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

    def _build_menus(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        self._add_menu_action(file_menu, "New Project", self.new_project, "Ctrl+Shift+N")
        self._add_menu_action(file_menu, "New Sheet", self.new_sheet, "Ctrl+N")
        self._add_menu_action(file_menu, "New Graph", self.new_graph, "Ctrl+G")
        file_menu.addSeparator()
        self._add_menu_action(file_menu, "Open Project...", self.load_project, "Ctrl+O")
        self.recent_menu = file_menu.addMenu("Open Recent")
        self._update_recent_menu()
        self._add_menu_action(file_menu, "Import Data File...", self.import_data_file, "Ctrl+I")
        file_menu.addSeparator()
        self._add_menu_action(file_menu, "Save Project", self.save_project, "Ctrl+S")
        self._add_menu_action(file_menu, "Save Project As...", self.save_project_as, "Ctrl+Shift+S")
        file_menu.addSeparator()
        self.export_figure_action = self._add_menu_action(
            file_menu, "Export Figure...", self.export_current_figure, "Ctrl+E"
        )
        self._add_menu_action(file_menu, "Export All Figures...", self.export_all_figures)
        file_menu.addSeparator()
        self._add_menu_action(file_menu, "Exit", self.close, "Ctrl+Q")

        edit_menu = menubar.addMenu("&Edit")
        self._add_menu_action(edit_menu, "Undo", self.undo_workspace, "Ctrl+Z")
        redo_action = self._add_menu_action(edit_menu, "Redo", self.redo_workspace)
        redo_action.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        edit_menu.addSeparator()
        self.copy_figure_action = self._add_menu_action(
            edit_menu, "Copy Figure to Clipboard", self.copy_figure_to_clipboard, "Ctrl+Alt+C"
        )

        view_menu = menubar.addMenu("&View")
        theme_menu = view_menu.addMenu("Theme")
        self.theme_action_group = QActionGroup(self)
        self.theme_action_group.setExclusive(True)
        self.theme_actions: dict[str, QAction] = {}
        labels = {"light": "Light (White)", "dark": "Dark (Black)"}
        for theme_name in THEME_NAMES:
            action = QAction(labels[theme_name], self)
            action.setCheckable(True)
            action.setChecked(theme_name == self.interface_theme)
            action.triggered.connect(
                lambda checked=False, name=theme_name: self.set_interface_theme(name) if checked else None
            )
            self.theme_action_group.addAction(action)
            theme_menu.addAction(action)
            self.theme_actions[theme_name] = action

    def set_interface_theme(self, theme_name: str) -> None:
        """Switch the application chrome without changing figure styling."""
        app = QApplication.instance()
        if app is None:
            return
        self.interface_theme = apply_theme(app, theme_name)
        self.settings.setValue("interface_theme", self.interface_theme)
        action = getattr(self, "theme_actions", {}).get(self.interface_theme)
        if action is not None and not action.isChecked():
            action.setChecked(True)
        self.set_status(f"Interface theme: {self.interface_theme.title()}.")

    def _add_menu_action(self, menu: QMenu, text: str, slot, shortcut: str | None = None) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def recent_files(self) -> list[str]:
        value = self.settings.value("recent_files", [])
        if isinstance(value, str):
            value = [value]
        return [item for item in (value or []) if item]

    def add_recent_file(self, path: Path) -> None:
        items = [str(path)] + [item for item in self.recent_files() if item != str(path)]
        self.settings.setValue("recent_files", items[:RECENT_FILES_LIMIT])
        self._update_recent_menu()

    def _update_recent_menu(self) -> None:
        if not hasattr(self, "recent_menu"):
            return
        self.recent_menu.clear()
        files = self.recent_files()
        if not files:
            placeholder = self.recent_menu.addAction("(no recent projects)")
            placeholder.setEnabled(False)
            return
        for path_text in files:
            action = self.recent_menu.addAction(path_text)
            action.triggered.connect(lambda checked=False, p=path_text: self.open_project_path(Path(p)))
        self.recent_menu.addSeparator()
        self.recent_menu.addAction("Clear List", self._clear_recent_files)

    def _clear_recent_files(self) -> None:
        self.settings.setValue("recent_files", [])
        self._update_recent_menu()

    def dragEnterEvent(self, event) -> None:
        if self._dropped_paths(event):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = self._dropped_paths(event)
        if not paths:
            return
        path = paths[0]
        if path.suffix.lower() == ".json":
            self.open_project_path(path)
        else:
            self.import_data_path(path)
        event.acceptProposedAction()

    def _dropped_paths(self, event) -> list[Path]:
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        paths: list[Path] = []
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.suffix.lower() in DATA_FILE_SUFFIXES | {".json"}:
                paths.append(path)
        return paths

    def nudge_selected_annotations_from_key(self, event) -> bool:
        deltas = {
            Qt.Key_Left: (-1.0, 0.0),
            Qt.Key_Right: (1.0, 0.0),
            Qt.Key_Up: (0.0, 1.0),
            Qt.Key_Down: (0.0, -1.0),
        }
        delta = deltas.get(event.key())
        if delta is None or self.focus_widget_uses_text_shortcuts():
            return False
        indices = self.selected_annotation_indices()
        if not indices:
            return False
        modifiers = event.modifiers()
        step = 0.005
        if modifiers & Qt.ShiftModifier:
            step = 0.02
        elif modifiers & Qt.ControlModifier:
            step = 0.001
        self.push_undo_baseline()
        dx = delta[0] * step
        dy = delta[1] * step
        for idx in indices:
            annotation = self.annotations[idx]
            annotation.x += dx
            annotation.y += dy
            annotation.x2 = annotation.x + annotation.width
            annotation.y2 = annotation.y + annotation.height
            if idx < len(self.annotation_artists):
                self.update_annotation_artist(annotation, self.annotation_artists[idx])
            self.update_annotation_list_item(idx)
        current = self.annotation_list.currentRow()
        if current in indices:
            self.update_annotation_inputs(current)
        self.draw_annotation_handles(redraw=False)
        if self.canvas is not None:
            self.canvas.draw_idle()
        self.update_undo_baseline()
        return True

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
        splitter.setSizes([380, 720, 400])

        self.statusBar().showMessage("Ready")

    def _build_data_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        project_box = QGroupBox("Project Explorer")
        project_layout = QVBoxLayout(project_box)
        self.figure_tree = ProjectTreeWidget()
        self.figure_tree.setHeaderHidden(True)
        self.figure_tree.setMinimumHeight(80)
        self.figure_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.figure_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.figure_tree.customContextMenuRequested.connect(self.show_tree_menu)
        self.figure_tree.setDragDropMode(QAbstractItemView.InternalMove)
        self.figure_tree.setDragEnabled(True)
        self.figure_tree.setAcceptDrops(True)
        self.figure_tree.setDropIndicatorShown(True)
        self.figure_tree.node_dropped.connect(self.handle_tree_drop)
        self.figure_tree.itemExpanded.connect(self._on_item_expanded)
        self.figure_tree.itemCollapsed.connect(self._on_item_collapsed)
        project_layout.addWidget(self.figure_tree)
        project_buttons = QHBoxLayout()
        self.new_sheet_btn = QPushButton("New Sheet")
        self.new_graph_btn = QPushButton("New Graph")
        self.new_folder_btn = QPushButton("New Folder")
        project_buttons.addWidget(self.new_sheet_btn)
        project_buttons.addWidget(self.new_graph_btn)
        project_buttons.addWidget(self.new_folder_btn)
        project_layout.addLayout(project_buttons)
        project_buttons2 = QHBoxLayout()
        self.duplicate_figure_btn = QPushButton("Duplicate")
        self.rename_figure_btn = QPushButton("Rename")
        self.delete_figure_btn = QPushButton("Delete")
        project_buttons2.addWidget(self.duplicate_figure_btn)
        project_buttons2.addWidget(self.rename_figure_btn)
        project_buttons2.addWidget(self.delete_figure_btn)
        project_layout.addLayout(project_buttons2)
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
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
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
        self.plot_host.setObjectName("plotPreviewHost")
        self.plot_layout = QVBoxLayout(self.plot_host)
        self.plot_layout.setContentsMargins(8, 8, 8, 8)
        self.plot_layout.setAlignment(Qt.AlignCenter)
        self.plot_scroll.setWidget(self.plot_host)
        layout.addWidget(self.plot_scroll, 1)

        self.canvas: FigureCanvasQTAgg | None = None
        self.toolbar: NavigationToolbar2QT | None = None
        self.preview_base_width_px = 0
        self.preview_base_height_px = 0
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
        self.preview_issues = QLabel()
        self.preview_issues.setWordWrap(True)
        self.preview_issues.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.preview_issues.setAccessibleName("Figure issues")
        self.preview_issues.setObjectName("previewIssues")
        self.preview_issues.hide()
        layout.addWidget(self.preview_issues)
        self._replace_canvas(Figure(figsize=(4, 3), dpi=100))
        return panel

    def _build_settings_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 8, 4)
        layout.setSpacing(10)

        self.column_box = self._build_column_box()
        self.series_box = self._build_series_box()
        self.annotation_box = self._build_annotation_box()
        self.figure_box = self._build_figure_box()
        self.special_chars_box = self._build_special_chars_box()
        self.style_copy_box = self._build_style_copy_box()
        self.export_box = self._build_export_box()
        layout.addWidget(self.column_box)
        layout.addWidget(self.series_box)
        layout.addWidget(self.annotation_box)
        layout.addWidget(self.figure_box)
        layout.addWidget(self.special_chars_box)
        layout.addWidget(self.style_copy_box)
        layout.addWidget(self.export_box)
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
        self.series_settings = SeriesSettingsPanel(
            gradient_stops=self.settings.value("custom_gradient")
        )
        self.series_settings.install_compatibility_aliases(self)
        return self.series_settings

    def _build_annotation_box(self) -> QGroupBox:
        self.annotation_settings = AnnotationSettingsPanel(self.annotations)
        self.annotation_settings.install_compatibility_aliases(self)
        self.annotation_list.installEventFilter(self)
        return self.annotation_settings

    def _build_figure_box(self) -> QGroupBox:
        self.figure_settings = FigureSettingsPanel(self.plot_config)
        self.figure_settings.install_compatibility_aliases(self)
        return self.figure_settings

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

        layout.addWidget(QLabel("Saved styles"))
        self.saved_style_combo = QComboBox()
        self.saved_style_combo.setPlaceholderText("Select a saved style")
        layout.addWidget(self.saved_style_combo)

        save_row = QHBoxLayout()
        self.saved_style_name_edit = QLineEdit()
        self.saved_style_name_edit.setPlaceholderText("Style name")
        self.saved_style_name_edit.setMaxLength(80)
        self.save_named_style_btn = QPushButton("Save named style")
        save_row.addWidget(self.saved_style_name_edit, 1)
        save_row.addWidget(self.save_named_style_btn)
        layout.addLayout(save_row)

        saved_actions_row = QHBoxLayout()
        self.apply_named_style_btn = QPushButton("Apply saved")
        self.delete_named_style_btn = QPushButton("Delete")
        self.apply_named_style_btn.setEnabled(False)
        self.delete_named_style_btn.setEnabled(False)
        saved_actions_row.addWidget(self.apply_named_style_btn)
        saved_actions_row.addWidget(self.delete_named_style_btn)
        layout.addLayout(saved_actions_row)
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
        self.export_btn.setObjectName("primary")
        self.export_all_btn = QPushButton("Export all figures")
        self.new_project_btn = QPushButton("New project")
        self.save_project_btn = QPushButton("Save project")
        self.save_project_btn.setObjectName("primary")
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
        self.figure_tree.currentItemChanged.connect(self.on_tree_selection_changed)
        self.new_sheet_btn.clicked.connect(self.new_sheet)
        self.new_graph_btn.clicked.connect(self.new_graph)
        self.new_folder_btn.clicked.connect(self.new_folder)
        self.duplicate_figure_btn.clicked.connect(self.duplicate_selected_node)
        self.rename_figure_btn.clicked.connect(self.rename_selected_node)
        self.delete_figure_btn.clicked.connect(self.delete_selected_node)
        self.move_figure_up_btn.clicked.connect(lambda: self.move_selected_node(-1))
        self.move_figure_down_btn.clicked.connect(lambda: self.move_selected_node(1))
        self.fit_preview_check.toggled.connect(self.update_canvas_size)
        self.preview_zoom_spin.valueChanged.connect(self.update_canvas_size)
        self.center_preview_btn.clicked.connect(self.center_preview)
        self.center_plot_box_btn.clicked.connect(self.center_plot_box)
        self.center_content_btn.clicked.connect(self.center_content)
        self.fit_canvas_btn.clicked.connect(self.fit_canvas_to_content)
        self.y_list.itemChanged.connect(self.handle_plot_y_changed)
        self.y_list.itemClicked.connect(self.handle_plot_y_clicked)
        self.style_target_combo.currentTextChanged.connect(self.handle_style_target_changed)
        self.x_scale_combo.currentTextChanged.connect(self.update_axis_control_states)
        self.y_scale_combo.currentTextChanged.connect(self.update_axis_control_states)
        self.x_break_check.toggled.connect(self.update_axis_control_states)
        self.y_break_check.toggled.connect(self.update_axis_control_states)
        self.color_btn.clicked.connect(self.choose_series_color)
        self.apply_cmap_btn.clicked.connect(self.apply_colormap_to_plotted_series)
        self.cmap_base_color_btn.clicked.connect(self.choose_cmap_base_color)
        self.annotation_settings.color_requested.connect(self.choose_annotation_color)
        self.y_axis_color_btn.clicked.connect(self.choose_y_axis_color)
        self.y2_axis_color_btn.clicked.connect(self.choose_y2_axis_color)
        self.edit_legend_btn.clicked.connect(self.edit_legend_text)
        self.annotation_settings.add_requested.connect(self.add_annotation)
        self.annotation_settings.remove_requested.connect(self.remove_selected_annotation)
        self.apply_palette_btn.clicked.connect(self.apply_recommended_palette_to_series)
        self.apply_gradient_btn.clicked.connect(self.apply_custom_gradient_to_series)
        self.gradient_editor.changed.connect(self._save_custom_gradient)
        self.annotation_settings.selection_changed.connect(
            lambda _index: self.draw_annotation_handles()
        )
        self.annotation_settings.connect_changed(
            lambda source: self.handle_undoable_widget_change(
                self.update_selected_annotation, source
            )
        )
        self.export_btn.clicked.connect(self.export_current_figure)
        self.export_all_btn.clicked.connect(self.export_all_figures)
        self.new_project_btn.clicked.connect(self.new_project)
        self.save_project_btn.clicked.connect(self.save_project)
        self.load_project_btn.clicked.connect(self.load_project)
        self.copy_style_btn.clicked.connect(self.copy_current_style)
        self.apply_style_btn.clicked.connect(self.apply_copied_style)
        self.saved_style_name_edit.textChanged.connect(self.update_named_style_save_button)
        self.saved_style_name_edit.returnPressed.connect(self.save_named_style)
        self.save_named_style_btn.clicked.connect(lambda: self.save_named_style())
        self.apply_named_style_btn.clicked.connect(lambda: self.apply_named_style())
        self.delete_named_style_btn.clicked.connect(lambda: self.delete_named_style())

        self.series_settings.connect_changed(
            lambda source: self.handle_undoable_widget_change(
                self.apply_series_widget_state, source
            )
        )

        self.figure_settings.connect_changed(self.handle_figure_setting_changed)
        for widget in (self.trim_check, self.transparent_check):
            self._connect_change(widget, self.schedule_render)

    def _connect_change(self, widget, callback) -> None:
        for signal_name in ("textChanged", "currentTextChanged", "valueChanged", "toggled"):
            signal = getattr(widget, signal_name, None)
            if signal is not None:
                signal.connect(
                    lambda *args, cb=callback, source=widget:
                    self.handle_undoable_widget_change(cb, source)
                )
                return

    def handle_undoable_widget_change(self, callback, source: QWidget | None = None) -> None:
        """Apply a widget edit while merging rapid changes from one control.

        Text edits and spin-box repeats can emit on every keystroke/step. A
        single undo entry for the whole short editing burst is both more useful
        and avoids deep-copying a large workbook into the undo stack each time.
        """
        if self.loading_project_figure:
            callback()
            return
        self._set_modified(True)
        self.undo_history.perform_change(
            callback,
            coalesce_key=id(source) if source is not None else None,
        )

    def handle_figure_setting_changed(self, source: QWidget) -> None:
        """Apply one figure edit, including any dependent widget changes.

        Qt emits after the source widget has changed. The undo manager keeps a
        pre-signal baseline, so every dependent update must finish inside the
        same change callback before the new baseline is captured.
        """

        def apply_change() -> None:
            if source is self.preset_combo:
                self.handle_preset_changed(self.preset_combo.currentText())
            elif source is self.plot_width_spin:
                self.handle_plot_dimension_changed("width")
            elif source is self.plot_height_spin:
                self.handle_plot_dimension_changed("height")
            elif source is self.plot_ratio_lock_check:
                self.handle_plot_ratio_lock_toggled(
                    self.plot_ratio_lock_check.isChecked()
                )
            elif source is self.plot_ratio_preset_combo:
                self.handle_plot_ratio_preset_changed(
                    self.plot_ratio_preset_combo.currentText()
                )
            self.schedule_render()

        self.handle_undoable_widget_change(apply_change, source)

    def _reset_undo_coalescing(self) -> None:
        self.undo_history.end_coalescing()

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
        self.df = with_metadata_rows(parsed.dataframe)
        self.series_by_y.clear()
        self.populate_table()
        self.populate_columns()
        self.set_status(
            f"Loaded {len(parsed.dataframe)} data rows x {len(self.df.columns)} cols; delimiter={parsed.delimiter}; header={parsed.has_header}"
        )
        self.schedule_render()

    def import_data_file(self) -> None:
        filters = "Data files (*.csv *.txt *.tsv *.dat);;All files (*)"
        path, _ = QFileDialog.getOpenFileName(self, "Import data file", str(self.last_folder), filters)
        if not path:
            return
        self.import_data_path(Path(path))

    def import_data_path(self, path: Path) -> None:
        try:
            text = self._read_text_file(path)
        except Exception as exc:
            QMessageBox.warning(self, "Import data", f"Could not read {path}:\n{exc}")
            return
        if not self._table_is_blank():
            reply = QMessageBox.question(
                self,
                "Import data",
                "Replace the current figure's data with the file contents?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply != QMessageBox.Yes:
                return
        self.last_folder = path.parent
        self.replace_table_from_text(text)

    def _read_text_file(self, path: Path) -> str:
        raw = path.read_bytes()
        for encoding in ("utf-8-sig", "utf-8", "cp949"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    def copy_figure_to_clipboard(self) -> None:
        self.render_timer.stop()
        config = self.collect_plot_config()
        try:
            encoded = self.rendering.render_bytes(
                self._render_request(config),
                format="png",
            )
        except Exception as exc:
            message = f"Clipboard copy failed: {exc}"
            self.update_render_issues([message])
            self.set_status(message)
            return
        image = QImage.fromData(encoded.data, "PNG")
        QApplication.clipboard().setImage(image)
        self.set_status(f"Copied figure to clipboard ({image.width()} x {image.height()} px at {config.dpi} DPI).")

    def autosave_project(self) -> None:
        if not self._is_modified:
            return
        try:
            self.save_active_state()
            payload = self.project_payload()
            payload["autosave_origin"] = str(self.current_project_path) if self.current_project_path else ""
            self._write_json_atomic(self.autosave_path, payload)
        except Exception as exc:
            self.set_status(f"Autosave failed: {exc}")

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict, indent: int | None = None) -> None:
        write_json_atomic(path, payload, indent=indent)

    def _remove_autosave(self) -> None:
        try:
            if self.autosave_path.exists():
                self.autosave_path.unlink()
        except OSError:
            pass

    def maybe_restore_autosave(self) -> None:
        if not self.autosave_path.exists():
            return
        reply = QMessageBox.question(
            self,
            "Recover work",
            "pubfig found an autosaved session (possibly from a crash). Restore it?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            self._remove_autosave()
            return
        try:
            with open(self.autosave_path, "r", encoding="utf-8") as file:
                payload = json.load(file)
            sheets, graphs, tree_root, active_node_id = self.load_payload_into_model(payload, "Recovered")
        except Exception as exc:
            QMessageBox.warning(self, "Recover work", f"Could not restore the autosave:\n{exc}")
            self._remove_autosave()
            return
        origin = payload.get("autosave_origin") or ""
        self.current_project_path = Path(origin) if origin else None
        self.set_model(sheets, graphs, tree_root, active_node_id)
        self.undo_history.reset()
        self._set_modified(True)
        self.set_status("Restored autosaved session.")

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
        return make_blank_dataframe(rows, columns)

    def capture_active_graph(self) -> Graph | None:
        """Flush the active editor session into its canonical graph object."""
        if self.active_graph_id is None or self.active_graph_id not in self.graphs:
            return None
        graph = self.graphs[self.active_graph_id]
        graph.plot_config = self.collect_plot_config()
        graph.annotations = self.annotations
        graph.plot_config.annotations = graph.annotations
        graph.series_by_y = self.series_by_y
        graph.checked_y = self.checked_y_columns()
        return graph

    def sync_active_sheet_df(self) -> None:
        """Flush the live table buffer back into the active sheet object."""
        if self.active_sheet_id and self.active_sheet_id in self.sheets:
            self.df = self.table.dataframe()
            self.sheets[self.active_sheet_id].df = self.df

    def current_workspace_snapshot(self) -> ProjectDocument:
        # Flush live state into the model BEFORE deep-copying so edits made since
        # the last switch are not lost on undo.
        self.sync_active_sheet_df()
        captured = self.capture_active_graph()
        if captured is not None:
            self.graphs[self.active_graph_id] = captured
        return deepcopy(self.document)

    def _set_modified(self, modified: bool) -> None:
        self._is_modified = modified
        self._update_window_title()

    def _update_window_title(self) -> None:
        name = self.current_project_path.name if self.current_project_path else "Untitled project"
        marker = "*" if self._is_modified else ""
        self.setWindowTitle(f"pubfig — {name}{marker}")

    def push_current_undo_state(self) -> None:
        if self.restoring_undo or self.loading_project_figure:
            return
        self._set_modified(True)
        self.undo_history.checkpoint_current()

    def push_undo_baseline(self) -> None:
        if (
            self.restoring_undo
            or self.loading_project_figure
            or not self.undo_history.has_baseline
        ):
            return
        self._set_modified(True)
        self.undo_history.checkpoint_baseline()

    def update_undo_baseline(self) -> None:
        if self.restoring_undo or self.loading_project_figure:
            return
        self.undo_history.refresh_baseline()

    def undo_workspace(self) -> None:
        if not self.undo_history.undo():
            self.set_status("Nothing to undo.")
            return
        self.set_status(
            f"Undid last change ({self.undo_history.undo_count} undo, "
            f"{self.undo_history.redo_count} redo available)."
        )

    def redo_workspace(self) -> None:
        if not self.undo_history.redo():
            self.set_status("Nothing to redo.")
            return
        self.set_status(
            f"Redid change ({self.undo_history.undo_count} undo, "
            f"{self.undo_history.redo_count} redo available)."
        )

    def _restore_workspace_snapshot(self, snapshot: ProjectDocument) -> None:
        self.document = snapshot
        self.active_graph_id = None
        self.active_sheet_id = None
        self.refresh_tree()
        node = self._find_node(self.active_node_id) or self._first_leaf()
        if node is not None:
            self.active_node_id = node.id
            self.load_node(node)

    # ----- model bootstrap -----

    def _init_blank_project(self) -> None:
        """Reset to a project with a single empty sheet (no graph yet)."""
        document = ProjectDocument()
        node_id = self._seed_blank_sheet(document.sheets, document.tree_root)
        self.set_model(
            document.sheets,
            document.graphs,
            document.tree_root,
            node_id,
        )

    def set_model(
        self,
        sheets: dict[str, Sheet],
        graphs: dict[str, Graph],
        tree_root: TreeNode,
        active_node_id: str | None,
    ) -> None:
        """Install a freshly-loaded/migrated model and show its active node."""
        self.document = ProjectDocument(
            sheets=sheets,
            graphs=graphs,
            tree_root=tree_root,
            active_node_id=active_node_id,
        )
        self.active_graph_id = None
        self.active_sheet_id = None
        self.refresh_tree()
        node = self._find_node(active_node_id) or self._first_leaf()
        if node is None:
            self.active_node_id = self._seed_blank_sheet(self.sheets, self.tree_root)
            self.refresh_tree()
            node = self._find_node(self.active_node_id)
        self.active_node_id = node.id
        self.load_node(node)

    # ----- tree navigation helpers -----

    def _find_node(self, node_id: str | None, root: TreeNode | None = None) -> TreeNode | None:
        return find_node(root or self.tree_root, node_id)

    def _find_parent(self, node_id: str, root: TreeNode | None = None) -> TreeNode | None:
        return find_parent(root or self.tree_root, node_id)

    def _first_leaf(self, root: TreeNode | None = None) -> TreeNode | None:
        return first_leaf(root or self.tree_root)

    def active_node(self) -> TreeNode | None:
        return self._find_node(self.active_node_id)

    def current_sheet_id(self) -> str | None:
        """The sheet whose data the center table should show for the current
        selection: the sheet itself, or a graph's referenced sheet."""
        node = self.active_node()
        if node is None:
            return None
        if node.type == "sheet":
            return node.ref_id
        if node.type == "graph":
            graph = self.graphs.get(node.ref_id)
            return graph.sheet_id if graph else None
        return None

    def _target_folder_for_new(self) -> TreeNode:
        """Where a new sheet/folder should land: the selected folder, or the
        folder containing the selected leaf, else the root. Graph nodes are
        never folders, so their containing folder is used."""
        node = self.active_node()
        if node is None:
            return self.tree_root
        if node.type == "folder":
            return node
        # For a sheet/graph leaf, climb to the nearest folder ancestor.
        return self._containing_folder(node)

    def _containing_folder(self, node: TreeNode) -> TreeNode:
        """The nearest folder (or root) that should contain a sibling of node."""
        parent = self._find_parent(node.id)
        while parent is not None and parent.type not in ("folder",):
            # parent is a sheet (graph's parent) -> go one level up
            parent = self._find_parent(parent.id)
        return parent or self.tree_root

    def _sheet_node_for(self, sheet_id: str) -> TreeNode | None:
        """The tree node of type 'sheet' that backs the given sheet id."""
        def walk(node: TreeNode) -> TreeNode | None:
            for child in node.children:
                if child.type == "sheet" and child.ref_id == sheet_id:
                    return child
                found = walk(child)
                if found is not None:
                    return found
            return None

        return walk(self.tree_root)

    # ----- save / load split -----

    def save_active_state(self) -> None:
        """Flush the live table+plot widgets into the active sheet/graph."""
        if self.loading_project_figure:
            return
        self.sync_active_sheet_df()
        captured = self.capture_active_graph()
        if captured is not None:
            self.graphs[self.active_graph_id] = captured

    def load_sheet_into_table(self, sheet: Sheet, *, populate_column_controls: bool = True) -> None:
        self.df = sheet.df
        self.populate_table()
        if populate_column_controls:
            self.populate_columns()

    def load_graph(self, graph: Graph) -> None:
        """Load a graph's plot state. The graph's sheet must already be in
        self.df (load the sheet first)."""
        self.plot_config = graph.plot_config
        self.annotations = graph.annotations
        self.plot_config.annotations = self.annotations
        self.series_by_y = graph.series_by_y
        self._load_config_into_widgets()
        self.populate_columns()
        self._set_checked_y_columns(
            [c for c in graph.checked_y if c in list(map(str, self.df.columns))]
        )
        self.refresh_series_configs()
        self.update_style_targets()
        self.refresh_cmap_column_list()
        self.refresh_annotation_list()
        self.update_axis_control_states()

    def load_node(self, node: TreeNode) -> None:
        """Show a tree node: load its sheet into the table (editable) and, if a
        graph, its plot state; then render."""
        # Discard a preview queued by the previous context. The selected graph
        # is rendered synchronously once its complete state has been loaded.
        self.render_timer.stop()
        self.loading_project_figure = True
        try:
            sheet_id = self.current_sheet_id()
            sheet = self.sheets.get(sheet_id) if sheet_id else None
            if sheet is not None:
                sheet_changed = sheet.id != self.active_sheet_id
                self.active_sheet_id = sheet.id
                if sheet_changed:
                    # A graph loads its column controls after installing its
                    # own series state. Building them here did the work twice.
                    self.load_sheet_into_table(
                        sheet,
                        populate_column_controls=node.type != "graph",
                    )
            if node.type == "graph" and node.ref_id in self.graphs:
                self.active_graph_id = node.ref_id
                self.load_graph(self.graphs[node.ref_id])
            else:
                # Bare sheet or folder: no plot context.
                self.active_graph_id = None
                self.series_by_y = {}
                if sheet is not None:
                    self._set_checked_y_columns([])
                    self.refresh_series_configs()
                    self.update_style_targets()
                    self.refresh_cmap_column_list()
            self.set_editing_context(
                sheet is not None,
                self.active_graph_id is not None,
            )
        finally:
            self.loading_project_figure = False
        self.render_plot()

    def set_editing_context(self, has_sheet: bool, has_graph: bool) -> None:
        """Keep controls aligned with the selected project node.

        Figure settings edited while a bare sheet/folder is selected have no
        graph model to receive them, so leaving those controls enabled creates
        changes that appear saveable but are silently discarded.
        """
        self.table.setEnabled(has_sheet)
        self.column_box.setEnabled(has_graph)
        self.series_box.setEnabled(has_graph)
        self.annotation_box.setEnabled(has_graph)
        self.figure_box.setEnabled(has_graph)
        self.style_copy_box.setEnabled(has_graph)
        self.special_chars_box.setEnabled(has_sheet)
        self.trim_check.setEnabled(has_graph)
        self.transparent_check.setEnabled(has_graph)
        self.export_btn.setEnabled(has_graph)
        self.export_figure_action.setEnabled(has_graph)
        self.copy_figure_action.setEnabled(has_graph)
        if not has_sheet:
            self.data_info.setText("Select a sheet to edit data.")

    # ----- tree widget rendering -----

    def refresh_tree(self) -> None:
        self.figure_tree.blockSignals(True)
        self.figure_tree.clear()
        self._tree_items = {}
        for child in self.tree_root.children:
            self._add_tree_item(child, self.figure_tree.invisibleRootItem())
        active_item = self._tree_items.get(self.active_node_id)
        if active_item is not None:
            self.figure_tree.setCurrentItem(active_item)
        self.figure_tree.blockSignals(False)
        self.update_project_move_buttons()

    def _node_label(self, node: TreeNode) -> str:
        prefix = {"folder": "[F] ", "sheet": "[S] ", "graph": "[G] "}.get(node.type, "")
        if node.type == "sheet":
            name = self.sheets[node.ref_id].name if node.ref_id in self.sheets else node.name
        elif node.type == "graph":
            name = self.graphs[node.ref_id].name if node.ref_id in self.graphs else node.name
        else:
            name = node.name
        return f"{prefix}{name}"

    def _add_tree_item(self, node: TreeNode, parent_item) -> None:
        item = QTreeWidgetItem(parent_item)
        item.setText(0, self._node_label(node))
        item.setData(0, Qt.UserRole, node.id)
        self._tree_items[node.id] = item
        for child in node.children:
            self._add_tree_item(child, item)
        if node.children:
            item.setExpanded(node.expanded)

    def _on_item_expanded(self, item) -> None:
        node = self._find_node(item.data(0, Qt.UserRole))
        if node is not None:
            node.expanded = True

    def _on_item_collapsed(self, item) -> None:
        node = self._find_node(item.data(0, Qt.UserRole))
        if node is not None:
            node.expanded = False

    def update_project_move_buttons(self) -> None:
        node = self.active_node()
        enabled = False
        if node is not None:
            parent = self._find_parent(node.id)
            enabled = parent is not None and len(parent.children) > 1
        self.move_figure_up_btn.setEnabled(enabled)
        self.move_figure_down_btn.setEnabled(enabled)

    def on_tree_selection_changed(self, current, previous) -> None:
        if self.loading_project_figure or current is None:
            self.update_project_move_buttons()
            return
        node_id = current.data(0, Qt.UserRole)
        if node_id == self.active_node_id:
            self.update_project_move_buttons()
            return
        self.save_active_state()
        node = self._find_node(node_id)
        if node is None:
            return
        self.active_node_id = node_id
        self.load_node(node)
        self.update_undo_baseline()
        self.update_project_move_buttons()
        self.set_status(f"Selected {self._node_label(node).strip()}.")

    # ----- node creation -----

    def _select_node(self, node_id: str) -> None:
        self.active_node_id = node_id
        self.refresh_tree()
        node = self._find_node(node_id)
        if node is not None:
            self.load_node(node)

    def new_sheet(self) -> None:
        self.push_current_undo_state()
        self.save_active_state()
        sheet = Sheet(id=new_id("sh"), name=self._unique_sheet_name(), df=self.blank_dataframe())
        self.sheets[sheet.id] = sheet
        node = TreeNode(id=new_id("nd"), type="sheet", name=sheet.name, ref_id=sheet.id)
        self._target_folder_for_new().children.append(node)
        self._select_node(node.id)
        self.update_undo_baseline()
        self.set_status(f"Created sheet {sheet.name}.")

    def new_graph(self) -> None:
        sheet_id = self.current_sheet_id()
        if sheet_id is None or sheet_id not in self.sheets:
            QMessageBox.information(self, "New graph", "Select a sheet (or a graph) first.")
            return
        self.push_current_undo_state()
        self.save_active_state()
        y_columns = [
            column
            for column in map(str, self.df.columns)
            if self._column_role(column) == "Y" and self._nearest_left_x(column)
        ]
        series_by_y = {
            column: default_series(self._nearest_left_x(column), column, index)
            for index, column in enumerate(y_columns)
        }
        graph = Graph(
            id=new_id("gr"),
            name=self._unique_graph_name(),
            sheet_id=sheet_id,
            plot_config=PlotConfig(),
            series_by_y=series_by_y,
            checked_y=y_columns,
            annotations=[],
        )
        self.graphs[graph.id] = graph
        node = TreeNode(id=new_id("nd"), type="graph", name=graph.name, ref_id=graph.id)
        # A graph always lives under the sheet node it references (hierarchy).
        sheet_node = self._sheet_node_for(sheet_id)
        if sheet_node is None:
            self._target_folder_for_new().children.append(node)
        else:
            sheet_node.expanded = True
            sheet_node.children.append(node)
        self._select_node(node.id)
        self.update_undo_baseline()
        self.set_status(f"Created graph {graph.name} on {self.sheets[sheet_id].name}.")

    def new_folder(self) -> None:
        self.push_current_undo_state()
        self.save_active_state()
        node = TreeNode(id=new_id("nd"), type="folder", name=self._unique_folder_name())
        self._target_folder_for_new().children.append(node)
        self._select_node(node.id)
        self.update_undo_baseline()
        self.set_status(f"Created folder {node.name}.")

    def duplicate_selected_node(self) -> None:
        node = self.active_node()
        if node is None:
            return
        self.push_current_undo_state()
        self.save_active_state()
        parent = self._find_parent(node.id) or self.tree_root
        clone = self._duplicate_node(node)
        parent.children.insert(parent.children.index(node) + 1, clone)
        self._select_node(clone.id)
        self.update_undo_baseline()
        self.set_status("Duplicated selection.")

    def _duplicate_node(self, node: TreeNode) -> TreeNode:
        """Deep-copy a tree node and the objects it references. For folders, the
        whole subtree is copied and graph→sheet references are remapped so a
        duplicated graph points at the duplicated sheet (when both are copied)."""
        sheet_id_map: dict[str, str] = {}

        def clone(n: TreeNode) -> TreeNode:
            if n.type == "sheet":
                src = self.sheets[n.ref_id]
                new_sheet = Sheet(id=new_id("sh"), name=self._unique_sheet_name(src.name), df=src.df.copy(deep=True))
                self.sheets[new_sheet.id] = new_sheet
                sheet_id_map[src.id] = new_sheet.id
                node_copy = TreeNode(id=new_id("nd"), type="sheet", name=new_sheet.name, ref_id=new_sheet.id, expanded=n.expanded)
                # Clone the sheet's child graphs so duplicates point at the new sheet.
                node_copy.children = [clone(child) for child in n.children]
                return node_copy
            if n.type == "graph":
                src = self.graphs[n.ref_id]
                new_g = deepcopy(src)
                new_g.id = new_id("gr")
                new_g.name = self._unique_graph_name(src.name)
                new_g.sheet_id = sheet_id_map.get(src.sheet_id, src.sheet_id)
                self.graphs[new_g.id] = new_g
                return TreeNode(id=new_id("nd"), type="graph", name=new_g.name, ref_id=new_g.id)
            # folder: clone children first so sheet ids are available for remap
            folder = TreeNode(id=new_id("nd"), type="folder", name=self._unique_folder_name(n.name), expanded=n.expanded)
            folder.children = [clone(child) for child in n.children]
            return folder

        return clone(node)

    def rename_selected_node(self) -> None:
        node = self.active_node()
        if node is None:
            return
        if node.type == "sheet":
            current = self.sheets[node.ref_id].name
        elif node.type == "graph":
            current = self.graphs[node.ref_id].name
        else:
            current = node.name
        name, ok = QInputDialog.getText(self, "Rename", "Name", text=current)
        name = name.strip()
        if not ok or not name:
            return
        self.push_current_undo_state()
        if node.type == "sheet":
            self.sheets[node.ref_id].name = name
        elif node.type == "graph":
            self.graphs[node.ref_id].name = name
        node.name = name
        self.refresh_tree()
        self.update_undo_baseline()
        self.set_status(f"Renamed to {name}.")

    def _graphs_referencing(self, sheet_id: str) -> list[str]:
        return [gid for gid, g in self.graphs.items() if g.sheet_id == sheet_id]

    def delete_selected_node(self) -> None:
        node = self.active_node()
        if node is None:
            return
        # Collect sheets that would be deleted, and guard against orphaning graphs.
        sheet_ids = self._collect_sheet_ids(node)
        if sheet_ids:
            dependents = [
                gid for sid in sheet_ids
                for gid in self._graphs_referencing(sid)
                if gid not in self._collect_graph_ids(node)
            ]
            if dependents:
                names = ", ".join(self.graphs[g].name for g in dependents)
                reply = QMessageBox.question(
                    self,
                    "Delete",
                    f"{len(dependents)} graph(s) reference this data and will also be deleted:\n{names}\n\nContinue?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return

        self.push_current_undo_state()
        self._remove_node_objects(node, also_dependents=True)
        parent = self._find_parent(node.id) or self.tree_root
        if node in parent.children:
            parent.children.remove(node)

        # Never leave an empty project.
        if not self.tree_root.children:
            self.sheets.clear()
            self.graphs.clear()
            self.active_node_id = self._seed_blank_sheet(self.sheets, self.tree_root)
        else:
            leaf = self._first_leaf() or self.tree_root.children[0]
            self.active_node_id = leaf.id

        self.active_graph_id = None
        self.active_sheet_id = None
        self._select_node(self.active_node_id)
        self.update_undo_baseline()
        self.set_status("Deleted selection.")

    def _collect_sheet_ids(self, node: TreeNode) -> set[str]:
        ids: set[str] = set()
        if node.type == "sheet" and node.ref_id:
            ids.add(node.ref_id)
        for child in node.children:
            ids |= self._collect_sheet_ids(child)
        return ids

    def _collect_graph_ids(self, node: TreeNode) -> set[str]:
        ids: set[str] = set()
        if node.type == "graph" and node.ref_id:
            ids.add(node.ref_id)
        for child in node.children:
            ids |= self._collect_graph_ids(child)
        return ids

    def _remove_node_objects(self, node: TreeNode, also_dependents: bool) -> None:
        """Delete the Sheet/Graph objects backing a node subtree. When
        also_dependents, graphs referencing a deleted sheet are removed too."""
        sheet_ids = self._collect_sheet_ids(node)
        graph_ids = self._collect_graph_ids(node)
        for gid in graph_ids:
            self.graphs.pop(gid, None)
        for sid in sheet_ids:
            self.sheets.pop(sid, None)
        if also_dependents:
            for sid in sheet_ids:
                for gid in self._graphs_referencing(sid):
                    self.graphs.pop(gid, None)
                    self._remove_nodes_by_ref(self.tree_root, "graph", gid)

    def _remove_nodes_by_ref(self, root: TreeNode, node_type: str, ref_id: str) -> None:
        root.children = [
            c for c in root.children if not (c.type == node_type and c.ref_id == ref_id)
        ]
        for child in root.children:
            self._remove_nodes_by_ref(child, node_type, ref_id)

    def move_selected_node(self, offset: int) -> None:
        node = self.active_node()
        if node is None:
            return
        parent = self._find_parent(node.id)
        if parent is None:
            return
        idx = parent.children.index(node)
        target = idx + offset
        if target < 0 or target >= len(parent.children):
            self.update_project_move_buttons()
            return
        self.push_current_undo_state()
        parent.children[idx], parent.children[target] = parent.children[target], parent.children[idx]
        self.refresh_tree()
        self.update_undo_baseline()
        self.set_status(f"Moved {'up' if offset < 0 else 'down'}.")

    def show_tree_menu(self, position) -> None:
        item = self.figure_tree.itemAt(position)
        if item is not None:
            self.figure_tree.setCurrentItem(item)
        node = self.active_node()
        menu = QMenu(self)
        menu.addAction("New Sheet", self.new_sheet)
        if node is not None and node.type in ("sheet", "graph"):
            menu.addAction("New Graph", self.new_graph)
        menu.addAction("New Folder", self.new_folder)
        if node is not None:
            menu.addSeparator()
            menu.addAction("Duplicate", self.duplicate_selected_node)
            menu.addAction("Rename", self.rename_selected_node)
            menu.addAction("Delete", self.delete_selected_node)
            move_menu = menu.addMenu("Move to folder")
            self._build_move_menu(move_menu, node)
        menu.exec(self.figure_tree.viewport().mapToGlobal(position))

    def _build_move_menu(self, menu, node: TreeNode) -> None:
        """Populate a menu with every folder the node can be moved into."""
        descendants = {node.id} | self._collect_descendant_ids(node)

        def add_folder(folder: TreeNode, depth: int) -> None:
            if folder.id not in descendants:
                label = ("    " * depth) + (folder.name or "Project")
                menu.addAction(label, lambda f=folder: self._move_node_to_folder(node, f))
            for child in folder.children:
                if child.type == "folder":
                    add_folder(child, depth + 1)

        add_folder(self.tree_root, 0)

    def _collect_descendant_ids(self, node: TreeNode) -> set[str]:
        ids: set[str] = set()
        for child in node.children:
            ids.add(child.id)
            ids |= self._collect_descendant_ids(child)
        return ids

    def _move_node_to_folder(self, node: TreeNode, folder: TreeNode) -> None:
        # Rule A: a graph follows its sheet, so move the sheet instead.
        if node.type == "graph":
            sheet_id = self.graphs[node.ref_id].sheet_id if node.ref_id in self.graphs else None
            node = self._sheet_node_for(sheet_id) if sheet_id else None
            if node is None:
                return
        parent = self._find_parent(node.id)
        if parent is None or folder is parent:
            return
        self.push_current_undo_state()
        parent.children.remove(node)
        folder.children.append(node)
        self.active_node_id = node.id
        self.refresh_tree()
        self.update_undo_baseline()
        self.set_status(f"Moved to {folder.name or 'Project'}.")

    def handle_tree_drop(self, source_id: str, target_id: str, position: str) -> None:
        """Apply a drag-and-drop move to the model. Only sheets and folders move
        freely; a graph always stays under its sheet, so dragging a graph moves
        its parent sheet instead."""
        source = self._find_node(source_id)
        if source is None:
            return
        # Rule A: graphs follow their sheet. Redirect a graph drag to its sheet.
        if source.type == "graph":
            sheet_id = self.graphs[source.ref_id].sheet_id if source.ref_id in self.graphs else None
            source = self._sheet_node_for(sheet_id) if sheet_id else None
            if source is None:
                return

        # Determine destination folder + insertion index.
        if position == "root" or not target_id:
            dest_folder, insert_index = self.tree_root, len(self.tree_root.children)
        else:
            target = self._find_node(target_id)
            if target is None:
                return
            # Can't drop a folder into itself or its own descendants.
            if source.type == "folder" and target_id in ({source.id} | self._collect_descendant_ids(source)):
                self.set_status("Cannot move a folder into itself.")
                return
            if position == "on" and target.type == "folder":
                dest_folder, insert_index = target, len(target.children)
            elif position == "on" and target.type == "sheet":
                # Dropping onto a sheet means "into the sheet's folder, next to it".
                dest_folder = self._containing_folder(target)
                insert_index = dest_folder.children.index(target) + 1
            else:
                # above/below a node: same folder as that node (graphs excluded as targets)
                anchor = target if target.type != "graph" else (self._sheet_node_for(
                    self.graphs[target.ref_id].sheet_id) if target.ref_id in self.graphs else None)
                if anchor is None:
                    return
                dest_folder = self._containing_folder(anchor)
                if anchor not in dest_folder.children:
                    return
                base = dest_folder.children.index(anchor)
                insert_index = base if position == "above" else base + 1

        src_parent = self._find_parent(source.id)
        if src_parent is None:
            return
        if dest_folder is src_parent and source in dest_folder.children:
            # Reorder within the same folder.
            old = dest_folder.children.index(source)
            if old < insert_index:
                insert_index -= 1
            if old == insert_index:
                return
        self.push_current_undo_state()
        src_parent.children.remove(source)
        insert_index = max(0, min(insert_index, len(dest_folder.children)))
        dest_folder.children.insert(insert_index, source)
        self.active_node_id = source.id
        self.refresh_tree()
        self.update_undo_baseline()
        self.set_status(f"Moved {self._node_label(source).strip()}.")

    # ----- unique-name helpers -----

    def _unique_sheet_name(self, base: str = "Sheet") -> str:
        return self._unique_name(base, {s.name for s in self.sheets.values()})

    def _unique_graph_name(self, base: str = "Graph") -> str:
        return self._unique_name(base, {g.name for g in self.graphs.values()})

    def _unique_folder_name(self, base: str = "Folder") -> str:
        existing = set()

        def walk(node: TreeNode) -> None:
            for child in node.children:
                if child.type == "folder":
                    existing.add(child.name)
                walk(child)

        walk(self.tree_root)
        return self._unique_name(base, existing)

    @staticmethod
    def _unique_name(base: str, existing: set[str]) -> str:
        root = re.sub(r"\s+\d+$", "", base).strip() or base
        idx = 1
        while f"{root} {idx}" in existing:
            idx += 1
        return f"{root} {idx}"

    def populate_table(self) -> None:
        """Attach the DataFrame to the virtual table without creating cells."""
        self.table.blockSignals(True)
        try:
            self.table.clearSelection()
            self.table.set_dataframe(self.df)
            self._update_column_headers()
            if len(self.df) * max(len(self.df.columns), 1) <= 2000:
                self.table.resizeColumnsToContents()
            self.data_info.setText(
                f"{max(len(self.df) - DATA_START_ROW, 0)} data rows, {len(self.df.columns)} columns"
            )
        finally:
            self.table.blockSignals(False)

    def populate_columns(self, preferred_y: list[str] | None = None) -> None:
        columns = list(map(str, self.df.columns))
        previous_y = (
            self.checked_y_columns()
            if preferred_y is None
            else list(preferred_y)
        )
        self.series_by_y = {key: value for key, value in self.series_by_y.items() if key in columns}
        self._refresh_error_column_combo()

        self.y_list.blockSignals(True)
        self.y_list.clear()

        y_columns = [column for column in columns if self._column_role(column) == "Y" and self._nearest_left_x(column)]
        for column in y_columns:
            item = QListWidgetItem(column)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.y_list.addItem(item)

        kept_y = [column for column in previous_y if column in y_columns]
        if kept_y or preferred_y is not None:
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
        self._reset_undo_coalescing()
        self.push_undo_baseline()
        while row >= len(self.df):
            self.df.loc[len(self.df)] = [""] * len(self.df.columns)
        self.df.iat[row, col] = item.text()
        if row == ROLE_ROW:
            self.table.blockSignals(True)
            try:
                self._normalize_role_cell(item)
            finally:
                self.table.blockSignals(False)
            self.df.iat[row, col] = item.text()
            self._update_column_headers()
        self.data_info.setText(f"{max(len(self.df) - DATA_START_ROW, 0)} data rows, {len(self.df.columns)} columns")
        if row in (ROLE_ROW, NAME_ROW):
            self.populate_columns()
        self.schedule_render()
        self.update_undo_baseline()

    def sync_dataframe_from_table(
        self,
        preferred_y: list[str] | None = None,
    ) -> None:
        # Keep the live model and its selection/current cell intact. Structural
        # table operations may replace the model's backing DataFrame.
        self.df = self.table.dataframe()
        self.table.setVerticalHeaderLabels(self._row_labels(self.table.rowCount()))
        self.populate_columns(preferred_y)
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
        self.table.blockSignals(False)
        self.sync_dataframe_from_table()
        self.update_undo_baseline()
        self.set_status(f"Added row {insert_at + 1}.")

    def add_column(self) -> None:
        self.push_current_undo_state()
        col = self.table.currentColumn()
        insert_at = self.table.columnCount() if col < 0 else col + 1
        name = self._next_column_name()
        self.table.blockSignals(True)
        self.table.insertColumn(insert_at, name)
        self.table.blockSignals(False)
        self.sync_dataframe_from_table()
        self.update_undo_baseline()
        self.set_status(f"Added column {insert_at + 1}.")

    @staticmethod
    def _legend_entry_row_ids(
        entry_count: int,
        row_lengths: list[int],
    ) -> list[int]:
        return legend_row_ids(entry_count, row_lengths)

    @classmethod
    def _remove_legend_sources(
        cls,
        config: PlotConfig,
        removed_sources: set[str],
    ) -> None:
        if config.legend_entries is None:
            return
        row_ids = cls._legend_entry_row_ids(
            len(config.legend_entries),
            config.legend_row_lengths,
        )
        kept: list[tuple[int, LegendEntryConfig]] = [
            (row_id, entry)
            for row_id, entry in zip(row_ids, config.legend_entries)
            if entry.source_y not in removed_sources
        ]
        config.legend_entries = [entry for _row_id, entry in kept]

        compact_lengths: list[int] = []
        previous_row: int | None = None
        for row_id, _entry in kept:
            if row_id != previous_row:
                compact_lengths.append(1)
                previous_row = row_id
            else:
                compact_lengths[-1] += 1
        config.legend_row_lengths = (
            []
            if all(length == 1 for length in compact_lengths)
            else compact_lengths
        )

    @staticmethod
    def _rename_graph_column_reference(
        graph: Graph,
        old_name: str,
        new_name: str,
    ) -> None:
        graph.checked_y = [
            new_name if column == old_name else column
            for column in graph.checked_y
        ]
        remapped_series: dict[str, SeriesConfig] = {}
        for key, series in graph.series_by_y.items():
            source_was_renamed = key == old_name or series.y == old_name
            if series.x == old_name:
                series.x = new_name
            if series.error_column == old_name:
                series.error_column = new_name
            if source_was_renamed:
                series.y = new_name
            remapped_series[new_name if source_was_renamed else key] = series
        graph.series_by_y = remapped_series
        if graph.plot_config.legend_entries is not None:
            for entry in graph.plot_config.legend_entries:
                if entry.source_y == old_name:
                    entry.source_y = new_name

    @classmethod
    def _delete_graph_column_references(
        cls,
        graph: Graph,
        removed_names: set[str],
    ) -> None:
        graph.checked_y = [
            column
            for column in graph.checked_y
            if column not in removed_names
        ]
        kept_series: dict[str, SeriesConfig] = {}
        for key, series in graph.series_by_y.items():
            if key in removed_names or series.y in removed_names:
                continue
            if series.x in removed_names:
                series.x = ""
            if series.error_column in removed_names:
                series.error_column = ""
            kept_series[key] = series
        graph.series_by_y = kept_series
        cls._remove_legend_sources(graph.plot_config, removed_names)

    def _rename_sheet_column_references(
        self,
        sheet_id: str | None,
        old_name: str,
        new_name: str,
    ) -> None:
        if sheet_id is None:
            return
        for graph in self.graphs.values():
            if graph.sheet_id == sheet_id:
                self._rename_graph_column_reference(
                    graph,
                    old_name,
                    new_name,
                )

    def _delete_sheet_column_references(
        self,
        sheet_id: str | None,
        removed_names: set[str],
    ) -> None:
        if sheet_id is None:
            return
        for graph in self.graphs.values():
            if graph.sheet_id == sheet_id:
                self._delete_graph_column_references(
                    graph,
                    removed_names,
                )

    def _restore_active_graph_column_state(self) -> list[str] | None:
        if self.active_graph_id is None:
            return None
        graph = self.graphs.get(self.active_graph_id)
        if graph is None:
            return None
        self.series_by_y = deepcopy(graph.series_by_y)
        self.plot_config.legend_entries = deepcopy(
            graph.plot_config.legend_entries
        )
        self.plot_config.legend_row_lengths = list(
            graph.plot_config.legend_row_lengths
        )
        return list(graph.checked_y)

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
        self._rename_sheet_column_references(
            self.active_sheet_id,
            old_name,
            new_name,
        )
        preferred_y = self._restore_active_graph_column_state()
        self.sync_dataframe_from_table(preferred_y)
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
        ranges = self.table.selectedRanges()
        if not ranges and self.table.currentRow() >= 0 and self.table.currentColumn() >= 0:
            ranges = [
                TableSelectionRange(
                    self.table.currentRow(),
                    self.table.currentColumn(),
                    self.table.currentRow(),
                    self.table.currentColumn(),
                )
            ]
        if not ranges:
            self.set_status("Select one or more cells to clear.")
            return
        self.push_current_undo_state()
        self.table.blockSignals(True)
        for selected_range in ranges:
            self.table.fill_range(selected_range, "")
        self.table.blockSignals(False)
        self.sync_dataframe_from_table()
        self.update_undo_baseline()
        self.set_status("Cleared selected cells.")

    def delete_selected_rows(self) -> None:
        intervals = [
            (max(selected.topRow(), DATA_START_ROW), selected.bottomRow())
            for selected in self.table.selectedRanges()
            if selected.bottomRow() >= DATA_START_ROW
        ]
        if not intervals and self.table.currentRow() >= DATA_START_ROW:
            intervals = [(self.table.currentRow(), self.table.currentRow())]
        intervals = self._merge_intervals(intervals)
        if not intervals:
            self.set_status("Role/Name rows are kept.")
            return
        removed_count = sum(last - first + 1 for first, last in intervals)
        self.push_current_undo_state()
        self.table.blockSignals(True)
        for first, last in reversed(intervals):
            self.table.removeRows(first, last - first + 1)
        if self.table.rowCount() == 0:
            self.table.setRowCount(1)
        self.table.blockSignals(False)
        self.sync_dataframe_from_table()
        self.update_undo_baseline()
        self.set_status(f"Deleted {removed_count} row(s).")

    def delete_selected_columns(self) -> None:
        intervals = [
            (selected.leftColumn(), selected.rightColumn())
            for selected in self.table.selectedRanges()
        ]
        if not intervals and self.table.currentColumn() >= 0:
            intervals = [(self.table.currentColumn(), self.table.currentColumn())]
        intervals = self._merge_intervals(intervals)
        if not intervals:
            self.set_status("Select one or more columns to delete.")
            return
        removed_names = [
            self._header_text(col)
            for first, last in intervals
            for col in range(first, last + 1)
        ]
        removed_count = sum(last - first + 1 for first, last in intervals)
        self.push_current_undo_state()
        self.table.blockSignals(True)
        for first, last in reversed(intervals):
            self.table.removeColumns(first, last - first + 1)
        if self.table.columnCount() == 0:
            self.table.setColumnCount(1)
            self.table.setHorizontalHeaderItem(0, QTableWidgetItem("Col 1"))
        self.table.blockSignals(False)
        self._delete_sheet_column_references(
            self.active_sheet_id,
            set(removed_names),
        )
        preferred_y = self._restore_active_graph_column_state()
        self.sync_dataframe_from_table(preferred_y)
        self.update_undo_baseline()
        self.set_status(f"Deleted {removed_count} column(s).")

    def _selected_columns(self) -> list[int]:
        cols: list[int] = []
        for selected_range in self.table.selectedRanges():
            cols.extend(range(selected_range.leftColumn(), selected_range.rightColumn() + 1))
        return sorted(set(cols))

    @staticmethod
    def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
        merged: list[list[int]] = []
        for first, last in sorted(
            (min(first, last), max(first, last))
            for first, last in intervals
        ):
            if not merged or first > merged[-1][1] + 1:
                merged.append([first, last])
            else:
                merged[-1][1] = max(merged[-1][1], last)
        return [(first, last) for first, last in merged]

    def _paste_grid(self, start_row: int, start_col: int, grid: list[list[str]]) -> None:
        row_count = start_row + len(grid)
        col_count = start_col + max(len(row) for row in grid)
        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)
        try:
            self._ensure_table_size(row_count, col_count)

            normalized_grid = [
                [
                    self._normalized_role_text(value)
                    if start_row + row_offset == ROLE_ROW
                    else value
                    for value in row_values
                ]
                for row_offset, row_values in enumerate(grid)
            ]
            self.table.set_block(start_row, start_col, normalized_grid)
        finally:
            self.table.blockSignals(False)
            self.table.setUpdatesEnabled(True)

    def _ensure_table_size(self, rows: int, columns: int) -> None:
        if self.table.rowCount() < rows:
            self.table.setRowCount(rows)
        if self.table.columnCount() < columns:
            self.table.setColumnCount(columns)
        self._ensure_metadata_rows()

    def sync_dataframe_after_paste(self, start_row: int, start_col: int, grid: list[list[str]]) -> None:
        self.df = self.table.dataframe()
        self.table.setVerticalHeaderLabels(self._row_labels(self.table.rowCount()))
        metadata_touched = start_row <= NAME_ROW < start_row + len(grid) or start_row <= ROLE_ROW < start_row + len(grid)
        if metadata_touched:
            self.populate_columns()
        self._update_column_headers()
        self.data_info.setText(f"{max(len(self.df) - DATA_START_ROW, 0)} data rows, {len(self.df.columns)} columns")
        self.schedule_render()

    def _table_is_blank(self) -> bool:
        dataframe = self.table.dataframe()
        if dataframe.empty or dataframe.shape[1] == 0:
            return True
        visible_values = dataframe.iloc[NAME_ROW:].fillna("").astype(str)
        return not visible_values.apply(lambda column: column.str.strip().ne("").any()).any()

    def _header_text(self, col: int) -> str:
        item = self.table.horizontalHeaderItem(col)
        text = item.text() if item is not None and item.text().strip() else f"Col {col + 1}"
        return text.split(" (", 1)[0]

    def _next_column_name(self) -> str:
        return next_column_name(
            self._header_text(column)
            for column in range(self.table.columnCount())
        )

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
        return column_role(self.df, column)

    def _nearest_left_x(self, y_column: str) -> str:
        return nearest_left_x(self.df, y_column)

    def _column_name(self, column: str) -> str:
        return column_name(self.df, column)

    def _plot_dataframe(self) -> pd.DataFrame:
        return plot_dataframe(self.df)

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
            self.table.set_display_header(col, label)

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
        self.update_axis_control_states()
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
        target_changed = self.style_target_combo.currentText() != y_column
        self.style_target_combo.setCurrentText(y_column)
        if not target_changed:
            self._load_series_into_widgets(self.series_by_y[y_column])
        self.set_status(f"Editing series: {y_column}")

    def update_style_targets(self) -> None:
        current = self.style_target_combo.currentText()
        plotted = self.checked_y_columns()
        target = self.series_settings.set_targets(plotted, current)
        if target and target in self.series_by_y:
            self._load_series_into_widgets(self.series_by_y[target])

    def handle_style_target_changed(self) -> None:
        target = self.style_target_combo.currentText()
        if target and target in self.series_by_y:
            self._load_series_into_widgets(self.series_by_y[target])

    def _load_series_into_widgets(self, series: SeriesConfig) -> None:
        self.series_settings.load_series(
            series,
            error_columns=map(str, self.df.columns),
        )

    def _refresh_error_column_combo(self) -> None:
        self.series_settings.set_error_columns(map(str, self.df.columns))

    def apply_series_widget_state(self) -> None:
        target = self.style_target_combo.currentText()
        if not target or target not in self.series_by_y:
            return
        self.table.blockSignals(True)
        series = self.series_by_y[target]
        was_shown_in_legend = series.show_in_legend
        self.series_settings.update_series(series)
        label = series.label
        if (
            self.plot_config.legend_entries is not None
            and series.show_in_legend
            and not was_shown_in_legend
            and not any(
                entry.source_y == target
                for entry in self.plot_config.legend_entries
            )
        ):
            self.plot_config.legend_entries.append(
                LegendEntryConfig(
                    source_y=target,
                    label=label or self._column_name(target),
                )
            )
            if self.plot_config.legend_row_lengths:
                self.plot_config.legend_row_lengths.append(1)
        col = list(map(str, self.df.columns)).index(target)
        name_item = self.table.item(NAME_ROW, col)
        if name_item is None:
            name_item = QTableWidgetItem("")
            self.table.setItem(NAME_ROW, col, name_item)
        name_item.setText(label)
        self.df.iat[NAME_ROW, col] = label
        self.table.blockSignals(False)
        self.update_axis_control_states()
        self.schedule_render()

    def update_axis_control_states(self, *args) -> None:
        right_axis_needed = any(
            self.series_by_y.get(column) is not None
            and self.series_by_y[column].y_axis == "right"
            for column in self.checked_y_columns()
        )
        for widget in (
            self.y2_label_edit,
            self.y2_tick_pad_spin,
            self.y2_axis_color_btn,
            self.y2_scale_combo,
            self.y2_scale_divisor_edit,
            self.y2_min_edit,
            self.y2_max_edit,
            self.y2_tick_interval_edit,
            self.y2_minor_divisions_spin,
            self.show_y2_tick_labels_check,
        ):
            widget.setEnabled(right_axis_needed)

        x_break_compatible = (
            self.x_scale_combo.currentText() == "linear"
            and not right_axis_needed
            and not self.y_break_check.isChecked()
        )
        y_break_compatible = (
            self.y_scale_combo.currentText() == "linear"
            and not right_axis_needed
            and not self.x_break_check.isChecked()
        )
        for widget in (
            self.x_break_left_min_edit,
            self.x_break_left_max_edit,
            self.x_break_right_min_edit,
            self.x_break_right_max_edit,
            self.x_break_gap_spin,
        ):
            widget.setEnabled(self.x_break_check.isChecked() and x_break_compatible)
        for widget in (
            self.y_break_lower_min_edit,
            self.y_break_lower_max_edit,
            self.y_break_upper_min_edit,
            self.y_break_upper_max_edit,
            self.y_break_gap_spin,
        ):
            widget.setEnabled(self.y_break_check.isChecked() and y_break_compatible)

        self.x_break_check.setToolTip(
            "" if x_break_compatible else "Broken X requires linear X, no Y2 series, and no broken Y axis."
        )
        self.y_break_check.setToolTip(
            "" if y_break_compatible else "Broken Y requires linear Y, no Y2 series, and no broken X axis."
        )

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

    def apply_custom_gradient_to_series(self) -> None:
        selected = self.selected_cmap_columns()
        if not selected:
            self.set_status("Select at least one series in the column list.")
            return
        self.push_current_undo_state()
        count = len(selected)
        for idx, y_column in enumerate(selected):
            t = 0.5 if count == 1 else idx / (count - 1)
            color, alpha = self.gradient_editor.sample(t)
            series = self.series_by_y.get(y_column)
            if series is None:
                x_column = self._nearest_left_x(y_column)
                series = default_series(x_column, y_column, idx)
                self.series_by_y[y_column] = series
            series.color = color
            series.alpha = round(alpha, 4)
            series.force_opaque = False
        target = self.style_target_combo.currentText()
        if target in self.series_by_y:
            self._load_series_into_widgets(self.series_by_y[target])
        self.schedule_render()
        self.update_undo_baseline()
        self.set_status(f"Applied custom gradient ({len(self.gradient_editor.stops)} stops) to {count} series.")

    def _save_custom_gradient(self) -> None:
        self.settings.setValue("custom_gradient", json.dumps(self.gradient_editor.stops_payload()))

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
        return self.series_settings.selected_cmap_columns()

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

    def refresh_cmap_column_list(self) -> None:
        self.series_settings.set_cmap_columns(self.checked_y_columns())

    def choose_cmap_base_color(self) -> None:
        current = self.cmap_base_color_btn.text()
        color = QColorDialog.getColor(QColor(current), self, "Choose base color")
        if not color.isValid():
            return
        self._set_cmap_base_color_button(color.name())

    def _set_cmap_base_color_button(self, color: str) -> None:
        self.series_settings.set_cmap_base_color(color)

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
        self.render_timer.stop()
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
        return annotation_display_geometry(ax, annotation)

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
        annotation = annotation_config_from_payload(payload)
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

    def capture_current_style(self) -> dict:
        config = deepcopy(self.collect_plot_config())
        include_annotations = self.include_annotations_style_check.isChecked()
        config.annotations = []
        return {
            "plot_config": config,
            "series_templates": deepcopy(self.selected_series_configs()),
            "annotations": deepcopy(self.annotations) if include_annotations else [],
            "has_annotations": include_annotations,
        }

    @staticmethod
    def style_bundle_to_payload(bundle: dict) -> dict:
        plot_config = asdict(bundle["plot_config"])
        plot_config.pop("annotations", None)
        return {
            "plot_config": plot_config,
            "series_templates": [asdict(series) for series in bundle.get("series_templates", [])],
            "annotations": [asdict(annotation) for annotation in bundle.get("annotations", [])],
            "has_annotations": bool(bundle.get("has_annotations")),
        }

    @staticmethod
    def style_bundle_from_payload(payload: dict) -> dict | None:
        if not isinstance(payload, dict) or not isinstance(payload.get("plot_config"), dict):
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
        raw = self.settings.value(SAVED_STYLES_SETTINGS_KEY, "")
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
        self.settings.setValue(
            SAVED_STYLES_SETTINGS_KEY,
            json.dumps(envelope, ensure_ascii=False, separators=(",", ":")),
        )
        self.settings.sync()

    def refresh_saved_style_combo(self, preferred_name: str = "") -> None:
        styles = self.saved_style_payloads()
        previous = preferred_name or self.saved_style_combo.currentText()
        names = sorted(styles, key=str.casefold)
        self.saved_style_combo.blockSignals(True)
        self.saved_style_combo.clear()
        self.saved_style_combo.addItems(names)
        if names:
            match = next((name for name in names if name.casefold() == previous.casefold()), names[0])
            self.saved_style_combo.setCurrentText(match)
        else:
            self.saved_style_combo.setCurrentIndex(-1)
        self.saved_style_combo.blockSignals(False)
        has_styles = bool(names)
        self.apply_named_style_btn.setEnabled(has_styles)
        self.delete_named_style_btn.setEnabled(has_styles)
        self.update_named_style_save_button()

    def update_named_style_save_button(self, *_args) -> None:
        name = self.saved_style_name_edit.text().strip()
        if name:
            display_name = name if len(name) <= 24 else name[:21] + "..."
            self.save_named_style_btn.setText(f'Save to "{display_name}"')
        else:
            self.save_named_style_btn.setText("Save named style")
        self.save_named_style_btn.setEnabled(bool(name))

    @staticmethod
    def matching_saved_style_name(styles: dict[str, dict], requested_name: str) -> str | None:
        requested_key = requested_name.casefold()
        return next((name for name in styles if name.casefold() == requested_key), None)

    def save_named_style(self, name: str | None = None, *, overwrite: bool | None = None) -> bool:
        requested_name = (name if name is not None else self.saved_style_name_edit.text()).strip()
        if not requested_name:
            self.set_status("Enter a style name first.")
            return False
        styles = self.saved_style_payloads()
        existing_name = self.matching_saved_style_name(styles, requested_name)
        if existing_name is not None:
            requested_name = existing_name
            if overwrite is None:
                reply = QMessageBox.question(
                    self,
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
        self.saved_style_name_edit.setText(requested_name)
        self.refresh_saved_style_combo(requested_name)
        note = " with annotations" if bundle.get("has_annotations") else ""
        self.set_status(f'Saved style "{requested_name}"{note}.')
        return True

    def delete_named_style(self, name: str | None = None, *, confirm: bool = True) -> bool:
        requested_name = (name if name is not None else self.saved_style_combo.currentText()).strip()
        styles = self.saved_style_payloads()
        stored_name = self.matching_saved_style_name(styles, requested_name)
        if stored_name is None:
            self.set_status("Select a saved style first.")
            return False
        if confirm:
            reply = QMessageBox.question(
                self,
                "Delete saved style",
                f'Delete the saved style "{stored_name}"?',
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return False
        del styles[stored_name]
        self.write_saved_style_payloads(styles)
        if self.saved_style_name_edit.text().strip().casefold() == stored_name.casefold():
            self.saved_style_name_edit.clear()
        self.refresh_saved_style_combo()
        self.set_status(f'Deleted saved style "{stored_name}".')
        return True

    def copy_current_style(self) -> None:
        self.style_clipboard = self.capture_current_style()
        include_annotations = self.style_clipboard.get("has_annotations")
        note = " with annotations" if include_annotations else ""
        self.set_status(f"Copied current style{note}.")

    def apply_copied_style(self) -> None:
        if not self.style_clipboard:
            self.set_status("Copy a style first.")
            return
        self.apply_style_bundle(self.style_clipboard, "copied style")

    def apply_named_style(self, name: str | None = None) -> bool:
        requested_name = (name if name is not None else self.saved_style_combo.currentText()).strip()
        styles = self.saved_style_payloads()
        stored_name = self.matching_saved_style_name(styles, requested_name)
        if stored_name is None:
            self.set_status("Select a saved style first.")
            return False
        bundle = self.style_bundle_from_payload(styles[stored_name])
        if bundle is None:
            self.set_status(f'Could not read saved style "{stored_name}".')
            return False
        self.apply_style_bundle(bundle, f'saved style "{stored_name}"')
        return True

    def apply_style_bundle(self, bundle: dict, source_label: str) -> None:
        self.push_current_undo_state()
        current = deepcopy(self.collect_plot_config())
        style_config = deepcopy(bundle["plot_config"])
        preserved_legend_visibility = (
            {
                y_column: series.show_in_legend
                for y_column, series in self.series_by_y.items()
            }
            if current.legend_entries is not None
            else None
        )

        # Legend entry sources and labels are graph content, not a reusable
        # visual style. Keep the target graph's mapping when applying a style
        # captured from another graph. In explicit mode, the per-series
        # visibility flags are part of that mapping too.
        for attr in ("title", "legend_entries", "legend_row_lengths"):
            setattr(style_config, attr, getattr(current, attr))

        include_annotations = self.include_annotations_style_check.isChecked() and bundle.get("has_annotations")
        self.annotations = deepcopy(bundle["annotations"]) if include_annotations else self.annotations
        style_config.annotations = self.annotations
        self.plot_config = style_config
        self._load_config_into_widgets()

        templates = bundle.get("series_templates", [])
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
            series.line_style = template.line_style
            series.line_width = template.line_width
            series.marker_size = template.marker_size
            series.y_offset = template.y_offset
            series.alpha = template.alpha
            series.force_opaque = template.force_opaque
            series.show_in_legend = (
                template.show_in_legend
                if preserved_legend_visibility is None
                else preserved_legend_visibility.get(
                    y_column,
                    series.show_in_legend,
                )
            )
            series.error_cap_size = template.error_cap_size
            series.x = x_column
            series.y = y_column
            series.label = self._column_name(y_column)
            self.series_by_y[y_column] = series

        self.refresh_annotation_list()
        self.update_style_targets()
        self.render_plot()
        self.update_undo_baseline()
        note = " and annotations" if include_annotations else ""
        self.set_status(f"Applied {source_label}{note}.")

    def refresh_annotation_list(self) -> None:
        self.annotation_settings.set_annotations(self.annotations)

    def update_annotation_list_item(self, row: int) -> None:
        self.annotation_settings.update_annotation_list_item(row)

    def selected_annotation_indices(self) -> list[int]:
        return self.annotation_settings.selected_annotation_indices()

    def update_selected_annotation(self) -> None:
        if self.annotation_settings.loading_annotation_inputs:
            return
        row = self.annotation_list.currentRow()
        if row < 0 or row >= len(self.annotations):
            return
        annotation = self.annotations[row]
        self.annotation_settings.update_annotation(annotation)
        self.schedule_render()

    def _set_annotation_color_button(self, color: str) -> None:
        self.annotation_settings.set_color(color)

    def _set_y2_axis_color_button(self, color: str) -> None:
        self.plot_config.y2_axis_color = color
        self.y2_axis_color_btn.setText(color)
        self.y2_axis_color_btn.setStyleSheet(color_swatch_style(color))

    def _set_y_axis_color_button(self, color: str) -> None:
        self.plot_config.y_axis_color = color
        self.y_axis_color_btn.setText(color)
        self.y_axis_color_btn.setStyleSheet(color_swatch_style(color))

    def _set_color_button(self, color: str) -> None:
        self.series_settings.set_color(color)

    def handle_preset_changed(self, preset_name: str) -> None:
        if preset_name == "Custom":
            return
        apply_preset(self.plot_config, preset_name)
        widgets = (
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
            self.x_label_offset_spin,
            self.y_label_offset_spin,
            self.x_tick_pad_spin,
            self.y_tick_pad_spin,
            self.y2_tick_pad_spin,
        )
        with signals_blocked(*widgets):
            self.width_spin.setValue(self.plot_config.width_mm)
            self.height_spin.setValue(self.plot_config.height_mm)
            self.plot_width_spin.setValue(self.plot_config.plot_width_mm)
            self.plot_height_spin.setValue(self.plot_config.plot_height_mm)
            self.plot_ratio_lock_check.setChecked(self.plot_config.plot_ratio_locked)
            self.plot_ratio_preset_combo.setCurrentText(
                self.plot_config.plot_ratio_preset
                if self.plot_config.plot_ratio_preset in PLOT_RATIO_PRESETS
                else "Current"
            )
            self.dpi_spin.setValue(self.plot_config.dpi)
            self.title_size_spin.setValue(self.plot_config.title_size)
            self.axis_size_spin.setValue(self.plot_config.axis_size)
            self.tick_size_spin.setValue(self.plot_config.tick_size)
            self.legend_size_spin.setValue(self.plot_config.legend_size)
            self.x_label_offset_spin.setValue(self.plot_config.x_label_offset_mm)
            self.y_label_offset_spin.setValue(self.plot_config.y_label_offset_mm)
            self.x_tick_pad_spin.setValue(self.plot_config.x_tick_pad)
            self.y_tick_pad_spin.setValue(self.plot_config.y_tick_pad)
            self.y2_tick_pad_spin.setValue(self.plot_config.y2_tick_pad)

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
        previous = self.updating_plot_ratio
        self.updating_plot_ratio = True
        try:
            with signals_blocked(self.plot_height_spin):
                self.plot_height_spin.setValue(
                    max(
                        self.plot_height_spin.minimum(),
                        min(
                            self.plot_height_spin.maximum(),
                            self.plot_width_spin.value() / ratio,
                        ),
                    )
                )
        finally:
            self.updating_plot_ratio = previous

    def apply_plot_ratio_from_height(self, ratio: float) -> None:
        previous = self.updating_plot_ratio
        self.updating_plot_ratio = True
        try:
            with signals_blocked(self.plot_width_spin):
                self.plot_width_spin.setValue(
                    max(
                        self.plot_width_spin.minimum(),
                        min(
                            self.plot_width_spin.maximum(),
                            self.plot_height_spin.value() * ratio,
                        ),
                    )
                )
        finally:
            self.updating_plot_ratio = previous

    def collect_plot_config(self) -> PlotConfig:
        self.figure_settings.update(self.plot_config)
        self.plot_config.plot_aspect_ratio = (
            self.active_plot_ratio()
            if self.plot_config.plot_ratio_locked
            else self.current_plot_ratio()
        )
        self.plot_config.annotations = self.annotations
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

    def _render_request(
        self,
        config: PlotConfig | None = None,
        *,
        label: str = "",
    ) -> RenderRequest:
        return RenderRequest(
            dataframe=self._plot_dataframe(),
            config=config or self.collect_plot_config(),
            series_configs=tuple(self.selected_series_configs()),
            label=label,
        )

    def _adopt_render_result(self, result) -> None:
        self.current_figure = result.figure
        self.annotation_artists = result.annotation_artists
        self.legend_artist = result.legend_artist
        self._replace_canvas(result.figure)
        self.draw_annotation_handles()
        self.update_render_issues(result.warnings)

    def schedule_render(self) -> None:
        if self.loading_project_figure:
            return
        self.render_timer.start()

    def render_plot(self) -> None:
        config = self.collect_plot_config()
        request = self._render_request(config)
        try:
            result = self.rendering.render_for_display(
                request,
                self._adopt_render_result,
            )
        except Exception as exc:
            message = f"Preview failed: {exc}"
            self.update_render_issues([message])
            self.set_status(message)
            return
        if result.warnings:
            self.set_status(" | ".join(result.warnings[:2]))
        elif not self.df.empty:
            figure_width = result.figure.get_figwidth() * 25.4
            figure_height = result.figure.get_figheight() * 25.4
            self.set_status(
                f"Rendered {len(request.series_configs)} series at {figure_width:g} x {figure_height:g} mm"
            )

    def update_render_issues(self, warnings: list[str]) -> None:
        if not warnings:
            self.preview_issues.clear()
            self.preview_issues.hide()
            return
        visible = warnings[:5]
        text = "Figure issues:\n" + "\n".join(f"• {warning}" for warning in visible)
        if len(warnings) > len(visible):
            text += f"\n• …and {len(warnings) - len(visible)} more"
        self.preview_issues.setText(text)
        self.preview_issues.show()

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
                    self.set_status("No visible content to center.")
                    return

                px_per_mm = result.figure.dpi / 25.4
                figure_width_px = (
                    result.figure.get_figwidth() * result.figure.dpi
                )
                figure_height_px = (
                    result.figure.get_figheight() * result.figure.dpi
                )
                dx_mm = (
                    figure_width_px / 2 - bbox.x0 - bbox.width / 2
                ) / px_per_mm
                dy_mm = (
                    figure_height_px / 2 - bbox.y0 - bbox.height / 2
                ) / px_per_mm
        except Exception as exc:
            message = f"Center content failed: {exc}"
            self.update_render_issues([message])
            self.set_status(message)
            return

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
                    self.width_spin.minimum(),
                    min(self.width_spin.maximum(), new_width_mm),
                )
                new_height_mm = max(
                    self.height_spin.minimum(),
                    min(self.height_spin.maximum(), new_height_mm),
                )
                new_left_mm = max(
                    0.0,
                    (plot_bbox.x0 - bbox.x0 + pad_px) / px_per_mm
                    - config.pad_left_mm,
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
            self.set_status(message)
            return

        self.push_current_undo_state()
        self.set_canvas_size(new_width_mm, new_height_mm)
        self.set_plot_margins(new_left_mm, new_right_mm, new_top_mm, new_bottom_mm)
        self.render_plot()
        self.update_undo_baseline()
        self.set_status(f"Fit canvas to content: {new_width_mm:g} x {new_height_mm:g} mm.")

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
        self.preview_figure_width_in = figure.get_figwidth()
        self.preview_figure_height_in = figure.get_figheight()
        # FigureCanvasQT derives Retina backing-store DPI from _original_dpi.
        # Keep that logical DPI separate from the device-pixel ratio so the
        # preview preserves publication point sizes on both standard and HiDPI
        # displays.
        figure._original_dpi = PREVIEW_DPI
        figure.set_dpi(PREVIEW_DPI)
        figure.set_size_inches(self.preview_figure_width_in, self.preview_figure_height_in, forward=False)

        creating_canvas = self.canvas is None
        old_canvas = self.canvas
        old_toolbar = self.toolbar
        old_figure = old_canvas.figure if old_canvas is not None else None

        # FigureCanvasQTAgg does not expose a supported API for replacing its
        # Figure. Assigning canvas.figure leaves the Agg backing store and Qt
        # paint state tied to the previous Figure, which can paint a large
        # "ghost" plot behind a newly-sized preview. Replace both widgets so
        # each Figure starts with a clean renderer and toolbar.
        self.annotation_handle_artists = []
        if old_toolbar is not None:
            old_toolbar.hide()
            self.plot_layout.removeWidget(old_toolbar)
            old_toolbar.setParent(None)
            old_toolbar.deleteLater()
        if old_canvas is not None:
            old_canvas.hide()
            self.plot_layout.removeWidget(old_canvas)
            old_canvas.setParent(None)
            old_canvas.deleteLater()
        if old_figure is not None and old_figure is not figure:
            old_figure.clear()

        self.canvas = FigureCanvasQTAgg(figure)
        self.canvas.setObjectName("figurePreview")
        self.canvas.setAccessibleName("Figure preview")
        self.canvas.installEventFilter(self)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.toolbar.setIconSize(QSize(20, 20))
        self.canvas.mpl_connect("button_press_event", self.handle_canvas_press)
        self.canvas.mpl_connect("motion_notify_event", self.handle_canvas_motion)
        self.canvas.mpl_connect("button_release_event", self.handle_canvas_release)
        self.canvas.mpl_connect("scroll_event", self.handle_canvas_scroll)
        self.canvas.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.plot_layout.addWidget(self.toolbar)
        self.plot_layout.addWidget(self.canvas, 0, Qt.AlignCenter)
        # Widgets created while the main window is already running can retain
        # Qt's explicit hidden state after being adopted by the layout.
        self.toolbar.show()
        self.canvas.show()

        self.preview_base_width_px = max(80, int(self.preview_figure_width_in * PREVIEW_DPI))
        self.preview_base_height_px = max(60, int(self.preview_figure_height_in * PREVIEW_DPI))
        self.update_canvas_size(center=creating_canvas)
        self.plot_host.update()
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
            scale = min(usable_w / base_w, usable_h / base_h, 4.0)
            scale = max(scale, 0.1)
        else:
            scale = self.preview_zoom_spin.value() / 100.0
        self.preview_zoom_spin.setEnabled(not self.fit_preview_check.isChecked())
        # Size from the exact figure dimensions. ``preview_base_*`` is integer
        # cached for fit calculations, but using it again here can compound
        # truncation enough to create a visible HiDPI pixel mismatch.
        canvas_w = max(80, round(self.preview_figure_width_in * PREVIEW_DPI * scale))
        canvas_h = max(60, round(self.preview_figure_height_in * PREVIEW_DPI * scale))
        logical_dpi = PREVIEW_DPI * scale
        device_pixel_ratio = max(float(self.canvas.device_pixel_ratio), 1.0)
        self.current_figure._original_dpi = logical_dpi
        self.current_figure.set_dpi(logical_dpi * device_pixel_ratio)
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
            if contains and getattr(event, "dblclick", False):
                self.edit_legend_text()
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
        indices = list(range(len(self.annotation_artists) - 1, -1, -1))
        current = self.annotation_list.currentRow()
        if 0 <= current < len(self.annotation_artists):
            indices = [current] + [idx for idx in indices if idx != current]
        return indices

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

    def edit_legend_text(self) -> None:
        series_configs = self.selected_series_configs()
        if not series_configs:
            self.set_status("No plotted series are available for the legend.")
            return

        candidates = self.legend_editor_candidates(series_configs)
        entries = self.legend_editor_entries(candidates)
        dialog = LegendEditorDialog(
            candidates,
            entries,
            self.plot_config.legend_row_lengths,
            automatic=self.plot_config.legend_entries is None,
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        new_entries, row_lengths = dialog.result_config()
        changed = self.apply_legend_entry_config(
            new_entries,
            row_lengths,
            series_configs,
        )
        if not changed:
            self.set_status("Legend was not changed.")
            return
        self.update_undo_baseline()
        self.render_plot()
        self.set_status("Updated independent legend handles and names.")

    def legend_editor_candidates(
        self,
        series_configs: list[SeriesConfig],
    ) -> list[tuple[str, str, bool]]:
        left = [series for series in series_configs if series.y_axis != "right"]
        right = [series for series in series_configs if series.y_axis == "right"]
        return [
            (
                series.y,
                series.label or self._column_name(series.y),
                series.show_in_legend,
            )
            for series in left + right
        ]

    def legend_editor_entries(
        self,
        candidates: list[tuple[str, str, bool]],
    ) -> list[LegendEntryConfig]:
        if self.plot_config.legend_entries is None:
            return [
                LegendEntryConfig(source_y=source_y, label=label)
                for source_y, label, visible in candidates
                if visible
            ]

        entries = deepcopy(self.plot_config.legend_entries)
        used_sources = {entry.source_y for entry in entries}
        for source_y, label, visible in candidates:
            if visible and source_y not in used_sources:
                entries.append(LegendEntryConfig(source_y=source_y, label=label))
        return entries

    def apply_legend_entry_config(
        self,
        entries: list[LegendEntryConfig] | None,
        row_lengths: list[int],
        series_configs: list[SeriesConfig],
    ) -> bool:
        normalized_entries = (
            None
            if entries is None
            else [
                LegendEntryConfig(
                    source_y=str(entry.source_y),
                    label=str(entry.label),
                )
                for entry in entries
                if entry.source_y
            ]
        )
        clean_row_lengths = [max(0, int(length)) for length in row_lengths]
        if clean_row_lengths and all(length == 1 for length in clean_row_lengths):
            clean_row_lengths = []

        if (
            normalized_entries == self.plot_config.legend_entries
            and clean_row_lengths == self.plot_config.legend_row_lengths
        ):
            return False

        self.push_current_undo_state()
        if normalized_entries is not None:
            previous_sources = (
                {
                    entry.source_y
                    for entry in self.plot_config.legend_entries
                }
                if self.plot_config.legend_entries is not None
                else {
                    series.y
                    for series in series_configs
                    if series.show_in_legend
                }
            )
            selected_sources = {
                entry.source_y
                for entry in normalized_entries
            }
            for series in series_configs:
                if series.y not in selected_sources:
                    series.show_in_legend = False
                elif series.y not in previous_sources:
                    series.show_in_legend = True

        self.plot_config.legend_entries = normalized_entries
        self.plot_config.legend_row_lengths = clean_row_lengths
        self.update_style_targets()
        return True

    def legend_anchor_axes_fraction(self) -> tuple[float, float] | None:
        ax = self.legend_axes()
        if ax is None or self.legend_artist is None or self.canvas is None:
            return None
        renderer = self.canvas.get_renderer()
        bbox = self.legend_artist.get_window_extent(renderer=renderer)
        x, y = ax.transAxes.inverted().transform((bbox.x0, bbox.y1))
        return float(x), float(y)

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
            self._update_hover_hint(event)
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

    def _update_hover_hint(self, event) -> None:
        hint = ""
        if event.x is not None and event.y is not None:
            if self.legend_artist is not None:
                contains, _ = self.legend_artist.contains(event)
                if contains:
                    hint = "Legend: drag to move, double-click to edit entries, Ctrl constrains the drag."
            if not hint and self.annotation_handle_hit_test(event.x, event.y) is not None:
                hint = "Handle: drag to resize/rotate the selected annotation, Ctrl constrains."
            if not hint and self.best_annotation_hit(event) is not None:
                hint = "Annotation: drag to move, arrow keys nudge, Shift/Ctrl+drag moves the whole selection."
        if hint != self._hover_hint:
            self._hover_hint = hint
            if hint:
                self.set_status(hint)

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
        return rotated_endpoint(start_x, start_y, width_px, height_px, angle)

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
            try:
                handle.remove()
            except (NotImplementedError, ValueError):
                # The owning Figure may already have been replaced.
                pass
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
        self.drag_mode = None
        self.drag_original = None
        self.drag_group_original = None
        self.update_undo_baseline()
        self.set_status("Moved annotation.")

    def update_annotation_inputs(self, index: int) -> None:
        if index < 0 or index >= len(self.annotations):
            return
        self.annotation_settings.load_annotation(self.annotations[index])
        self.draw_annotation_handles()

    def export_current_figure(self) -> None:
        filters = "PNG (*.png);;PDF (*.pdf);;SVG (*.svg);;TIFF (*.tif *.tiff);;EPS (*.eps)"
        path, selected_filter = QFileDialog.getSaveFileName(
            self, "Export figure", str(self.last_folder / "figure.png"), filters
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
                (value for name, value in suffix_by_filter.items() if selected_filter.startswith(name)),
                ".png",
            )
            path_obj = path_obj.with_suffix(suffix)
        self.last_folder = path_obj.parent
        self.render_timer.stop()
        config = self.collect_plot_config()
        try:
            self.rendering.export_one(
                ExportJob(self._render_request(config, label=path_obj.stem), path_obj),
                retain_figure=True,
                accept=self._adopt_render_result,
            )
        except Exception as exc:
            message = f"Could not export {path_obj}:\n{exc}"
            QMessageBox.warning(self, "Export figure", message)
            self.set_status(f"Export failed: {exc}")
            return
        self.set_status(f"Exported figure: {path_obj}")

    def _ordered_graphs_with_folder(self) -> list[tuple[Graph, str]]:
        """Graphs in project-explorer (tree) order, each paired with the name of
        its nearest ancestor folder ("" when the graph sits under the root)."""
        ordered: list[tuple[Graph, str]] = []

        def walk(node: TreeNode, folder_name: str) -> None:
            for child in node.children:
                if child.type == "graph" and child.ref_id in self.graphs:
                    ordered.append((self.graphs[child.ref_id], folder_name))
                child_folder = child.name if child.type == "folder" else folder_name
                walk(child, child_folder)

        walk(self.tree_root, "")
        return ordered

    def export_all_figures(self) -> None:
        self.save_active_state()
        graphs = self._ordered_graphs_with_folder()
        if not graphs:
            QMessageBox.information(self, "Export all figures", "No graphs to export.")
            return

        folder = QFileDialog.getExistingDirectory(self, "Export all figures", str(self.last_folder))
        if not folder:
            return

        output_dir = Path(folder)
        self.last_folder = output_dir
        self.render_timer.stop()

        jobs: list[ExportJob] = []
        reserved_paths: set[Path] = set()
        for graph, folder_name in graphs:
            sheet = self.sheets.get(graph.sheet_id)
            if sheet is None:
                continue
            config = deepcopy(graph.plot_config)
            config.annotations = deepcopy(graph.annotations)
            graph_part = self._safe_export_filename(graph.name)
            if folder_name:
                base_name = f"{self._safe_export_filename(folder_name)}_{graph_part}"
            else:
                base_name = graph_part
            path = self._unique_export_path(output_dir / f"{base_name}.png", reserved_paths)
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
        self.set_status(message)

    def _project_plot_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        return plot_dataframe(df)

    def _project_column_name(self, df: pd.DataFrame, column: str) -> str:
        return column_name(df, column)

    def _project_nearest_left_x(self, df: pd.DataFrame, y_column: str) -> str:
        return nearest_left_x(df, y_column)

    def _project_series_configs(self, graph: Graph, df: pd.DataFrame) -> list[SeriesConfig]:
        series_configs: list[SeriesConfig] = []
        for idx, y_column in enumerate(graph.checked_y):
            if y_column not in df.columns:
                continue
            x_column = self._project_nearest_left_x(df, y_column)
            if not x_column:
                continue
            series = deepcopy(graph.series_by_y.get(y_column) or default_series(x_column, y_column, idx))
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

    def save_project(self) -> None:
        self.save_active_state()
        if not self.sheets:
            QMessageBox.information(self, "Save project", "Paste or load data first.")
            return
        path = self.current_project_path or self._choose_project_save_path()
        if path is not None:
            self._save_project_to(path)

    def _choose_project_save_path(self) -> Path | None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save project",
            str(self.last_folder / "graph_project.json"),
            "JSON (*.json)",
        )
        return Path(path) if path else None

    def _save_project_to(self, path: Path) -> bool:
        self.current_project_path = path
        try:
            self.write_project(self.current_project_path)
        except Exception as exc:
            QMessageBox.warning(self, "Save project", f"Could not save the project:\n{exc}")
            self.set_status(f"Save failed: {exc}")
            return False
        self.last_folder = self.current_project_path.parent
        self.add_recent_file(self.current_project_path)
        self.set_status(f"Saved project: {self.current_project_path}")
        return True

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
        self._init_blank_project()
        self.current_project_path = None
        self.undo_history.reset()
        self._set_modified(False)
        self.set_status("Started a new project.")

    def project_payload(self) -> dict:
        return document_to_payload(self.project_document())

    def project_document(self) -> ProjectDocument:
        return self.document

    def write_project(self, path: Path) -> None:
        if path.suffix.lower() != ".json":
            path = path.with_suffix(".json")
            self.current_project_path = path
        self._write_json_atomic(path, self.project_payload(), indent=2)
        self._set_modified(False)
        self._remove_autosave()

    def save_project_as(self) -> None:
        self.save_active_state()
        if not self.sheets:
            QMessageBox.information(self, "Save project", "Paste or load data first.")
            return
        path = self._choose_project_save_path()
        if path is not None:
            self._save_project_to(path)

    def load_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load project", str(self.last_folder), "JSON (*.json)")
        if not path:
            return
        self.open_project_path(Path(path))

    def open_project_path(self, path: Path) -> None:
        try:
            document = read_project_document(path)
        except Exception as exc:
            QMessageBox.warning(self, "Load project", f"Could not load {path}:\n{exc}")
            return

        self.set_model(
            document.sheets,
            document.graphs,
            document.tree_root,
            document.active_node_id,
        )
        self.last_folder = path.parent
        self.current_project_path = path
        self.undo_history.reset()
        self._set_modified(False)
        self.add_recent_file(path)
        self.set_status(f"Loaded project: {path}")

    def load_payload_into_model(
        self, payload: dict, fallback_name: str
    ) -> tuple[dict[str, Sheet], dict[str, Graph], TreeNode, str | None]:
        return decode_project_payload(payload, fallback_name)

    def _seed_blank_sheet(self, sheets: dict[str, Sheet], tree_root: TreeNode) -> str:
        """Add one empty sheet + node to an otherwise-empty model; return its node id."""
        sheet = Sheet(id=new_id("sh"), name="Sheet 1", df=self.blank_dataframe())
        sheets[sheet.id] = sheet
        node = TreeNode(id=new_id("nd"), type="sheet", name=sheet.name, ref_id=sheet.id)
        tree_root.children.append(node)
        return node.id

    def _load_config_into_widgets(self) -> None:
        self.figure_settings.load(self.plot_config)
        with signals_blocked(self.trim_check, self.transparent_check):
            self.trim_check.setChecked(self.plot_config.trim_whitespace)
            self.transparent_check.setChecked(self.plot_config.transparent)
        self.update_axis_control_states()

    def set_status(self, message: str) -> None:
        self.statusBar().showMessage(message)

    def _int_spin(self, value: int, minimum: int, maximum: int) -> QSpinBox:
        spin = NoWheelSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        spin.setMinimumWidth(86)
        return spin
