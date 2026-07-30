"""Validated visual sampling plans compiled onto the canonical media timeline."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .adaptive_sampler import ChangeEvent

MAX_FIXED_SAMPLES = 5_000


class SamplingModel(BaseModel):
    """Reject unknown fields and assignment-time violations in persisted plans."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class TimeRange(SamplingModel):
    start_us: int = Field(ge=0, strict=True)
    end_us: int = Field(gt=0, strict=True)

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.end_us <= self.start_us:
            raise ValueError("time range end_us must be greater than start_us")
        return self


class SamplingMode(StrEnum):
    ADAPTIVE = "adaptive"
    FIXED_INTERVAL = "fixed_interval"
    SKIP = "skip"


class SamplingSpec(SamplingModel):
    mode: SamplingMode = SamplingMode.ADAPTIVE
    interval_us: int | None = Field(default=None, ge=100_000, strict=True)

    @model_validator(mode="after")
    def validate_mode_fields(self) -> Self:
        if self.mode is SamplingMode.FIXED_INTERVAL:
            if self.interval_us is None:
                raise ValueError("fixed_interval sampling requires interval_us")
        elif self.interval_us is not None:
            raise ValueError(f"{self.mode.value} sampling does not accept interval_us")
        return self


class SamplingOverride(SamplingModel):
    range: TimeRange
    sampling: SamplingSpec


class SamplingSegment(SamplingModel):
    start_us: int = Field(ge=0, strict=True)
    end_us: int = Field(gt=0, strict=True)
    sampling: SamplingSpec
    source: Literal["default", "override"]
    override_index: int | None = Field(default=None, ge=0, strict=True)
    estimated_sample_count: int = Field(default=0, ge=0, strict=True)

    @model_validator(mode="after")
    def validate_segment(self) -> Self:
        if self.end_us <= self.start_us:
            raise ValueError("sampling segment end_us must be greater than start_us")
        if self.source == "default" and self.override_index is not None:
            raise ValueError("default sampling segment cannot have override_index")
        if self.source == "override" and self.override_index is None:
            raise ValueError("override sampling segment requires override_index")
        if (
            self.sampling.mode is not SamplingMode.FIXED_INTERVAL
            and self.estimated_sample_count != 0
        ):
            raise ValueError("only fixed_interval segments estimate frame samples")
        return self


class SamplingPlan(SamplingModel):
    default: SamplingSpec = Field(default_factory=SamplingSpec)
    overrides: list[SamplingOverride] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_override_overlap(self) -> Self:
        ordered = sorted(
            enumerate(self.overrides),
            key=lambda item: (item[1].range.start_us, item[1].range.end_us, item[0]),
        )
        for (_, previous), (_, current) in zip(ordered, ordered[1:], strict=False):
            if current.range.start_us < previous.range.end_us:
                raise ValueError("sampling overrides cannot overlap")
        return self

    def compile(
        self,
        duration_us: int,
        *,
        max_fixed_samples: int = MAX_FIXED_SAMPLES,
    ) -> list[SamplingSegment]:
        return compile_sampling_plan(
            self,
            duration_us=duration_us,
            max_fixed_samples=max_fixed_samples,
        )


def compile_sampling_plan(
    plan: SamplingPlan,
    *,
    duration_us: int,
    max_fixed_samples: int = MAX_FIXED_SAMPLES,
) -> list[SamplingSegment]:
    """Split one plan into ordered, gap-free, non-overlapping execution segments."""

    if isinstance(duration_us, bool) or not isinstance(duration_us, int):
        raise TypeError("duration_us must be an integer")
    if duration_us <= 0:
        raise ValueError("duration_us must be positive")
    if isinstance(max_fixed_samples, bool) or not isinstance(max_fixed_samples, int):
        raise TypeError("max_fixed_samples must be an integer")
    if max_fixed_samples < 1:
        raise ValueError("max_fixed_samples must be positive")

    indexed_overrides = sorted(
        enumerate(plan.overrides),
        key=lambda item: (item[1].range.start_us, item[1].range.end_us, item[0]),
    )
    for _, override in indexed_overrides:
        if override.range.end_us > duration_us:
            raise ValueError(
                "sampling override ends after media duration "
                f"({override.range.end_us} > {duration_us})"
            )

    segments: list[SamplingSegment] = []
    cursor = 0
    for original_index, override in indexed_overrides:
        if cursor < override.range.start_us:
            segments.append(
                _segment(
                    start_us=cursor,
                    end_us=override.range.start_us,
                    sampling=plan.default,
                    source="default",
                )
            )
        segments.append(
            _segment(
                start_us=override.range.start_us,
                end_us=override.range.end_us,
                sampling=override.sampling,
                source="override",
                override_index=original_index,
            )
        )
        cursor = override.range.end_us

    if cursor < duration_us:
        segments.append(
            _segment(
                start_us=cursor,
                end_us=duration_us,
                sampling=plan.default,
                source="default",
            )
        )

    fixed_sample_count = sum(item.estimated_sample_count for item in segments)
    if fixed_sample_count > max_fixed_samples:
        raise ValueError(
            "fixed_interval sampling would request "
            f"{fixed_sample_count} frames; maximum is {max_fixed_samples}"
        )
    return segments


def merge_change_events(events: Iterable[ChangeEvent]) -> list[ChangeEvent]:
    """Deduplicate visual observations by exact source-frame identity and sort by PTS."""

    selected: dict[tuple[object, ...], ChangeEvent] = {}
    for event in events:
        key = _keyframe_identity(event)
        current = selected.get(key)
        if current is None or _event_priority(event) > _event_priority(current):
            selected[key] = event

    ordered = sorted(
        selected.values(),
        key=lambda item: (
            item.keyframe_us,
            item.transition_us,
            item.keyframe.pts if item.keyframe.pts is not None else -1,
        ),
    )
    normalized: list[ChangeEvent] = []
    previous = None
    for event in ordered:
        normalized.append(replace(event, previous_keyframe=previous))
        previous = event.keyframe
    return normalized


def _segment(
    *,
    start_us: int,
    end_us: int,
    sampling: SamplingSpec,
    source: Literal["default", "override"],
    override_index: int | None = None,
) -> SamplingSegment:
    count = (
        _fixed_sample_count(start_us, end_us, sampling.interval_us)
        if sampling.mode is SamplingMode.FIXED_INTERVAL
        else 0
    )
    return SamplingSegment(
        start_us=start_us,
        end_us=end_us,
        sampling=sampling,
        source=source,
        override_index=override_index,
        estimated_sample_count=count,
    )


def _fixed_sample_count(start_us: int, end_us: int, interval_us: int | None) -> int:
    if interval_us is None:
        raise ValueError("fixed_interval sampling requires interval_us")
    duration_us = end_us - start_us
    return ((duration_us - 1) // interval_us) + 1


def _keyframe_identity(event: ChangeEvent) -> tuple[object, ...]:
    timestamp = event.keyframe
    if timestamp.pts is None:
        return ("canonical_us", timestamp.time_us)
    return (
        "pts",
        timestamp.pts,
        timestamp.time_base.numerator,
        timestamp.time_base.denominator,
    )


def _event_priority(event: ChangeEvent) -> tuple[int, int, float]:
    return (
        int(event.sampling_mode == "adaptive"),
        int(event.refined),
        event.state_score,
    )
