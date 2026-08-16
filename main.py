"""Backward-compatible source-tree launcher for pubfig."""

from __future__ import annotations

from collections.abc import Sequence

from pubfig.application import main as application_main


def main(argv: Sequence[str] | None = None) -> int:
    """Delegate to the packaged entry point."""

    return application_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
