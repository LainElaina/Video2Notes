"""FFprobe integration that preserves rational time bases and stream offsets."""

from __future__ import annotations

import json
import shutil
import subprocess
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from video2notes.artifacts import sha256_file
from video2notes.domain import MediaManifest, MediaStream, Rational


def probe_media(
    source: str | Path,
    *,
    ffprobe_path: str = "ffprobe",
) -> MediaManifest:
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"media does not exist: {source_path}")
    executable = shutil.which(ffprobe_path)
    if executable is None:
        raise FileNotFoundError(f"required executable '{ffprobe_path}' was not found on PATH")

    command = [
        executable,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(source_path),
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
    return parse_probe_payload(
        payload,
        source_path=source_path,
        source_sha256=sha256_file(source_path),
        file_size=source_path.stat().st_size,
    )


def parse_probe_payload(
    payload: dict[str, Any],
    *,
    source_path: str | Path,
    source_sha256: str,
    file_size: int,
) -> MediaManifest:
    streams: list[MediaStream] = []
    for raw_stream in payload.get("streams") or []:
        if not isinstance(raw_stream, dict):
            continue
        time_base = _parse_rational(raw_stream.get("time_base"))
        codec_type = str(raw_stream.get("codec_type") or "unknown")
        if time_base is None:
            if codec_type in {"video", "audio"}:
                raise ValueError(
                    f"ffprobe did not return a usable time_base for {codec_type} stream"
                )
            time_base = Rational(numerator=1, denominator=1_000_000)

        start_pts = _parse_int(raw_stream.get("start_pts"))
        start_time_us = _seconds_to_us(raw_stream.get("start_time"))
        if start_time_us is None and start_pts is not None:
            start_time_us = time_base.timestamp_us(start_pts)

        duration_ts = _parse_int(raw_stream.get("duration_ts"))
        duration_us = _seconds_to_us(raw_stream.get("duration"))
        if duration_us is None and duration_ts is not None:
            duration_us = time_base.timestamp_us(duration_ts)

        tags = raw_stream.get("tags")
        language = None
        if isinstance(tags, dict) and tags.get("language") is not None:
            language = str(tags["language"])

        streams.append(
            MediaStream(
                index=int(raw_stream.get("index") or 0),
                codec_type=codec_type,
                codec_name=_optional_string(raw_stream.get("codec_name")),
                time_base=time_base,
                start_pts=start_pts,
                start_time_us=start_time_us,
                duration_ts=duration_ts,
                duration_us=duration_us,
                avg_frame_rate=_parse_rational(raw_stream.get("avg_frame_rate")),
                real_frame_rate=_parse_rational(raw_stream.get("r_frame_rate")),
                width=_parse_int(raw_stream.get("width")),
                height=_parse_int(raw_stream.get("height")),
                sample_rate=_parse_int(raw_stream.get("sample_rate")),
                channels=_parse_int(raw_stream.get("channels")),
                language=language,
            )
        )

    if not any(item.codec_type in {"video", "audio"} for item in streams):
        raise ValueError("input does not contain an audio or video stream")

    raw_format = payload.get("format")
    format_payload = raw_format if isinstance(raw_format, dict) else {}
    format_start_us = _seconds_to_us(format_payload.get("start_time"))
    start_candidates = [item.start_time_us for item in streams if item.start_time_us is not None]
    if format_start_us is not None:
        start_candidates.append(format_start_us)
    timeline_origin_us = min(start_candidates, default=0)

    duration_us = _seconds_to_us(format_payload.get("duration"))
    if duration_us is None:
        end_candidates = [
            (item.start_time_us or timeline_origin_us) + item.duration_us
            for item in streams
            if item.duration_us is not None
        ]
        duration_us = max(end_candidates, default=timeline_origin_us) - timeline_origin_us

    return MediaManifest(
        source_path=str(Path(source_path).expanduser().resolve()),
        source_sha256=source_sha256,
        container_format=_optional_string(format_payload.get("format_name")),
        file_size=file_size,
        duration_us=max(0, duration_us),
        timeline_origin_us=timeline_origin_us,
        streams=streams,
    )


def _parse_rational(value: object) -> Rational | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"0/0", "N/A"}:
        return None
    if "/" in text:
        numerator_text, denominator_text = text.split("/", 1)
        numerator = int(numerator_text)
        denominator = int(denominator_text)
    else:
        decimal = Decimal(text)
        numerator, denominator = decimal.as_integer_ratio()
    if denominator == 0:
        return None
    return Rational(numerator=numerator, denominator=denominator)


def _seconds_to_us(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "N/A":
        return None
    try:
        microseconds = Decimal(text) * Decimal(1_000_000)
    except InvalidOperation:
        return None
    return int(microseconds.to_integral_value(rounding=ROUND_HALF_UP))


def _parse_int(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "N/A":
        return None
    return int(text)


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
