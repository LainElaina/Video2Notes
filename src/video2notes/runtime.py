"""Resolve persisted model-role configuration into lazy pipeline backends.

The registry is safe to serialize because it contains only credential
references. Secret values are fetched from the OS credential store while the
in-memory backend is built and are never copied into a Pydantic model.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from video2notes.audio import FasterWhisperBackend, FasterWhisperConfig
from video2notes.llm import (
    AnthropicMessagesBackend,
    GeminiGenerateContentBackend,
    GeminiInteractionsBackend,
    GenerationError,
    GenerationRequest,
    GenerationResult,
    OllamaNativeChatBackend,
    OpenAIChatCompletionsBackend,
    OpenAIResponsesBackend,
    StructuredGenerationBackend,
)
from video2notes.notes import EvidenceNoteComposer
from video2notes.ocr import PaddleOcrBackend, PaddleOcrConfig
from video2notes.pipeline import PipelineRuntime
from video2notes.providers import (
    ModelRegistry,
    ModelSpec,
    ProviderKind,
    ProviderProtocol,
    ProviderSpec,
)
from video2notes.sources import SourceRegistry
from video2notes.system import HardwareSnapshot


class SecretReader(Protocol):
    def get(self, provider_id: str) -> str | None: ...


class RuntimeConfigurationError(RuntimeError):
    """A selected backend cannot be represented by the installed adapters."""


class FallbackStructuredBackend:
    """Try explicitly configured structured-output models in registry order."""

    def __init__(self, backends: Sequence[StructuredGenerationBackend]):
        if not backends:
            raise ValueError("at least one structured backend is required")
        self.backends = tuple(backends)
        self.provider_id = "|".join(item.provider_id for item in self.backends)
        self.model_id = "|".join(item.model_id for item in self.backends)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        last_error: GenerationError | None = None
        for backend in self.backends:
            try:
                return backend.generate(request)
            except GenerationError as error:
                last_error = error
        if last_error is None:  # pragma: no cover - protected by constructor
            raise GenerationError("no structured generation backend is available")
        raise GenerationError(
            f"all {len(self.backends)} configured backends failed for {request.role}"
        ) from None


@dataclass(frozen=True, slots=True)
class RuntimeBuildResult:
    runtime: PipelineRuntime
    warnings: tuple[str, ...]


def build_pipeline_runtime(
    registry: ModelRegistry,
    *,
    secret_store: SecretReader | None = None,
    source_registry: SourceRegistry | None = None,
    hardware: HardwareSnapshot | None = None,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    pdf_browser_executable: str | None = None,
) -> RuntimeBuildResult:
    """Build lazy local engines and role-specific LLM clients.

    Missing optional model paths or credentials produce diagnostics and leave
    that role disabled. The pipeline can therefore still create a conservative
    extractive note from captions and metadata.
    """

    warnings: list[str] = []
    asr = _resolve_asr(registry, "asr.primary", warnings)
    secondary_asr = _resolve_asr(registry, "asr.secondary", warnings)
    ocr = _resolve_ocr(registry, warnings)
    fact = _resolve_structured_role(
        registry,
        "notes.fact_extractor",
        secret_store,
        warnings,
    )
    draft = _resolve_structured_role(
        registry,
        "notes.drafter",
        secret_store,
        warnings,
    )
    verifier = _resolve_structured_role(
        registry,
        "notes.verifier",
        secret_store,
        warnings,
    )
    if (fact is None) != (draft is None):
        warnings.append(
            "Both notes.fact_extractor and notes.drafter must be usable; "
            "structured note generation was disabled."
        )
        fact = None
        draft = None
        verifier = None

    return RuntimeBuildResult(
        runtime=PipelineRuntime(
            source_registry=source_registry or SourceRegistry.default(),
            note_composer=EvidenceNoteComposer(
                fact_backend=fact,
                draft_backend=draft,
                verifier_backend=verifier,
            ),
            asr_backend=asr,
            secondary_asr_backend=secondary_asr,
            ocr_backend=ocr,
            hardware=hardware,
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
            pdf_browser_executable=pdf_browser_executable,
        ),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _role_models(
    registry: ModelRegistry,
    role: str,
    warnings: list[str],
) -> list[ModelSpec]:
    binding = registry.roles.get(role)
    if binding is None:
        return []
    resolved: list[ModelSpec] = []
    for model_id in [binding.primary_model_id, *binding.fallback_model_ids]:
        model = registry.models[model_id]
        provider = registry.providers[model.provider_id]
        if not model.enabled:
            warnings.append(f"{role}: model '{model.id}' is disabled.")
            continue
        if not provider.enabled:
            warnings.append(f"{role}: provider '{provider.id}' is disabled.")
            continue
        resolved.append(model)
    return resolved


def _resolve_asr(
    registry: ModelRegistry,
    role: str,
    warnings: list[str],
) -> FasterWhisperBackend | None:
    for model in _role_models(registry, role, warnings):
        provider = registry.providers[model.provider_id]
        engine = str(model.settings.get("engine", "")).strip().lower()
        if not engine and "whisper" in model.model_id.lower():
            engine = "faster_whisper"
        if provider.kind is not ProviderKind.LOCAL or engine not in {
            "faster_whisper",
            "faster-whisper",
        }:
            warnings.append(
                f"{role}: '{model.id}' is not supported by the local faster-whisper adapter."
            )
            continue
        payload = dict(model.settings)
        payload.pop("engine", None)
        if not str(payload.get("model_path", "")).strip():
            warnings.append(
                f"{role}: '{model.id}' needs settings.model_path pointing "
                "to an existing local faster-whisper model."
            )
            continue
        try:
            return FasterWhisperBackend(FasterWhisperConfig.model_validate(payload))
        except ValidationError:
            warnings.append(f"{role}: '{model.id}' has invalid runtime settings.")
    return None


def _resolve_ocr(
    registry: ModelRegistry,
    warnings: list[str],
) -> PaddleOcrBackend | None:
    for model in _role_models(registry, "ocr.primary", warnings):
        provider = registry.providers[model.provider_id]
        engine = str(model.settings.get("engine", "")).strip().lower()
        if not engine and "paddle" in model.model_id.lower():
            engine = "paddleocr"
        if provider.kind is not ProviderKind.LOCAL or engine not in {
            "paddleocr",
            "paddle_ocr",
        }:
            warnings.append(
                f"ocr.primary: '{model.id}' is not supported by the local PaddleOCR adapter."
            )
            continue
        payload = dict(model.settings)
        payload.pop("engine", None)
        required = ("detection_model_dir", "recognition_model_dir")
        if any(not str(payload.get(name, "")).strip() for name in required):
            warnings.append(
                f"ocr.primary: '{model.id}' needs local detector and recognizer model directories."
            )
            continue
        try:
            return PaddleOcrBackend(PaddleOcrConfig.model_validate(payload))
        except ValidationError:
            warnings.append(f"ocr.primary: '{model.id}' has invalid runtime settings.")
    return None


def _resolve_structured_role(
    registry: ModelRegistry,
    role: str,
    secret_store: SecretReader | None,
    warnings: list[str],
) -> StructuredGenerationBackend | None:
    backends: list[StructuredGenerationBackend] = []
    for model in _role_models(registry, role, warnings):
        provider = registry.providers[model.provider_id]
        backend = _structured_backend(provider, model, secret_store, role, warnings)
        if backend is not None:
            backends.append(backend)
    if not backends:
        return None
    if len(backends) == 1:
        return backends[0]
    return FallbackStructuredBackend(backends)


def _structured_backend(
    provider: ProviderSpec,
    model: ModelSpec,
    secret_store: SecretReader | None,
    role: str,
    warnings: list[str],
) -> StructuredGenerationBackend | None:
    if provider.protocol is ProviderProtocol.LOCAL:
        warnings.append(
            f"{role}: local text model '{model.id}' has no configured HTTP generation adapter."
        )
        return None
    if provider.base_url is None:  # pragma: no cover - ProviderSpec validates this
        raise RuntimeConfigurationError(f"provider '{provider.id}' has no base URL")

    api_key: str | None = None
    if provider.credential_ref is not None:
        api_key = secret_store.get(provider.id) if secret_store is not None else None
        if api_key is None:
            warnings.append(f"{role}: credential for provider '{provider.id}' is not available.")
            return None
    if provider.protocol is ProviderProtocol.OPENAI_RESPONSES:
        return OpenAIResponsesBackend(
            provider_id=provider.id,
            model_id=model.model_id,
            base_url=provider.base_url,
            api_key=api_key,
            timeout_seconds=provider.request_timeout_seconds,
        )
    if provider.protocol is ProviderProtocol.OPENAI_CHAT_COMPLETIONS:
        legacy_max_tokens = bool(
            model.settings.get(
                "legacy_max_tokens",
                provider.protocol_options.get("legacy_max_tokens", False),
            )
        )
        return OpenAIChatCompletionsBackend(
            provider_id=provider.id,
            model_id=model.model_id,
            base_url=provider.base_url,
            api_key=api_key,
            legacy_max_tokens=legacy_max_tokens,
            timeout_seconds=provider.request_timeout_seconds,
        )
    if provider.protocol is ProviderProtocol.ANTHROPIC_MESSAGES:
        if api_key is None:
            warnings.append(f"{role}: credential for provider '{provider.id}' is not configured.")
            return None
        anthropic_version = str(
            provider.protocol_options.get("anthropic_version", "2023-06-01")
        ).strip()
        if not anthropic_version:
            warnings.append(f"{role}: provider '{provider.id}' has an invalid Anthropic version.")
            return None
        return AnthropicMessagesBackend(
            provider_id=provider.id,
            model_id=model.model_id,
            base_url=provider.base_url,
            api_key=api_key,
            anthropic_version=anthropic_version,
            timeout_seconds=provider.request_timeout_seconds,
        )
    if provider.protocol is ProviderProtocol.GEMINI_GENERATE_CONTENT:
        if api_key is None:
            warnings.append(f"{role}: credential for provider '{provider.id}' is not configured.")
            return None
        return GeminiGenerateContentBackend(
            provider_id=provider.id,
            model_id=model.model_id,
            base_url=provider.base_url,
            api_key=api_key,
            timeout_seconds=provider.request_timeout_seconds,
        )
    if provider.protocol is ProviderProtocol.GEMINI_INTERACTIONS:
        if api_key is None:
            warnings.append(f"{role}: credential for provider '{provider.id}' is not configured.")
            return None
        return GeminiInteractionsBackend(
            provider_id=provider.id,
            model_id=model.model_id,
            base_url=provider.base_url,
            api_key=api_key,
            timeout_seconds=provider.request_timeout_seconds,
        )
    if provider.protocol is ProviderProtocol.OLLAMA_NATIVE_CHAT:
        return OllamaNativeChatBackend(
            provider_id=provider.id,
            model_id=model.model_id,
            base_url=provider.base_url,
            timeout_seconds=provider.request_timeout_seconds,
        )

    warnings.append(
        f"{role}: protocol '{provider.protocol.value}' has no structured-generation adapter."
    )
    return None
