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
    PerformanceOverrides,
    QualityMode,
    SecondaryAsrPolicy,
    build_execution_plan,
)
from .resources import (
    ExperienceMode,
    ResourceBudget,
    ResourcePreference,
    ResourceRecommendation,
    ResourceReserve,
    recommend_resources,
)

__all__ = [
    "ExecutionPlan",
    "ExperienceMode",
    "GpuDevice",
    "HardwareSnapshot",
    "HardwareTier",
    "PerformanceOverrides",
    "ProcessingEstimate",
    "QualityMode",
    "ResourceBudget",
    "ResourcePreference",
    "ResourceRecommendation",
    "ResourceReserve",
    "SecondaryAsrPolicy",
    "build_execution_plan",
    "detect_hardware",
    "estimate_processing_time",
    "recommend_hardware_tier",
    "recommend_resources",
]
