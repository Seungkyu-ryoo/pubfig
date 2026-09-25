"""Build the window widgets and connect feature-controller actions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..table_view import SpreadsheetTableWidget
from ..theme import THEME_NAMES
from .annotation_settings import AnnotationSettingsPanel
from .figure_settings import FigureSettingsPanel
from .series_settings import SeriesSettingsPanel
from .table_actions import TableActions
from .widgets import ProjectTreeWidget, integer_spin

if TYPE_CHECKING:
    from .main_window import GraphDrawerWindow


class WindowLayout(QObject):
    """Build the window widgets and connect feature-controller actions."""

    def __init__(self, window: GraphDrawerWindow) -> None:
        super().__init__(window)
        self.window = window
        self.session = window.session

    _int_spin = staticmethod(integer_spin)

    def _build_menus(self) -> None:
        menubar = self.window.menuBar()

        file_menu = menubar.addMenu("&File")
        self._add_menu_action(
            file_menu, "New Project", self.window.files.new_project, "Ctrl+Shift+N"
        )
        self._add_menu_action(
            file_menu, "New Sheet", self.window.project_tree.new_sheet, "Ctrl+N"
        )
        self._add_menu_action(
            file_menu, "New Graph", self.window.project_tree.new_graph, "Ctrl+G"
        )
        file_menu.addSeparator()
        self._add_menu_action(
            file_menu, "Open Project...", self.window.files.load_project, "Ctrl+O"
        )
        self.window.recent_menu = file_menu.addMenu("Open Recent")
        self.window.files._update_recent_menu()
        self._add_menu_action(
            file_menu,
            "Import Data File...",
            self.window.table_editor.import_data_file,
            "Ctrl+I",
        )
        file_menu.addSeparator()
        self._add_menu_action(
            file_menu, "Save Project", self.window.files.save_project, "Ctrl+S"
        )
        self._add_menu_action(
            file_menu,
            "Save Project As...",
            self.window.files.save_project_as,
            "Ctrl+Shift+S",
        )
        file_menu.addSeparator()
        self.window.export_figure_action = self._add_menu_action(
            file_menu,
            "Export Figure...",
            self.window.preview.export_current_figure,
            "Ctrl+E",
        )
        self._add_menu_action(
            file_menu, "Export All Figures...", self.window.preview.export_all_figures
        )
        self.window.export_selected_action = self._add_menu_action(
            file_menu, "Export Selected Figures...", self.window.preview.export_selected_figures
        )
        self.window.export_selected_action.setEnabled(False)
        file_menu.addSeparator()
        self._add_menu_action(file_menu, "Exit", self.window.close, "Ctrl+Q")

        edit_menu = menubar.addMenu("&Edit")
        self._add_menu_action(
            edit_menu, "Undo", self.window.workspace.undo_workspace, "Ctrl+Z"
        )
        redo_action = self._add_menu_action(
            edit_menu, "Redo", self.window.workspace.redo_workspace
        )
        redo_action.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        edit_menu.addSeparator()
        self.window.copy_figure_action = self._add_menu_action(
            edit_menu,
            "Copy Figure to Clipboard",
            self.window.preview.copy_figure_to_clipboard,
            "Ctrl+Alt+C",
        )
        edit_menu.addSeparator()
        self.window.rename_project_action = self._add_menu_action(
            edit_menu, "Rename Project...", self.window.project_tree.rename_project_root
        )

        view_menu = menubar.addMenu("&View")
        theme_menu = view_menu.addMenu("Theme")
        self.window.theme_action_group = QActionGroup(self.window)
        self.window.theme_action_group.setExclusive(True)
        self.window.theme_actions: dict[str, QAction] = {}
        labels = {"light": "Light (White)", "dark": "Dark (Black)"}
        for theme_name in THEME_NAMES:
            action = QAction(labels[theme_name], self.window)
            action.setCheckable(True)
            action.setChecked(theme_name == self.window.interface_theme)
            action.triggered.connect(
                lambda checked=False, name=theme_name: (
                    self.window.set_interface_theme(name) if checked else None
                )
            )
            self.window.theme_action_group.addAction(action)
            theme_menu.addAction(action)
            self.window.theme_actions[theme_name] = action

    def _add_menu_action(
        self, menu: QMenu, text: str, slot, shortcut: str | None = None
    ) -> QAction:
        action = QAction(text, self.window)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(8, 8, 8, 8)
        self.window.setCentralWidget(root)

        splitter = QSplitter(Qt.Horizontal)
        root_layout.addWidget(splitter, 1)

        splitter.addWidget(self._build_data_panel())
        splitter.addWidget(self._build_plot_panel())
        splitter.addWidget(self._build_settings_panel())
        splitter.setSizes([380, 720, 400])

        self.window.statusBar().showMessage("Ready")

    def _build_data_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        project_box = QGroupBox("Project Explorer")
        project_layout = QVBoxLayout(project_box)
        self.window.figure_tree = ProjectTreeWidget()
        self.window.figure_tree.setHeaderHidden(True)
        self.window.figure_tree.setMinimumHeight(80)
        self.window.figure_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.window.figure_tree.setToolTip(
            "Shift-click to select a range; Ctrl/Cmd-click to select individual graphs. "
            "Use Export selected figures to export the highlighted graphs."
        )
        self.window.figure_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.window.figure_tree.customContextMenuRequested.connect(
            self.window.project_tree.show_tree_menu
        )
        self.window.figure_tree.setDragDropMode(QAbstractItemView.InternalMove)
        self.window.figure_tree.setDragEnabled(True)
        self.window.figure_tree.setAcceptDrops(True)
        self.window.figure_tree.setDropIndicatorShown(True)
        self.window.figure_tree.node_dropped.connect(
            self.window.project_tree.handle_tree_drop
        )
        self.window.figure_tree.itemExpanded.connect(
            self.window.project_tree._on_item_expanded
        )
        self.window.figure_tree.itemCollapsed.connect(
            self.window.project_tree._on_item_collapsed
        )
        project_layout.addWidget(self.window.figure_tree)
        project_buttons = QHBoxLayout()
        self.window.new_sheet_btn = QPushButton("New Sheet")
        self.window.new_graph_btn = QPushButton("New Graph")
        self.window.new_folder_btn = QPushButton("New Folder")
        project_buttons.addWidget(self.window.new_sheet_btn)
        project_buttons.addWidget(self.window.new_graph_btn)
        project_buttons.addWidget(self.window.new_folder_btn)
        project_layout.addLayout(project_buttons)
        project_buttons2 = QHBoxLayout()
        self.window.duplicate_figure_btn = QPushButton("Duplicate")
        self.window.rename_figure_btn = QPushButton("Rename")
        self.window.rename_figure_btn.setToolTip(
            "Rename the selected project item (F2)"
        )
        self.window.delete_figure_btn = QPushButton("Delete")
        project_buttons2.addWidget(self.window.duplicate_figure_btn)
        project_buttons2.addWidget(self.window.rename_figure_btn)
        project_buttons2.addWidget(self.window.delete_figure_btn)
        project_layout.addLayout(project_buttons2)
        project_move_buttons = QHBoxLayout()
        self.window.move_figure_up_btn = QPushButton("Up")
        self.window.move_figure_down_btn = QPushButton("Down")
        project_move_buttons.addWidget(self.window.move_figure_up_btn)
        project_move_buttons.addWidget(self.window.move_figure_down_btn)
        project_layout.addLayout(project_move_buttons)

        self.window.table = SpreadsheetTableWidget()
        self.window.table.pasted.connect(self.window.table_editor.handle_table_paste)
        self.window.table.copied.connect(self.window.set_status)
        self.window.table.delete_requested.connect(
            self.window.table_editor.clear_selected_cells
        )
        self.window.table.itemChanged.connect(
            self.window.table_editor.handle_table_item_changed
        )
        self.window.table.dataframeChanged.connect(
            self.window.preview.preview_numeric_cache.clear
        )
        self.window.table.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.AnyKeyPressed
        )
        self.window.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.window.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.window.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.window.table_actions = TableActions(self.window)
        self.window.table.customContextMenuRequested.connect(
            self.window.table_editor.show_table_menu
        )
        self.window.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Interactive
        )
        self.window.table.horizontalHeader().setMinimumSectionSize(20)
        self.window.table.verticalHeader().setVisible(True)
        self.window.table.verticalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.window.table.verticalHeader().setMinimumSectionSize(18)

        self.window.data_splitter = QSplitter(Qt.Vertical)
        self.window.data_splitter.addWidget(project_box)
        self.window.data_splitter.addWidget(self.window.table)
        self.window.data_splitter.setChildrenCollapsible(False)
        self.window.data_splitter.setStretchFactor(0, 0)
        self.window.data_splitter.setStretchFactor(1, 1)
        self.window.data_splitter.setSizes([260, 520])
        layout.addWidget(self.window.data_splitter, 1)

        self.window.data_info = QLabel("No data")
        layout.addWidget(self.window.data_info)
        return panel

    def _build_plot_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 0, 8, 0)

        self.window.plot_scroll = QScrollArea()
        self.window.plot_scroll.setWidgetResizable(False)
        self.window.plot_host = QWidget()
        self.window.plot_host.setObjectName("plotPreviewHost")
        self.window.plot_layout = QVBoxLayout(self.window.plot_host)
        self.window.plot_layout.setContentsMargins(8, 8, 8, 8)
        self.window.plot_layout.setAlignment(Qt.AlignCenter)
        self.window.plot_scroll.setWidget(self.window.plot_host)
        layout.addWidget(self.window.plot_scroll, 1)

        self.window.preview.canvas: FigureCanvasQTAgg | None = None
        self.window.preview.toolbar: NavigationToolbar2QT | None = None
        self.window.preview.preview_base_width_px = 0
        self.window.preview.preview_base_height_px = 0
        self.window.preview.preview_figure_width_in = 4.0
        self.window.preview.preview_figure_height_in = 3.0
        preview_row = QHBoxLayout()
        self.window.fit_preview_check = QCheckBox("Fit preview")
        self.window.fit_preview_check.setChecked(True)
        self.window.preview_zoom_spin = self.window.ui_builder._int_spin(100, 25, 400)
        preview_row.addWidget(self.window.fit_preview_check)
        preview_row.addWidget(QLabel("Zoom %"))
        preview_row.addWidget(self.window.preview_zoom_spin)
        self.window.center_preview_btn = QPushButton("Center")
        preview_row.addWidget(self.window.center_preview_btn)
        preview_row.addStretch(1)
        layout.addLayout(preview_row)
        self.window.preview_issues = QLabel()
        self.window.preview_issues.setWordWrap(True)
        self.window.preview_issues.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.window.preview_issues.setAccessibleName("Figure issues")
        self.window.preview_issues.setObjectName("previewIssues")
        self.window.preview_issues.hide()
        layout.addWidget(self.window.preview_issues)
        self.window.preview._replace_canvas(Figure(figsize=(4, 3), dpi=100))
        return panel

    def _build_settings_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 8, 4)
        layout.setSpacing(10)

        self.window.column_box = self._build_column_box()
        self.window.series_box = self._build_series_box()
        self.window.annotation_box = self._build_annotation_box()
        self.window.figure_box = self._build_figure_box()
        self.window.special_chars_box = self._build_special_chars_box()
        self.window.style_copy_box = self._build_style_copy_box()
        self.window.export_box = self._build_export_box()
        layout.addWidget(self.window.column_box)
        layout.addWidget(self.window.series_box)
        layout.addWidget(self.window.annotation_box)
        layout.addWidget(self.window.figure_box)
        layout.addWidget(self.window.special_chars_box)
        layout.addWidget(self.window.style_copy_box)
        layout.addWidget(self.window.export_box)
        layout.addStretch(1)
        scroll.setWidget(panel)
        return scroll

    def _build_column_box(self) -> QGroupBox:
        box = QGroupBox("Origin-style columns")
        layout = QFormLayout(box)
        self.window.y_list = QListWidget()
        self.window.y_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.window.y_list.setMaximumHeight(140)
        self.window.plot_y_all_btn = QPushButton("All")
        self.window.plot_y_all_btn.setObjectName("plotYAllButton")
        self.window.plot_y_all_btn.setToolTip("Plot all available Y columns")
        self.window.plot_y_none_btn = QPushButton("None")
        self.window.plot_y_none_btn.setObjectName("plotYNoneButton")
        self.window.plot_y_none_btn.setToolTip("Clear all plotted Y columns")

        plot_y_widget = QWidget()
        plot_y_layout = QVBoxLayout(plot_y_widget)
        plot_y_layout.setContentsMargins(0, 0, 0, 0)
        plot_y_layout.addWidget(self.window.y_list)
        plot_y_buttons = QHBoxLayout()
        plot_y_buttons.setContentsMargins(0, 0, 0, 0)
        plot_y_buttons.addWidget(self.window.plot_y_all_btn)
        plot_y_buttons.addWidget(self.window.plot_y_none_btn)
        plot_y_buttons.addStretch(1)
        plot_y_layout.addLayout(plot_y_buttons)

        hint = QLabel(
            "Set table Role row to X/Y. Each Y uses the nearest X on its left."
        )
        hint.setWordWrap(True)
        layout.addRow(hint)
        layout.addRow("Plot Y", plot_y_widget)
        return box

    def _build_series_box(self) -> QGroupBox:
        self.window.series_settings = SeriesSettingsPanel(
            gradient_stops=self.window.settings.value("custom_gradient")
        )
        self.window.series_settings.install_compatibility_aliases(self.window)
        return self.window.series_settings

    def _build_annotation_box(self) -> QGroupBox:
        self.window.annotation_settings = AnnotationSettingsPanel(
            self.session.annotations
        )
        self.window.annotation_settings.install_compatibility_aliases(self.window)
        self.window.annotation_settings.list_widget.installEventFilter(self.window)
        return self.window.annotation_settings

    def _build_figure_box(self) -> QGroupBox:
        self.window.figure_settings = FigureSettingsPanel(self.session.plot_config)
        self.window.figure_settings.install_compatibility_aliases(self.window)
        return self.window.figure_settings

    def _build_style_copy_box(self) -> QGroupBox:
        box = QGroupBox("Style copy")
        layout = QVBoxLayout(box)
        self.window.include_annotations_style_check = QCheckBox("Include annotations")
        self.window.include_annotations_style_check.setChecked(True)
        self.window.copy_style_btn = QPushButton("Copy current style")
        self.window.apply_style_btn = QPushButton("Apply style")
        layout.addWidget(self.window.include_annotations_style_check)
        layout.addWidget(self.window.copy_style_btn)
        layout.addWidget(self.window.apply_style_btn)

        layout.addWidget(QLabel("Saved styles"))
        self.window.saved_style_combo = QComboBox()
        self.window.saved_style_combo.setPlaceholderText("Select a saved style")
        layout.addWidget(self.window.saved_style_combo)

        save_row = QHBoxLayout()
        self.window.saved_style_name_edit = QLineEdit()
        self.window.saved_style_name_edit.setPlaceholderText("Style name")
        self.window.saved_style_name_edit.setMaxLength(80)
        self.window.save_named_style_btn = QPushButton("Save named style")
        save_row.addWidget(self.window.saved_style_name_edit, 1)
        save_row.addWidget(self.window.save_named_style_btn)
        layout.addLayout(save_row)

        saved_actions_row = QHBoxLayout()
        self.window.apply_named_style_btn = QPushButton("Apply saved")
        self.window.delete_named_style_btn = QPushButton("Delete")
        self.window.apply_named_style_btn.setEnabled(False)
        self.window.delete_named_style_btn.setEnabled(False)
        saved_actions_row.addWidget(self.window.apply_named_style_btn)
        saved_actions_row.addWidget(self.window.delete_named_style_btn)
        layout.addLayout(saved_actions_row)
        return box

    def _build_special_chars_box(self) -> QGroupBox:
        box = QGroupBox("Special characters")
        layout = QVBoxLayout(box)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        groups = {
            "Greek": [
                "\u03b1",
                "\u03b2",
                "\u03b3",
                "\u03b4",
                "\u03b5",
                "\u03b8",
                "\u03bb",
                "\u03bc",
                "\u03c0",
                "\u03c3",
                "\u03c4",
                "\u03c6",
                "\u03c7",
                "\u03c9",
                "\u0394",
                "\u03a9",
            ],
            "Math": [
                "\u00b0",
                "\u00b1",
                "\u00d7",
                "\u00f7",
                "\u00b7",
                "\u2212",
                "\u2264",
                "\u2265",
                "\u2248",
                "\u2260",
                "\u221e",
                "\u221a",
                "\u2206",
                "\u2202",
                "\u222b",
                "\u2211",
            ],
            "Units": [
                "\u00b5",
                "\u212b",
                "\u00b0C",
                "\u03a9",
                "\u03a9\u00b7cm",
                "cm^2",
                "m^2",
                "10^{-3}",
                "^-1",
                "_2",
                "_3",
                "_{0.5}",
                "bar{1}",
                "bar{2}",
                "bar{3}",
                "bar{0}",
            ],
            "Arrows": [
                "\u2190",
                "\u2192",
                "\u2191",
                "\u2193",
                "\u2194",
                "\u21d0",
                "\u21d2",
                "\u21d4",
                "\u21b5",
                "\u21c4",
                "\u25b2",
                "\u25bc",
                "\u25c0",
                "\u25b6",
                "\u25b3",
                "\u25bd",
            ],
            "Shapes": [
                "\u2b1b",
                "\u2b1c",
                "\u25fc",
                "\u25fb",
                "\u25aa",
                "\u25ab",
                "\u25cf",
                "\u25cb",
                "\u25c6",
                "\u25c7",
                "\u25b2",
                "\u25b3",
                "\u25bc",
                "\u25bd",
                "\u25c0",
                "\u25c1",
                "\u25b6",
                "\u25b7",
                "\u2605",
                "\u2606",
                "\u2713",
                "\u2717",
                "\u25ac",
                "\u25ad",
                "\u25ef",
                "\u25cc",
            ],
        }

        for label, chars in groups.items():
            tabs.addTab(self._build_special_char_tab(chars), label)
        for field in self.window.special_text_fields():
            field.installEventFilter(self.window)
        self.window.annotations_editor.special_text_target = (
            self.window.annotation_settings.text_edit
        )
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
            button.clicked.connect(
                lambda checked=False, value=char: (
                    self.window.annotations_editor.insert_special_character(value)
                )
            )
            grid.addWidget(button, idx // 4, idx % 4)
        return tab

    def _build_export_box(self) -> QGroupBox:
        box = QGroupBox("Export / Project")
        layout = QVBoxLayout(box)

        self.window.trim_check = QCheckBox("Trim whitespace")
        self.window.transparent_check = QCheckBox("Transparent background")
        self.window.export_btn = QPushButton("Export figure")
        self.window.export_btn.setObjectName("primary")
        self.window.export_all_btn = QPushButton("Export all figures")
        self.window.export_selected_btn = QPushButton("Export selected figures (0)")
        self.window.export_selected_btn.setEnabled(False)
        self.window.export_selected_btn.setToolTip(
            "Export only highlighted Graph entries as PNG files. "
            "Selecting a sheet or folder does not include its unselected graphs."
        )
        self.window.new_project_btn = QPushButton("New project")
        self.window.save_project_btn = QPushButton("Save project")
        self.window.save_project_btn.setObjectName("primary")
        self.window.load_project_btn = QPushButton("Load project")

        layout.addWidget(self.window.trim_check)
        layout.addWidget(self.window.transparent_check)
        layout.addWidget(self.window.export_btn)
        layout.addWidget(self.window.export_selected_btn)
        layout.addWidget(self.window.export_all_btn)
        layout.addWidget(self.window.new_project_btn)
        layout.addWidget(self.window.save_project_btn)
        layout.addWidget(self.window.load_project_btn)
        return box

    def _connect_signals(self) -> None:
        self.window.figure_tree.itemSelectionChanged.connect(
            self.window.preview.update_export_selection
        )
        self.window.figure_tree.currentItemChanged.connect(
            self.window.project_tree.on_tree_selection_changed
        )
        self.window.figure_tree.rename_requested.connect(
            self.window.project_tree.rename_node
        )
        self.window.new_sheet_btn.clicked.connect(self.window.project_tree.new_sheet)
        self.window.new_graph_btn.clicked.connect(self.window.project_tree.new_graph)
        self.window.new_folder_btn.clicked.connect(self.window.project_tree.new_folder)
        self.window.duplicate_figure_btn.clicked.connect(
            self.window.project_tree.duplicate_selected_node
        )
        self.window.rename_figure_btn.clicked.connect(
            self.window.project_tree.rename_selected_node
        )
        self.window.delete_figure_btn.clicked.connect(
            self.window.project_tree.delete_selected_node
        )
        self.window.move_figure_up_btn.clicked.connect(
            lambda: self.window.project_tree.move_selected_node(-1)
        )
        self.window.move_figure_down_btn.clicked.connect(
            lambda: self.window.project_tree.move_selected_node(1)
        )
        self.window.fit_preview_check.toggled.connect(
            self.window.preview.update_canvas_size
        )
        self.window.preview_zoom_spin.valueChanged.connect(
            self.window.preview.update_canvas_size
        )
        self.window.center_preview_btn.clicked.connect(
            self.window.preview.center_preview
        )
        self.window.figure_settings.center_plot_box_btn.clicked.connect(
            self.window.preview.center_plot_box
        )
        self.window.figure_settings.center_content_btn.clicked.connect(
            self.window.preview.center_content
        )
        self.window.figure_settings.fit_canvas_btn.clicked.connect(
            self.window.preview.fit_canvas_to_content
        )
        self.window.y_list.itemChanged.connect(
            self.window.series_editor.handle_plot_y_changed
        )
        self.window.y_list.itemClicked.connect(
            self.window.series_editor.handle_plot_y_clicked
        )
        self.window.plot_y_all_btn.clicked.connect(
            lambda _checked=False: self.window.table_editor.set_all_plot_y_checked(True)
        )
        self.window.plot_y_none_btn.clicked.connect(
            lambda _checked=False: self.window.table_editor.set_all_plot_y_checked(
                False
            )
        )
        self.window.series_settings.target_combo.currentTextChanged.connect(
            self.window.series_editor.handle_style_target_changed
        )
        self.window.figure_settings.x_scale_combo.currentTextChanged.connect(
            self.window.figure_editor.update_axis_control_states
        )
        self.window.figure_settings.y_scale_combo.currentTextChanged.connect(
            self.window.figure_editor.update_axis_control_states
        )
        self.window.figure_settings.y2_scale_combo.currentTextChanged.connect(
            self.window.figure_editor.update_axis_control_states
        )
        self.window.figure_settings.x_break_check.toggled.connect(
            self.window.figure_editor.update_axis_control_states
        )
        self.window.figure_settings.y_break_check.toggled.connect(
            self.window.figure_editor.update_axis_control_states
        )
        self.window.series_settings.color_button.clicked.connect(
            self.window.series_editor.choose_series_color
        )
        self.window.series_settings.apply_cmap_button.clicked.connect(
            self.window.series_editor.apply_colormap_to_plotted_series
        )
        self.window.series_settings.cmap_base_color_button.clicked.connect(
            self.window.series_editor.choose_cmap_base_color
        )
        self.window.annotation_settings.color_requested.connect(
            self.window.annotations_editor.choose_annotation_color
        )
        self.window.figure_settings.y_axis_color_btn.clicked.connect(
            self.window.figure_editor.choose_y_axis_color
        )
        self.window.figure_settings.y2_axis_color_btn.clicked.connect(
            self.window.figure_editor.choose_y2_axis_color
        )
        self.window.figure_settings.edit_legend_btn.clicked.connect(
            self.window.legend_editor.edit_legend_text
        )
        self.window.annotation_settings.add_requested.connect(
            self.window.annotations_editor.add_annotation
        )
        self.window.annotation_settings.remove_requested.connect(
            self.window.annotations_editor.remove_selected_annotation
        )
        self.window.series_settings.apply_palette_button.clicked.connect(
            self.window.series_editor.apply_recommended_palette_to_series
        )
        self.window.series_settings.apply_gradient_button.clicked.connect(
            self.window.series_editor.apply_custom_gradient_to_series
        )
        self.window.series_settings.gradient_editor.changed.connect(
            self.window.series_editor._save_custom_gradient
        )
        self.window.annotation_settings.selection_changed.connect(
            lambda _index: self.window.interaction.draw_annotation_handles()
        )
        self.window.annotation_settings.connect_changed(
            lambda source: self.window.workspace.handle_undoable_widget_change(
                self.window.annotations_editor.update_selected_annotation, source
            )
        )
        self.window.export_btn.clicked.connect(
            self.window.preview.export_current_figure
        )
        self.window.export_all_btn.clicked.connect(
            self.window.preview.export_all_figures
        )
        self.window.export_selected_btn.clicked.connect(
            self.window.preview.export_selected_figures
        )
        self.window.new_project_btn.clicked.connect(self.window.files.new_project)
        self.window.save_project_btn.clicked.connect(self.window.files.save_project)
        self.window.load_project_btn.clicked.connect(self.window.files.load_project)
        self.window.copy_style_btn.clicked.connect(
            self.window.styles.copy_current_style
        )
        self.window.apply_style_btn.clicked.connect(
            self.window.styles.apply_copied_style
        )
        self.window.saved_style_name_edit.textChanged.connect(
            self.window.styles.update_named_style_save_button
        )
        self.window.saved_style_name_edit.returnPressed.connect(
            self.window.styles.save_named_style
        )
        self.window.save_named_style_btn.clicked.connect(
            lambda: self.window.styles.save_named_style()
        )
        self.window.apply_named_style_btn.clicked.connect(
            lambda: self.window.styles.apply_named_style()
        )
        self.window.delete_named_style_btn.clicked.connect(
            lambda: self.window.styles.delete_named_style()
        )

        self.window.series_settings.connect_changed(
            lambda source: self.window.workspace.handle_undoable_widget_change(
                self.window.series_editor.apply_series_widget_state, source
            )
        )

        self.window.figure_settings.connect_changed(
            self.window.figure_editor.handle_figure_setting_changed
        )
        for widget in (self.window.trim_check, self.window.transparent_check):
            self.window.workspace._connect_change(
                widget, self.window.preview.schedule_render
            )
