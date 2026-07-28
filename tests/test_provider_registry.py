from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from video2notes.providers import (
    Capability,
    KeyringSecretStore,
    Locality,
    ModelRegistry,
    ModelSpec,
    ProviderKind,
    ProviderSpec,
    SecretStatus,
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
