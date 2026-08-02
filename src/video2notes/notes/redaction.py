"""Privacy filtering for note-facing text and model payloads.

Raw OCR/ASR artifacts remain available inside the local run for audit and
rework.  This module protects the separate note/export boundary, where a
transient credential or contact detail should not become part of a Markdown,
HTML, PDF, or remote-model request by default.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from video2notes.domain import EvidenceSpan

from .models import (
    FactCard,
    NoteDocument,
    NoteMetadata,
    NoteScreenshot,
    NoteSection,
    SupportingMaterial,
)

_RTMP_URL = re.compile(r"(?i)\brtmps?://[^\s<>{}\[\]\"']+")
_EMAIL = re.compile(r"(?i)(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])")
_MOBILE_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_SENSITIVE_QUERY = re.compile(
    r"(?i)([?&](?:access_?token|auth|authorization|key|signature|stream_?key|"
    r"token|secret)=)[^&#\s]+"
)
_ASSIGNED_SECRET = re.compile(
    r"(?i)(\b(?:api[_ -]?key|authorization|bearer|password|secret|stream[_ -]?key|"
    r"token)\b\s*[:=]\s*)([A-Z0-9_./+~-]{8,})"
)
_STREAM_VALUE = re.compile(
    r"(?i)((?:推流码|推流密钥|流串密钥|stream\s*key)\s*[:：=]?\s*)"
    r"([A-Z0-9_./+~-]{6,})"
)
_CONTEXT_NUMBER = re.compile(
    r"(?i)((?:QQ群?|QQ群号|群号|交流群|直播间号|房间号|房间ID|身份码|账号ID|"
    r"用户ID)\D{0,24})(\d{5,16})(?!\d)"
)
_WECHAT_VALUE = re.compile(r"(?i)((?:微信号?|wechat|wx)\s*[:：=]?\s*)([A-Z][A-Z0-9_-]{5,19})")


def redact_note_text(value: str) -> str:
    """Replace likely private values while retaining their semantic category."""

    text = _RTMP_URL.sub("[已隐藏推流地址]", value)
    text = _SENSITIVE_QUERY.sub(r"\1[已隐藏参数]", text)
    text = _ASSIGNED_SECRET.sub(r"\1[已隐藏密钥]", text)
    text = _STREAM_VALUE.sub(r"\1[已隐藏推流密钥]", text)
    text = _EMAIL.sub("[已隐藏邮箱]", text)
    text = _MOBILE_PHONE.sub("[已隐藏手机号]", text)
    text = _CONTEXT_NUMBER.sub(r"\1[已隐藏标识]", text)
    return _WECHAT_VALUE.sub(r"\1[已隐藏微信号]", text)


def contains_sensitive_note_text(value: str) -> bool:
    """Return whether the export policy would change ``value``."""

    return redact_note_text(value) != value


def redacted_evidence_copy(evidence: EvidenceSpan) -> EvidenceSpan:
    """Return an ID/timeline-preserving evidence copy safe for note consumers."""

    return evidence.model_copy(
        update={
            "raw_text": (
                redact_note_text(evidence.raw_text) if evidence.raw_text is not None else None
            ),
            "normalized_text": (
                redact_note_text(evidence.normalized_text)
                if evidence.normalized_text is not None
                else None
            ),
            "provenance": _redact_tree(evidence.provenance),
        },
        deep=True,
    )


def redacted_evidence_payload(evidence: EvidenceSpan) -> dict[str, object]:
    """Serialize a redacted evidence copy for an external model request."""

    return redacted_evidence_copy(evidence).model_dump(mode="json")


def redacted_supporting_material_copy(
    material: SupportingMaterial,
) -> SupportingMaterial:
    """Return a text-safe copy while preserving immutable artifact identity."""

    return material.model_copy(
        update={
            "title": redact_note_text(material.title),
            "text_content": (
                redact_note_text(material.text_content)
                if material.text_content is not None
                else None
            ),
            "original_name": (
                redact_note_text(material.original_name)
                if material.original_name is not None
                else None
            ),
        },
        deep=True,
    )


def sanitize_note_document(note: NoteDocument) -> NoteDocument:
    """Apply the export policy to every user-visible or embedded text field."""

    metadata = NoteMetadata.model_validate(
        {
            **note.metadata.model_dump(mode="python"),
            "title": redact_note_text(note.metadata.title),
            "source_locator": redact_note_text(note.metadata.source_locator),
            "source_url": (
                redact_note_text(note.metadata.source_url)
                if note.metadata.source_url is not None
                else None
            ),
            "author": (
                redact_note_text(note.metadata.author) if note.metadata.author is not None else None
            ),
            "quality_warnings": [redact_note_text(item) for item in note.metadata.quality_warnings],
        }
    )
    facts = [
        FactCard.model_validate(
            {**item.model_dump(mode="python"), "claim": redact_note_text(item.claim)}
        )
        for item in note.facts
    ]
    sections = [
        NoteSection.model_validate(
            {
                **section.model_dump(mode="python"),
                "title": redact_note_text(section.title),
                "summary": redact_note_text(section.summary),
                "body_markdown": redact_note_text(section.body_markdown),
                "screenshots": [
                    NoteScreenshot.model_validate(
                        {
                            **screenshot.model_dump(mode="python"),
                            "caption": redact_note_text(screenshot.caption),
                            "alt_text": redact_note_text(screenshot.alt_text),
                        }
                    )
                    for screenshot in section.screenshots
                ],
            }
        )
        for section in note.sections
    ]
    materials = [redacted_supporting_material_copy(item) for item in note.supporting_materials]
    return NoteDocument(
        schema_version=note.schema_version,
        metadata=metadata,
        abstract=redact_note_text(note.abstract),
        key_takeaways=[redact_note_text(item) for item in note.key_takeaways],
        sections=sections,
        facts=facts,
        evidence=[redacted_evidence_copy(item) for item in note.evidence],
        supporting_materials=materials,
        glossary={
            redact_note_text(key): redact_note_text(value) for key, value in note.glossary.items()
        },
    )


def redact_text_sequence(values: Sequence[str]) -> list[str]:
    """Redact joined OCR context before returning the original item boundaries."""

    return [redact_note_text(item) for item in values]


def _redact_tree(value: Any) -> Any:
    if isinstance(value, str):
        return redact_note_text(value)
    if isinstance(value, Mapping):
        return {key: _redact_tree(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_tree(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_tree(item) for item in value)
    return value
