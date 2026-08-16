from __future__ import annotations

import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

import main as legacy_main
from pubfig.application import create_application, main


class FakeWindow:
    def __init__(self) -> None:
        self.show_calls = 0

    def show(self) -> None:
        self.show_calls += 1


class ApplicationBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication(["pubfig-test"])

    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        settings_path = Path(self.temp_directory.name) / "settings.ini"
        self.settings = QSettings(str(settings_path), QSettings.IniFormat)

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_create_application_is_non_blocking_and_can_keep_window_hidden(self) -> None:
        self.settings.setValue("interface_theme", "dark")

        session = create_application(
            ["pubfig-test"],
            show=False,
            settings=self.settings,
            window_factory=FakeWindow,
        )

        self.assertIs(session.application, self.application)
        self.assertFalse(session.owns_application)
        self.assertEqual(session.window.show_calls, 0)
        self.assertEqual(session.application.property("pubfigTheme"), "dark")

    def test_create_application_shows_window_without_starting_event_loop(self) -> None:
        window = FakeWindow()

        session = create_application(
            ["pubfig-test"],
            settings=self.settings,
            window_factory=lambda: window,
        )

        self.assertIs(session.window, window)
        self.assertEqual(window.show_calls, 1)

    def test_main_is_the_only_bootstrap_path_that_executes_session(self) -> None:
        session = Mock()
        session.exec.return_value = 23
        with patch("pubfig.application.create_application", return_value=session) as create:
            result = main(["pubfig-test", "--example"])

        self.assertEqual(result, 23)
        create.assert_called_once_with(["pubfig-test", "--example"])
        session.exec.assert_called_once_with()

    def test_python_m_pubfig_dispatches_to_application_main(self) -> None:
        with patch("pubfig.application.main", return_value=17) as packaged_main:
            with self.assertRaises(SystemExit) as raised:
                runpy.run_module("pubfig", run_name="__main__")

        self.assertEqual(raised.exception.code, 17)
        packaged_main.assert_called_once_with()

    def test_legacy_main_delegates_to_packaged_entrypoint(self) -> None:
        with patch.object(legacy_main, "application_main", return_value=9) as packaged_main:
            result = legacy_main.main(["legacy-main"])

        self.assertEqual(result, 9)
        packaged_main.assert_called_once_with(["legacy-main"])


if __name__ == "__main__":
    unittest.main()
