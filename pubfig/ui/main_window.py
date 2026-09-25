"""Main Qt window: controller composition and application event routing."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QSettings, Qt, QTimer
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QSpinBox,
    QWidget,
)

from ..editor_session import EditorSession
from ..table_view import SpreadsheetTableWidget
from ..theme import apply_theme, normalize_theme_name
from .annotations_editor import AnnotationController
from .canvas_interaction import CanvasInteractionController
from .constants import DATA_FILE_SUFFIXES
from .constants import MAX_UNDO_STACK_WEIGHT_BYTES as MAX_UNDO_STACK_WEIGHT_BYTES
from .figure_editor import FigureController
from .legend_editor import LegendController
from .preview_controller import PreviewController
from .project_files import ProjectFilesController
from .project_tree import ProjectTreeController
from .series_editor import SeriesController
from .style_controller import StyleController
from .table_editor import TableEditor
from .window_compat import legacy_window_api
from .window_layout import WindowLayout
from .workspace_controller import WorkspaceController


@legacy_window_api
class GraphDrawerWindow(QMainWindow):
    """Compose the editor; feature controllers own its workflows and state."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("pubfig — Untitled project")
        self.resize(1500, 900)
        self.setAcceptDrops(True)
        self.session = EditorSession()
        self.settings = QSettings("pubfig", "pubfig")
        self._application_event_filter_installed = False
        app = QApplication.instance()
        active_theme = app.property("pubfigTheme") if app is not None else None
        if active_theme is None:
            active_theme = self.settings.value("interface_theme", "light")
        self.interface_theme = normalize_theme_name(active_theme)
        self.workspace = WorkspaceController(self)
        self.files = ProjectFilesController(self)
        self.project_tree = ProjectTreeController(self)
        self.table_editor = TableEditor(self)
        self.series_editor = SeriesController(self)
        self.figure_editor = FigureController(self)
        self.annotations_editor = AnnotationController(self)
        self.styles = StyleController(self)
        self.interaction = CanvasInteractionController(self)
        self.legend_editor = LegendController(self)
        self.preview = PreviewController(self)
        self.ui_builder = WindowLayout(self)
        self.ui_builder._build_ui()
        self.ui_builder._build_menus()
        self.ui_builder._connect_signals()
        self.styles.refresh_saved_style_combo()
        self.workspace._init_blank_project()
        self.workspace.undo_history.reset()
        if app is not None:
            app.installEventFilter(self)
            self._application_event_filter_installed = True
        self.files.autosave_timer.start()
        # Keep the documented startup hook overridable by embedders.
        QTimer.singleShot(0, self.maybe_restore_autosave)

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.Save):
            self.files.save_project()
            return
        if event.matches(QKeySequence.Redo):
            self.workspace.redo_workspace()
            return
        if event.matches(QKeySequence.Undo):
            self.workspace.undo_workspace()
            return
        if (
            event.key() in (Qt.Key_Delete, Qt.Key_Backspace)
            and self.annotations_editor.delete_selected_annotation_from_key()
        ):
            return
        if self.focus_widget_uses_text_shortcuts():
            super().keyPressEvent(event)
            return
        if self.interaction.nudge_selected_annotations_from_key(event):
            return
        if (
            event.matches(QKeySequence.Copy)
            and self.annotations_editor.copy_selected_annotation()
        ):
            return
        if (
            event.matches(QKeySequence.Paste)
            and self.annotations_editor.paste_annotation()
        ):
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Wheel and isinstance(
            watched, (QAbstractSpinBox, QComboBox)
        ):
            # Never let the scroll wheel change numeric/combo values. Forward the
            # scroll to the enclosing scroll area so the panel still scrolls.
            scroller = watched.parentWidget()
            while scroller is not None and not isinstance(
                scroller, QAbstractScrollArea
            ):
                scroller = scroller.parentWidget()
            if scroller is not None:
                QApplication.sendEvent(scroller.viewport(), event)
            return True
        if event.type() == QEvent.FocusIn and watched in self.special_text_fields():
            self.annotations_editor.special_text_target = watched
        if event.type() == QEvent.KeyPress:
            if event.matches(QKeySequence.Save):
                self.files.save_project()
                return True
            if event.matches(QKeySequence.Redo):
                self.workspace.redo_workspace()
                return True
            if event.matches(QKeySequence.Undo):
                self.workspace.undo_workspace()
                return True
            if (
                event.key() in (Qt.Key_Delete, Qt.Key_Backspace)
                and self.annotations_editor.delete_selected_annotation_from_key()
            ):
                return True
        if event.type() == QEvent.KeyPress and watched is getattr(self, "canvas", None):
            if self.interaction.nudge_selected_annotations_from_key(event):
                return True
        if event.type() == QEvent.KeyPress and watched in (
            getattr(getattr(self, "annotation_settings", None), "list_widget", None),
            getattr(self, "canvas", None),
        ):
            if (
                event.matches(QKeySequence.Copy)
                and self.annotations_editor.copy_selected_annotation()
            ):
                return True
            if (
                event.matches(QKeySequence.Paste)
                and self.annotations_editor.paste_annotation()
            ):
                return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event) -> None:
        if not self.files.confirm_close():
            event.ignore()
            return
        self.files.shutdown()
        self.preview.close()
        if self._application_event_filter_installed:
            application = QApplication.instance()
            if application is not None:
                application.removeEventFilter(self)
            self._application_event_filter_installed = False
        event.accept()

    def focus_widget_uses_text_shortcuts(self) -> bool:
        focus = QApplication.focusWidget()
        return isinstance(
            focus,
            (
                QLineEdit,
                QPlainTextEdit,
                QSpinBox,
                QDoubleSpinBox,
                SpreadsheetTableWidget,
            ),
        )

    def special_text_fields(self) -> tuple[QWidget, ...]:
        return (
            self.annotation_settings.text_edit,
            self.figure_settings.title_edit,
            self.figure_settings.x_label_edit,
            self.figure_settings.y_label_edit,
            self.figure_settings.y2_label_edit,
            self.series_settings.label_edit,
        )

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

    def dragEnterEvent(self, event) -> None:
        if self._dropped_paths(event):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = self._dropped_paths(event)
        if not paths:
            return
        path = paths[0]
        if path.suffix.lower() == ".json":
            self.files.open_project_path(path)
        else:
            self.table_editor.import_data_path(path)
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

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "fit_preview_check") and self.fit_preview_check.isChecked():
            self.preview.update_canvas_size()

    def set_status(self, message: str) -> None:
        self.statusBar().showMessage(message)
