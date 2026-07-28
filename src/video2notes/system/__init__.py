"""Hardware discovery and independent performance/quality planning."""

from .hardware import (
    GpuDevice,
    HardwareSnapshot,
    HardwareTier,
    detect_hardware,
    recommend_hardware_tier,
)
from .profiles import (
    ExecutionPlan,
    QualityMode,
    SecondaryAsrPolicy,
    build_execution_plan,
)

__all__ = [
    "ExecutionPlan",
    "GpuDevice",
    "HardwareSnapshot",
    "HardwareTier",
    "QualityMode",
    "SecondaryAsrPolicy",
    "build_execution_plan",
    "detect_hardware",
    "recommend_hardware_tier",
]
