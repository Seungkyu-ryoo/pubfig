"""Application bootstrap helpers for the pubfig GUI.

Creating the Qt application and entering its event loop are deliberately
separate.  Tests and embedding applications can call :func:`create_application`
without blocking, while command-line entry points call :func:`main`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import sys
from typing import cast

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from .theme import apply_theme
from .ui.main_window import GraphDrawerWindow


@dataclass(frozen=True)
class ApplicationSession:
    """A created application/window pair whose event loop has not started."""

    application: QApplication
    window: GraphDrawerWindow
    owns_application: bool

    def exec(self) -> int:
        """Enter the Qt event loop and return its process exit code."""

        return self.application.exec()


def create_application(
    argv: Sequence[str] | None = None,
    *,
    show: bool = True,
    settings: QSettings | None = None,
    window_factory: Callable[[], GraphDrawerWindow] | None = None,
) -> ApplicationSession:
    """Create and configure pubfig without entering the Qt event loop.

    ``argv`` is the complete Qt argument sequence, including the executable
    name.  An existing :class:`QApplication` is reused, which makes this API
    suitable for notebooks, tests, and other Qt hosts.  ``window_factory`` and
    ``settings`` are injectable to keep bootstrap tests isolated.
    """

    existing = QApplication.instance()
    owns_application = existing is None
    if existing is None:
        arguments = list(sys.argv if argv is None else argv)
        application = QApplication(arguments or ["pubfig"])
        application.setApplicationName("pubfig")
        application.setOrganizationName("pubfig")
    else:
        application = cast(QApplication, existing)

    preferences = settings if settings is not None else QSettings("pubfig", "pubfig")
    apply_theme(application, preferences.value("interface_theme", "light"))

    factory = window_factory or GraphDrawerWindow
    window = factory()
    if show:
        window.show()
    return ApplicationSession(application, window, owns_application)


def main(argv: Sequence[str] | None = None) -> int:
    """Create the default GUI and run it until the user exits."""

    return create_application(argv).exec()


__all__ = ["ApplicationSession", "create_application", "main"]
