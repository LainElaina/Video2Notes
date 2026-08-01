"""PTS-preserving video decoding built on PyAV.

The visual pipeline samples *source frames* by their presentation timestamps.
It never manufactures timestamps from a frame counter or nominal frame rate.
PyAV is used as the FFmpeg binding so every decoded image stays paired with
its raw ``pts`` and rational ``time_base``.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

from PIL import Image

from video2notes.domain import MediaTimestamp, Rational


class VideoDecodeError(RuntimeError):
    """Base error for timestamp-aware video decoding."""


class MissingPresentationTimestampError(VideoDecodeError):
    """Raised when a decoded source frame has no usable presentation timestamp."""


class ExactFrameNotFoundError(VideoDecodeError):
    """Raised when a previously observed source PTS cannot be decoded again."""


@dataclass(frozen=True, slots=True)
class DecodedVideoFrame:
    """A decoded source frame and its exact position on the canonical timeline."""

    timestamp: MediaTimestamp
    image: Image.Image = field(repr=False, compare=False)
    stream_index: int
    key_frame: bool
    picture_type: str | None = None
    requested_time_us: int | None = None


def iter_video_frames(
    source: str | Path,
    *,
    timeline_origin_us: int,
    stream_index: int | None = None,
    sample_fps: float | None = None,
    sample_period_us: int | None = None,
    start_us: int = 0,
    end_us: int | None = None,
    target_size: tuple[int, int] | None = None,
    decode_threads: int | None = None,
) -> Iterator[DecodedVideoFrame]:
    """Decode video frames while retaining their source PTS.

    ``start_us`` and ``end_us`` are positions on the shared canonical media
    timeline. The end is exclusive. When ``sample_fps`` or
    ``sample_period_us`` is provided, the decoder emits the first real source
    frame at or after each sampling tick; the emitted timestamp always remains
    that source frame's PTS. ``requested_time_us`` records the tick that selected
    a sampled frame without replacing its physical timestamp.
    """

    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"video does not exist: {source_path}")
    if start_us < 0:
        raise ValueError("start_us cannot be negative")
    if end_us is not None and end_us <= start_us:
        raise ValueError("end_us must be greater than start_us")
    if sample_fps is not None and sample_fps <= 0:
        raise ValueError("sample_fps must be positive")
    if sample_period_us is not None and sample_period_us <= 0:
        raise ValueError("sample_period_us must be positive")
    if sample_fps is not None and sample_period_us is not None:
        raise ValueError("provide sample_fps or sample_period_us, not both")
    if decode_threads is not None and decode_threads < 1:
        raise ValueError("decode_threads must be positive")
    _validate_target_size(target_size)

    av = _load_av()
    with av.open(str(source_path), mode="r") as container:
        stream = _select_video_stream(container, stream_index)
        _configure_decode_threads(stream, decode_threads)
        if start_us > 0:
            _seek_to_canonical_time(
                container,
                stream,
                time_us=start_us,
                timeline_origin_us=timeline_origin_us,
            )

        sample_period = (
            Fraction(sample_period_us, 1)
            if sample_period_us is not None
            else (
                Fraction(1_000_000, 1) / Fraction(str(sample_fps))
                if sample_fps is not None
                else None
            )
        )
        next_sample = Fraction(start_us, 1)

        for frame in container.decode(stream):
            timestamp = _timestamp_for_frame(
                frame,
                stream,
                timeline_origin_us=timeline_origin_us,
            )
            if timestamp.time_us < start_us:
                continue
            if end_us is not None and timestamp.time_us >= end_us:
                break

            requested_time_us: int | None = None
            if sample_period is not None:
                if Fraction(timestamp.time_us, 1) < next_sample:
                    continue
                requested_time_us = int(next_sample)
                elapsed = Fraction(timestamp.time_us - start_us, 1)
                completed_ticks = elapsed // sample_period
                next_sample = (
                    Fraction(start_us, 1)
                    + (completed_ticks + 1) * sample_period
                )

            yield _decoded_frame(
                frame,
                timestamp,
                stream_index=int(stream.index),
                target_size=target_size,
                requested_time_us=requested_time_us,
            )


def decode_video_frame_at(
    source: str | Path,
    *,
    timestamp: MediaTimestamp,
    timeline_origin_us: int,
    stream_index: int | None = None,
    target_size: tuple[int, int] | None = None,
    decode_threads: int | None = None,
) -> DecodedVideoFrame:
    """Decode the exact source frame identified by ``timestamp``.

    This is used for stable reference frames and full-resolution previews.
    Seeking is only an optimization: the result is accepted solely when both
    raw PTS and rational time base identify the requested source frame.
    """

    if timestamp.pts is None:
        raise ExactFrameNotFoundError("an exact frame requires a raw source PTS")
    if decode_threads is not None and decode_threads < 1:
        raise ValueError("decode_threads must be positive")
    _validate_target_size(target_size)

    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"video does not exist: {source_path}")

    av = _load_av()
    with av.open(str(source_path), mode="r") as container:
        stream = _select_video_stream(container, stream_index)
        _configure_decode_threads(stream, decode_threads)
        stream_time_base = _fraction_for_time_base(stream.time_base)
        target_time_base = timestamp.time_base.fraction
        target_source_time = Fraction(timestamp.pts, 1) * target_time_base
        target_stream_pts = _seconds_to_pts_floor(
            target_source_time,
            stream_time_base,
        )
        container.seek(
            target_stream_pts,
            backward=True,
            any_frame=False,
            stream=stream,
        )

        for frame in container.decode(stream):
            candidate = _timestamp_for_frame(
                frame,
                stream,
                timeline_origin_us=timeline_origin_us,
            )
            candidate_time_base = candidate.time_base.fraction
            if (
                candidate.pts == timestamp.pts
                and candidate_time_base == target_time_base
            ):
                return _decoded_frame(
                    frame,
                    candidate,
                    stream_index=int(stream.index),
                    target_size=target_size,
                )

            candidate_source_time = (
                Fraction(candidate.pts, 1) * candidate_time_base
                if candidate.pts is not None
                else Fraction(candidate.source_time_us, 1_000_000)
            )
            if candidate_source_time > target_source_time:
                break

    raise ExactFrameNotFoundError(
        "could not decode exact video frame "
        f"pts={timestamp.pts} time_base="
        f"{timestamp.time_base.numerator}/{timestamp.time_base.denominator}"
    )


def _load_av() -> Any:
    try:
        import av
    except ImportError as error:  # pragma: no cover - exercised by bootstrap failures
        raise VideoDecodeError(
            "PyAV is required for PTS-preserving video decoding; "
            "install the project's declared dependencies"
        ) from error
    return av


def _select_video_stream(container: Any, stream_index: int | None) -> Any:
    streams = list(container.streams.video)
    if stream_index is None:
        if not streams:
            raise VideoDecodeError("input does not contain a video stream")
        return streams[0]
    for stream in streams:
        if int(stream.index) == stream_index:
            return stream
    raise VideoDecodeError(f"video stream index {stream_index} does not exist")


def _configure_decode_threads(stream: Any, decode_threads: int | None) -> None:
    if decode_threads is None:
        return
    # PyAV forwards these settings to the bundled FFmpeg codec context. AUTO
    # lets the codec choose frame/slice threading while the execution plan
    # still owns the hard worker limit.
    stream.thread_type = "AUTO"
    stream.thread_count = decode_threads


def _seek_to_canonical_time(
    container: Any,
    stream: Any,
    *,
    time_us: int,
    timeline_origin_us: int,
) -> None:
    source_time = Fraction(timeline_origin_us + time_us, 1_000_000)
    stream_time_base = _fraction_for_time_base(stream.time_base)
    offset = _seconds_to_pts_floor(source_time, stream_time_base)
    container.seek(offset, backward=True, any_frame=False, stream=stream)


def _timestamp_for_frame(
    frame: Any,
    stream: Any,
    *,
    timeline_origin_us: int,
) -> MediaTimestamp:
    if frame.pts is None:
        raise MissingPresentationTimestampError(
            f"decoded frame in video stream {stream.index} has no presentation timestamp"
        )
    time_base_value = frame.time_base or stream.time_base
    time_base_fraction = _fraction_for_time_base(time_base_value)
    time_base = Rational(
        numerator=time_base_fraction.numerator,
        denominator=time_base_fraction.denominator,
    )
    return MediaTimestamp.from_pts(
        int(frame.pts),
        time_base,
        timeline_origin_us=timeline_origin_us,
        timestamp_kind="pts",
    )


def _decoded_frame(
    frame: Any,
    timestamp: MediaTimestamp,
    *,
    stream_index: int,
    target_size: tuple[int, int] | None,
    requested_time_us: int | None = None,
) -> DecodedVideoFrame:
    image = _frame_to_image(frame, target_size=target_size)
    raw_picture_type = getattr(frame, "pict_type", None)
    picture_type = str(raw_picture_type) if raw_picture_type is not None else None
    return DecodedVideoFrame(
        timestamp=timestamp,
        image=image,
        stream_index=stream_index,
        key_frame=bool(getattr(frame, "key_frame", False)),
        picture_type=picture_type,
        requested_time_us=requested_time_us,
    )


def _frame_to_image(
    frame: Any,
    *,
    target_size: tuple[int, int] | None,
) -> Image.Image:
    if target_size is None:
        return cast(Image.Image, frame.to_image()).convert("RGB")

    target_width, target_height = target_size
    scale = min(target_width / int(frame.width), target_height / int(frame.height))
    scaled_width = max(1, min(target_width, round(int(frame.width) * scale)))
    scaled_height = max(1, min(target_height, round(int(frame.height) * scale)))
    converted = frame.reformat(
        width=scaled_width,
        height=scaled_height,
        format="rgb24",
    )
    image = cast(Image.Image, converted.to_image()).convert("RGB")
    if image.size == target_size:
        return image

    canvas = Image.new("RGB", target_size, (0, 0, 0))
    left = (target_width - scaled_width) // 2
    top = (target_height - scaled_height) // 2
    canvas.paste(image, (left, top))
    return canvas


def _fraction_for_time_base(value: Any) -> Fraction:
    if value is None:
        raise VideoDecodeError("video stream does not expose a time base")
    fraction = Fraction(value)
    if fraction <= 0:
        raise VideoDecodeError("video stream time base must be positive")
    return fraction


def _seconds_to_pts_floor(seconds: Fraction, time_base: Fraction) -> int:
    return int(seconds // time_base)


def _validate_target_size(target_size: tuple[int, int] | None) -> None:
    if target_size is None:
        return
    if target_size[0] <= 0 or target_size[1] <= 0:
        raise ValueError("target_size dimensions must be positive")
