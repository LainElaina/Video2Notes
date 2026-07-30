"""Persistent, run-scoped supporting materials for local note work."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from io import BytesIO
from pathlib import Path
from typing import Self

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from video2notes.artifacts import RunWorkspace
from video2notes.domain import ArtifactKind, ArtifactRef

MAX_MATERIAL_BYTES = 25 * 1024 * 1024
MAX_TEXT_CHARACTERS = 1_000_000
_SAFE_TITLE = re.compile(r"[\x00-\x1f\x7f]+")


class MaterialKind(StrEnum):
    TEXT = "text"
    IMAGE = "image"


class MaterialStatus(StrEnum):
    ACTIVE = "active"
    DELETED = "deleted"


class MaterialModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class TextMaterialRequest(MaterialModel):
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=MAX_TEXT_CHARACTERS)
    start_us: int | None = Field(default=None, ge=0)
    end_us: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        _validate_optional_range(self.start_us, self.end_us)
        return self


class RunMaterial(MaterialModel):
    id: str = Field(pattern=r"^material-[A-Za-z0-9]+$")
    run_id: str = Field(min_length=1)
    kind: MaterialKind
    title: str = Field(min_length=1, max_length=240)
    original_name: str | None = None
    media_type: str
    artifact: ArtifactRef
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)
    text_content: str | None = None
    start_us: int | None = Field(default=None, ge=0)
    end_us: int | None = Field(default=None, ge=0)
    status: MaterialStatus = MaterialStatus.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    deleted_at: datetime | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        _validate_optional_range(self.start_us, self.end_us)
        if self.kind is MaterialKind.TEXT and self.text_content is None:
            raise ValueError("text material requires text_content")
        if self.status is MaterialStatus.DELETED and self.deleted_at is None:
            raise ValueError("deleted material requires deleted_at")
        return self


class MaterialIndex(MaterialModel):
    schema_version: int = 1
    run_id: str
    materials: list[RunMaterial] = Field(default_factory=list)


class MaterialStore:
    """Atomic JSON index plus content-addressed files inside one run."""

    def __init__(self, workspace: RunWorkspace):
        self.workspace = workspace
        self.index_path = workspace.artifact_path("supporting", "materials.json")

    def list(self, *, include_deleted: bool = False) -> list[RunMaterial]:
        index = self._load()
        if include_deleted:
            return index.materials
        return [item for item in index.materials if item.status is MaterialStatus.ACTIVE]

    def add_text(self, request: TextMaterialRequest) -> RunMaterial:
        normalized = request.content.strip()
        if not normalized:
            raise ValueError("supporting text cannot be blank")
        encoded = normalized.encode("utf-8")
        if len(encoded) > MAX_MATERIAL_BYTES:
            raise ValueError("supporting text exceeds the local size limit")
        identifier = _new_material_id()
        digest = hashlib.sha256(encoded).hexdigest()
        target = self.workspace.artifact_path(
            "supporting",
            "files",
            identifier,
            f"{digest}.md",
        )
        _atomic_write_bytes(target, encoded)
        material = RunMaterial(
            id=identifier,
            run_id=self.workspace.manifest.run_id,
            kind=MaterialKind.TEXT,
            title=_clean_title(request.title),
            media_type="text/markdown; charset=utf-8",
            artifact=self.workspace.ref_for(target, kind=ArtifactKind.SUPPORTING),
            sha256=digest,
            size_bytes=len(encoded),
            text_content=normalized,
            start_us=request.start_us,
            end_us=request.end_us,
        )
        self._append(material)
        return material

    def add_file(
        self,
        *,
        filename: str,
        content_type: str | None,
        content: bytes,
        title: str | None = None,
        start_us: int | None = None,
        end_us: int | None = None,
    ) -> RunMaterial:
        _validate_optional_range(start_us, end_us)
        if not content:
            raise ValueError("supporting file is empty")
        if len(content) > MAX_MATERIAL_BYTES:
            raise ValueError("supporting file exceeds the local size limit")
        detected_type, suffix = _detect_image(content)
        if content_type and content_type.partition(";")[0].strip() not in {
            "",
            "application/octet-stream",
            detected_type,
        }:
            raise ValueError("declared media type does not match the image content")
        identifier = _new_material_id()
        digest = hashlib.sha256(content).hexdigest()
        target = self.workspace.artifact_path(
            "supporting",
            "files",
            identifier,
            f"{digest}.{suffix}",
        )
        _atomic_write_bytes(target, content)
        safe_name = Path(filename.replace("\\", "/")).name[:240] or None
        material = RunMaterial(
            id=identifier,
            run_id=self.workspace.manifest.run_id,
            kind=MaterialKind.IMAGE,
            title=_clean_title(title or safe_name or "补充图片"),
            original_name=safe_name,
            media_type=detected_type,
            artifact=self.workspace.ref_for(
                target,
                kind=ArtifactKind.SUPPORTING,
                media_type=detected_type,
            ),
            sha256=digest,
            size_bytes=len(content),
            start_us=start_us,
            end_us=end_us,
        )
        self._append(material)
        return material

    def delete(self, material_id: str) -> RunMaterial:
        index = self._load()
        for material in index.materials:
            if material.id != material_id:
                continue
            if material.status is MaterialStatus.DELETED:
                return material
            replacement = material.model_copy(
                update={
                    "status": MaterialStatus.DELETED,
                    "deleted_at": datetime.now(UTC),
                }
            )
            index.materials = [
                replacement if item.id == material_id else item for item in index.materials
            ]
            self._save(index)
            return replacement
        raise KeyError(material_id)

    def _append(self, material: RunMaterial) -> None:
        index = self._load()
        index.materials.append(material)
        self._save(index)

    def _load(self) -> MaterialIndex:
        if not self.index_path.is_file():
            return MaterialIndex(run_id=self.workspace.manifest.run_id)
        index = MaterialIndex.model_validate_json(self.index_path.read_text(encoding="utf-8"))
        if index.run_id != self.workspace.manifest.run_id:
            raise ValueError("material index belongs to a different run")
        return index

    def _save(self, index: MaterialIndex) -> None:
        _atomic_write_text(
            self.index_path,
            json.dumps(index.model_dump(mode="json"), ensure_ascii=False, indent=2),
        )


def _validate_optional_range(start_us: int | None, end_us: int | None) -> None:
    if (start_us is None) != (end_us is None):
        raise ValueError("start_us and end_us must be provided together")
    if start_us is not None and end_us is not None and end_us <= start_us:
        raise ValueError("end_us must be greater than start_us")


def _detect_image(content: bytes) -> tuple[str, str]:
    try:
        with Image.open(BytesIO(content)) as image:
            image.verify()
            image_format = (image.format or "").upper()
    except (OSError, UnidentifiedImageError):
        raise ValueError("supporting file is not a valid image") from None
    mapping = {
        "JPEG": ("image/jpeg", "jpg"),
        "PNG": ("image/png", "png"),
        "WEBP": ("image/webp", "webp"),
    }
    try:
        return mapping[image_format]
    except KeyError:
        raise ValueError("only PNG, JPEG, and WebP materials are supported") from None


def _clean_title(value: str) -> str:
    cleaned = " ".join(_SAFE_TITLE.sub(" ", value).split()).strip()
    if not cleaned:
        raise ValueError("supporting material title cannot be blank")
    return cleaned[:240]


def _new_material_id() -> str:
    return f"material-{uuid.uuid4().hex[:16]}"


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def _atomic_write_text(path: Path, content: str) -> None:
    _atomic_write_bytes(path, content.encode("utf-8"))
