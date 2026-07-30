"""Content-adaptive discovery of stable visual states in a video.

This module deliberately does not perform OCR.  Its job is to find *when* the
visual state changed and to choose a stable, sharp frame for the expensive OCR
stage.  A cheap coarse pass covers the whole video.  Only candidate transition
windows are decoded again at a higher frame rate.

PyAV performs PTS-preserving FFmpeg decoding and Pillow supplies deterministic
image metrics. Production profiles can replace the metric backend with
OpenCV/CUDA or a learned detector without changing the event contract.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Literal

from PIL import Image, ImageChops, ImageFilter, ImageStat

from video2notes.domain import MediaTimestamp, Rational
from video2notes.media import (
    decode_video_frame_at,
    iter_video_frames,
    probe_media,
)

ChangeReason = Literal[
    "initial",
    "hard_cut",
    "text_or_ui_change",
    "state_change",
    "fixed_interval",
]


@dataclass(frozen=True, slots=True)
class AdaptiveScanConfig:
    """Thresholds and sampling rates for adaptive visual scanning.

    Thresholds are normalized to ``0..1``.  The defaults are conservative
    starting points, not universal truth.  They must eventually be calibrated
    against the repository's labelled evaluation corpus.
    """

    coarse_fps: float = 3.0
    fine_fps: float = 12.0
    analysis_width: int = 640
    analysis_height: int = 360

    hard_cut_threshold: float = 0.135
    state_change_threshold: float = 0.030
    text_change_threshold: float = 0.024
    stable_step_threshold: float = 0.027

    min_persistence_ms: int = 500
    settle_ms: int = 500
    cooldown_ms: int = 500
    max_transition_ms: int = 2_500
    refine_padding_ms: int = 1_250

    tile_columns: int = 8
    tile_rows: int = 5
    top_tile_fraction: float = 0.10

    def validate(self) -> None:
        if self.coarse_fps <= 0 or self.fine_fps <= 0:
            raise ValueError("sampling rates must be positive")
        if self.fine_fps < self.coarse_fps:
            raise ValueError("fine_fps must be greater than or equal to coarse_fps")
        if self.analysis_width < 64 or self.analysis_height < 64:
            raise ValueError("analysis dimensions are too small")
        if self.analysis_width % 2 or self.analysis_height % 2:
            raise ValueError("analysis dimensions must be even for FFmpeg")
        for name in (
            "hard_cut_threshold",
            "state_change_threshold",
            "text_change_threshold",
            "stable_step_threshold",
            "top_tile_fraction",
        ):
            value = getattr(self, name)
            if not 0 < value <= 1:
                raise ValueError(f"{name} must be in the range (0, 1]")
        if self.tile_columns < 1 or self.tile_rows < 1:
            raise ValueError("tile grid dimensions must be positive")


@dataclass(frozen=True, slots=True)
class FrameMetrics:
    """Normalized difference between two analysis frames."""

    luma_delta: float
    edge_delta: float
    histogram_delta: float
    sparse_edge_delta: float
    perceptual_hash_delta: float

    @property
    def scene_score(self) -> float:
        return _clamp01(
            0.48 * self.luma_delta
            + 0.22 * self.histogram_delta
            + 0.18 * self.edge_delta
            + 0.12 * self.perceptual_hash_delta
        )

    @property
    def text_score(self) -> float:
        # Sparse edge changes make a newly revealed bullet or a small UI label
        # visible even when the global histogram barely moves.
        return _clamp01(
            0.56 * self.sparse_edge_delta
            + 0.24 * self.edge_delta
            + 0.12 * self.luma_delta
            + 0.08 * self.perceptual_hash_delta
        )

    @property
    def state_score(self) -> float:
        return max(self.scene_score, self.text_score)


@dataclass(frozen=True, slots=True)
class FrameObservation:
    timestamp: MediaTimestamp
    image: Image.Image = field(repr=False, compare=False)
    sharpness: float

    @property
    def timestamp_us(self) -> int:
        return self.timestamp.time_us

    @property
    def timestamp_ms(self) -> int:
        """Rounded millisecond view retained for callers of the research API."""

        return round(self.timestamp_us / 1000)


@dataclass(frozen=True, slots=True)
class ChangeEvent:
    """A persistent visual state boundary and its representative keyframe."""

    transition: MediaTimestamp
    keyframe: MediaTimestamp
    previous_keyframe: MediaTimestamp | None
    reason: ChangeReason
    state_score: float
    scene_score: float
    text_score: float
    step_score: float
    refined: bool
    preview_path: str | None = None
    sampling_mode: Literal["adaptive", "fixed_interval"] = "adaptive"
    requested_time_us: int | None = None
    requested_interval_us: int | None = None
    segment_start_us: int | None = None
    segment_end_us: int | None = None

    @property
    def transition_us(self) -> int:
        return self.transition.time_us

    @property
    def keyframe_us(self) -> int:
        return self.keyframe.time_us

    @property
    def previous_keyframe_us(self) -> int | None:
        return self.previous_keyframe.time_us if self.previous_keyframe is not None else None

    @property
    def transition_ms(self) -> int:
        """Rounded millisecond view retained for compatibility."""

        return round(self.transition_us / 1000)

    @property
    def keyframe_ms(self) -> int:
        """Rounded millisecond view retained for compatibility."""

        return round(self.keyframe_us / 1000)

    @property
    def previous_keyframe_ms(self) -> int | None:
        """Rounded millisecond view retained for compatibility."""

        value = self.previous_keyframe_us
        return round(value / 1000) if value is not None else None

    def to_dict(self) -> dict[str, object]:
        return {
            "transition_us": self.transition_us,
            "transition_source_time_us": self.transition.source_time_us,
            "transition_pts": self.transition.pts,
            "transition_time_base": self.transition.time_base.model_dump(mode="json"),
            "keyframe_us": self.keyframe_us,
            "keyframe_source_time_us": self.keyframe.source_time_us,
            "keyframe_pts": self.keyframe.pts,
            "keyframe_time_base": self.keyframe.time_base.model_dump(mode="json"),
            "previous_keyframe_us": self.previous_keyframe_us,
            "previous_keyframe_source_time_us": (
                self.previous_keyframe.source_time_us
                if self.previous_keyframe is not None
                else None
            ),
            "previous_keyframe_pts": (
                self.previous_keyframe.pts if self.previous_keyframe is not None else None
            ),
            "previous_keyframe_time_base": (
                self.previous_keyframe.time_base.model_dump(mode="json")
                if self.previous_keyframe is not None
                else None
            ),
            "reason": self.reason,
            "state_score": self.state_score,
            "scene_score": self.scene_score,
            "text_score": self.text_score,
            "step_score": self.step_score,
            "refined": self.refined,
            "preview_path": self.preview_path,
            "sampling_mode": self.sampling_mode,
            "requested_time_us": self.requested_time_us,
            "requested_interval_us": self.requested_interval_us,
            "segment_start_us": self.segment_start_us,
            "segment_end_us": self.segment_end_us,
        }


@dataclass(frozen=True, slots=True)
class VideoProbe:
    duration_us: int
    width: int
    height: int
    frame_rate: float | None
    timeline_origin_us: int
    stream_index: int
    stream_time_base: Rational

    @property
    def duration_ms(self) -> int:
        return round(self.duration_us / 1000)

    def to_dict(self) -> dict[str, object]:
        return {
            "duration_us": self.duration_us,
            "width": self.width,
            "height": self.height,
            "frame_rate": self.frame_rate,
            "timeline_origin_us": self.timeline_origin_us,
            "stream_index": self.stream_index,
            "stream_time_base": self.stream_time_base.model_dump(mode="json"),
        }


@dataclass(frozen=True, slots=True)
class ScanResult:
    source: str
    probe: VideoProbe
    config: AdaptiveScanConfig
    events: tuple[ChangeEvent, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "source": self.source,
            "probe": self.probe.to_dict(),
            "config": asdict(self.config),
            "events": [event.to_dict() for event in self.events],
        }

    def write_json(self, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return output


@dataclass(slots=True)
class _PendingTransition:
    trigger: MediaTimestamp
    reason: ChangeReason
    observations: list[tuple[FrameObservation, FrameMetrics, FrameMetrics]]
    stable_since_us: int | None = None


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


def _normalized_mean(image: Image.Image) -> float:
    return ImageStat.Stat(image).mean[0] / 255.0


def _edge_map(gray: Image.Image) -> Image.Image:
    edge = gray.filter(ImageFilter.FIND_EDGES)
    # FIND_EDGES produces a bright one-pixel border.  Clear it so that it does
    # not dominate small tiles.
    if edge.width > 2 and edge.height > 2:
        clean = Image.new("L", edge.size, 0)
        clean.paste(edge.crop((1, 1, edge.width - 1, edge.height - 1)), (1, 1))
        return clean
    return edge


def _dhash(gray: Image.Image) -> int:
    small = gray.resize((9, 8), Image.Resampling.BILINEAR)
    # ``getdata`` is deprecated in Pillow 13; an L-mode image's byte string is
    # already the flat 0..255 pixel sequence needed here.
    pixels = small.tobytes()
    value = 0
    bit = 0
    for row in range(8):
        offset = row * 9
        for column in range(8):
            if pixels[offset + column] > pixels[offset + column + 1]:
                value |= 1 << bit
            bit += 1
    return value


def _histogram_delta(left: Image.Image, right: Image.Image) -> float:
    left_hist = left.histogram()
    right_hist = right.histogram()
    pixel_count = max(1, left.width * left.height)
    l1 = sum(abs(a - b) for a, b in zip(left_hist, right_hist, strict=True))
    return _clamp01(l1 / (2.0 * pixel_count))


def _sparse_edge_delta(
    left_edge: Image.Image,
    right_edge: Image.Image,
    *,
    columns: int,
    rows: int,
    top_fraction: float,
) -> float:
    width, height = left_edge.size
    tile_scores: list[float] = []
    for row in range(rows):
        top = row * height // rows
        bottom = (row + 1) * height // rows
        for column in range(columns):
            left = column * width // columns
            right = (column + 1) * width // columns
            box = (left, top, right, bottom)
            delta = ImageChops.difference(
                left_edge.crop(box),
                right_edge.crop(box),
            )
            tile_scores.append(_normalized_mean(delta))

    if not tile_scores:
        return 0.0
    keep = max(1, math.ceil(len(tile_scores) * top_fraction))
    strongest = sorted(tile_scores, reverse=True)[:keep]
    return _clamp01(sum(strongest) / len(strongest))


def compare_frames(
    left: Image.Image,
    right: Image.Image,
    config: AdaptiveScanConfig,
) -> FrameMetrics:
    """Calculate global and text-sensitive difference metrics."""

    if left.size != right.size:
        raise ValueError("frames must have identical dimensions")

    left_gray = left if left.mode == "L" else left.convert("L")
    right_gray = right if right.mode == "L" else right.convert("L")
    luma = _normalized_mean(ImageChops.difference(left_gray, right_gray))
    left_edge = _edge_map(left_gray)
    right_edge = _edge_map(right_gray)
    edge = _normalized_mean(ImageChops.difference(left_edge, right_edge))
    sparse = _sparse_edge_delta(
        left_edge,
        right_edge,
        columns=config.tile_columns,
        rows=config.tile_rows,
        top_fraction=config.top_tile_fraction,
    )
    hash_delta = (_dhash(left_gray) ^ _dhash(right_gray)).bit_count() / 64.0
    return FrameMetrics(
        luma_delta=luma,
        edge_delta=edge,
        histogram_delta=_histogram_delta(left_gray, right_gray),
        sparse_edge_delta=sparse,
        perceptual_hash_delta=hash_delta,
    )


def frame_sharpness(image: Image.Image) -> float:
    gray = image if image.mode == "L" else image.convert("L")
    return ImageStat.Stat(_edge_map(gray)).var[0]


class StableStateDetector:
    """Turn a stream of sampled frames into persistent visual-state events."""

    def __init__(self, config: AdaptiveScanConfig):
        config.validate()
        self.config = config

    def detect(self, observations: Iterable[FrameObservation]) -> list[ChangeEvent]:
        iterator = iter(observations)
        first = next(iterator, None)
        if first is None:
            return []

        events = [
            ChangeEvent(
                transition=first.timestamp,
                keyframe=first.timestamp,
                previous_keyframe=None,
                reason="initial",
                state_score=0.0,
                scene_score=0.0,
                text_score=0.0,
                step_score=0.0,
                refined=False,
            )
        ]
        reference = first
        previous = first
        pending: _PendingTransition | None = None
        last_emitted_us = first.timestamp_us

        for current in iterator:
            if current.timestamp_us <= previous.timestamp_us:
                raise ValueError("frame observations must have strictly increasing PTS")
            step_metrics = compare_frames(previous.image, current.image, self.config)
            reference_metrics = compare_frames(reference.image, current.image, self.config)

            if pending is None:
                reason = self._classify_candidate(
                    reference_metrics,
                    step_metrics,
                    current.timestamp_us - last_emitted_us,
                )
                if reason is not None:
                    pending = _PendingTransition(
                        trigger=current.timestamp,
                        reason=reason,
                        observations=[],
                    )

            if pending is not None:
                pending.observations.append((current, reference_metrics, step_metrics))
                if step_metrics.state_score <= self.config.stable_step_threshold:
                    if pending.stable_since_us is None:
                        pending.stable_since_us = current.timestamp_us
                else:
                    pending.stable_since_us = None

                elapsed_us = current.timestamp_us - pending.trigger.time_us
                settled_for = (
                    current.timestamp_us - pending.stable_since_us
                    if pending.stable_since_us is not None
                    else 0
                )
                should_resolve = (
                    elapsed_us >= self.config.min_persistence_ms * 1000
                    and settled_for >= self.config.settle_ms * 1000
                )
                timed_out = elapsed_us >= self.config.max_transition_ms * 1000

                if should_resolve or timed_out:
                    resolved = self._resolve_pending(
                        pending,
                        reference,
                        events[-1].keyframe,
                    )
                    if resolved is not None:
                        event, reference = resolved
                        events.append(event)
                        last_emitted_us = event.keyframe_us
                    pending = None

            previous = current

        # A transition at the end of a short clip may not have a full settle
        # window.  Resolve it only if the last samples all support a new state.
        if pending is not None:
            resolved = self._resolve_pending(
                pending,
                reference,
                events[-1].keyframe,
            )
            if resolved is not None:
                event, _ = resolved
                events.append(event)

        return events

    def _classify_candidate(
        self,
        reference: FrameMetrics,
        step: FrameMetrics,
        since_last_event_us: int,
    ) -> ChangeReason | None:
        if since_last_event_us < self.config.cooldown_ms * 1000:
            return None
        if step.scene_score >= self.config.hard_cut_threshold:
            return "hard_cut"
        if (
            reference.text_score >= self.config.text_change_threshold
            and reference.state_score >= self.config.state_change_threshold
        ):
            return "text_or_ui_change"
        if reference.state_score >= self.config.state_change_threshold:
            return "state_change"
        return None

    def _resolve_pending(
        self,
        pending: _PendingTransition,
        reference: FrameObservation,
        previous_keyframe: MediaTimestamp,
    ) -> tuple[ChangeEvent, FrameObservation] | None:
        if not pending.observations:
            return None

        # Only the consecutive qualifying suffix represents the current state.
        # Its actual PTS span, not a frame count derived from nominal FPS,
        # determines whether the state persisted long enough.
        qualifying_tail: list[tuple[FrameObservation, FrameMetrics, FrameMetrics]] = []
        for item in reversed(pending.observations):
            if not self._is_changed_state(item[1]):
                break
            qualifying_tail.append(item)
        qualifying_tail.reverse()
        if len(qualifying_tail) < 2:
            return None
        persistence_us = qualifying_tail[-1][0].timestamp_us - qualifying_tail[0][0].timestamp_us
        if persistence_us < self.config.min_persistence_ms * 1000:
            return None

        stable_tail: list[tuple[FrameObservation, FrameMetrics, FrameMetrics]] = []
        for item in reversed(qualifying_tail):
            if item[2].state_score > self.config.stable_step_threshold:
                break
            stable_tail.append(item)
        stable_tail.reverse()
        if len(stable_tail) < 2:
            return None
        stable_us = stable_tail[-1][0].timestamp_us - stable_tail[0][0].timestamp_us
        if stable_us < self.config.settle_ms * 1000:
            return None

        # Prefer the sharpest stable image, but only within the persistent tail
        # so a transient early frame cannot win.
        selected = max(stable_tail, key=lambda item: item[0].sharpness)
        observation, state_metrics, step_metrics = selected
        verified = compare_frames(reference.image, observation.image, self.config)
        if (
            verified.state_score < self.config.state_change_threshold
            and verified.text_score < self.config.text_change_threshold
        ):
            return None

        reason = pending.reason
        # The selected frame is stable, so its own step score will usually be
        # low. Preserve hard_cut only when the transition window actually
        # contained a cut-sized step.
        if (
            reason == "hard_cut"
            and step_metrics.scene_score < self.config.hard_cut_threshold
            and not any(
                metrics.scene_score >= self.config.hard_cut_threshold
                for _, _, metrics in pending.observations
            )
        ):
            reason = "state_change"

        event = ChangeEvent(
            transition=pending.trigger,
            keyframe=observation.timestamp,
            previous_keyframe=previous_keyframe,
            reason=reason,
            state_score=state_metrics.state_score,
            scene_score=state_metrics.scene_score,
            text_score=state_metrics.text_score,
            step_score=max(item[2].state_score for item in pending.observations),
            refined=False,
        )
        return event, observation

    def _is_changed_state(self, metrics: FrameMetrics) -> bool:
        return (
            metrics.state_score >= self.config.state_change_threshold
            or metrics.text_score >= self.config.text_change_threshold
        )


class AdaptiveVideoScanner:
    """PyAV-backed two-pass visual-state scanner with source-frame PTS."""

    def __init__(
        self,
        config: AdaptiveScanConfig | None = None,
        *,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        cancel_check: Callable[[], None] | None = None,
    ):
        self.config = config or AdaptiveScanConfig()
        self.config.validate()
        # ffmpeg_path remains accepted for research-CLI compatibility. PyAV now
        # performs decoding; FFprobe remains the authoritative stream probe.
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.cancel_check = cancel_check

    def scan(
        self,
        source: str | Path,
        *,
        preview_dir: str | Path | None = None,
    ) -> ScanResult:
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"video does not exist: {source_path}")

        self._check_cancelled()
        probe = self.probe(source_path)
        return self._scan_probed(
            source_path,
            probe=probe,
            start_us=0,
            end_us=probe.duration_us,
            preview_dir=preview_dir,
        )

    def scan_range(
        self,
        source: str | Path,
        *,
        start_us: int,
        end_us: int,
        preview_dir: str | Path | None = None,
        probe: VideoProbe | None = None,
    ) -> ScanResult:
        """Adaptively scan one half-open canonical-time range."""

        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"video does not exist: {source_path}")
        if start_us < 0:
            raise ValueError("start_us cannot be negative")
        if end_us <= start_us:
            raise ValueError("end_us must be greater than start_us")

        self._check_cancelled()
        resolved_probe = probe or self.probe(source_path)
        if end_us > resolved_probe.duration_us:
            raise ValueError("scan range ends after media duration")
        return self._scan_probed(
            source_path,
            probe=resolved_probe,
            start_us=start_us,
            end_us=end_us,
            preview_dir=preview_dir,
        )

    def _scan_probed(
        self,
        source_path: Path,
        *,
        probe: VideoProbe,
        start_us: int,
        end_us: int,
        preview_dir: str | Path | None,
    ) -> ScanResult:
        coarse = StableStateDetector(self.config).detect(
            self._decode_frames(
                source_path,
                probe=probe,
                fps=self.config.coarse_fps,
                start_us=start_us,
                end_us=end_us,
            )
        )
        refined = self._refine_events(
            source_path,
            coarse,
            probe,
            start_us=start_us,
            end_us=end_us,
        )

        if preview_dir is not None:
            refined = self._write_previews(
                source_path,
                refined,
                Path(preview_dir).expanduser().resolve(),
                probe,
            )

        return ScanResult(
            source=str(source_path),
            probe=probe,
            config=self.config,
            events=tuple(refined),
        )

    def probe(self, source: str | Path) -> VideoProbe:
        media = probe_media(source, ffprobe_path=self.ffprobe_path)
        stream = media.video_stream
        if stream is None:
            raise ValueError("input does not contain a video stream")
        frame_rate = (
            float(stream.avg_frame_rate.fraction) if stream.avg_frame_rate is not None else None
        )
        return VideoProbe(
            duration_us=media.duration_us,
            width=stream.width or 0,
            height=stream.height or 0,
            frame_rate=frame_rate,
            timeline_origin_us=media.timeline_origin_us,
            stream_index=stream.index,
            stream_time_base=stream.time_base,
        )

    def _decode_frames(
        self,
        source: Path,
        *,
        probe: VideoProbe,
        fps: float,
        start_us: int = 0,
        end_us: int | None = None,
    ) -> Iterator[FrameObservation]:
        for decoded in iter_video_frames(
            source,
            timeline_origin_us=probe.timeline_origin_us,
            stream_index=probe.stream_index,
            sample_fps=fps,
            start_us=start_us,
            end_us=end_us,
            target_size=(
                self.config.analysis_width,
                self.config.analysis_height,
            ),
        ):
            self._check_cancelled()
            yield FrameObservation(
                timestamp=decoded.timestamp,
                image=decoded.image,
                sharpness=frame_sharpness(decoded.image),
            )

    def _frame_at(
        self,
        source: Path,
        timestamp: MediaTimestamp,
        probe: VideoProbe,
    ) -> FrameObservation:
        self._check_cancelled()
        decoded = decode_video_frame_at(
            source,
            timestamp=timestamp,
            timeline_origin_us=probe.timeline_origin_us,
            stream_index=probe.stream_index,
            target_size=(
                self.config.analysis_width,
                self.config.analysis_height,
            ),
        )
        return FrameObservation(
            timestamp=decoded.timestamp,
            image=decoded.image,
            sharpness=frame_sharpness(decoded.image),
        )

    def _refine_events(
        self,
        source: Path,
        coarse_events: list[ChangeEvent],
        probe: VideoProbe,
        *,
        start_us: int = 0,
        end_us: int | None = None,
    ) -> list[ChangeEvent]:
        if len(coarse_events) <= 1:
            return coarse_events

        refined = [coarse_events[0]]
        reference = self._frame_at(source, coarse_events[0].keyframe, probe)
        range_end_us = probe.duration_us if end_us is None else end_us
        for event in coarse_events[1:]:
            candidate = self._refine_event(
                source,
                event,
                reference,
                probe,
                start_us=start_us,
                end_us=range_end_us,
            )
            if candidate.keyframe_us <= refined[-1].keyframe_us:
                continue
            refined.append(candidate)
            reference = self._frame_at(source, candidate.keyframe, probe)
        return refined

    def _refine_event(
        self,
        source: Path,
        coarse: ChangeEvent,
        reference: FrameObservation,
        probe: VideoProbe,
        *,
        start_us: int = 0,
        end_us: int | None = None,
    ) -> ChangeEvent:
        range_end_us = probe.duration_us if end_us is None else end_us
        start_us = max(
            start_us,
            reference.timestamp_us,
            coarse.transition_us - self.config.refine_padding_ms * 1000,
        )
        end_us = min(
            probe.duration_us,
            range_end_us,
            coarse.keyframe_us + self.config.refine_padding_ms * 1000,
        )
        if end_us <= start_us:
            return coarse
        frames = list(
            self._decode_frames(
                source,
                probe=probe,
                fps=self.config.fine_fps,
                start_us=start_us,
                end_us=end_us,
            )
        )
        if len(frames) < 2:
            return coarse

        state_metrics = [
            compare_frames(reference.image, frame.image, self.config) for frame in frames
        ]
        step_metrics = [
            FrameMetrics(0, 0, 0, 0, 0),
            *[
                compare_frames(frames[index - 1].image, frames[index].image, self.config)
                for index in range(1, len(frames))
            ],
        ]

        crossing_index = self._find_persistent_crossing(
            frames,
            state_metrics,
            minimum_us=self.config.min_persistence_ms * 1000,
        )

        if crossing_index is None:
            return coarse

        stable_window: list[int] = []
        for index in range(crossing_index, len(frames)):
            if step_metrics[index].state_score <= self.config.stable_step_threshold:
                stable_window.append(index)
                if (
                    len(stable_window) >= 2
                    and frames[stable_window[-1]].timestamp_us
                    - frames[stable_window[0]].timestamp_us
                    >= self.config.settle_ms * 1000
                ):
                    break
            else:
                stable_window.clear()

        if (
            len(stable_window) < 2
            or frames[stable_window[-1]].timestamp_us - frames[stable_window[0]].timestamp_us
            < self.config.settle_ms * 1000
        ):
            return coarse

        selected_index = max(
            stable_window,
            key=lambda item: frames[item].sharpness,
        )
        selected_frame = frames[selected_index]
        selected_state = state_metrics[selected_index]
        transition_step_peak = max(
            metric.state_score
            for metric in step_metrics[crossing_index : min(len(step_metrics), selected_index + 1)]
        )
        reason: ChangeReason
        if transition_step_peak >= self.config.hard_cut_threshold:
            reason = "hard_cut"
        elif selected_state.text_score >= self.config.text_change_threshold:
            reason = "text_or_ui_change"
        else:
            reason = "state_change"

        return ChangeEvent(
            transition=frames[crossing_index].timestamp,
            keyframe=selected_frame.timestamp,
            previous_keyframe=reference.timestamp,
            reason=reason,
            state_score=selected_state.state_score,
            scene_score=selected_state.scene_score,
            text_score=selected_state.text_score,
            step_score=transition_step_peak,
            refined=True,
        )

    def _find_persistent_crossing(
        self,
        frames: list[FrameObservation],
        metrics: list[FrameMetrics],
        *,
        minimum_us: int,
    ) -> int | None:
        """Find the first changed-state run that spans ``minimum_us`` in PTS."""

        run_start: int | None = None
        for index, metric in enumerate(metrics):
            is_changed = (
                metric.state_score >= self.config.state_change_threshold
                or metric.text_score >= self.config.text_change_threshold
            )
            if not is_changed:
                run_start = None
                continue
            if run_start is None:
                run_start = index
                continue
            if frames[index].timestamp_us - frames[run_start].timestamp_us >= minimum_us:
                return run_start
        return None

    def _write_previews(
        self,
        source: Path,
        events: list[ChangeEvent],
        preview_dir: Path,
        probe: VideoProbe,
    ) -> list[ChangeEvent]:
        preview_dir.mkdir(parents=True, exist_ok=True)
        updated: list[ChangeEvent] = []
        for index, event in enumerate(events):
            self._check_cancelled()
            preview_path = preview_dir / (
                f"{index:04d}_{event.keyframe_us:015d}us_{event.reason}.jpg"
            )
            decoded = decode_video_frame_at(
                source,
                timestamp=event.keyframe,
                timeline_origin_us=probe.timeline_origin_us,
                stream_index=probe.stream_index,
                target_size=None,
            )
            decoded.image.save(
                preview_path,
                format="JPEG",
                quality=95,
                subsampling=0,
            )
            updated.append(replace(event, preview_path=str(preview_path)))
        return updated

    def _check_cancelled(self) -> None:
        if self.cancel_check is not None:
            self.cancel_check()


def synthetic_observations(
    images: Iterable[Image.Image],
    *,
    fps: float,
) -> Iterator[FrameObservation]:
    """Build observations for deterministic tests and calibration tools."""

    if fps <= 0:
        raise ValueError("fps must be positive")
    microsecond_time_base = Rational(numerator=1, denominator=1_000_000)
    for index, image in enumerate(images):
        timestamp_us = round(index * 1_000_000.0 / fps)
        yield FrameObservation(
            timestamp=MediaTimestamp.from_pts(
                timestamp_us,
                microsecond_time_base,
                timeline_origin_us=0,
            ),
            image=image.convert("RGB"),
            sharpness=frame_sharpness(image),
        )


def timestamped_observations(
    images: Iterable[Image.Image],
    *,
    timestamps_us: Iterable[int],
) -> Iterator[FrameObservation]:
    """Build observations at explicit, potentially non-uniform timestamps."""

    microsecond_time_base = Rational(numerator=1, denominator=1_000_000)
    for image, timestamp_us in zip(images, timestamps_us, strict=True):
        timestamp = MediaTimestamp.from_pts(
            timestamp_us,
            microsecond_time_base,
            timeline_origin_us=0,
        )
        yield FrameObservation(
            timestamp=timestamp,
            image=image.convert("RGB"),
            sharpness=frame_sharpness(image),
        )
