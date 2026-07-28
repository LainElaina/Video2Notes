"""Canonical note document and deterministic output renderers."""

from .composer import (
    EvidenceNoteComposer,
    InvocationSummary,
    NoteCompositionResult,
    build_deterministic_note,
)
from .models import (
    FactCard,
    NoteDocument,
    NoteMetadata,
    NoteScreenshot,
    NoteSection,
)
from .render import (
    format_timestamp,
    render_html,
    render_markdown,
    render_pdf_from_html,
    write_html,
    write_markdown,
)

__all__ = [
    "FactCard",
    "EvidenceNoteComposer",
    "InvocationSummary",
    "NoteDocument",
    "NoteCompositionResult",
    "NoteMetadata",
    "NoteScreenshot",
    "NoteSection",
    "format_timestamp",
    "build_deterministic_note",
    "render_html",
    "render_markdown",
    "render_pdf_from_html",
    "write_html",
    "write_markdown",
]
