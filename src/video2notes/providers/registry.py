"""Provider -> model capability -> processing role configuration."""

from __future__ import annotations

import json
import os
import uuid
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RegistryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ProviderKind(StrEnum):
    LOCAL = "local"
    OPENAI_COMPATIBLE = "openai_compatible"
    OLLAMA = "ollama"


class Locality(StrEnum):
    LOCAL = "local"
    CLOUD = "cloud"


class Capability(StrEnum):
    TEXT = "text"
    VISION = "vision"
    STRUCTURED_OUTPUT = "structured_output"
    LONG_CONTEXT = "long_context"
    EMBEDDINGS = "embeddings"
    ASR = "asr"
    LANGUAGE_ID = "language_id"
    SEGMENT_TIMESTAMPS = "segment_timestamps"
    WORD_TIMESTAMPS = "word_timestamps"
    WORD_CONFIDENCE = "word_confidence"
    OCR = "ocr"
    OCR_BOXES = "ocr_boxes"
    OCR_CONFIDENCE = "ocr_confidence"
    VIDEO_FRAME_METRICS = "video_frame_metrics"


ROLE_REQUIREMENTS: dict[str, frozenset[Capability]] = {
    "vision.change_detector": frozenset({Capability.VIDEO_FRAME_METRICS}),
    "vision.text_detector": frozenset({Capability.OCR, Capability.OCR_BOXES}),
    "vision.frame_explainer": frozenset({Capability.TEXT, Capability.VISION}),
    "ocr.primary": frozenset({Capability.OCR, Capability.OCR_BOXES}),
    "ocr.escalation": frozenset({Capability.VISION, Capability.TEXT}),
    "asr.primary": frozenset({Capability.ASR, Capability.SEGMENT_TIMESTAMPS}),
    "asr.secondary": frozenset({Capability.ASR, Capability.SEGMENT_TIMESTAMPS}),
    "asr.adjudicator": frozenset({Capability.TEXT}),
    "notes.fact_extractor": frozenset({Capability.TEXT, Capability.STRUCTURED_OUTPUT}),
    "notes.drafter": frozenset({Capability.TEXT, Capability.LONG_CONTEXT}),
    "notes.verifier": frozenset({Capability.TEXT, Capability.STRUCTURED_OUTPUT}),
    "translation": frozenset({Capability.TEXT}),
}


class ProviderSpec(RegistryModel):
    id: str
    display_name: str
    kind: ProviderKind
    base_url: str | None = None
    credential_ref: str | None = None
    locality: Locality
    enabled: bool = True

    @model_validator(mode="after")
    def validate_endpoint(self) -> Self:
        if self.kind in {ProviderKind.OPENAI_COMPATIBLE, ProviderKind.OLLAMA} and not self.base_url:
            raise ValueError(f"{self.kind.value} provider requires base_url")
        if self.kind is ProviderKind.LOCAL and self.credential_ref is not None:
            raise ValueError("local deterministic providers cannot have credentials")
        return self


class ModelSpec(RegistryModel):
    id: str
    provider_id: str
    model_id: str
    display_name: str
    capabilities: set[Capability]
    locality: Locality
    context_window: int | None = Field(default=None, gt=0)
    enabled: bool = True


class RoleBinding(RegistryModel):
    role: str
    primary_model_id: str
    fallback_model_ids: list[str] = Field(default_factory=list)
    escalation_rule: str | None = None


class ModelRegistry(RegistryModel):
    schema_version: int = 1
    providers: dict[str, ProviderSpec] = Field(default_factory=dict)
    models: dict[str, ModelSpec] = Field(default_factory=dict)
    roles: dict[str, RoleBinding] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_references_and_capabilities(self) -> Self:
        for provider_id, provider in self.providers.items():
            if provider.id != provider_id:
                raise ValueError(f"provider key '{provider_id}' does not match provider.id")

        for model_id, model in self.models.items():
            if model.id != model_id:
                raise ValueError(f"model key '{model_id}' does not match model.id")
            if model.provider_id not in self.providers:
                raise ValueError(
                    f"model '{model_id}' references unknown provider '{model.provider_id}'"
                )

        for role, binding in self.roles.items():
            if role != binding.role:
                raise ValueError(f"role key '{role}' does not match binding.role")
            self._validate_binding(binding)
        return self

    def bind(
        self,
        role: str,
        primary_model_id: str,
        *,
        fallback_model_ids: list[str] | None = None,
        escalation_rule: str | None = None,
    ) -> None:
        binding = RoleBinding(
            role=role,
            primary_model_id=primary_model_id,
            fallback_model_ids=fallback_model_ids or [],
            escalation_rule=escalation_rule,
        )
        self._validate_binding(binding)
        self.roles[role] = binding

    def compatible_models(self, role: str) -> list[ModelSpec]:
        required = ROLE_REQUIREMENTS.get(role)
        if required is None:
            raise ValueError(f"unknown processing role: {role}")
        return [
            model
            for model in self.models.values()
            if model.enabled
            and required.issubset(model.capabilities)
            and self.providers[model.provider_id].enabled
        ]

    def save(self, path: str | Path) -> Path:
        destination = Path(path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, destination)
        return destination

    @classmethod
    def load(cls, path: str | Path) -> Self:
        source = Path(path).expanduser().resolve()
        return cls.model_validate_json(source.read_text(encoding="utf-8"))

    @classmethod
    def with_local_defaults(cls) -> Self:
        local = ProviderSpec(
            id="local",
            display_name="Local engines",
            kind=ProviderKind.LOCAL,
            locality=Locality.LOCAL,
        )
        models = {
            "adaptive-scan": ModelSpec(
                id="adaptive-scan",
                provider_id="local",
                model_id="deterministic-adaptive-scan-v2",
                display_name="Adaptive visual scanner",
                capabilities={Capability.VIDEO_FRAME_METRICS},
                locality=Locality.LOCAL,
            ),
            "paddleocr": ModelSpec(
                id="paddleocr",
                provider_id="local",
                model_id="paddleocr",
                display_name="PaddleOCR",
                capabilities={
                    Capability.OCR,
                    Capability.OCR_BOXES,
                    Capability.OCR_CONFIDENCE,
                },
                locality=Locality.LOCAL,
            ),
            "faster-whisper": ModelSpec(
                id="faster-whisper",
                provider_id="local",
                model_id="faster-whisper",
                display_name="faster-whisper",
                capabilities={
                    Capability.ASR,
                    Capability.LANGUAGE_ID,
                    Capability.SEGMENT_TIMESTAMPS,
                    Capability.WORD_TIMESTAMPS,
                    Capability.WORD_CONFIDENCE,
                },
                locality=Locality.LOCAL,
            ),
        }
        registry = cls(
            providers={"local": local},
            models=models,
        )
        registry.bind("vision.change_detector", "adaptive-scan")
        registry.bind("vision.text_detector", "paddleocr")
        registry.bind("ocr.primary", "paddleocr")
        registry.bind("asr.primary", "faster-whisper")
        return registry

    def _validate_binding(self, binding: RoleBinding) -> None:
        required = ROLE_REQUIREMENTS.get(binding.role)
        if required is None:
            raise ValueError(f"unknown processing role: {binding.role}")
        model_ids = [binding.primary_model_id, *binding.fallback_model_ids]
        for model_id in model_ids:
            model = self.models.get(model_id)
            if model is None:
                raise ValueError(f"role '{binding.role}' references unknown model '{model_id}'")
            missing = required - model.capabilities
            if missing:
                missing_names = ", ".join(sorted(item.value for item in missing))
                raise ValueError(
                    f"model '{model_id}' cannot serve role '{binding.role}'; "
                    f"missing capabilities: {missing_names}"
                )
