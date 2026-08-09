"""Atomic artifact manifests with deterministic stage cache fingerprints."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import time
import uuid
from collections.abc import Iterable, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, Self

from video2notes.domain import (
    ArtifactKind,
    ArtifactManifest,
    ArtifactRef,
    ProcessingScope,
    RunStatus,
    SourceDescriptor,
    StageStatus,
)
from video2notes.domain.models import StageRecord

_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


def sha256_file(path: str | Path, *, chunk_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def new_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:12]}"


class RunWorkspace:
    """Own one run directory and its atomic manifest."""

    DIRECTORY_NAMES = (
        "source",
        "system",
        "media",
        "subtitles",
        "audio",
        "asr",
        "vision",
        "ocr",
        "evidence",
        "notes",
        "render",
        "supporting",
        "operations",
        "revisions",
        "logs",
    )

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.manifest_path = self.root / "manifest.json"
        if not self.manifest_path.is_file():
            raise FileNotFoundError(f"run manifest does not exist: {self.manifest_path}")
        self.manifest = ArtifactManifest.model_validate_json(
            self.manifest_path.read_text(encoding="utf-8")
        )
        if self.manifest.schema_version < 2:
            # v1 predates processing_scope. Pydantic supplies the legacy
            # audio-visual default; persist the explicit v2 contract so later
            # readers never have to infer the run's modality boundary again.
            self.manifest.schema_version = 2
            self._save()

    @classmethod
    def create(
        cls,
        runs_root: str | Path,
        *,
        source: SourceDescriptor,
        profile: str,
        processing_scope: ProcessingScope = ProcessingScope.AUDIO_VISUAL,
        run_id: str | None = None,
    ) -> Self:
        resolved_id = _sanitize_id(run_id or new_run_id())
        root = Path(runs_root).expanduser().resolve() / resolved_id
        root.mkdir(parents=True, exist_ok=False)
        for directory in cls.DIRECTORY_NAMES:
            (root / directory).mkdir()
        manifest = ArtifactManifest(
            run_id=resolved_id,
            source=source,
            profile=profile,
            processing_scope=processing_scope,
        )
        workspace = cls.__new__(cls)
        workspace.root = root
        workspace.manifest_path = root / "manifest.json"
        workspace.manifest = manifest
        workspace._save()
        return workspace

    def artifact_path(self, directory: str, *parts: str) -> Path:
        if directory not in self.DIRECTORY_NAMES:
            raise ValueError(f"unknown artifact directory: {directory}")
        candidate = (self.root / directory).joinpath(*parts).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError("artifact path escaped the run directory")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate

    def stage(
        self,
        stage_name: str,
        *,
        stage_version: str,
        config: Mapping[str, Any] | None = None,
        inputs: Iterable[ArtifactRef] = (),
    ) -> StageTransaction:
        return StageTransaction(
            self,
            stage_name=stage_name,
            stage_version=stage_version,
            config=dict(config or {}),
            inputs=list(inputs),
        )

    def ref_for(
        self,
        path: str | Path,
        *,
        kind: ArtifactKind,
        media_type: str | None = None,
    ) -> ArtifactRef:
        resolved = Path(path).resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"artifact does not exist: {resolved}")
        if not resolved.is_relative_to(self.root):
            raise ValueError("artifact must be inside its run directory")
        relative = resolved.relative_to(self.root).as_posix()
        guessed_type = media_type or mimetypes.guess_type(resolved.name)[0]
        return ArtifactRef(
            kind=kind,
            relative_path=relative,
            sha256=sha256_file(resolved),
            size_bytes=resolved.stat().st_size,
            media_type=guessed_type,
        )

    def verify_ref(self, artifact: ArtifactRef) -> bool:
        path = (self.root / artifact.relative_path).resolve()
        return (
            path.is_relative_to(self.root)
            and path.is_file()
            and path.stat().st_size == artifact.size_bytes
            and sha256_file(path) == artifact.sha256
        )

    def set_status(self, status: RunStatus) -> None:
        self.manifest.status = status
        self._save()

    def add_warning(self, message: str) -> None:
        if message not in self.manifest.warnings:
            self.manifest.warnings.append(message)
            self._save()

    def mark_cancelled(self, *, stage_name: str | None = None) -> None:
        self.manifest.status = RunStatus.CANCELLED
        if stage_name is not None:
            stage = self.manifest.stages.get(stage_name)
            if stage is not None and stage.status in {
                StageStatus.PENDING,
                StageStatus.RUNNING,
                StageStatus.FAILED,
            }:
                stage.status = StageStatus.CANCELLED
                stage.error = None
        self._save()

    def _save(self) -> None:
        self.manifest.updated_at = datetime.now(UTC)
        _atomic_write_json(
            self.manifest_path,
            self.manifest.model_dump(mode="json"),
        )


class StageTransaction:
    """Context manager that records success/failure and recognizes cache hits."""

    def __init__(
        self,
        workspace: RunWorkspace,
        *,
        stage_name: str,
        stage_version: str,
        config: dict[str, Any],
        inputs: list[ArtifactRef],
    ):
        self.workspace = workspace
        self.stage_name = stage_name
        self.stage_version = stage_version
        self.config = config
        self.inputs = inputs
        self.config_hash = stable_hash(config)
        self.fingerprint = stable_hash(
            {
                "stage_name": stage_name,
                "stage_version": stage_version,
                "config_hash": self.config_hash,
                "inputs": [
                    {
                        "path": item.relative_path,
                        "sha256": item.sha256,
                    }
                    for item in inputs
                ],
            }
        )
        self.record: StageRecord | None = None
        self.cached = False
        self._started_monotonic: float | None = None

    def __enter__(self) -> Self:
        existing = self.workspace.manifest.stages.get(self.stage_name)
        if (
            existing is not None
            and existing.status is StageStatus.COMPLETED
            and existing.fingerprint == self.fingerprint
            and all(self.workspace.verify_ref(item) for item in existing.outputs)
        ):
            self.record = existing
            self.cached = True
            return self

        attempt = 1 if existing is None else existing.attempt + 1
        self.record = StageRecord(
            stage_name=self.stage_name,
            stage_version=self.stage_version,
            fingerprint=self.fingerprint,
            status=StageStatus.RUNNING,
            attempt=attempt,
            config_hash=self.config_hash,
            inputs=self.inputs,
            started_at=datetime.now(UTC),
        )
        self.workspace.manifest.stages[self.stage_name] = self.record
        self.workspace.manifest.status = RunStatus.RUNNING
        self.workspace._save()
        self._started_monotonic = time.perf_counter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        del exc_type, traceback
        if self.cached:
            return False
        if self.record is None or self._started_monotonic is None:
            raise RuntimeError("stage transaction was not entered")

        self.record.finished_at = datetime.now(UTC)
        self.record.wall_time_seconds = max(
            0.0,
            time.perf_counter() - self._started_monotonic,
        )
        if exc_value is None:
            self.record.status = StageStatus.COMPLETED
            self.record.error = None
        else:
            self.record.status = StageStatus.FAILED
            self.record.error = f"{type(exc_value).__name__}: {exc_value}"
            self.workspace.manifest.status = RunStatus.FAILED
        self.workspace._save()
        return False

    @property
    def outputs(self) -> list[ArtifactRef]:
        if self.record is None:
            return []
        return self.record.outputs

    def add_output(
        self,
        path: str | Path,
        *,
        kind: ArtifactKind,
        media_type: str | None = None,
    ) -> ArtifactRef:
        if self.cached:
            raise RuntimeError("cannot add outputs to a cached stage")
        if self.record is None:
            raise RuntimeError("stage transaction was not entered")
        artifact = self.workspace.ref_for(path, kind=kind, media_type=media_type)
        self.record.outputs.append(artifact)
        return artifact

    def add_warning(self, message: str) -> None:
        if self.record is None:
            raise RuntimeError("stage transaction was not entered")
        self.record.warnings.append(message)

    def add_metric(self, name: str, value: float | int | str | bool | None) -> None:
        if self.record is None:
            raise RuntimeError("stage transaction was not entered")
        self.record.metrics[name] = value


def _sanitize_id(value: str) -> str:
    safe = _SAFE_ID.sub("-", value).strip("-.")
    if not safe:
        raise ValueError("run id does not contain safe characters")
    return safe[:120]


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == 7:
                    raise
                # Windows can briefly deny replacement while an API reader has
                # the previous manifest open. Keep the retry local and bounded.
                time.sleep(0.005 * (attempt + 1))
    finally:
        # Antivirus/indexer handles can briefly retain the temp file on
        # Windows; cleanup is best effort after an atomic replacement.
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
