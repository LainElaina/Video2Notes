"""Platform subtitle parsing that preserves cue intervals and language metadata."""

from __future__ import annotations

import codecs
import hashlib
import html
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from video2notes.domain import EvidenceModality, EvidenceSpan

_TIMING_LINE = re.compile(r"^\s*(?P<start>[0-9:,\.]+)\s*-->\s*(?P<end>[0-9:,\.]+)(?:\s+.*)?$")
_TIMESTAMP = re.compile(
    r"^(?:(?P<hours>\d+):)?(?P<minutes>\d{1,2}):"
    r"(?P<seconds>\d{1,2})(?:[\.,](?P<fraction>\d+))?$"
)
_LANG_TAG = re.compile(r"<lang\s+([A-Za-z0-9_-]+)(?:\s[^>]*)?>", re.IGNORECASE)
_VOICE_TAG = re.compile(r"<v(?:\.[^\s>]*)*\s+([^>]+)>", re.IGNORECASE)
_MARKUP_TAG = re.compile(r"<[^>]*>")


class SubtitleParseError(ValueError):
    """Raised when a subtitle cue cannot be mapped to a precise interval."""


def parse_subtitle_file(
    path: str | Path,
    *,
    run_id: str,
    language: str | None = None,
    timeline_offset_us: int = 0,
    provider: str = "platform",
    model: str = "platform-caption",
    version: str = "subtitle-parser-v1",
) -> list[EvidenceSpan]:
    subtitle_path = Path(path).expanduser().resolve()
    text = _decode_subtitle_bytes(subtitle_path.read_bytes())
    return parse_subtitle_text(
        text,
        run_id=run_id,
        language=language,
        timeline_offset_us=timeline_offset_us,
        provider=provider,
        model=model,
        version=version,
        source_name=subtitle_path.name,
    )


def parse_subtitle_text(
    text: str,
    *,
    run_id: str,
    language: str | None = None,
    timeline_offset_us: int = 0,
    provider: str = "platform",
    model: str = "platform-caption",
    version: str = "subtitle-parser-v1",
    source_name: str = "inline",
) -> list[EvidenceSpan]:
    """Parse SRT or WebVTT without merging overlapping cues."""

    normalized = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    default_language = language
    cursor = 0
    if lines and lines[0].strip().upper().startswith("WEBVTT"):
        cursor = 1
        while cursor < len(lines) and lines[cursor].strip():
            header = lines[cursor].strip()
            if header.casefold().startswith("language:") and default_language is None:
                default_language = header.split(":", 1)[1].strip() or None
            cursor += 1

    evidence: list[EvidenceSpan] = []
    cue_index = 0
    while cursor < len(lines):
        current = lines[cursor].strip()
        if not current:
            cursor += 1
            continue
        if current.upper().startswith(("NOTE", "STYLE", "REGION")):
            cursor = _skip_block(lines, cursor)
            continue

        identifier: str | None = None
        timing_match = _TIMING_LINE.match(lines[cursor])
        if timing_match is None and cursor + 1 < len(lines):
            next_match = _TIMING_LINE.match(lines[cursor + 1])
            if next_match is not None:
                identifier = current
                cursor += 1
                timing_match = next_match
        if timing_match is None:
            cursor += 1
            continue

        raw_timing = lines[cursor].strip()
        start_us = parse_subtitle_timestamp(timing_match.group("start")) + timeline_offset_us
        end_us = parse_subtitle_timestamp(timing_match.group("end")) + timeline_offset_us
        if start_us < 0 or end_us < 0:
            raise SubtitleParseError("subtitle timeline offset produced a negative cue timestamp")
        if end_us < start_us:
            raise SubtitleParseError(f"subtitle cue ends before it starts: {raw_timing}")

        cursor += 1
        cue_lines: list[str] = []
        while cursor < len(lines) and lines[cursor].strip():
            cue_lines.append(lines[cursor])
            cursor += 1
        if not cue_lines:
            continue

        tagged_text = "\n".join(cue_lines)
        cue_language = _extract_language(tagged_text) or default_language
        speaker = _extract_speaker(tagged_text)
        raw_text = _strip_markup(tagged_text)
        if not raw_text.strip():
            continue
        stable_id = _cue_id(
            source_name=source_name,
            cue_index=cue_index,
            start_us=start_us,
            end_us=end_us,
            text=raw_text,
        )
        evidence.append(
            EvidenceSpan(
                id=stable_id,
                run_id=run_id,
                modality=EvidenceModality.PLATFORM_CAPTION,
                start_us=start_us,
                end_us=end_us,
                language=cue_language,
                raw_text=raw_text,
                normalized_text=" ".join(raw_text.split()),
                confidence=None,
                confidence_kind="platform_caption_unscored",
                provider=provider,
                model=model,
                version=version,
                speaker=speaker,
                provenance={
                    "subtitle_source": source_name,
                    "cue_index": cue_index,
                    "cue_identifier": identifier,
                    "raw_timing": raw_timing,
                    "timeline_offset_us": timeline_offset_us,
                },
            )
        )
        cue_index += 1
    return evidence


def parse_subtitle_timestamp(value: str) -> int:
    match = _TIMESTAMP.fullmatch(value.strip())
    if match is None:
        raise SubtitleParseError(f"invalid subtitle timestamp: {value!r}")
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes"))
    seconds = int(match.group("seconds"))
    if minutes >= 60 or seconds >= 60:
        raise SubtitleParseError(f"invalid subtitle timestamp: {value!r}")
    fraction_text = match.group("fraction") or "0"
    try:
        fraction = Decimal(f"0.{fraction_text}")
    except InvalidOperation as error:
        raise SubtitleParseError(f"invalid subtitle timestamp: {value!r}") from error
    total_seconds = Decimal(hours * 3600 + minutes * 60 + seconds) + fraction
    return int((total_seconds * Decimal(1_000_000)).to_integral_value(rounding=ROUND_HALF_UP))


def _skip_block(lines: list[str], cursor: int) -> int:
    cursor += 1
    while cursor < len(lines) and lines[cursor].strip():
        cursor += 1
    return cursor


def _decode_subtitle_bytes(payload: bytes) -> str:
    if payload.startswith(codecs.BOM_UTF8):
        return payload.decode("utf-8-sig")
    if payload.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return payload.decode("utf-32")
    if payload.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return payload.decode("utf-16")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SubtitleParseError(
            "subtitle is not UTF-8 and has no supported UTF-16/UTF-32 BOM"
        ) from error


def _extract_language(text: str) -> str | None:
    match = _LANG_TAG.search(text)
    return match.group(1) if match is not None else None


def _extract_speaker(text: str) -> str | None:
    match = _VOICE_TAG.search(text)
    if match is None:
        return None
    speaker = html.unescape(match.group(1)).strip()
    return speaker or None


def _strip_markup(text: str) -> str:
    untagged = _MARKUP_TAG.sub("", text)
    return html.unescape(untagged).replace("\xa0", " ").strip()


def _cue_id(
    *,
    source_name: str,
    cue_index: int,
    start_us: int,
    end_us: int,
    text: str,
) -> str:
    payload = f"{source_name}\0{cue_index}\0{start_us}\0{end_us}\0{text}".encode()
    return f"caption-{hashlib.sha256(payload).hexdigest()[:20]}"
