"""Offline intrinsic diagnostics for completed Video2Notes runs."""

from .diagnostics import (
    EvaluationError,
    RunArtifactError,
    RunNotCompleteError,
    RunProfileSetError,
    RunSourceMismatchError,
    compare_runs,
    diagnose_run,
)
from .models import (
    EvidenceClassDiagnostics,
    EvidenceReferenceDiagnostics,
    NoteDiagnostics,
    OutputDiagnostics,
    ProfileComparison,
    RunComparison,
    RunDiagnostics,
    SourceIdentity,
    StageTiming,
    WarningDiagnostics,
)
from .render import render_comparison_markdown, render_diagnostics_markdown, render_json

__all__ = [
    "EvaluationError",
    "EvidenceClassDiagnostics",
    "EvidenceReferenceDiagnostics",
    "NoteDiagnostics",
    "OutputDiagnostics",
    "ProfileComparison",
    "RunArtifactError",
    "RunComparison",
    "RunDiagnostics",
    "RunNotCompleteError",
    "RunProfileSetError",
    "RunSourceMismatchError",
    "SourceIdentity",
    "StageTiming",
    "WarningDiagnostics",
    "compare_runs",
    "diagnose_run",
    "render_comparison_markdown",
    "render_diagnostics_markdown",
    "render_json",
]
