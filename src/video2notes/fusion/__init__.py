"""Deterministic temporal fusion before any semantic model is allowed to reason."""

from .timeline import (
    ConflictKind,
    EvidenceConflict,
    EvidenceLink,
    EvidenceWindow,
    FusionResult,
    LinkRelation,
    build_evidence_timeline,
)

__all__ = [
    "ConflictKind",
    "EvidenceConflict",
    "EvidenceLink",
    "EvidenceWindow",
    "FusionResult",
    "LinkRelation",
    "build_evidence_timeline",
]
