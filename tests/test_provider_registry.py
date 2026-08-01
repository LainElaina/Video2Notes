from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from video2notes.providers import (
    PROTOCOL_CATALOG,
    AuthScheme,
    Capability,
    KeyringSecretStore,
    Locality,
    ModelRegistry,
    ModelSpec,
    ProviderAuthError,
    ProviderKind,
    ProviderProtocol,
    ProviderSpec,
    SecretStatus,
    StreamTransport,
    provider_auth_headers,
)


class InMemoryKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.values[(service_name, username)] = password

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.values.get((service_name, username))

    def delete_password(self, service_name: str, username: str) -> None:
        del self.values[(service_name, username)]


class ModelRegistryTests(unittest.TestCase):
    def test_shared_auth_headers_support_custom_openai_compatible_protocols(
        self,
    ) -> None:
        provider = ProviderSpec(
            id="proxy",
            display_name="Proxy",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            protocol=ProviderProtocol.OPENAI_CHAT_COMPLETIONS,
            auth_scheme=AuthScheme.CUSTOM_HEADER,
            base_url="https://proxy.example/v1",
            locality=Locality.CLOUD,
            protocol_options={
                "auth_header_name": "api-key",
                "auth_header_prefix": "Token ",
            },
        )
        self.assertEqual(
            provider_auth_headers(provider, "private"),
            {"api-key": "Token private"},
        )
        unsafe = provider.model_copy(
            update={"protocol_options": {"auth_header_name": "Cookie"}}
        )
        with self.assertRaises(ProviderAuthError):
            provider_auth_headers(unsafe, "private")

    def test_schema_v1_payload_migrates_to_explicit_protocol_and_auth(self) -> None:
        registry = ModelRegistry.model_validate(
            {
                "schema_version": 1,
                "providers": {
                    "cloud": {
                        "id": "cloud",
                        "display_name": "Legacy cloud",
                        "kind": "openai_compatible",
                        "base_url": "https://example.invalid/v1",
                        "credential_ref": "keyring://Video2Notes/providers/cloud",
                        "endpoint_style": "responses",
                        "locality": "cloud",
                    }
                },
                "models": {},
                "roles": {},
            }
        )

        provider = registry.providers["cloud"]
        self.assertEqual(registry.schema_version, 2)
        self.assertEqual(provider.protocol, ProviderProtocol.OPENAI_RESPONSES)
        self.assertEqual(provider.auth_scheme, AuthScheme.BEARER)
        self.assertEqual(provider.endpoint_style, "responses")
        dumped = registry.model_dump(mode="json")
        self.assertEqual(dumped["schema_version"], 2)
        self.assertNotIn("secret", str(dumped).lower())

    def test_protocol_catalog_describes_transport_without_model_names(self) -> None:
        required = {
            ProviderProtocol.LOCAL,
            ProviderProtocol.OPENAI_RESPONSES,
            ProviderProtocol.OPENAI_CHAT_COMPLETIONS,
            ProviderProtocol.ANTHROPIC_MESSAGES,
            ProviderProtocol.GEMINI_GENERATE_CONTENT,
            ProviderProtocol.GEMINI_INTERACTIONS,
            ProviderProtocol.OLLAMA_NATIVE_CHAT,
        }
        self.assertTrue(required.issubset(PROTOCOL_CATALOG))
        self.assertEqual(
            PROTOCOL_CATALOG[ProviderProtocol.ANTHROPIC_MESSAGES].default_auth_scheme,
            AuthScheme.X_API_KEY,
        )
        self.assertEqual(
            PROTOCOL_CATALOG[ProviderProtocol.GEMINI_GENERATE_CONTENT].discovery_path,
            "/models",
        )
        self.assertEqual(
            PROTOCOL_CATALOG[ProviderProtocol.OLLAMA_NATIVE_CHAT].stream_transport,
            StreamTransport.NDJSON,
        )
        self.assertFalse(
            PROTOCOL_CATALOG[
                ProviderProtocol.OPENAI_AUDIO_TRANSCRIPTIONS
            ].structured_generation_adapter
        )
        self.assertFalse(
            PROTOCOL_CATALOG[ProviderProtocol.CUSTOM_HTTP].structured_generation_adapter
        )
        for template in PROTOCOL_CATALOG.values():
            self.assertFalse(hasattr(template, "models"))
            self.assertFalse(hasattr(template, "model_ids"))

    def test_protocol_template_does_not_infer_model_capabilities(self) -> None:
        provider = ProviderSpec(
            id="anthropic",
            display_name="Anthropic",
            kind=ProviderKind.ANTHROPIC,
            protocol=ProviderProtocol.ANTHROPIC_MESSAGES,
            auth_scheme=AuthScheme.X_API_KEY,
            base_url="https://api.anthropic.com/v1",
            locality=Locality.CLOUD,
        )
        model = ModelSpec(
            id="unverified",
            provider_id=provider.id,
            model_id="user-supplied-model",
            display_name="Unverified",
            capabilities=set(),
            locality=Locality.CLOUD,
        )
        registry = ModelRegistry(
            providers={provider.id: provider},
            models={model.id: model},
        )
        self.assertEqual(registry.compatible_models("notes.fact_extractor"), [])

    def test_incompatible_role_binding_is_rejected(self) -> None:
        provider = ProviderSpec(
            id="cloud",
            display_name="Cloud",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://example.invalid/v1",
            locality=Locality.CLOUD,
        )
        text_model = ModelSpec(
            id="text-only",
            provider_id="cloud",
            model_id="text-only",
            display_name="Text only",
            capabilities={Capability.TEXT, Capability.STRUCTURED_OUTPUT},
            locality=Locality.CLOUD,
        )
        registry = ModelRegistry(
            providers={"cloud": provider},
            models={"text-only": text_model},
        )
        with self.assertRaisesRegex(ValueError, "missing capabilities: vision"):
            registry.bind("vision.frame_explainer", "text-only")

    def test_registry_round_trip_contains_references_not_secrets(self) -> None:
        registry = ModelRegistry.with_local_defaults()
        registry.providers["cloud"] = ProviderSpec(
            id="cloud",
            display_name="OpenAI-compatible",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://example.invalid/v1",
            credential_ref="keyring://Video2Notes/providers/cloud",
            locality=Locality.CLOUD,
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = registry.save(Path(temporary) / "providers.json")
            payload = path.read_text(encoding="utf-8")
            loaded = ModelRegistry.load(path)
        self.assertNotIn("sk-test", payload)
        self.assertEqual(
            loaded.providers["cloud"].credential_ref,
            "keyring://Video2Notes/providers/cloud",
        )
        self.assertEqual(
            [model.id for model in loaded.compatible_models("asr.primary")],
            ["faster-whisper"],
        )

    def test_provider_requires_endpoint(self) -> None:
        with self.assertRaises(ValidationError):
            ProviderSpec(
                id="ollama",
                display_name="Ollama",
                kind=ProviderKind.OLLAMA,
                locality=Locality.LOCAL,
            )


class SecretStoreTests(unittest.TestCase):
    def test_secret_value_never_appears_in_reference_or_status(self) -> None:
        backend = InMemoryKeyring()
        store = KeyringSecretStore(backend)
        reference = store.set("cloud", "sk-private-value")
        self.assertEqual(reference, "keyring://Video2Notes/providers/cloud")
        self.assertNotIn("sk-private-value", reference)
        self.assertEqual(store.status("cloud"), SecretStatus.CONFIGURED)
        self.assertEqual(store.get("cloud"), "sk-private-value")
        store.delete("cloud")
        self.assertEqual(store.status("cloud"), SecretStatus.NOT_CONFIGURED)
