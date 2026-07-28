"""PyInstaller entry point for the self-contained local Video2Notes API."""

from __future__ import annotations

from video2notes.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
