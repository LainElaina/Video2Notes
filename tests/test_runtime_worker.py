from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from typing import Any

from PIL import Image
from pydantic import ValidationError

from video2notes.audio import ASRTranscript, FasterWhisperConfig
from video2notes.components.runtime_models import (
    RUNTIME_PACKAGE_MANIFEST,
    RuntimeCapabilitySpec,
    RuntimeLicenseSpec,
    RuntimePackageManifest,
    RuntimePayloadFile,
    RuntimeTransport,
)
from video2notes.ocr import (
    BackendOcrOutput,
    OcrModelInvocation,
    PaddleOcrConfig,
)
from video2notes.runtime_worker import RuntimeWorkerHost
from video2notes.workers import (
    RuntimeWorkerAsrBackend,
    RuntimeWorkerOcrBackend,
    RuntimeWorkerResponse,
)


class FakeWorkerClient:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.identity = FakeIdentity()

    def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        retry_once: bool = True,
    ) -> dict[str, Any]:
        del retry_once
        self.requests.append((method, params))
        if method == "asr.transcribe":
            return ASRTranscript(
                provider="worker",
                model="test",
                version="1",
            ).model_dump(mode="json")
        if method == "ocr.recognize":
            return BackendOcrOutput(
                lines=[],
                invocation=OcrModelInvocation(
                    engine="worker",
                    version="1",
                    backend="test",
                ),
            ).model_dump(mode="json")
        raise AssertionError(method)


class FakeIdentity:
    def as_dict(self) -> dict[str, str | int]:
        return {
            "source": "managed",
            "package_id": "test-pack",
            "package_version": "1",
            "manifest_sha256": "0" * 64,
            "protocol_version": 1,
            "instance_id": "managed:test-pack:1",
        }


class RuntimeWorkerTests(unittest.TestCase):
    def test_response_contract_never_accepts_mixed_success_and_error(self) -> None:
        with self.assertRaises(ValidationError):
            RuntimeWorkerResponse(
                request_id="request",
                ok=True,
                result={},
                error_code="should-not-exist",
            )
        with self.assertRaises(ValidationError):
            RuntimeWorkerResponse(request_id="request", ok=False)

    def test_worker_backends_preserve_existing_asr_and_ocr_contracts(self) -> None:
        client = FakeWorkerClient()
        asr = RuntimeWorkerAsrBackend(
            client,  # type: ignore[arg-type]
            FasterWhisperConfig(model_path="D:/models/whisper"),
        )
        transcript = asr.transcribe(Path("D:/audio.wav"), language="zh")
        self.assertEqual(transcript.provider, "worker")
        self.assertEqual(client.requests[0][0], "asr.transcribe")
        self.assertEqual(client.requests[0][1]["language"], "zh")

        ocr = RuntimeWorkerOcrBackend(
            client,  # type: ignore[arg-type]
            PaddleOcrConfig(
                detection_model_dir="D:/models/detection",
                recognition_model_dir="D:/models/recognition",
            ),
        )
        output = ocr.recognize(Image.new("RGB", (8, 8), "white"), language_hints=("zh",))
        self.assertEqual(output.invocation.engine, "worker")
        self.assertEqual(client.requests[1][0], "ocr.recognize")
        image_path = Path(str(client.requests[1][1]["image_path"]))
        self.assertFalse(image_path.exists())

    def test_host_hello_is_derived_from_the_validated_package_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            worker = root / "runtime-worker.exe"
            notice = root / "licenses" / "THIRD_PARTY_NOTICES.md"
            notice.parent.mkdir()
            worker.write_bytes(b"worker")
            notice.write_text("license", encoding="utf-8")
            files = (
                self._payload(root, worker),
                self._payload(root, notice),
            )
            manifest = RuntimePackageManifest(
                package_id="test-runtime",
                version="1.0.0",
                display_name="Test runtime",
                target_triple="x86_64-pc-windows-msvc",
                runtime_protocol_version=1,
                capabilities=(
                    RuntimeCapabilitySpec(
                        capability_id="asr.faster_whisper",
                        engine_id="faster-whisper",
                        protocol_version=1,
                        transport=RuntimeTransport.WORKER,
                        entrypoint="runtime-worker.exe",
                        supported_devices=("cpu", "cuda"),
                    ),
                ),
                licenses=(
                    RuntimeLicenseSpec(
                        name="Notices",
                        relative_path="licenses/THIRD_PARTY_NOTICES.md",
                    ),
                ),
                upstream_sources=("https://example.invalid/runtime",),
                payload_size_bytes=sum(item.size_bytes for item in files),
                files=files,
            )
            (root / RUNTIME_PACKAGE_MANIFEST).write_text(
                manifest.model_dump_json(indent=2),
                encoding="utf-8",
            )

            hello = RuntimeWorkerHost(root).hello()

            self.assertEqual(hello.package_id, "test-runtime")
            self.assertEqual(hello.capabilities, ("asr.faster_whisper",))
            self.assertEqual(
                hello.supported_devices["asr.faster_whisper"],
                ("cpu", "cuda"),
            )

    @staticmethod
    def _payload(root: Path, path: Path) -> RuntimePayloadFile:
        return RuntimePayloadFile(
            relative_path=path.relative_to(root).as_posix(),
            size_bytes=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
