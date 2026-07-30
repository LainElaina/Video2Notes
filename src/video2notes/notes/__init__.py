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
    SupportingMaterial,
    SupportingMaterialKind,
)
from .render import (
    format_timestamp,
    render_html,
    render_markdown,
    render_pdf_from_html,
    write_html,
    write_markdown,
)
from .reporting import OutputFormat, ReportPreset, ReportSpec, ResolvedReportSpec
from .revisions import (
    LatestReportRevision,
    ReportRevisionIndex,
    ReportRevisionRecord,
    ReportRevisionService,
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
    "SupportingMaterial",
    "SupportingMaterialKind",
    "OutputFormat",
    "LatestReportRevision",
    "ReportPreset",
    "ReportRevisionIndex",
    "ReportRevisionRecord",
    "ReportRevisionService",
    "ReportSpec",
    "ResolvedReportSpec",
    "format_timestamp",
    "build_deterministic_note",
    "render_html",
    "render_markdown",
    "render_pdf_from_html",
    "write_html",
    "write_markdown",
]
