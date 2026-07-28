"""Content-adaptive discovery of stable visual states in a video.

This module deliberately does not perform OCR.  Its job is to find *when* the
visual state changed and to choose a stable, sharp frame for the expensive OCR
stage.  A cheap coarse pass covers the whole video.  Only candidate transition
windows are decoded again at a higher frame rate.

The implementation is dependency-light on purpose: FFmpeg performs decoding
and Pillow supplies deterministic image metrics.  Production profiles can
replace the metric backend with OpenCV/CUDA or a learned detector without
changing the event contract.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import median
from typing import BinaryIO, Literal

from PIL import Image, ImageChops, ImageFilter, ImageStat


ChangeReason = Literal["initial", "hard_cut", "text_or_ui_change", "state_change"]


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
    timestamp_ms: int
    image: Image.Image = field(repr=False, compare=False)
    sharpness: float


@dataclass(frozen=True, slots=True)
class ChangeEvent:
    """A persistent visual state boundary and its representative keyframe."""

    transition_ms: int
    keyframe_ms: int
    previous_keyframe_ms: int | None
    reason: ChangeReason
    state_score: float
    scene_score: float
    text_score: float
    step_score: float
    refined: bool
    preview_path: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class VideoProbe:
    duration_ms: int
    width: int
    height: int
    frame_rate: float | None


@dataclass(frozen=True, slots=True)
class ScanResult:
    source: str
    probe: VideoProbe
    config: AdaptiveScanConfig
    events: tuple[ChangeEvent, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "source": self.source,
            "probe": asdict(self.probe),
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
    trigger_ms: int
    reason: ChangeReason
    observations: list[tuple[FrameObservation, FrameMetrics, FrameMetrics]]
    stable_since_ms: int | None = None


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
                transition_ms=first.timestamp_ms,
                keyframe_ms=first.timestamp_ms,
                previous_keyframe_ms=None,
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
        last_emitted_ms = first.timestamp_ms

        for current in iterator:
            step_metrics = compare_frames(previous.image, current.image, self.config)
            reference_metrics = compare_frames(reference.image, current.image, self.config)

            if pending is None:
                reason = self._classify_candidate(
                    reference_metrics,
                    step_metrics,
                    current.timestamp_ms - last_emitted_ms,
                )
                if reason is not None:
                    pending = _PendingTransition(
                        trigger_ms=current.timestamp_ms,
                        reason=reason,
                        observations=[],
                    )

            if pending is not None:
                pending.observations.append((current, reference_metrics, step_metrics))
                if step_metrics.state_score <= self.config.stable_step_threshold:
                    if pending.stable_since_ms is None:
                        pending.stable_since_ms = current.timestamp_ms
                else:
                    pending.stable_since_ms = None

                elapsed = current.timestamp_ms - pending.trigger_ms
                settled_for = (
                    current.timestamp_ms - pending.stable_since_ms
                    if pending.stable_since_ms is not None
                    else 0
                )
                should_resolve = (
                    elapsed >= self.config.min_persistence_ms
                    and settled_for >= self.config.settle_ms
                )
                timed_out = elapsed >= self.config.max_transition_ms

                if should_resolve or timed_out:
                    resolved = self._resolve_pending(
                        pending,
                        reference,
                        events[-1].keyframe_ms,
                        timed_out=timed_out,
                    )
                    if resolved is not None:
                        event, reference = resolved
                        events.append(event)
                        last_emitted_ms = event.keyframe_ms
                    pending = None

            previous = current

        # A transition at the end of a short clip may not have a full settle
        # window.  Resolve it only if the last samples all support a new state.
        if pending is not None:
            resolved = self._resolve_pending(
                pending,
                reference,
                events[-1].keyframe_ms,
                timed_out=True,
            )
            if resolved is not None:
                event, _ = resolved
                events.append(event)

        return events

    def _classify_candidate(
        self,
        reference: FrameMetrics,
        step: FrameMetrics,
        since_last_event_ms: int,
    ) -> ChangeReason | None:
        if since_last_event_ms < self.config.cooldown_ms:
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
        previous_keyframe_ms: int,
        *,
        timed_out: bool,
    ) -> tuple[ChangeEvent, FrameObservation] | None:
        if not pending.observations:
            return None

        persistence_count = max(
            2,
            math.ceil(
                self.config.min_persistence_ms
                * self.config.coarse_fps
                / 1000.0
            ),
        )
        tail = pending.observations[-persistence_count:]
        qualifying = [
            item
            for item in tail
            if (
                item[1].state_score >= self.config.state_change_threshold
                or item[1].text_score >= self.config.text_change_threshold
            )
        ]
        required = persistence_count if not timed_out else max(2, persistence_count - 1)
        if len(qualifying) < required:
            return None

        stable_items = [
            item
            for item in tail
            if item[2].state_score <= self.config.stable_step_threshold
        ]
        if not stable_items:
            return None

        # Prefer the sharpest stable image, but only within the persistent tail
        # so a transient early frame cannot win.
        selected = max(stable_items, key=lambda item: item[0].sharpness)
        observation, state_metrics, step_metrics = selected
        verified = compare_frames(reference.image, observation.image, self.config)
        if (
            verified.state_score < self.config.state_change_threshold
            and verified.text_score < self.config.text_change_threshold
        ):
            return None

        reason = pending.reason
        if reason == "hard_cut" and step_metrics.scene_score < self.config.hard_cut_threshold:
            # The selected frame is stable, so its own step score will usually
            # be low.  Preserve hard_cut only when the transition window
            # actually contained a cut-sized step.
            if not any(
                metrics.scene_score >= self.config.hard_cut_threshold
                for _, _, metrics in pending.observations
            ):
                reason = "state_change"

        event = ChangeEvent(
            transition_ms=pending.trigger_ms,
            keyframe_ms=observation.timestamp_ms,
            previous_keyframe_ms=previous_keyframe_ms,
            reason=reason,
            state_score=state_metrics.state_score,
            scene_score=state_metrics.scene_score,
            text_score=state_metrics.text_score,
            step_score=max(
                item[2].state_score for item in pending.observations
            ),
            refined=False,
        )
        return event, observation


class AdaptiveVideoScanner:
    """FFmpeg-backed two-pass visual-state scanner."""

    def __init__(
        self,
        config: AdaptiveScanConfig | None = None,
        *,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
    ):
        self.config = config or AdaptiveScanConfig()
        self.config.validate()
        self.ffmpeg_path = _resolve_executable(ffmpeg_path)
        self.ffprobe_path = _resolve_executable(ffprobe_path)

    def scan(
        self,
        source: str | Path,
        *,
        preview_dir: str | Path | None = None,
    ) -> ScanResult:
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"video does not exist: {source_path}")

        probe = self.probe(source_path)
        coarse = StableStateDetector(self.config).detect(
            self._decode_frames(source_path, fps=self.config.coarse_fps)
        )
        refined = self._refine_events(source_path, coarse)

        if preview_dir is not None:
            refined = self._write_previews(
                source_path,
                refined,
                Path(preview_dir).expanduser().resolve(),
            )

        return ScanResult(
            source=str(source_path),
            probe=probe,
            config=self.config,
            events=tuple(refined),
        )

    def probe(self, source: str | Path) -> VideoProbe:
        command = [
            self.ffprobe_path,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,avg_frame_rate:format=duration",
            "-of",
            "json",
            str(source),
        ]
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        payload = json.loads(completed.stdout)
        streams = payload.get("streams") or []
        if not streams:
            raise ValueError("input does not contain a video stream")
        stream = streams[0]
        duration = float((payload.get("format") or {}).get("duration") or 0.0)
        return VideoProbe(
            duration_ms=max(0, round(duration * 1000)),
            width=int(stream.get("width") or 0),
            height=int(stream.get("height") or 0),
            frame_rate=_parse_frame_rate(stream.get("avg_frame_rate")),
        )

    def _decode_frames(
        self,
        source: Path,
        *,
        fps: float,
        start_ms: int = 0,
        end_ms: int | None = None,
    ) -> Iterator[FrameObservation]:
        command = [self.ffmpeg_path, "-hide_banner", "-loglevel", "error"]
        if start_ms > 0:
            command.extend(["-ss", f"{start_ms / 1000.0:.6f}"])
        command.extend(["-i", str(source)])
        if end_ms is not None:
            duration_ms = max(1, end_ms - start_ms)
            command.extend(["-t", f"{duration_ms / 1000.0:.6f}"])

        filter_graph = (
            f"fps={fps},"
            f"scale={self.config.analysis_width}:{self.config.analysis_height}:"
            "force_original_aspect_ratio=decrease,"
            f"pad={self.config.analysis_width}:{self.config.analysis_height}:"
            "(ow-iw)/2:(oh-ih)/2"
        )
        command.extend(
            [
                "-map",
                "0:v:0",
                "-an",
                "-sn",
                "-dn",
                "-vf",
                filter_graph,
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "pipe:1",
            ]
        )

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if process.stdout is None or process.stderr is None:
            process.kill()
            raise RuntimeError("failed to open FFmpeg pipes")

        frame_bytes = (
            self.config.analysis_width
            * self.config.analysis_height
            * 3
        )
        index = 0
        try:
            while True:
                raw = _read_exact(process.stdout, frame_bytes)
                if not raw:
                    break
                if len(raw) != frame_bytes:
                    raise RuntimeError("FFmpeg returned a truncated video frame")
                image = Image.frombytes(
                    "RGB",
                    (self.config.analysis_width, self.config.analysis_height),
                    raw,
                )
                timestamp_ms = start_ms + round(index * 1000.0 / fps)
                yield FrameObservation(
                    timestamp_ms=timestamp_ms,
                    image=image,
                    sharpness=frame_sharpness(image),
                )
                index += 1
        finally:
            process.stdout.close()
            stderr = process.stderr.read().decode("utf-8", errors="replace")
            process.stderr.close()
            return_code = process.wait()
            if return_code and not _generator_is_closing():
                raise RuntimeError(f"FFmpeg frame decode failed: {stderr.strip()}")

    def _frame_at(self, source: Path, timestamp_ms: int) -> FrameObservation:
        # Decode a tiny window because a raw one-frame seek can land before the
        # requested presentation timestamp with long-GOP media.
        end_ms = timestamp_ms + max(250, round(1000 / self.config.fine_fps))
        frames = list(
            self._decode_frames(
                source,
                fps=self.config.fine_fps,
                start_ms=max(0, timestamp_ms),
                end_ms=end_ms,
            )
        )
        if not frames:
            raise RuntimeError(f"could not decode frame at {timestamp_ms} ms")
        return frames[0]

    def _refine_events(
        self,
        source: Path,
        coarse_events: list[ChangeEvent],
    ) -> list[ChangeEvent]:
        if len(coarse_events) <= 1:
            return coarse_events

        refined = [coarse_events[0]]
        reference = self._frame_at(source, coarse_events[0].keyframe_ms)
        for event in coarse_events[1:]:
            candidate = self._refine_event(source, event, reference)
            if candidate.keyframe_ms <= refined[-1].keyframe_ms:
                continue
            refined.append(candidate)
            reference = self._frame_at(source, candidate.keyframe_ms)
        return refined

    def _refine_event(
        self,
        source: Path,
        coarse: ChangeEvent,
        reference: FrameObservation,
    ) -> ChangeEvent:
        start_ms = max(
            reference.timestamp_ms,
            coarse.transition_ms - self.config.refine_padding_ms,
        )
        end_ms = coarse.keyframe_ms + self.config.refine_padding_ms
        frames = list(
            self._decode_frames(
                source,
                fps=self.config.fine_fps,
                start_ms=start_ms,
                end_ms=end_ms,
            )
        )
        if len(frames) < 2:
            return coarse

        state_metrics = [
            compare_frames(reference.image, frame.image, self.config)
            for frame in frames
        ]
        step_metrics = [
            FrameMetrics(0, 0, 0, 0, 0),
            *[
                compare_frames(frames[index - 1].image, frames[index].image, self.config)
                for index in range(1, len(frames))
            ],
        ]

        persistence_frames = max(
            2,
            math.ceil(
                self.config.min_persistence_ms * self.config.fine_fps / 1000.0
            ),
        )
        crossing_index: int | None = None
        for index in range(0, len(frames) - persistence_frames + 1):
            window = state_metrics[index : index + persistence_frames]
            if all(
                metric.state_score >= self.config.state_change_threshold
                or metric.text_score >= self.config.text_change_threshold
                for metric in window
            ):
                crossing_index = index
                break

        if crossing_index is None:
            return coarse

        settle_frames = max(
            2,
            math.ceil(self.config.settle_ms * self.config.fine_fps / 1000.0),
        )
        stable_window: list[int] = []
        for index in range(crossing_index, len(frames)):
            if step_metrics[index].state_score <= self.config.stable_step_threshold:
                stable_window.append(index)
                if len(stable_window) >= settle_frames:
                    break
            else:
                stable_window.clear()

        if len(stable_window) < settle_frames:
            return coarse

        selected_index = max(
            stable_window,
            key=lambda item: frames[item].sharpness,
        )
        selected_frame = frames[selected_index]
        selected_state = state_metrics[selected_index]
        transition_step_peak = max(
            metric.state_score
            for metric in step_metrics[
                crossing_index : min(len(step_metrics), selected_index + 1)
            ]
        )
        reason: ChangeReason
        if transition_step_peak >= self.config.hard_cut_threshold:
            reason = "hard_cut"
        elif selected_state.text_score >= self.config.text_change_threshold:
            reason = "text_or_ui_change"
        else:
            reason = "state_change"

        return ChangeEvent(
            transition_ms=frames[crossing_index].timestamp_ms,
            keyframe_ms=selected_frame.timestamp_ms,
            previous_keyframe_ms=reference.timestamp_ms,
            reason=reason,
            state_score=selected_state.state_score,
            scene_score=selected_state.scene_score,
            text_score=selected_state.text_score,
            step_score=transition_step_peak,
            refined=True,
        )

    def _write_previews(
        self,
        source: Path,
        events: list[ChangeEvent],
        preview_dir: Path,
    ) -> list[ChangeEvent]:
        preview_dir.mkdir(parents=True, exist_ok=True)
        updated: list[ChangeEvent] = []
        for index, event in enumerate(events):
            preview_path = preview_dir / (
                f"{index:04d}_{event.keyframe_ms:012d}ms_{event.reason}.jpg"
            )
            command = [
                self.ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{event.keyframe_ms / 1000.0:.6f}",
                "-i",
                str(source),
                "-map",
                "0:v:0",
                "-frames:v",
                "1",
                "-q:v",
                "2",
                "-y",
                str(preview_path),
            ]
            subprocess.run(command, check=True, capture_output=True)
            payload = event.to_dict()
            payload["preview_path"] = str(preview_path)
            updated.append(ChangeEvent(**payload))
        return updated


def _resolve_executable(name: str) -> str:
    resolved = shutil.which(name)
    if resolved is None:
        raise FileNotFoundError(
            f"required executable '{name}' was not found on PATH"
        )
    return resolved


def _parse_frame_rate(value: object) -> float | None:
    if not isinstance(value, str) or not value or value == "0/0":
        return None
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        denominator_value = float(denominator)
        if denominator_value == 0:
            return None
        return float(numerator) / denominator_value
    return float(value)


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _generator_is_closing() -> bool:
    # When a consumer intentionally stops iterating, GeneratorExit reaches the
    # finally block.  FFmpeg then commonly exits with a broken-pipe status.  The
    # scanner always consumes streams fully, so this is primarily defensive.
    import sys

    return sys.exc_info()[0] is GeneratorExit


def synthetic_observations(
    images: Iterable[Image.Image],
    *,
    fps: float,
) -> Iterator[FrameObservation]:
    """Build observations for deterministic tests and calibration tools."""

    for index, image in enumerate(images):
        yield FrameObservation(
            timestamp_ms=round(index * 1000.0 / fps),
            image=image.convert("RGB"),
            sharpness=frame_sharpness(image),
        )
