"""Video2Notes core package."""

from .vision.adaptive_sampler import (
    AdaptiveScanConfig,
    AdaptiveVideoScanner,
    ChangeEvent,
    ScanResult,
)

__all__ = [
    "AdaptiveScanConfig",
    "AdaptiveVideoScanner",
    "ChangeEvent",
    "ScanResult",
]

__version__ = "0.2.0"
