from __future__ import annotations

import unittest

from video2notes.audio import FasterWhisperBackend
from video2notes.llm import EndpointStyle, OpenAICompatibleBackend
from video2notes.ocr import PaddleOcrBackend
from video2notes.providers import (
    Capability,
    Locality,
    ModelRegistry,
    ModelSpec,
    ProviderKind,
    ProviderSpec,
)
from video2notes.runtime import FallbackStructuredBackend, build_pipeline_runtime


class SecretFixture:
    def __init__(self, values: dict[str, str] | None = None):
        self.values = values or {}

    def get(self, provider_id: str) -> str | None:
        return self.values.get(provider_id)


class RuntimeConfigurationTests(unittest.TestCase):
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
        self.assertEqual(result.warnings, ())

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
