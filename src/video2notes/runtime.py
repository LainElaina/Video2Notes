"""Resolve persisted model-role configuration into lazy pipeline backends.

The registry is safe to serialize because it contains only credential
references. Secret values are fetched from the OS credential store while the
in-memory backend is built and are never copied into a Pydantic model.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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
    AuthScheme,
    ModelRegistry,
    ModelSpec,
    ProviderAuthError,
    ProviderKind,
    ProviderProtocol,
    ProviderSpec,
    provider_auth_headers,
)
from video2notes.sources import SourceRegistry
from video2notes.system import (
    AccelerationCapabilities,
    ExperienceMode,
    HardwareSnapshot,
    PerformanceOverrides,
    QualityMode,
    ResourcePreference,
    ResourceReserve,
)

_QUALITY_PROFILES_KEY = "quality_profiles"


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
    hardware_disk_path: str | None = None,
    experience_mode: ExperienceMode = ExperienceMode.GUIDED,
    resource_preference: ResourcePreference = ResourcePreference.BALANCED,
    resource_reserve: ResourceReserve | None = None,
    performance_overrides: PerformanceOverrides | None = None,
    acceleration_capabilities: AccelerationCapabilities | None = None,
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
    asr, asr_profiles = _resolve_asr(registry, "asr.primary", warnings)
    secondary_asr, secondary_asr_profiles = _resolve_asr(
        registry,
        "asr.secondary",
        warnings,
    )
    ocr, ocr_profiles = _resolve_ocr(registry, warnings)
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
            asr_backends_by_quality=asr_profiles,
            secondary_asr_backends_by_quality=secondary_asr_profiles,
            ocr_backends_by_quality=ocr_profiles,
            hardware=hardware,
            hardware_disk_path=hardware_disk_path,
            experience_mode=experience_mode,
            resource_preference=resource_preference,
            resource_reserve=resource_reserve,
            performance_overrides=performance_overrides,
            acceleration_capabilities=acceleration_capabilities,
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
) -> tuple[FasterWhisperBackend | None, dict[QualityMode, FasterWhisperBackend]]:
    for model in _role_models(registry, role, warnings):
        provider = registry.providers[model.provider_id]
        if provider.kind is not ProviderKind.LOCAL:
            warnings.append(
                f"{role}: '{model.id}' is not supported by the local faster-whisper adapter."
            )
            continue

        base_settings = dict(model.settings)
        raw_profiles = base_settings.pop(_QUALITY_PROFILES_KEY, None)
        backend = _build_asr_backend(
            model,
            base_settings,
            label=role,
            warnings=warnings,
        )
        if backend is None:
            continue
        profiles = _resolve_asr_profiles(
            model,
            base_settings,
            raw_profiles,
            backend=backend,
            role=role,
            warnings=warnings,
        )
        return backend, profiles
    return None, {}


def _build_asr_backend(
    model: ModelSpec,
    settings: Mapping[str, object],
    *,
    label: str,
    warnings: list[str],
) -> FasterWhisperBackend | None:
    engine = str(settings.get("engine", "")).strip().lower()
    if not engine and "whisper" in model.model_id.lower():
        engine = "faster_whisper"
    if engine not in {"faster_whisper", "faster-whisper"}:
        warnings.append(
            f"{label}: '{model.id}' is not supported by the local faster-whisper adapter."
        )
        return None
    payload = dict(settings)
    payload.pop("engine", None)
    payload.pop(_QUALITY_PROFILES_KEY, None)
    if not str(payload.get("model_path", "")).strip():
        warnings.append(
            f"{label}: '{model.id}' needs settings.model_path pointing "
            "to an existing local faster-whisper model."
        )
        return None
    try:
        return FasterWhisperBackend(FasterWhisperConfig.model_validate(payload))
    except ValidationError:
        warnings.append(f"{label}: '{model.id}' has invalid runtime settings.")
        return None


def _resolve_asr_profiles(
    model: ModelSpec,
    base_settings: Mapping[str, object],
    raw_profiles: object,
    *,
    backend: FasterWhisperBackend,
    role: str,
    warnings: list[str],
) -> dict[QualityMode, FasterWhisperBackend]:
    if raw_profiles is None:
        return {}
    if not isinstance(raw_profiles, Mapping):
        warnings.append(f"{role}: '{model.id}' quality_profiles must be an object.")
        return {mode: backend for mode in QualityMode}

    resolved: dict[QualityMode, FasterWhisperBackend] = {}
    for mode in QualityMode:
        raw_profile = raw_profiles.get(mode.value, raw_profiles.get(mode))
        if raw_profile is None:
            resolved[mode] = backend
            continue
        if not isinstance(raw_profile, Mapping):
            warnings.append(
                f"{role}.{mode.value}: '{model.id}' profile settings must be an object."
            )
            resolved[mode] = backend
            continue
        profile_settings = {**base_settings, **dict(raw_profile)}
        profile_backend = _build_asr_backend(
            model,
            profile_settings,
            label=f"{role}.{mode.value}",
            warnings=warnings,
        )
        resolved[mode] = profile_backend or backend
    return resolved


def _resolve_ocr(
    registry: ModelRegistry,
    warnings: list[str],
) -> tuple[PaddleOcrBackend | None, dict[QualityMode, PaddleOcrBackend]]:
    for model in _role_models(registry, "ocr.primary", warnings):
        provider = registry.providers[model.provider_id]
        if provider.kind is not ProviderKind.LOCAL:
            warnings.append(
                f"ocr.primary: '{model.id}' is not supported by the local PaddleOCR adapter."
            )
            continue

        base_settings = dict(model.settings)
        raw_profiles = base_settings.pop(_QUALITY_PROFILES_KEY, None)
        backend = _build_ocr_backend(
            model,
            base_settings,
            label="ocr.primary",
            warnings=warnings,
        )
        if backend is None:
            continue
        profiles = _resolve_ocr_profiles(
            model,
            base_settings,
            raw_profiles,
            backend=backend,
            warnings=warnings,
        )
        return backend, profiles
    return None, {}


def _build_ocr_backend(
    model: ModelSpec,
    settings: Mapping[str, object],
    *,
    label: str,
    warnings: list[str],
) -> PaddleOcrBackend | None:
    engine = str(settings.get("engine", "")).strip().lower()
    if not engine and "paddle" in model.model_id.lower():
        engine = "paddleocr"
    if engine not in {"paddleocr", "paddle_ocr"}:
        warnings.append(
            f"{label}: '{model.id}' is not supported by the local PaddleOCR adapter."
        )
        return None
    payload = dict(settings)
    payload.pop("engine", None)
    payload.pop(_QUALITY_PROFILES_KEY, None)
    required = ("detection_model_dir", "recognition_model_dir")
    if any(not str(payload.get(name, "")).strip() for name in required):
        warnings.append(
            f"{label}: '{model.id}' needs local detector and recognizer model directories."
        )
        return None
    try:
        return PaddleOcrBackend(PaddleOcrConfig.model_validate(payload))
    except ValidationError:
        warnings.append(f"{label}: '{model.id}' has invalid runtime settings.")
        return None


def _resolve_ocr_profiles(
    model: ModelSpec,
    base_settings: Mapping[str, object],
    raw_profiles: object,
    *,
    backend: PaddleOcrBackend,
    warnings: list[str],
) -> dict[QualityMode, PaddleOcrBackend]:
    role = "ocr.primary"
    if raw_profiles is None:
        return {}
    if not isinstance(raw_profiles, Mapping):
        warnings.append(f"{role}: '{model.id}' quality_profiles must be an object.")
        return {mode: backend for mode in QualityMode}

    resolved: dict[QualityMode, PaddleOcrBackend] = {}
    for mode in QualityMode:
        raw_profile = raw_profiles.get(mode.value, raw_profiles.get(mode))
        if raw_profile is None:
            resolved[mode] = backend
            continue
        if not isinstance(raw_profile, Mapping):
            warnings.append(
                f"{role}.{mode.value}: '{model.id}' profile settings must be an object."
            )
            resolved[mode] = backend
            continue
        profile_settings = {**base_settings, **dict(raw_profile)}
        profile_backend = _build_ocr_backend(
            model,
            profile_settings,
            label=f"{role}.{mode.value}",
            warnings=warnings,
        )
        resolved[mode] = profile_backend or backend
    return resolved


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
    if provider.auth_scheme is not AuthScheme.NONE and provider.credential_ref is None:
        warnings.append(f"{role}: credential for provider '{provider.id}' is not configured.")
        return None
    if provider.credential_ref is not None:
        api_key = secret_store.get(provider.id) if secret_store is not None else None
        if api_key is None:
            warnings.append(f"{role}: credential for provider '{provider.id}' is not available.")
            return None
    if provider.protocol is ProviderProtocol.OPENAI_RESPONSES:
        try:
            auth_headers = provider_auth_headers(provider, api_key)
        except ProviderAuthError:
            warnings.append(f"{role}: provider '{provider.id}' has invalid authentication.")
            return None
        return OpenAIResponsesBackend(
            provider_id=provider.id,
            model_id=model.model_id,
            base_url=provider.base_url,
            auth_headers=auth_headers,
            timeout_seconds=provider.request_timeout_seconds,
        )
    if provider.protocol is ProviderProtocol.OPENAI_CHAT_COMPLETIONS:
        try:
            auth_headers = provider_auth_headers(provider, api_key)
        except ProviderAuthError:
            warnings.append(f"{role}: provider '{provider.id}' has invalid authentication.")
            return None
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
            auth_headers=auth_headers,
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
