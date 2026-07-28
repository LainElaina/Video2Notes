"""Backend protocol and text/readability helpers for OCR."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import Protocol

from PIL import Image, ImageFilter, ImageStat

from .models import BackendOcrOutput


class OcrBackend(Protocol):
    """Small injectable boundary implemented by PaddleOCR and test fakes."""

    def recognize(
        self,
        image: Image.Image,
        *,
        language_hints: Sequence[str] = (),
    ) -> BackendOcrOutput:
        """Recognize text without inventing content outside the returned image."""


_WHITESPACE = re.compile(r"\s+")


def normalize_ocr_text(value: str) -> str:
    """Apply reversible-enough canonicalization while retaining ``raw_text``."""

    normalized = unicodedata.normalize("NFKC", value)
    normalized = _WHITESPACE.sub(" ", normalized).strip()
    return normalized


def detect_script(value: str) -> str:
    """Return a coarse Unicode script label without pretending to know language."""

    scripts: set[str] = set()
    for character in value:
        if character.isspace() or unicodedata.category(character).startswith("P"):
            continue
        codepoint = ord(character)
        if (
            0x3400 <= codepoint <= 0x4DBF
            or 0x4E00 <= codepoint <= 0x9FFF
            or 0xF900 <= codepoint <= 0xFAFF
        ):
            scripts.add("han")
        elif 0x3040 <= codepoint <= 0x30FF:
            scripts.add("japanese_kana")
        elif 0xAC00 <= codepoint <= 0xD7AF:
            scripts.add("hangul")
        elif 0x0400 <= codepoint <= 0x052F:
            scripts.add("cyrillic")
        elif 0x0600 <= codepoint <= 0x06FF:
            scripts.add("arabic")
        elif 0x0900 <= codepoint <= 0x097F:
            scripts.add("devanagari")
        elif character.isascii() and character.isalpha():
            scripts.add("latin")
        elif character.isalpha():
            scripts.add("other")
    if not scripts:
        return "unknown"
    if len(scripts) == 1:
        return next(iter(scripts))
    return "mixed"


def tokenize_ocr_text(value: str) -> list[str]:
    """Tokenize Latin words and individual CJK glyphs without optional NLP models."""

    text = normalize_ocr_text(value).casefold()
    tokens: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            tokens.append("".join(current))
            current.clear()

    for character in text:
        codepoint = ord(character)
        is_cjk = (
            0x3400 <= codepoint <= 0x4DBF
            or 0x4E00 <= codepoint <= 0x9FFF
            or 0xF900 <= codepoint <= 0xFAFF
            or 0x3040 <= codepoint <= 0x30FF
            or 0xAC00 <= codepoint <= 0xD7AF
        )
        if is_cjk:
            flush()
            tokens.append(character)
        elif character.isalnum() or character in {"_", "'", "’"}:
            current.append(character)
        else:
            flush()
    flush()
    return tokens


def image_readability(image: Image.Image) -> float:
    """Estimate contrast and edge energy, returning a conservative 0..1 score."""

    gray = image if image.mode == "L" else image.convert("L")
    if gray.width < 2 or gray.height < 2:
        return 0.0
    contrast = min(1.0, float(ImageStat.Stat(gray).stddev[0]) / 64.0)
    edges = gray.filter(ImageFilter.FIND_EDGES)
    if edges.width > 2 and edges.height > 2:
        # FIND_EDGES paints an artificial one-pixel image border; omit it so a
        # blank white crop is not classified as readable text.
        edges = edges.crop((1, 1, edges.width - 1, edges.height - 1))
    edge_mean = min(1.0, float(ImageStat.Stat(edges).mean[0]) / 48.0)
    return min(1.0, 0.6 * contrast + 0.4 * edge_mean)
