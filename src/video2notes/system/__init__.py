"""Hardware discovery and independent performance/quality planning."""

from .estimate import ProcessingEstimate, estimate_processing_time
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
    "ProcessingEstimate",
    "QualityMode",
    "SecondaryAsrPolicy",
    "build_execution_plan",
    "detect_hardware",
    "estimate_processing_time",
    "recommend_hardware_tier",
]
