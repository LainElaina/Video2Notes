from __future__ import annotations

import unittest

from video2notes.audio import FasterWhisperBackend
from video2notes.llm import (
    AnthropicMessagesBackend,
    EndpointStyle,
    GeminiGenerateContentBackend,
    GeminiInteractionsBackend,
    OllamaNativeChatBackend,
    OpenAIChatCompletionsBackend,
    OpenAICompatibleBackend,
)
from video2notes.ocr import PaddleOcrBackend
from video2notes.providers import (
    AuthScheme,
    Capability,
    Locality,
    ModelRegistry,
    ModelSpec,
    ProviderKind,
    ProviderProtocol,
    ProviderSpec,
)
from video2notes.runtime import FallbackStructuredBackend, build_pipeline_runtime
from video2notes.system import QualityMode


class SecretFixture:
    def __init__(self, values: dict[str, str] | None = None):
        self.values = values or {}

    def get(self, provider_id: str) -> str | None:
        return self.values.get(provider_id)


class RuntimeConfigurationTests(unittest.TestCase):
    def test_structured_runtime_dispatches_by_protocol(self) -> None:
        cases = (
            (
                "openai-chat",
                ProviderKind.OPENAI,
                ProviderProtocol.OPENAI_CHAT_COMPLETIONS,
                AuthScheme.BEARER,
                "https://api.openai.test/v1",
                OpenAIChatCompletionsBackend,
                True,
            ),
            (
                "anthropic",
                ProviderKind.ANTHROPIC,
                ProviderProtocol.ANTHROPIC_MESSAGES,
                AuthScheme.X_API_KEY,
                "https://api.anthropic.test/v1",
                AnthropicMessagesBackend,
                False,
            ),
            (
                "gemini-content",
                ProviderKind.GOOGLE,
                ProviderProtocol.GEMINI_GENERATE_CONTENT,
                AuthScheme.X_GOOG_API_KEY,
                "https://generativelanguage.test/v1beta",
                GeminiGenerateContentBackend,
                False,
            ),
            (
                "gemini-interactions",
                ProviderKind.GOOGLE,
                ProviderProtocol.GEMINI_INTERACTIONS,
                AuthScheme.X_GOOG_API_KEY,
                "https://generativelanguage.test/v1beta",
                GeminiInteractionsBackend,
                False,
            ),
            (
                "ollama-native",
                ProviderKind.OLLAMA,
                ProviderProtocol.OLLAMA_NATIVE_CHAT,
                AuthScheme.NONE,
                "http://127.0.0.1:11434",
                OllamaNativeChatBackend,
                False,
            ),
        )
        for (
            provider_id,
            kind,
            protocol,
            auth_scheme,
            base_url,
            backend_type,
            legacy_max_tokens,
        ) in cases:
            with self.subTest(protocol=protocol):
                registry = ModelRegistry.with_local_defaults()
                credential_ref = None
                secrets: dict[str, str] = {}
                if auth_scheme is not AuthScheme.NONE:
                    credential_ref = f"keyring://Video2Notes/providers/{provider_id}"
                    secrets[provider_id] = "private-key"
                registry.providers[provider_id] = ProviderSpec(
                    id=provider_id,
                    display_name=provider_id,
                    kind=kind,
                    protocol=protocol,
                    auth_scheme=auth_scheme,
                    base_url=base_url,
                    credential_ref=credential_ref,
                    locality=(
                        Locality.LOCAL
                        if protocol is ProviderProtocol.OLLAMA_NATIVE_CHAT
                        else Locality.CLOUD
                    ),
                )
                model = ModelSpec(
                    id=f"{provider_id}-model",
                    provider_id=provider_id,
                    model_id="user-selected-model",
                    display_name="User selected model",
                    capabilities={
                        Capability.TEXT,
                        Capability.STRUCTURED_OUTPUT,
                        Capability.LONG_CONTEXT,
                    },
                    locality=registry.providers[provider_id].locality,
                    settings={"legacy_max_tokens": legacy_max_tokens},
                )
                registry.models[model.id] = model
                registry.bind("notes.fact_extractor", model.id)
                registry.bind("notes.drafter", model.id)

                result = build_pipeline_runtime(
                    registry,
                    secret_store=SecretFixture(secrets),
                )

                backend = result.runtime.note_composer.fact_backend
                self.assertIsInstance(backend, backend_type)
                if isinstance(backend, OpenAIChatCompletionsBackend):
                    self.assertTrue(backend.legacy_max_tokens)
                self.assertFalse(any(item.startswith("notes.") for item in result.warnings))

    def test_unconfigured_local_defaults_degrade_explicitly(self) -> None:
        result = build_pipeline_runtime(ModelRegistry.with_local_defaults())

        self.assertIsNone(result.runtime.asr_backend)
        self.assertIsNone(result.runtime.ocr_backend)
        self.assertTrue(any("model_path" in item for item in result.warnings))
        self.assertTrue(any("detector" in item for item in result.warnings))

    def test_local_asr_and_ocr_settings_build_lazy_backends(self) -> None:
        registry = ModelRegistry.with_local_defaults()
        registry.models["faster-whisper"].settings = {
            "engine": "faster_whisper",
            "model_path": "D:/models/whisper-large-v3",
            "device": "cuda",
            "compute_type": "float16",
        }
        registry.models["paddleocr"].settings = {
            "engine": "paddleocr",
            "detection_model_dir": "D:/models/paddle/det",
            "recognition_model_dir": "D:/models/paddle/rec",
            "device": "gpu:0",
        }

        result = build_pipeline_runtime(registry)

        self.assertIsInstance(result.runtime.asr_backend, FasterWhisperBackend)
        self.assertIsInstance(result.runtime.ocr_backend, PaddleOcrBackend)
        self.assertEqual(result.runtime.asr_backends_by_quality, {})
        self.assertEqual(result.runtime.ocr_backends_by_quality, {})
        self.assertEqual(result.warnings, ())

    def test_quality_profiles_build_distinct_lazy_backends_and_strip_extensions(
        self,
    ) -> None:
        registry = ModelRegistry.with_local_defaults()
        registry.models["faster-whisper"].settings = {
            "engine": "faster_whisper",
            "model_path": "D:/models/whisper-primary",
            "device": "cuda",
            "compute_type": "float16",
            "quality_profiles": {
                "fast": {
                    "engine": "faster_whisper",
                    "model_path": "D:/models/whisper-primary",
                    "device": "cuda",
                    "compute_type": "float16",
                },
                "accurate": {
                    "engine": "faster_whisper",
                    "model_path": "D:/models/whisper-accurate",
                    "device": "cuda",
                    "compute_type": "float16",
                },
            },
        }
        registry.models["paddleocr"].settings = {
            "engine": "paddleocr",
            "detection_model_dir": "D:/models/paddle/mobile-det",
            "recognition_model_dir": "D:/models/paddle/mobile-rec",
            "device": "gpu:0",
            "quality_profiles": {
                "accurate": {
                    "engine": "paddleocr",
                    "detection_model_dir": "D:/models/paddle/server-det",
                    "recognition_model_dir": "D:/models/paddle/server-rec",
                    "device": "gpu:0",
                }
            },
        }

        result = build_pipeline_runtime(registry)

        self.assertEqual(set(result.runtime.asr_backends_by_quality), set(QualityMode))
        self.assertEqual(set(result.runtime.ocr_backends_by_quality), set(QualityMode))
        self.assertEqual(
            result.runtime.asr_backends_by_quality[QualityMode.ACCURATE].config.model_path,
            "D:/models/whisper-accurate",
        )
        self.assertEqual(
            result.runtime.asr_backends_by_quality[QualityMode.BALANCED].config.model_path,
            "D:/models/whisper-primary",
        )
        self.assertEqual(
            result.runtime.ocr_backends_by_quality[
                QualityMode.ACCURATE
            ].config.detection_model_dir,
            "D:/models/paddle/server-det",
        )
        self.assertEqual(
            result.runtime.ocr_backends_by_quality[
                QualityMode.FAST
            ].config.detection_model_dir,
            "D:/models/paddle/mobile-det",
        )
        self.assertEqual(result.warnings, ())

    def test_invalid_quality_profile_falls_back_to_actual_primary_backend(self) -> None:
        registry = ModelRegistry.with_local_defaults()
        registry.models["faster-whisper"].settings = {
            "engine": "faster_whisper",
            "model_path": "D:/models/whisper-primary",
            "quality_profiles": {
                "fast": {"beam_size": 0},
            },
        }

        result = build_pipeline_runtime(registry)

        primary = result.runtime.asr_backend
        fast = result.runtime.asr_backends_by_quality[QualityMode.FAST]
        self.assertIsInstance(primary, FasterWhisperBackend)
        self.assertIsInstance(fast, FasterWhisperBackend)
        assert isinstance(primary, FasterWhisperBackend)
        self.assertEqual(fast.config, primary.config)
        self.assertTrue(any("asr.primary.fast" in item for item in result.warnings))

    def test_note_roles_resolve_provider_style_secret_and_fallback(self) -> None:
        registry = ModelRegistry.with_local_defaults()
        registry.providers["cloud"] = ProviderSpec(
            id="cloud",
            display_name="Cloud",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://example.invalid/v1",
            credential_ref="keyring://Video2Notes/providers/cloud",
            endpoint_style="responses",
            locality=Locality.CLOUD,
        )
        registry.providers["local-api"] = ProviderSpec(
            id="local-api",
            display_name="Local API",
            kind=ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434/v1",
            locality=Locality.LOCAL,
        )
        capabilities = {
            Capability.TEXT,
            Capability.STRUCTURED_OUTPUT,
            Capability.LONG_CONTEXT,
        }
        registry.models["reasoner"] = ModelSpec(
            id="reasoner",
            provider_id="cloud",
            model_id="reasoner-v1",
            display_name="Reasoner",
            capabilities=capabilities,
            locality=Locality.CLOUD,
        )
        registry.models["fallback"] = ModelSpec(
            id="fallback",
            provider_id="local-api",
            model_id="qwen-local",
            display_name="Fallback",
            capabilities=capabilities,
            locality=Locality.LOCAL,
        )
        for role in (
            "notes.fact_extractor",
            "notes.drafter",
            "notes.verifier",
        ):
            registry.bind(role, "reasoner", fallback_model_ids=["fallback"])

        result = build_pipeline_runtime(
            registry,
            secret_store=SecretFixture({"cloud": "private-key"}),
        )

        composer = result.runtime.note_composer
        self.assertIsInstance(composer.fact_backend, FallbackStructuredBackend)
        assert isinstance(composer.fact_backend, FallbackStructuredBackend)
        first = composer.fact_backend.backends[0]
        self.assertIsInstance(first, OpenAICompatibleBackend)
        assert isinstance(first, OpenAICompatibleBackend)
        self.assertEqual(first.endpoint_style, EndpointStyle.RESPONSES)
        self.assertNotIn("private-key", repr(result))
        self.assertFalse(any(item.startswith("notes.") for item in result.warnings))

    def test_missing_referenced_secret_disables_structured_notes(self) -> None:
        registry = ModelRegistry.with_local_defaults()
        registry.providers["cloud"] = ProviderSpec(
            id="cloud",
            display_name="Cloud",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://example.invalid/v1",
            credential_ref="keyring://Video2Notes/providers/cloud",
            locality=Locality.CLOUD,
        )
        model = ModelSpec(
            id="notes",
            provider_id="cloud",
            model_id="notes",
            display_name="Notes",
            capabilities={
                Capability.TEXT,
                Capability.STRUCTURED_OUTPUT,
                Capability.LONG_CONTEXT,
            },
            locality=Locality.CLOUD,
        )
        registry.models[model.id] = model
        registry.bind("notes.fact_extractor", model.id)
        registry.bind("notes.drafter", model.id)

        result = build_pipeline_runtime(registry, secret_store=SecretFixture())

        self.assertIsNone(result.runtime.note_composer.fact_backend)
        self.assertTrue(any("credential" in item for item in result.warnings))

    def test_required_auth_without_credential_reference_disables_notes(self) -> None:
        registry = ModelRegistry.with_local_defaults()
        registry.providers["cloud"] = ProviderSpec(
            id="cloud",
            display_name="Cloud",
            kind=ProviderKind.OPENAI,
            protocol=ProviderProtocol.OPENAI_RESPONSES,
            auth_scheme=AuthScheme.BEARER,
            base_url="https://example.invalid/v1",
            locality=Locality.CLOUD,
        )
        registry.models["notes"] = ModelSpec(
            id="notes",
            provider_id="cloud",
            model_id="user-selected-model",
            display_name="Notes",
            capabilities={
                Capability.TEXT,
                Capability.STRUCTURED_OUTPUT,
                Capability.LONG_CONTEXT,
            },
            locality=Locality.CLOUD,
        )
        registry.bind("notes.fact_extractor", "notes")
        registry.bind("notes.drafter", "notes")

        result = build_pipeline_runtime(registry, secret_store=SecretFixture())

        self.assertIsNone(result.runtime.note_composer.fact_backend)
        self.assertTrue(any("not configured" in item for item in result.warnings))
