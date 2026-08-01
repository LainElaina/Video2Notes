from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from PIL import Image

from video2notes.api import ApiContext, create_app
from video2notes.components import (
    ComponentManager,
    ComponentManifest,
    DownloadResult,
    DownloadSource,
    ModuleProbeResult,
)
from video2notes.providers import (
    AuthScheme,
    Capability,
    KeyringSecretStore,
    Locality,
    ModelRegistry,
    ModelSpec,
    ProviderKind,
    ProviderProtocol,
    ProviderSpec,
)
from video2notes.sources import SourceInput


class InMemoryKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.values[(service_name, username)] = password

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.values.get((service_name, username))

    def delete_password(self, service_name: str, username: str) -> None:
        del self.values[(service_name, username)]


class ApiComponentDownloader:
    def download(
        self,
        manifest: ComponentManifest,
        destination: Path,
        *,
        resume: bool,
    ) -> DownloadResult:
        del resume
        destination.mkdir(parents=True, exist_ok=True)
        for relative in manifest.required_files:
            payload = destination / relative
            payload.parent.mkdir(parents=True, exist_ok=True)
            payload.write_bytes(b"managed-model")
        for relative in manifest.required_nonempty_directories:
            payload = destination / relative
            payload.mkdir(parents=True, exist_ok=True)
            (payload / "inference.bin").write_bytes(b"managed-model")
        return DownloadResult(source_revision="api-test")


class ApiAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.backend = InMemoryKeyring()
        registry = ModelRegistry.with_local_defaults()
        registry.providers["cloud"] = ProviderSpec(
            id="cloud",
            display_name="Test cloud",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            protocol=ProviderProtocol.OPENAI_CHAT_COMPLETIONS,
            auth_scheme=AuthScheme.BEARER,
            base_url="https://example.test/v1",
            locality=Locality.CLOUD,
        )
        self.context = ApiContext(
            self.temporary.name,
            token="test-token",
            model_registry=registry,
            secret_store=KeyringSecretStore(self.backend),
        )
        self.client = TestClient(create_app(self.context))
        self.addCleanup(self.client.close)
        self.headers = {"X-Video2Notes-Token": "test-token"}

    def test_health_is_public_but_machine_data_is_protected(self) -> None:
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        self.assertEqual(self.client.get("/api/system").status_code, 401)
        response = self.client.get("/api/system", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn("fast", response.json()["plans"])
        self.assertIn("accurate", response.json()["plans"])
        self.assertEqual(response.json()["performance"]["experience_mode"], "guided")
        self.assertIn("budget", response.json()["recommendation"])

    def test_performance_settings_are_validated_persisted_and_applied(self) -> None:
        guided = self.client.get("/api/performance", headers=self.headers)
        self.assertEqual(guided.status_code, 200)
        self.assertEqual(guided.json()["preference"], "balanced")

        invalid = self.client.put(
            "/api/performance",
            headers=self.headers,
            json={
                "schema_version": 1,
                "experience_mode": "guided",
                "preference": "balanced",
                "overrides": {"cpu_workers": 8},
            },
        )
        self.assertEqual(invalid.status_code, 422)

        saved = self.client.put(
            "/api/performance",
            headers=self.headers,
            json={
                "schema_version": 1,
                "experience_mode": "professional",
                "preference": "responsive",
                "reserve": {"cpu_reserve_ratio": 0.5},
                "overrides": {
                    "cpu_workers": 2,
                    "remote_model_concurrency": 1,
                },
            },
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["overrides"]["cpu_workers"], 2)
        persisted = self.client.get("/api/performance", headers=self.headers)
        self.assertEqual(persisted.json(), saved.json())
        system = self.client.get("/api/system", headers=self.headers)
        self.assertEqual(system.json()["performance"], saved.json())
        self.assertEqual(system.json()["recommendation"]["preference"], "responsive")
        self.assertEqual(
            self.context.pipeline.runtime.experience_mode.value,
            "professional",
        )
        self.assertEqual(
            self.context.pipeline.runtime.performance_overrides.cpu_workers,
            2,
        )

    def test_configuration_catalog_exposes_protocols_roles_and_no_model_ids(
        self,
    ) -> None:
        response = self.client.get("/api/configuration-catalog", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        protocols = {item["protocol"]: item for item in payload["protocols"]}
        self.assertIn("openai_responses", protocols)
        self.assertIn("anthropic_messages", protocols)
        self.assertIn("gemini_interactions", protocols)
        self.assertIn("ollama_native_chat", protocols)
        self.assertEqual(
            protocols["ollama_native_chat"]["stream_transport"],
            "ndjson",
        )
        roles = {item["role"]: item for item in payload["roles"]}
        self.assertEqual(
            set(roles["notes.fact_extractor"]["required_capabilities"]),
            {"text", "structured_output"},
        )
        self.assertIn("word_timestamps", payload["capabilities"])
        for protocol in payload["protocols"]:
            self.assertNotIn("model_id", protocol)
            self.assertNotIn("models", protocol)

    def test_processing_estimate_and_runtime_diagnostics_are_protected(self) -> None:
        self.assertEqual(
            self.client.post(
                "/api/estimate",
                json={"duration_seconds": 600, "quality_mode": "balanced"},
            ).status_code,
            401,
        )
        estimate = self.client.post(
            "/api/estimate",
            headers=self.headers,
            json={
                "duration_seconds": 600,
                "quality_mode": "balanced",
                "source_height": 2160,
                "source_fps": 60,
            },
        )
        self.assertEqual(estimate.status_code, 200)
        self.assertGreater(
            estimate.json()["upper_seconds"],
            estimate.json()["lower_seconds"],
        )
        runtime = self.client.get("/api/runtime", headers=self.headers)
        self.assertEqual(runtime.status_code, 200)
        self.assertFalse(runtime.json()["injected"])
        self.assertIsInstance(runtime.json()["warnings"], list)

    def test_component_inventory_and_one_click_prepare_activate_local_models(
        self,
    ) -> None:
        data_root = Path(self.temporary.name) / "component-api"
        runtime_root = data_root / "portable-runtime"
        runtime_root.mkdir(parents=True)
        binaries: dict[str, str] = {}
        for name in ("ffmpeg", "ffprobe"):
            executable = runtime_root / f"{name}.exe"
            executable.write_bytes(b"tool")
            binaries[name] = str(executable)
        downloader = ApiComponentDownloader()
        manager = ComponentManager(
            data_root,
            runtime_root=runtime_root,
            binary_locator=lambda name: binaries.get(name),
            module_probe=lambda module, distribution: ModuleProbeResult(
                available=True,
                version=f"test-{distribution}",
                path=f"runtime/{module}",
            ),
            downloaders={
                DownloadSource.HUGGINGFACE_SNAPSHOT: downloader,
                DownloadSource.PADDLE_COMPATIBLE: downloader,
            },
        )
        context = ApiContext(
            data_root,
            token="component-token",
            model_registry=ModelRegistry.with_local_defaults(),
            secret_store=KeyringSecretStore(InMemoryKeyring()),
            component_manager=manager,
        )
        client = TestClient(create_app(context))
        self.addCleanup(client.close)
        headers = {"X-Video2Notes-Token": "component-token"}

        before = client.get("/api/components", headers=headers)
        self.assertEqual(before.status_code, 200)
        self.assertFalse(before.json()["inventory"]["ready"])
        prepared = client.post(
            "/api/components/prepare",
            headers=headers,
            json={"hardware_tier": "cpu_igpu", "activate": True},
        )

        self.assertEqual(prepared.status_code, 200)
        self.assertTrue(prepared.json()["activated"])
        self.assertTrue(prepared.json()["report"]["inventory"]["ready"])
        self.assertEqual(len(prepared.json()["results"]), 2)
        self.assertIn(
            "model_path",
            context.model_registry.models["faster-whisper"].settings,
        )
        self.assertIn(
            "detection_model_dir",
            context.model_registry.models["paddleocr"].settings,
        )
        self.assertIsNotNone(context.pipeline.runtime.asr_backend)
        self.assertIsNotNone(context.pipeline.runtime.ocr_backend)

        registry = context.model_registry.model_copy(deep=True)
        registry.models["custom-whisper"] = ModelSpec(
            id="custom-whisper",
            provider_id="local",
            model_id="custom-whisper",
            display_name="Custom Whisper",
            capabilities={Capability.ASR, Capability.SEGMENT_TIMESTAMPS},
            locality=Locality.LOCAL,
            settings={},
        )
        registry.models["custom-ocr"] = ModelSpec(
            id="custom-ocr",
            provider_id="local",
            model_id="custom-ocr",
            display_name="Custom OCR",
            capabilities={Capability.OCR, Capability.OCR_BOXES},
            locality=Locality.LOCAL,
            settings={},
        )
        registry.bind(
            "asr.primary",
            "faster-whisper",
            fallback_model_ids=["custom-whisper"],
            escalation_rule="keep-user-fallback",
        )
        registry.save(context.registry_path)
        context.model_registry = registry
        context.refresh_pipeline()

        reused = client.post(
            "/api/components/prepare",
            headers=headers,
            json={"hardware_tier": "cpu_igpu", "activate": True},
        )
        self.assertTrue(reused.json()["activated"])
        preserved = context.model_registry.roles["asr.primary"]
        self.assertEqual(preserved.fallback_model_ids, ["custom-whisper"])
        self.assertEqual(preserved.escalation_rule, "keep-user-fallback")

        registry = context.model_registry.model_copy(deep=True)
        registry.bind("asr.primary", "custom-whisper")
        registry.bind("ocr.primary", "custom-ocr")
        registry.bind("vision.text_detector", "custom-ocr")
        registry.save(context.registry_path)
        context.model_registry = registry
        context.refresh_pipeline()

        conflicted = client.post(
            "/api/components/prepare",
            headers=headers,
            json={"hardware_tier": "cpu_igpu", "activate": True},
        )
        self.assertEqual(conflicted.status_code, 200)
        self.assertFalse(conflicted.json()["activated"])
        self.assertEqual(
            context.model_registry.roles["asr.primary"].primary_model_id,
            "custom-whisper",
        )
        self.assertEqual(
            context.model_registry.roles["ocr.primary"].primary_model_id,
            "custom-ocr",
        )

    def test_provider_secret_is_write_only(self) -> None:
        previous_pipeline = self.context.pipeline
        response = self.client.put(
            "/api/providers/cloud/secret",
            headers=self.headers,
            json={"secret": "private-value"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("private-value", response.text)
        self.assertEqual(
            self.backend.values[("Video2Notes", "cloud")],
            "private-value",
        )
        self.assertIsNot(self.context.pipeline, previous_pipeline)

    def test_run_manifest_and_artifact_are_confined(self) -> None:
        response = self.client.post(
            "/api/runs",
            headers=self.headers,
            json={
                "source": SourceInput.local("C:/video.mp4").model_dump(mode="json"),
                "quality_mode": "balanced",
            },
        )
        self.assertEqual(response.status_code, 200)
        run_id = response.json()["run_id"]
        workspace = self.context.get_workspace(run_id)
        note = workspace.artifact_path("notes", "note.md")
        note.write_text("# safe", encoding="utf-8")

        result = self.client.get(
            f"/api/runs/{run_id}/artifact",
            headers=self.headers,
            params={"path": "notes/note.md"},
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.text, "# safe")
        escape = self.client.get(
            f"/api/runs/{run_id}/artifact",
            headers=self.headers,
            params={"path": "../manifest.json"},
        )
        self.assertEqual(escape.status_code, 404)
        raw_manifest = self.client.get(
            f"/api/runs/{run_id}/artifact",
            headers=self.headers,
            params={"path": "manifest.json"},
        )
        self.assertEqual(raw_manifest.status_code, 404)

    def test_supporting_text_and_image_materials_are_persisted(self) -> None:
        created = self.client.post(
            "/api/runs",
            headers=self.headers,
            json={
                "source": SourceInput.local("C:/video.mp4").model_dump(mode="json"),
                "quality_mode": "balanced",
            },
        )
        run_id = created.json()["run_id"]
        text = self.client.post(
            f"/api/runs/{run_id}/materials/text",
            headers=self.headers,
            json={
                "title": "评论区资料",
                "content": "补充的文字证据",
                "start_us": 1_000_000,
                "end_us": 2_000_000,
            },
        )
        self.assertEqual(text.status_code, 200)

        image_stream = BytesIO()
        Image.new("RGB", (8, 8), (20, 160, 100)).save(image_stream, format="PNG")
        image = self.client.post(
            f"/api/runs/{run_id}/materials/files",
            headers=self.headers,
            params={"title": "补充图片"},
            files={"file": ("../../unsafe.png", image_stream.getvalue(), "image/png")},
        )
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.json()["original_name"], "unsafe.png")

        listed = self.client.get(
            f"/api/runs/{run_id}/materials",
            headers=self.headers,
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 2)
        material_id = text.json()["id"]
        deleted = self.client.delete(
            f"/api/runs/{run_id}/materials/{material_id}",
            headers=self.headers,
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["status"], "deleted")

    def test_report_revision_routes_expose_readiness_and_contract_validation(
        self,
    ) -> None:
        created = self.client.post(
            "/api/runs",
            headers=self.headers,
            json={
                "source": SourceInput.local("C:/video.mp4").model_dump(mode="json"),
                "quality_mode": "balanced",
            },
        )
        run_id = created.json()["run_id"]

        listed = self.client.get(
            f"/api/runs/{run_id}/report-revisions",
            headers=self.headers,
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["latest_revision_id"], None)
        self.assertEqual(listed.json()["revisions"], [])

        latest = self.client.get(
            f"/api/runs/{run_id}/report-revisions/latest",
            headers=self.headers,
        )
        self.assertEqual(latest.status_code, 404)

        not_ready = self.client.post(
            f"/api/runs/{run_id}/report-revisions",
            headers=self.headers,
            json={
                "preset": "executive",
                "output_formats": ["markdown", "html"],
            },
        )
        self.assertEqual(not_ready.status_code, 409)

        invalid = self.client.post(
            f"/api/runs/{run_id}/report-revisions",
            headers=self.headers,
            json={
                "preset": "detailed",
                "output_formats": ["html"],
            },
        )
        self.assertEqual(invalid.status_code, 422)

    def test_provider_registry_response_contains_no_keyring_values(self) -> None:
        self.backend.set_password("Video2Notes", "local", "private-value")
        result = self.client.get("/api/providers", headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertNotIn("private-value", result.text)

    def test_provider_connection_test_is_real_and_protected(self) -> None:
        self.assertEqual(self.client.post("/api/providers/local/test").status_code, 401)
        local = self.client.post(
            "/api/providers/local/test",
            headers=self.headers,
        )
        self.assertEqual(local.status_code, 200)
        self.assertEqual(local.json()["status"], "connected")

        configured = self.client.put(
            "/api/providers/cloud/secret",
            headers=self.headers,
            json={"secret": "test-secret"},
        )
        self.assertEqual(configured.status_code, 200)

        response = MagicMock()
        response.__enter__.return_value.status = 200
        with patch(
            "video2notes.api.app.urllib.request.urlopen",
            return_value=response,
        ) as urlopen:
            cloud = self.client.post(
                "/api/providers/cloud/test",
                headers=self.headers,
            )
        self.assertEqual(cloud.status_code, 200)
        self.assertEqual(cloud.json()["status"], "connected")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.test/v1/models")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-secret")
        self.assertNotIn("test-secret", cloud.text)

    def test_provider_discovery_is_protocol_aware_and_declares_no_capabilities(
        self,
    ) -> None:
        configured = self.client.put(
            "/api/providers/cloud/secret",
            headers=self.headers,
            json={"secret": "test-secret"},
        )
        self.assertEqual(configured.status_code, 200)
        response = MagicMock()
        response.__enter__.return_value.read.return_value = (
            b'{"data":[{"id":"beta","context_window":2048},'
            b'{"id":"alpha","display_name":"Alpha"}]}'
        )
        with patch(
            "video2notes.api.app.urllib.request.urlopen",
            return_value=response,
        ) as urlopen:
            discovered = self.client.get(
                "/api/providers/cloud/discover",
                headers=self.headers,
            )
        self.assertEqual(discovered.status_code, 200)
        self.assertEqual(
            [item["model_id"] for item in discovered.json()["models"]],
            ["alpha", "beta"],
        )
        self.assertNotIn("capabilities", discovered.text)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer test-secret")

        gemini = ProviderSpec(
            id="gemini",
            display_name="Gemini test",
            kind=ProviderKind.GOOGLE,
            protocol=ProviderProtocol.GEMINI_GENERATE_CONTENT,
            auth_scheme=AuthScheme.X_GOOG_API_KEY,
            base_url="https://generativelanguage.googleapis.com/v1beta",
            locality=Locality.CLOUD,
        )
        gemini.credential_ref = self.context.secret_store.set("gemini", "gemini-key")
        self.context.model_registry.providers[gemini.id] = gemini
        response.__enter__.return_value.read.return_value = (
            b'{"models":[{"name":"models/gemini-x",'
            b'"displayName":"Gemini X","inputTokenLimit":32768}]}'
        )
        with patch(
            "video2notes.api.app.urllib.request.urlopen",
            return_value=response,
        ) as urlopen:
            discovered = self.client.get(
                "/api/providers/gemini/discover",
                headers=self.headers,
            )
        self.assertEqual(discovered.status_code, 200)
        self.assertEqual(discovered.json()["models"][0]["model_id"], "gemini-x")
        self.assertEqual(discovered.json()["models"][0]["context_window"], 32768)
        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://generativelanguage.googleapis.com/v1beta/models",
        )
        self.assertEqual(request.get_header("X-goog-api-key"), "gemini-key")

    def test_unknown_local_probe_is_a_safe_validation_error(self) -> None:
        missing = Path(self.temporary.name) / "missing.mp4"
        result = self.client.post(
            "/api/sources/probe",
            headers=self.headers,
            json={
                "source": SourceInput.local(missing).model_dump(mode="json"),
                "auth": {"kind": "none"},
                "policy": {"mode": "fast"},
            },
        )
        self.assertEqual(result.status_code, 422)
        self.assertNotIn("Traceback", result.text)
