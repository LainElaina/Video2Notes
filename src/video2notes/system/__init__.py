"""Hardware discovery and independent performance/quality planning."""

from .acceleration import (
    AccelerationCapabilities,
    EngineAcceleration,
    align_execution_plan_with_acceleration,
    detect_acceleration_capabilities,
    prepare_nvidia_cuda_runtime,
)
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
    "AccelerationCapabilities",
    "EngineAcceleration",
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
    "align_execution_plan_with_acceleration",
    "build_execution_plan",
    "detect_acceleration_capabilities",
    "detect_hardware",
    "estimate_processing_time",
    "prepare_nvidia_cuda_runtime",
    "recommend_hardware_tier",
    "recommend_resources",
]
