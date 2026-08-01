"""Provider, protocol, model capability, and processing-role configuration.

Schema v2 separates the provider's identity/deployment kind from the wire
protocol and authentication scheme.  Protocol templates describe transport
facts only; model capabilities remain explicit on :class:`ModelSpec` and are
never inferred from a provider brand or protocol.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RegistryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ProviderKind(StrEnum):
    """Provider identity/deployment family, independent from its protocol."""

    LOCAL = "local"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    OPENAI_COMPATIBLE = "openai_compatible"
    OLLAMA = "ollama"
    CUSTOM = "custom"


class Locality(StrEnum):
    LOCAL = "local"
    CLOUD = "cloud"


class ProviderProtocol(StrEnum):
    LOCAL = "local"
    OPENAI_RESPONSES = "openai_responses"
    OPENAI_CHAT_COMPLETIONS = "openai_chat_completions"
    OPENAI_AUDIO_TRANSCRIPTIONS = "openai_audio_transcriptions"
    ANTHROPIC_MESSAGES = "anthropic_messages"
    GEMINI_GENERATE_CONTENT = "gemini_generate_content"
    GEMINI_INTERACTIONS = "gemini_interactions"
    OLLAMA_NATIVE_CHAT = "ollama_native_chat"
    CUSTOM_HTTP = "custom_http"


class AuthScheme(StrEnum):
    NONE = "none"
    BEARER = "bearer"
    X_API_KEY = "x_api_key"
    X_GOOG_API_KEY = "x_goog_api_key"
    CUSTOM_HEADER = "custom_header"


class StreamTransport(StrEnum):
    NONE = "none"
    SSE = "sse"
    NDJSON = "ndjson"


@dataclass(frozen=True, slots=True)
class ProtocolTemplate:
    """Transport-level protocol metadata; deliberately contains no model IDs."""

    protocol: ProviderProtocol
    display_name: str
    default_auth_scheme: AuthScheme
    default_base_url: str | None
    request_path: str | None
    discovery_path: str | None
    request_content_type: str
    structured_generation_adapter: bool
    supports_json_schema_transport: bool
    supports_image_transport: bool
    supports_streaming_transport: bool
    stream_transport: StreamTransport


PROTOCOL_CATALOG: dict[ProviderProtocol, ProtocolTemplate] = {
    ProviderProtocol.LOCAL: ProtocolTemplate(
        protocol=ProviderProtocol.LOCAL,
        display_name="Local in-process engine",
        default_auth_scheme=AuthScheme.NONE,
        default_base_url=None,
        request_path=None,
        discovery_path=None,
        request_content_type="in-process",
        structured_generation_adapter=False,
        supports_json_schema_transport=False,
        supports_image_transport=False,
        supports_streaming_transport=False,
        stream_transport=StreamTransport.NONE,
    ),
    ProviderProtocol.OPENAI_RESPONSES: ProtocolTemplate(
        protocol=ProviderProtocol.OPENAI_RESPONSES,
        display_name="OpenAI Responses",
        default_auth_scheme=AuthScheme.BEARER,
        default_base_url="https://api.openai.com/v1",
        request_path="/responses",
        discovery_path="/models",
        request_content_type="application/json",
        structured_generation_adapter=True,
        supports_json_schema_transport=True,
        supports_image_transport=True,
        supports_streaming_transport=True,
        stream_transport=StreamTransport.SSE,
    ),
    ProviderProtocol.OPENAI_CHAT_COMPLETIONS: ProtocolTemplate(
        protocol=ProviderProtocol.OPENAI_CHAT_COMPLETIONS,
        display_name="OpenAI Chat Completions",
        default_auth_scheme=AuthScheme.BEARER,
        default_base_url="https://api.openai.com/v1",
        request_path="/chat/completions",
        discovery_path="/models",
        request_content_type="application/json",
        structured_generation_adapter=True,
        supports_json_schema_transport=True,
        supports_image_transport=True,
        supports_streaming_transport=True,
        stream_transport=StreamTransport.SSE,
    ),
    ProviderProtocol.OPENAI_AUDIO_TRANSCRIPTIONS: ProtocolTemplate(
        protocol=ProviderProtocol.OPENAI_AUDIO_TRANSCRIPTIONS,
        display_name="OpenAI Audio Transcriptions",
        default_auth_scheme=AuthScheme.BEARER,
        default_base_url="https://api.openai.com/v1",
        request_path="/audio/transcriptions",
        discovery_path="/models",
        request_content_type="multipart/form-data",
        structured_generation_adapter=False,
        supports_json_schema_transport=False,
        supports_image_transport=False,
        supports_streaming_transport=True,
        stream_transport=StreamTransport.SSE,
    ),
    ProviderProtocol.ANTHROPIC_MESSAGES: ProtocolTemplate(
        protocol=ProviderProtocol.ANTHROPIC_MESSAGES,
        display_name="Anthropic Messages",
        default_auth_scheme=AuthScheme.X_API_KEY,
        default_base_url="https://api.anthropic.com/v1",
        request_path="/messages",
        discovery_path="/models",
        request_content_type="application/json",
        structured_generation_adapter=True,
        supports_json_schema_transport=True,
        supports_image_transport=True,
        supports_streaming_transport=True,
        stream_transport=StreamTransport.SSE,
    ),
    ProviderProtocol.GEMINI_GENERATE_CONTENT: ProtocolTemplate(
        protocol=ProviderProtocol.GEMINI_GENERATE_CONTENT,
        display_name="Gemini generateContent",
        default_auth_scheme=AuthScheme.X_GOOG_API_KEY,
        default_base_url="https://generativelanguage.googleapis.com/v1beta",
        request_path="/models/{model}:generateContent",
        discovery_path="/models",
        request_content_type="application/json",
        structured_generation_adapter=True,
        supports_json_schema_transport=True,
        supports_image_transport=True,
        supports_streaming_transport=True,
        stream_transport=StreamTransport.SSE,
    ),
    ProviderProtocol.GEMINI_INTERACTIONS: ProtocolTemplate(
        protocol=ProviderProtocol.GEMINI_INTERACTIONS,
        display_name="Gemini Interactions",
        default_auth_scheme=AuthScheme.X_GOOG_API_KEY,
        default_base_url="https://generativelanguage.googleapis.com/v1beta",
        request_path="/interactions",
        discovery_path="/models",
        request_content_type="application/json",
        structured_generation_adapter=True,
        supports_json_schema_transport=True,
        supports_image_transport=True,
        supports_streaming_transport=True,
        stream_transport=StreamTransport.SSE,
    ),
    ProviderProtocol.OLLAMA_NATIVE_CHAT: ProtocolTemplate(
        protocol=ProviderProtocol.OLLAMA_NATIVE_CHAT,
        display_name="Ollama native chat",
        default_auth_scheme=AuthScheme.NONE,
        default_base_url="http://127.0.0.1:11434",
        request_path="/api/chat",
        discovery_path="/api/tags",
        request_content_type="application/json",
        structured_generation_adapter=True,
        supports_json_schema_transport=True,
        supports_image_transport=True,
        supports_streaming_transport=True,
        stream_transport=StreamTransport.NDJSON,
    ),
    ProviderProtocol.CUSTOM_HTTP: ProtocolTemplate(
        protocol=ProviderProtocol.CUSTOM_HTTP,
        display_name="Custom HTTP (experimental)",
        default_auth_scheme=AuthScheme.CUSTOM_HEADER,
        default_base_url=None,
        request_path=None,
        discovery_path=None,
        request_content_type="application/json",
        structured_generation_adapter=False,
        supports_json_schema_transport=False,
        supports_image_transport=False,
        supports_streaming_transport=False,
        stream_transport=StreamTransport.NONE,
    ),
}


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


def _enum_value(value: object) -> str | None:
    if isinstance(value, StrEnum):
        return value.value
    return value if isinstance(value, str) else None


def _legacy_protocol(kind: object, endpoint_style: object) -> ProviderProtocol:
    kind_value = _enum_value(kind)
    style_value = _enum_value(endpoint_style) or "chat_completions"
    if kind_value == ProviderKind.LOCAL.value:
        return ProviderProtocol.LOCAL
    if kind_value == ProviderKind.ANTHROPIC.value:
        return ProviderProtocol.ANTHROPIC_MESSAGES
    if kind_value == ProviderKind.GOOGLE.value:
        return ProviderProtocol.GEMINI_GENERATE_CONTENT
    if kind_value == ProviderKind.CUSTOM.value:
        return ProviderProtocol.CUSTOM_HTTP
    if kind_value == ProviderKind.OPENAI.value:
        if endpoint_style is None:
            return ProviderProtocol.OPENAI_RESPONSES
        if style_value == "chat_completions":
            return ProviderProtocol.OPENAI_CHAT_COMPLETIONS
        return ProviderProtocol.OPENAI_RESPONSES
    if style_value == "responses":
        return ProviderProtocol.OPENAI_RESPONSES
    return ProviderProtocol.OPENAI_CHAT_COMPLETIONS


def _legacy_auth(kind: object, protocol: ProviderProtocol) -> AuthScheme:
    kind_value = _enum_value(kind)
    if protocol is ProviderProtocol.LOCAL or kind_value == ProviderKind.OLLAMA.value:
        return AuthScheme.NONE
    return PROTOCOL_CATALOG[protocol].default_auth_scheme


class ProviderSpec(RegistryModel):
    id: str
    display_name: str
    kind: ProviderKind
    protocol: ProviderProtocol
    auth_scheme: AuthScheme
    base_url: str | None = None
    credential_ref: str | None = None
    # Deprecated schema-v1 compatibility field. ``protocol`` is authoritative.
    endpoint_style: Literal["responses", "chat_completions"] = "chat_completions"
    protocol_options: dict[str, Any] = Field(default_factory=dict)
    request_timeout_seconds: float = Field(default=180, gt=0)
    locality: Locality
    enabled: bool = True

    @model_validator(mode="before")
    @classmethod
    def migrate_v1_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        protocol_value = migrated.get("protocol")
        if protocol_value is None:
            protocol = _legacy_protocol(
                migrated.get("kind"),
                migrated.get("endpoint_style"),
            )
            migrated["protocol"] = protocol.value
        else:
            protocol = ProviderProtocol(protocol_value)
        if migrated.get("auth_scheme") is None:
            migrated["auth_scheme"] = _legacy_auth(
                migrated.get("kind"),
                protocol,
            ).value
        if "endpoint_style" not in migrated:
            migrated["endpoint_style"] = (
                "responses"
                if protocol is ProviderProtocol.OPENAI_RESPONSES
                else "chat_completions"
            )
        return migrated

    @model_validator(mode="after")
    def validate_endpoint(self) -> Self:
        if self.protocol is not ProviderProtocol.LOCAL and not self.base_url:
            raise ValueError(f"{self.protocol.value} provider requires base_url")
        if self.protocol is ProviderProtocol.LOCAL and self.base_url is not None:
            raise ValueError("local in-process providers cannot have a base_url")
        if self.kind is ProviderKind.LOCAL and self.credential_ref is not None:
            raise ValueError("local deterministic providers cannot have credentials")
        if self.protocol is ProviderProtocol.LOCAL and self.auth_scheme is not AuthScheme.NONE:
            raise ValueError("local in-process providers cannot use HTTP authentication")
        if (
            self.protocol is ProviderProtocol.ANTHROPIC_MESSAGES
            and self.auth_scheme is not AuthScheme.X_API_KEY
        ):
            raise ValueError("Anthropic Messages requires x_api_key authentication")
        if self.protocol in {
            ProviderProtocol.GEMINI_GENERATE_CONTENT,
            ProviderProtocol.GEMINI_INTERACTIONS,
        } and self.auth_scheme is not AuthScheme.X_GOOG_API_KEY:
            raise ValueError("Gemini protocols require x_goog_api_key authentication")
        if (
            self.protocol is ProviderProtocol.OLLAMA_NATIVE_CHAT
            and self.auth_scheme is not AuthScheme.NONE
        ):
            raise ValueError("Ollama native chat uses no HTTP authentication")
        return self


class ModelSpec(RegistryModel):
    id: str
    provider_id: str
    model_id: str
    display_name: str
    capabilities: set[Capability]
    locality: Locality
    context_window: int | None = Field(default=None, gt=0)
    settings: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class RoleBinding(RegistryModel):
    role: str
    primary_model_id: str
    fallback_model_ids: list[str] = Field(default_factory=list)
    escalation_rule: str | None = None


class ModelRegistry(RegistryModel):
    schema_version: Literal[2] = 2
    providers: dict[str, ProviderSpec] = Field(default_factory=dict)
    models: dict[str, ModelSpec] = Field(default_factory=dict)
    roles: dict[str, RoleBinding] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def migrate_v1_registry(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        version = migrated.get("schema_version", 1)
        if version == 1:
            migrated["schema_version"] = 2
        return migrated

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
            protocol=ProviderProtocol.LOCAL,
            auth_scheme=AuthScheme.NONE,
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
