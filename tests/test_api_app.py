from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from PIL import Image

from video2notes.api import ApiContext, create_app
from video2notes.providers import (
    KeyringSecretStore,
    Locality,
    ModelRegistry,
    ProviderKind,
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
