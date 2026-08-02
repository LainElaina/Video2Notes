"""Stable contracts shared by acquisition, analysis, notes, and the UI."""

from .models import (
    ArtifactKind,
    ArtifactManifest,
    ArtifactRef,
    BoundingBox,
    EvidenceModality,
    EvidenceSpan,
    MediaManifest,
    MediaStream,
    MediaTimestamp,
    ModelInvocation,
    ProcessingScope,
    Rational,
    RunStatus,
    SourceDescriptor,
    StageStatus,
    VisualState,
)

__all__ = [
    "ArtifactKind",
    "ArtifactManifest",
    "ArtifactRef",
    "BoundingBox",
    "EvidenceModality",
    "EvidenceSpan",
    "MediaManifest",
    "MediaStream",
    "MediaTimestamp",
    "ModelInvocation",
    "ProcessingScope",
    "Rational",
    "RunStatus",
    "SourceDescriptor",
    "StageStatus",
    "VisualState",
]
