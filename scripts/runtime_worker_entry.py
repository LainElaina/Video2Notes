"""PyInstaller entry point for an isolated Video2Notes inference worker."""

from video2notes.runtime_worker import main

if __name__ == "__main__":
    raise SystemExit(main())
