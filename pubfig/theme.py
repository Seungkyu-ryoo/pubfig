"""IBM Carbon-inspired light and dark themes for the pubfig GUI."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QPushButton


DEFAULT_THEME = "light"
THEME_NAMES = ("light", "dark")
RADIUS = 2

THEMES = {
    "light": {
        "bg": "#f4f4f4",
        "surface": "#ffffff",
        "surface_alt": "#f4f4f4",
        "surface_hover": "#e8e8e8",
        "border": "#e0e0e0",
        "border_strong": "#8d8d8d",
        "text": "#161616",
        "text_muted": "#525252",
        "text_disabled": "#8d8d8d",
        "accent": "#0f62fe",
        "accent_hover": "#0353e9",
        "accent_pressed": "#002d9c",
        "accent_disabled": "#a6c8ff",
        "danger": "#da1e28",
        "preview": "#e0e0e0",
        "warning_bg": "#fff8e1",
        "warning_text": "#684e00",
        "warning_border": "#f1c21b",
        "scroll": "#a8a8a8",
        "on_accent": "#ffffff",
        "tooltip_bg": "#262626",
        "tooltip_text": "#ffffff",
    },
    "dark": {
        "bg": "#161616",
        "surface": "#262626",
        "surface_alt": "#393939",
        "surface_hover": "#474747",
        "border": "#525252",
        "border_strong": "#8d8d8d",
        "text": "#f4f4f4",
        "text_muted": "#c6c6c6",
        "text_disabled": "#8d8d8d",
        "accent": "#0f62fe",
        "accent_hover": "#4589ff",
        "accent_pressed": "#78a9ff",
        "accent_disabled": "#0043ce",
        "danger": "#fa4d56",
        "preview": "#0f0f0f",
        "warning_bg": "#332b00",
        "warning_text": "#fddc69",
        "warning_border": "#b28600",
        "scroll": "#6f6f6f",
        "on_accent": "#ffffff",
        "tooltip_bg": "#f4f4f4",
        "tooltip_text": "#161616",
    },
}


def normalize_theme_name(theme_name: object) -> str:
    """Return a supported theme name, falling back to the light theme."""
    name = str(theme_name or "").strip().lower()
    return name if name in THEME_NAMES else DEFAULT_THEME


def build_stylesheet(theme_name: str) -> str:
    """Build the application QSS from one Carbon-inspired token set."""
    t = THEMES[normalize_theme_name(theme_name)]
    return f"""
* {{
    font-size: 13px;
}}

QWidget {{
    background-color: {t["bg"]};
    color: {t["text"]};
}}

QMainWindow, QDialog {{
    background-color: {t["bg"]};
}}

QToolTip {{
    background-color: {t["tooltip_bg"]};
    color: {t["tooltip_text"]};
    border: 1px solid {t["border_strong"]};
    padding: 5px 8px;
}}

/* ---- Flat Carbon-style sections ---- */
QGroupBox {{
    background-color: {t["surface"]};
    border: 1px solid {t["border"]};
    border-radius: 0;
    margin-top: 14px;
    padding: 10px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 8px;
    top: 1px;
    padding: 0 5px;
    color: {t["text_muted"]};
    text-transform: uppercase;
    font-size: 11px;
    letter-spacing: 0.5px;
}}
QGroupBox:disabled {{
    color: {t["text_disabled"]};
}}

/* ---- Buttons ---- */
QPushButton {{
    background-color: {t["surface_alt"]};
    color: {t["text"]};
    border: 1px solid {t["border_strong"]};
    border-radius: {RADIUS}px;
    padding: 6px 12px;
    min-height: 18px;
}}
QPushButton:hover {{
    background-color: {t["surface_hover"]};
    border-color: {t["text_muted"]};
}}
QPushButton:pressed {{
    background-color: {t["border"]};
}}
QPushButton:focus {{
    border: 2px solid {t["accent"]};
    padding: 5px 11px;
}}
QPushButton:disabled {{
    color: {t["text_disabled"]};
    background-color: {t["surface_alt"]};
    border-color: {t["border"]};
}}

QPushButton#primary {{
    background-color: {t["accent"]};
    color: {t["on_accent"]};
    border: 1px solid {t["accent"]};
    font-weight: 600;
}}
QPushButton#primary:hover {{
    background-color: {t["accent_hover"]};
    border-color: {t["accent_hover"]};
}}
QPushButton#primary:pressed {{
    background-color: {t["accent_pressed"]};
    border-color: {t["accent_pressed"]};
}}
QPushButton#primary:disabled {{
    background-color: {t["accent_disabled"]};
    border-color: {t["accent_disabled"]};
    color: {t["on_accent"]};
}}

/* ---- Inputs ---- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: {t["surface_alt"]};
    color: {t["text"]};
    border: 1px solid {t["border"]};
    border-bottom: 1px solid {t["border_strong"]};
    border-radius: 0;
    padding: 4px 8px;
    min-height: 18px;
    selection-background-color: {t["accent"]};
    selection-color: {t["on_accent"]};
}}
QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover,
QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{
    background-color: {t["surface_hover"]};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {t["accent"]};
    border-bottom: 2px solid {t["accent"]};
}}
QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled,
QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    background-color: {t["surface"]};
    color: {t["text_disabled"]};
    border-color: {t["border"]};
}}
QLineEdit[invalid="true"] {{
    border-color: {t["danger"]};
}}

QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    width: 16px;
    border: none;
    background: transparent;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: none;
    width: 0; height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-bottom: 5px solid {t["text_muted"]};
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: none;
    width: 0; height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {t["text_muted"]};
}}

QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox::down-arrow {{
    image: none;
    width: 0; height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {t["text_muted"]};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background-color: {t["surface"]};
    color: {t["text"]};
    border: 1px solid {t["border_strong"]};
    padding: 3px;
    outline: none;
    selection-background-color: {t["accent"]};
    selection-color: {t["on_accent"]};
}}

/* ---- Tabs ---- */
QTabWidget::pane {{
    background-color: {t["surface"]};
    border: none;
    border-top: 1px solid {t["border"]};
    top: -1px;
}}
QTabBar::tab {{
    background-color: {t["surface"]};
    color: {t["text_muted"]};
    padding: 7px 12px 6px;
    margin: 0;
    border: none;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:hover {{
    color: {t["text"]};
    background-color: {t["surface_hover"]};
}}
QTabBar::tab:selected {{
    color: {t["text"]};
    border-bottom: 2px solid {t["accent"]};
    font-weight: 600;
}}

/* ---- Lists, trees, and tables ---- */
QListWidget, QTableWidget, QTableView, QTreeView {{
    background-color: {t["surface"]};
    alternate-background-color: {t["surface_alt"]};
    color: {t["text"]};
    border: 1px solid {t["border"]};
    border-radius: 0;
    outline: none;
    gridline-color: {t["border"]};
    selection-background-color: {t["accent"]};
    selection-color: {t["on_accent"]};
}}
QListWidget::item, QTreeView::item {{
    padding: 4px 6px;
}}
QListWidget::item:hover, QTreeView::item:hover {{
    background-color: {t["surface_hover"]};
}}
QListWidget::item:selected, QTreeView::item:selected,
QTableWidget::item:selected, QTableView::item:selected {{
    background-color: {t["accent"]};
    color: {t["on_accent"]};
}}
QHeaderView::section {{
    background-color: {t["surface_alt"]};
    color: {t["text_muted"]};
    padding: 5px 6px;
    border: none;
    border-right: 1px solid {t["border"]};
    border-bottom: 1px solid {t["border"]};
    font-weight: 600;
}}
QTableCornerButton::section {{
    background-color: {t["surface_alt"]};
    border: none;
    border-right: 1px solid {t["border"]};
    border-bottom: 1px solid {t["border"]};
}}

/* ---- Checkboxes ---- */
QCheckBox {{
    spacing: 7px;
    background: transparent;
}}
QCheckBox:disabled {{
    color: {t["text_disabled"]};
}}

/* ---- Scrollbars and splitters ---- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {t["scroll"]};
    border-radius: 0;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {t["text_muted"]}; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {t["scroll"]};
    border-radius: 0;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{ background: {t["text_muted"]}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{
    background: {t["bg"]};
}}
QSplitter::handle:horizontal {{
    width: 6px;
    border-left: 1px solid {t["border"]};
}}
QSplitter::handle:vertical {{
    height: 6px;
    border-top: 1px solid {t["border"]};
}}
QSplitter::handle:hover {{
    background: {t["accent"]};
}}

/* ---- Menu, toolbar, and status ---- */
QMenuBar {{
    background-color: {t["surface"]};
    color: {t["text"]};
    border-bottom: 1px solid {t["border"]};
}}
QMenuBar::item {{
    background: transparent;
    padding: 5px 9px;
}}
QMenuBar::item:selected {{
    background-color: {t["surface_hover"]};
}}
QMenu {{
    background-color: {t["surface"]};
    color: {t["text"]};
    border: 1px solid {t["border_strong"]};
    padding: 3px;
}}
QMenu::item {{
    padding: 6px 26px 6px 12px;
}}
QMenu::item:selected {{
    background-color: {t["accent"]};
    color: {t["on_accent"]};
}}
QMenu::separator {{
    height: 1px;
    background: {t["border"]};
    margin: 4px 8px;
}}
QToolBar {{
    background-color: {t["preview"]};
    border: none;
    spacing: 2px;
    padding: 2px;
}}
QToolButton {{
    background: transparent;
    color: {t["text"]};
    border: 1px solid transparent;
    border-radius: {RADIUS}px;
    padding: 4px;
}}
QToolButton:hover {{
    background-color: {t["surface_hover"]};
    border-color: {t["border_strong"]};
}}
QToolButton:checked, QToolButton:pressed {{
    background-color: {t["accent"]};
    color: {t["on_accent"]};
}}
QStatusBar {{
    background: {t["surface"]};
    border-top: 1px solid {t["border"]};
    color: {t["text_muted"]};
}}

/* ---- Application-specific surfaces ---- */
QScrollArea {{
    border: none;
}}
QWidget#plotPreviewHost {{
    background-color: {t["preview"]};
}}
QWidget#figurePreview {{
    background-color: #ffffff;
    border: 1px solid {t["border_strong"]};
}}
QLabel#previewIssues {{
    background-color: {t["warning_bg"]};
    color: {t["warning_text"]};
    border: 1px solid {t["warning_border"]};
    border-radius: {RADIUS}px;
    padding: 6px 8px;
}}
QLabel {{
    background: transparent;
}}
"""


def color_swatch_style(color: str) -> str:
    """Return theme-independent QSS for a color-picker swatch button."""
    qcolor = QColor(color)
    luminance = 0.299 * qcolor.red() + 0.587 * qcolor.green() + 0.114 * qcolor.blue()
    text_color = "#161616" if luminance > 150 else "#ffffff"
    return (
        f"QPushButton {{ background-color: {color}; color: {text_color};"
        f" border: 1px solid rgba(128,128,128,0.8); border-radius: {RADIUS}px;"
        f" padding: 6px 12px; font-weight: 600; }}"
        f" QPushButton:hover {{ border: 2px solid rgba(128,128,128,1.0);"
        f" padding: 5px 11px; }}"
    )


def style_color_button(button: QPushButton, color: str) -> None:
    """Set *button*'s label to the hex code and apply the swatch style."""
    button.setText(color)
    button.setStyleSheet(color_swatch_style(color))


def apply_theme(app: QApplication, theme_name: str = DEFAULT_THEME) -> str:
    """Apply a Carbon-inspired theme and return its normalized name."""
    normalized = normalize_theme_name(theme_name)
    app.setStyle("Fusion")
    installed = set(QFontDatabase.families())
    for family in (
        "IBM Plex Sans",
        "Arial",
        "Segoe UI",
        "Noto Sans",
        "Liberation Sans",
        "DejaVu Sans",
    ):
        if family in installed:
            app.setFont(QFont(family))
            break
    app.setStyleSheet(build_stylesheet(normalized))
    app.setProperty("pubfigTheme", normalized)
    return normalized
