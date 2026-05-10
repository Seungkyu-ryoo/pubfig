"""Entry point for Graph_drawer."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app import GraphDrawerWindow


def main() -> int:
    app = QApplication(sys.argv)
    window = GraphDrawerWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
