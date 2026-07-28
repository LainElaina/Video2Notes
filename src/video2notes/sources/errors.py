"""Source-layer exceptions and cooperative cancellation."""

from __future__ import annotations

import threading


class SourceError(RuntimeError):
    """Base class for safe, user-facing source errors."""


class UnsupportedSourceError(SourceError):
    pass


class SourceProbeError(SourceError):
    pass


class SourceAcquisitionError(SourceError):
    pass


class QualityChangedError(SourceAcquisitionError):
    pass


class AcquisitionCancelled(SourceAcquisitionError):
    pass


class CancellationToken:
    """Thread-safe cooperative cancellation shared with yt-dlp hooks."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise AcquisitionCancelled("source operation was cancelled")

