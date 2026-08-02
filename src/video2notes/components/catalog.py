"""Versioned catalog of local ASR/OCR assets; cloud model IDs never belong here."""

from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from video2notes.system.hardware import HardwareTier

from .models import (
    ComponentManifest,
    ComponentModel,
    DownloadSource,
    LocalModelRole,
    TierRecommendation,
)


class ComponentCatalog(ComponentModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    manifests: dict[str, ComponentManifest]
    recommendations: dict[HardwareTier, TierRecommendation]

    @model_validator(mode="after")
    def validate_recommendations(self) -> Self:
        for tier, recommendation in self.recommendations.items():
            if recommendation.hardware_tier is not tier:
                raise ValueError("recommendation key must match hardware_tier")
            asr = self.manifests.get(recommendation.asr_component_id)
            ocr = self.manifests.get(recommendation.ocr_component_id)
            if asr is None or asr.role is not LocalModelRole.ASR:
                raise ValueError(f"{tier.value} recommendation references invalid ASR component")
            if ocr is None or ocr.role is not LocalModelRole.OCR:
                raise ValueError(f"{tier.value} recommendation references invalid OCR component")
        for component_id, manifest in self.manifests.items():
            if component_id != manifest.id:
                raise ValueError("manifest dictionary key must match manifest id")
        return self


_ASR_SMALL = ComponentManifest(
    id="asr-faster-whisper-small",
    version="1.0.0",
    display_name="faster-whisper small",
    role=LocalModelRole.ASR,
    engine="faster_whisper",
    source_kind=DownloadSource.HUGGINGFACE_SNAPSHOT,
    source="Systran/faster-whisper-small",
    revision="536b0662742c02347bc0e980a01041f333bce120",
    target_subdirectory="models/asr/faster-whisper-small/1.0.0",
    required_files=("config.json", "model.bin"),
)

_ASR_LARGE_V3 = ComponentManifest(
    id="asr-faster-whisper-large-v3",
    version="1.0.0",
    display_name="faster-whisper large-v3",
    role=LocalModelRole.ASR,
    engine="faster_whisper",
    source_kind=DownloadSource.HUGGINGFACE_SNAPSHOT,
    source="Systran/faster-whisper-large-v3",
    revision="edaa852ec7e145841d8ffdb056a99866b5f0a478",
    target_subdirectory="models/asr/faster-whisper-large-v3/1.0.0",
    required_files=("config.json", "model.bin"),
)

_OCR_MOBILE = ComponentManifest(
    id="ocr-paddle-ppocrv5-mobile",
    version="1.0.0",
    display_name="PaddleOCR PP-OCRv5 mobile detector + recognizer",
    role=LocalModelRole.OCR,
    engine="paddleocr",
    source_kind=DownloadSource.PADDLE_COMPATIBLE,
    source=(
        "paddleocr://"
        "PP-OCRv5_mobile_det@0d63e78e2b680928f6b1747d76a08db6e645efb7+"
        "PP-OCRv5_mobile_rec@682f20538d8c086cb2128e5cfac775e6c4904e85"
    ),
    target_subdirectory="models/ocr/ppocrv5-mobile/1.0.0",
    required_files=(
        "detection/inference.json",
        "detection/inference.pdiparams",
        "detection/inference.yml",
        "recognition/inference.json",
        "recognition/inference.pdiparams",
        "recognition/inference.yml",
    ),
)

_OCR_SERVER = ComponentManifest(
    id="ocr-paddle-ppocrv5-server",
    version="1.0.0",
    display_name="PaddleOCR PP-OCRv5 server detector + recognizer",
    role=LocalModelRole.OCR,
    engine="paddleocr",
    source_kind=DownloadSource.PADDLE_COMPATIBLE,
    source=(
        "paddleocr://"
        "PP-OCRv5_server_det@ca867c897ecbca8873081573a802ad70d499cb94+"
        "PP-OCRv5_server_rec@b26c3587fda8da3c8ec0ce357214b4d661ff1558"
    ),
    target_subdirectory="models/ocr/ppocrv5-server/1.0.0",
    required_files=(
        "detection/inference.json",
        "detection/inference.pdiparams",
        "detection/inference.yml",
        "recognition/inference.json",
        "recognition/inference.pdiparams",
        "recognition/inference.yml",
    ),
)


DEFAULT_COMPONENT_CATALOG = ComponentCatalog(
    manifests={
        item.id: item
        for item in (
            _ASR_SMALL,
            _ASR_LARGE_V3,
            _OCR_MOBILE,
            _OCR_SERVER,
        )
    },
    recommendations={
        HardwareTier.CPU_IGPU: TierRecommendation(
            hardware_tier=HardwareTier.CPU_IGPU,
            asr_component_id=_ASR_SMALL.id,
            ocr_component_id=_OCR_MOBILE.id,
            asr_device="cpu",
            asr_compute_type="int8",
            ocr_device="cpu",
            reason="CPU/iGPU machines use compact local models and serial inference.",
        ),
        HardwareTier.GPU_8GB: TierRecommendation(
            hardware_tier=HardwareTier.GPU_8GB,
            asr_component_id=_ASR_LARGE_V3.id,
            ocr_component_id=_OCR_MOBILE.id,
            asr_device="cuda",
            asr_compute_type="int8_float16",
            ocr_device="cpu",
            reason="8 GiB GPUs accelerate ASR; the Windows PaddleOCR runtime remains on CPU.",
        ),
        HardwareTier.GPU_12GB: TierRecommendation(
            hardware_tier=HardwareTier.GPU_12GB,
            asr_component_id=_ASR_LARGE_V3.id,
            ocr_component_id=_OCR_SERVER.id,
            asr_device="cuda",
            asr_compute_type="float16",
            ocr_device="cpu",
            reason="12 GiB GPUs accelerate large-v3 ASR; PaddleOCR remains on CPU.",
        ),
        HardwareTier.GPU_24GB_PLUS: TierRecommendation(
            hardware_tier=HardwareTier.GPU_24GB_PLUS,
            asr_component_id=_ASR_LARGE_V3.id,
            ocr_component_id=_OCR_SERVER.id,
            asr_device="cuda",
            asr_compute_type="float16",
            ocr_device="cpu",
            reason=(
                "24 GiB or larger GPUs use the strongest local models "
                "with CUDA ASR and CPU OCR."
            ),
        ),
    },
)
