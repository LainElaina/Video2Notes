"""Loopback-only FastAPI application used by the Tauri desktop shell."""

from .app import ApiContext, create_app

__all__ = ["ApiContext", "create_app"]
