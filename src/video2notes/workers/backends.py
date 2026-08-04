"""ASR/OCR adapters backed by one persistent isolated runtime worker."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

from PIL import Image

from video2notes.audio import ASRTranscript, FasterWhisperConfig
from video2notes.ocr import BackendOcrOutput, PaddleOcrConfig
from video2notes.system import ExecutionPlan

from .client import RuntimeWorkerClient


class RuntimeWorkerAsrBackend:
    def __init__(self, client: RuntimeWorkerClient, config: FasterWhisperConfig) -> None:
        self.client = client
        self.config = config

    @property
    def runtime_identity(self) -> dict[str, str | int]:
        return self.client.identity.as_dict()

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
    ) -> ASRTranscript:
        result = self.client.request(
            "asr.transcribe",
            {
                "audio_path": str(audio_path.expanduser().resolve()),
                "config": self.config.model_dump(mode="json"),
                "language": language,
                "language_hints": [],
            },
        )
        return ASRTranscript.model_validate(result)

    def transcribe_multilingual(
        self,
        audio_path: Path,
        *,
        language_hints: Sequence[str] = (),
    ) -> ASRTranscript:
        result = self.client.request(
            "asr.transcribe",
            {
                "audio_path": str(audio_path.expanduser().resolve()),
                "config": self.config.model_dump(mode="json"),
                "language": None,
                "language_hints": list(language_hints),
            },
        )
        return ASRTranscript.model_validate(result)

    def for_execution_plan(self, plan: ExecutionPlan) -> RuntimeWorkerAsrBackend:
        return RuntimeWorkerAsrBackend(
            self.client,
            self.config.model_copy(
                update={
                    "device": plan.asr_device,
                    "compute_type": plan.asr_compute_type,
                    "cpu_threads": plan.asr_cpu_threads,
                    "beam_size": plan.asr_beam_size,
                }
            ),
        )


class RuntimeWorkerOcrBackend:
    def __init__(self, client: RuntimeWorkerClient, config: PaddleOcrConfig) -> None:
        self.client = client
        self.config = config

    @property
    def runtime_identity(self) -> dict[str, str | int]:
        return self.client.identity.as_dict()

    def recognize(
        self,
        image: Image.Image,
        *,
        language_hints: Sequence[str] = (),
    ) -> BackendOcrOutput:
        descriptor, raw_path = tempfile.mkstemp(prefix="video2notes-ocr-", suffix=".png")
        os.close(descriptor)
        image_path = Path(raw_path)
        try:
            image.convert("RGB").save(image_path, format="PNG")
            result = self.client.request(
                "ocr.recognize",
                {
                    "image_path": str(image_path.resolve()),
                    "config": self.config.model_dump(mode="json"),
                    "language_hints": list(language_hints),
                },
            )
            return BackendOcrOutput.model_validate(result)
        finally:
            image_path.unlink(missing_ok=True)

    def for_execution_plan(self, plan: ExecutionPlan) -> RuntimeWorkerOcrBackend:
        device = "gpu:0" if plan.ocr_device == "cuda" else plan.ocr_device
        return RuntimeWorkerOcrBackend(
            self.client,
            self.config.model_copy(
                update={
                    "device": device,
                    "cpu_threads": plan.ocr_cpu_threads,
                }
            ),
        )
