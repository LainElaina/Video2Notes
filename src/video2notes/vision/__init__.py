"""Visual evidence discovery primitives."""

from .adaptive_sampler import (
    AdaptiveScanConfig,
    AdaptiveVideoScanner,
    ChangeEvent,
    FrameObservation,
    ScanResult,
    StableStateDetector,
    VideoProbe,
    timestamped_observations,
)
from .sampling import (
    MAX_FIXED_SAMPLES,
    SamplingMode,
    SamplingOverride,
    SamplingPlan,
    SamplingSegment,
    SamplingSpec,
    TimeRange,
    compile_sampling_plan,
    merge_change_events,
)

__all__ = [
    "AdaptiveScanConfig",
    "AdaptiveVideoScanner",
    "ChangeEvent",
    "FrameObservation",
    "MAX_FIXED_SAMPLES",
    "ScanResult",
    "SamplingMode",
    "SamplingOverride",
    "SamplingPlan",
    "SamplingSegment",
    "SamplingSpec",
    "StableStateDetector",
    "TimeRange",
    "VideoProbe",
    "compile_sampling_plan",
    "merge_change_events",
    "timestamped_observations",
]
