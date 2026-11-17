"""Application entry point for open-escl-client."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    """Run the open-escl-client application."""
    args = sys.argv[1:] if argv is None else argv
    if "--version" in args:
        from open_escl_client import __version__

        print(f"open-escl-client {__version__}")
        return 0

    from open_escl_client.ui.app import run_gui_app

    return run_gui_app([sys.argv[0], *args])


if __name__ == "__main__":
    raise SystemExit(main())
