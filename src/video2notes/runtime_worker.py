"""Executable host for isolated ASR/OCR runtime packages."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PIL import Image

from video2notes.audio import FasterWhisperBackend, FasterWhisperConfig
from video2notes.components.runtime_downloaders import (
    runtime_manifest_sha256,
    validate_runtime_package_root,
)
from video2notes.components.runtime_models import RUNTIME_PACKAGE_MANIFEST
from video2notes.ocr import PaddleOcrBackend, PaddleOcrConfig
from video2notes.workers.protocol import (
    RuntimeWorkerHello,
    RuntimeWorkerRequest,
    RuntimeWorkerResponse,
)


class RuntimeWorkerHost:
    def __init__(self, package_root: Path) -> None:
        validation = validate_runtime_package_root(package_root, full_hash=False)
        self.package_root = package_root.resolve()
        self.manifest = validation.manifest
        self.manifest_sha256 = runtime_manifest_sha256(self.manifest)
        self._asr_backends: dict[str, FasterWhisperBackend] = {}
        self._ocr_backends: dict[str, PaddleOcrBackend] = {}

    def hello(self) -> RuntimeWorkerHello:
        capabilities = self.manifest.capability_ids
        versions: dict[str, str] = {}
        for distribution in ("faster-whisper", "ctranslate2", "paddleocr", "paddlepaddle"):
            try:
                versions[distribution] = importlib.metadata.version(distribution)
            except importlib.metadata.PackageNotFoundError:
                continue
        supported = {
            item.capability_id: tuple(item.supported_devices)
            for item in self.manifest.capabilities
        }
        return RuntimeWorkerHello(
            package_id=self.manifest.package_id,
            package_version=self.manifest.version,
            manifest_sha256=self.manifest_sha256,
            capabilities=capabilities,
            supported_devices=supported,
            engine_versions=versions,
            cuda_available={
                capability: "cuda" in supported.get(capability, ())
                for capability in capabilities
            },
        )

    def handle(self, request: RuntimeWorkerRequest) -> RuntimeWorkerResponse:
        try:
            if request.method == "hello":
                result = self.hello().model_dump(mode="json")
            elif request.method == "asr.transcribe":
                result = self._transcribe(request.params)
            elif request.method == "ocr.recognize":
                result = self._recognize(request.params)
            elif request.method == "shutdown":
                result = {"shutdown": True}
            else:  # pragma: no cover - Pydantic protects this
                raise ValueError("unsupported method")
            return RuntimeWorkerResponse(request_id=request.request_id, ok=True, result=result)
        except Exception as error:
            return RuntimeWorkerResponse(
                request_id=request.request_id,
                ok=False,
                error_code="runtime_request_failed",
                error_type=type(error).__name__,
            )

    def _transcribe(self, params: dict[str, Any]) -> dict[str, Any]:
        config = FasterWhisperConfig.model_validate(params.get("config"))
        key = config.model_dump_json()
        backend = self._asr_backends.get(key)
        if backend is None:
            backend = FasterWhisperBackend(config)
            self._asr_backends[key] = backend
        path = Path(str(params.get("audio_path", ""))).expanduser().resolve()
        hints = params.get("language_hints")
        if isinstance(hints, list) and hints:
            transcript = backend.transcribe_multilingual(
                path,
                language_hints=[str(item) for item in hints],
            )
        else:
            raw_language = params.get("language")
            transcript = backend.transcribe(
                path,
                language=str(raw_language) if raw_language is not None else None,
            )
        return transcript.model_dump(mode="json")

    def _recognize(self, params: dict[str, Any]) -> dict[str, Any]:
        config = PaddleOcrConfig.model_validate(params.get("config"))
        key = config.model_dump_json()
        backend = self._ocr_backends.get(key)
        if backend is None:
            backend = PaddleOcrBackend(config)
            self._ocr_backends[key] = backend
        path = Path(str(params.get("image_path", ""))).expanduser().resolve()
        raw_hints = params.get("language_hints")
        hints = [str(item) for item in raw_hints] if isinstance(raw_hints, list) else []
        with Image.open(path) as image:
            output = backend.recognize(image.copy(), language_hints=hints)
        return output.model_dump(mode="json")


def _serve(host: RuntimeWorkerHost) -> int:
    for line in sys.stdin:
        should_shutdown = False
        try:
            request = RuntimeWorkerRequest.model_validate_json(line)
        except ValueError:
            response = RuntimeWorkerResponse(
                request_id="invalid-request",
                ok=False,
                error_code="invalid_request",
                error_type="ValidationError",
            )
        else:
            response = host.handle(request)
            should_shutdown = request.method == "shutdown"
        sys.stdout.write(response.model_dump_json() + "\n")
        sys.stdout.flush()
        if should_shutdown:
            return 0
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="video2notes-runtime-worker")
    parser.add_argument("mode", choices=("serve", "probe"), nargs="?", default="serve")
    parser.add_argument("--package-root", default=str(Path(sys.executable).resolve().parent))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    package_root = Path(arguments.package_root).expanduser().resolve()
    if not (package_root / RUNTIME_PACKAGE_MANIFEST).is_file():
        return 2
    try:
        host = RuntimeWorkerHost(package_root)
    except Exception:
        return 2
    if arguments.mode == "probe":
        print(json.dumps(host.hello().model_dump(mode="json"), separators=(",", ":")))
        return 0
    return _serve(host)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
