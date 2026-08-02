"""Lazy, local-model-only PaddleOCR adapter.

Importing this module does not import PaddleOCR, initialize CUDA, or download
model weights. Both detector and recognizer directories are mandatory and are
validated before PaddleOCR is imported.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Literal, cast

from PIL import Image
from pydantic import Field

from video2notes.system.acceleration import detect_acceleration_capabilities

from .models import (
    BackendOcrLine,
    BackendOcrOutput,
    OcrBox,
    OcrModel,
    OcrModelInvocation,
)


class OcrDependencyError(RuntimeError):
    """Raised when an explicitly selected optional OCR runtime is unavailable."""


class OcrConfigurationError(RuntimeError):
    """Raised when local model assets are absent or unsafe to fall back from."""


class OcrInferenceError(RuntimeError):
    """Raised when PaddleOCR returns a payload that cannot be interpreted."""


class PaddleOcrConfig(OcrModel):
    detection_model_dir: str = Field(min_length=1)
    recognition_model_dir: str = Field(min_length=1)
    language: str = Field(default="ch", min_length=1)
    device: str = Field(default="cpu", min_length=1)
    enable_mkldnn: bool = False
    cpu_threads: int = Field(default=1, ge=1, le=64)
    api_family: Literal["auto", "v2", "v3"] = "auto"


EngineFactory = Callable[[Mapping[str, object]], object]


class PaddleOcrBackend:
    """PaddleOCR implementation of :class:`OcrBackend`, initialized on first use."""

    def __init__(
        self,
        config: PaddleOcrConfig,
        *,
        engine_factory: EngineFactory | None = None,
    ) -> None:
        self._config = config
        self._engine_factory = engine_factory
        self._engine: object | None = None
        self._version: str | None = None
        self._active_api_family: Literal["v2", "v3"] | None = None

    @property
    def loaded(self) -> bool:
        return self._engine is not None

    @property
    def config(self) -> PaddleOcrConfig:
        return self._config

    def recognize(
        self,
        image: Image.Image,
        *,
        language_hints: Sequence[str] = (),
    ) -> BackendOcrOutput:
        engine = self._load_engine()
        array = self._image_array(image)
        family = self._active_api_family
        if family == "v3":
            predict = getattr(engine, "predict", None)
            if not callable(predict):
                raise OcrInferenceError("PaddleOCR v3 engine does not expose predict()")
            payload = predict(array)
            lines = _parse_v3_payload(payload, language=self._config.language)
        elif family == "v2":
            recognize = getattr(engine, "ocr", None)
            if not callable(recognize):
                raise OcrInferenceError("PaddleOCR v2 engine does not expose ocr()")
            payload = recognize(array, cls=False)
            lines = _parse_v2_payload(payload, language=self._config.language)
        else:  # pragma: no cover - protected by _load_engine
            raise OcrInferenceError("PaddleOCR API family was not initialized")

        hints = list(dict.fromkeys([self._config.language, *language_hints]))
        return BackendOcrOutput(
            lines=lines,
            invocation=OcrModelInvocation(
                engine="PaddleOCR",
                version=self._version or "unknown",
                backend=f"paddleocr-{family}",
                detection_model=Path(self._config.detection_model_dir).name,
                recognition_model=Path(self._config.recognition_model_dir).name,
                device=self._config.device,
                language_hints=hints,
                local_models_only=True,
            ),
        )

    def _load_engine(self) -> object:
        if self._engine is not None:
            return self._engine
        detection_dir = _validate_local_model_dir(
            self._config.detection_model_dir,
            label="detection",
        )
        recognition_dir = _validate_local_model_dir(
            self._config.recognition_model_dir,
            label="recognition",
        )
        if (
            self._engine_factory is None
            and self._config.device.casefold() not in {"cpu", "none"}
        ):
            capability = detect_acceleration_capabilities().ocr
            if not capability.cuda_available:
                raise OcrDependencyError(
                    "NVIDIA OCR acceleration is unavailable: " + capability.reason
                )
        if self._engine_factory is not None:
            self._version = "injected"
            return self._create_engine(
                self._engine_factory,
                detection_dir=detection_dir,
                recognition_dir=recognition_dir,
            )

        try:
            paddleocr = importlib.import_module("paddleocr")
        except ImportError as error:
            raise OcrDependencyError(
                "The bundled PaddleOCR runtime is unavailable. Use the full Video2Notes "
                "portable build or repair the packaged runtime."
            ) from error
        raw_version = getattr(paddleocr, "__version__", "unknown")
        self._version = str(raw_version)
        constructor = getattr(paddleocr, "PaddleOCR", None)
        if not callable(constructor):
            raise OcrDependencyError("installed paddleocr package does not expose PaddleOCR")

        def factory(arguments: Mapping[str, object]) -> object:
            return cast(Callable[..., object], constructor)(**arguments)

        return self._create_engine(
            factory,
            detection_dir=detection_dir,
            recognition_dir=recognition_dir,
        )

    def _create_engine(
        self,
        factory: EngineFactory,
        *,
        detection_dir: Path,
        recognition_dir: Path,
    ) -> object:
        requested = self._config.api_family
        if requested in {"auto", "v3"}:
            v3_arguments: dict[str, object] = {
                "text_detection_model_dir": str(detection_dir),
                "text_recognition_model_dir": str(recognition_dir),
                "use_doc_orientation_classify": False,
                "use_doc_unwarping": False,
                "use_textline_orientation": False,
                "lang": self._config.language,
                "device": self._config.device,
                "enable_mkldnn": self._config.enable_mkldnn,
                "cpu_threads": self._config.cpu_threads,
            }
            detection_name = _local_model_name(detection_dir)
            recognition_name = _local_model_name(recognition_dir)
            if detection_name is not None:
                v3_arguments["text_detection_model_name"] = detection_name
            if recognition_name is not None:
                v3_arguments["text_recognition_model_name"] = recognition_name
            try:
                self._engine = factory(v3_arguments)
                self._active_api_family = "v3"
                return self._engine
            except TypeError:
                if requested == "v3":
                    raise

        v2_arguments: dict[str, object] = {
            "det_model_dir": str(detection_dir),
            "rec_model_dir": str(recognition_dir),
            "use_angle_cls": False,
            "lang": self._config.language,
            "show_log": False,
            "use_gpu": self._config.device.casefold() not in {"cpu", "none"},
            "cpu_threads": self._config.cpu_threads,
        }
        self._engine = factory(v2_arguments)
        self._active_api_family = "v2"
        return self._engine

    @staticmethod
    def _image_array(image: Image.Image) -> object:
        try:
            numpy = importlib.import_module("numpy")
        except ImportError as error:  # pragma: no cover - PaddleOCR declares NumPy
            raise OcrDependencyError(
                "PaddleOCR inference requires NumPy, but it is not installed"
            ) from error
        asarray = getattr(numpy, "asarray", None)
        if not callable(asarray):
            raise OcrDependencyError("installed NumPy does not expose asarray()")
        return cast(Callable[[object], object], asarray)(image.convert("RGB"))


def _validate_local_model_dir(value: str, *, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise OcrConfigurationError(
            f"local PaddleOCR {label} model directory does not exist: {path}"
        )
    if not any(item.is_file() for item in path.rglob("*")):
        raise OcrConfigurationError(
            f"local PaddleOCR {label} model directory contains no model files: {path}"
        )
    return path


def _local_model_name(model_dir: Path) -> str | None:
    """Read the model identity required by PaddleOCR 3.7 without network access."""

    inference_config = model_dir / "inference.yml"
    if inference_config.is_file():
        for raw_line in inference_config.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line.startswith("model_name:"):
                value = line.partition(":")[2].strip().strip("\"'")
                if value:
                    return value
    directory_name = model_dir.name
    if directory_name.endswith("_infer"):
        return directory_name.removesuffix("_infer")
    return None


def _parse_v2_payload(payload: object, *, language: str) -> list[BackendOcrLine]:
    lines: list[BackendOcrLine] = []

    def visit(value: object) -> None:
        sequence = _as_sequence(value)
        if sequence is None:
            return
        parsed = _parse_v2_line(sequence, language=language)
        if parsed is not None:
            lines.append(parsed)
            return
        for item in sequence:
            visit(item)

    visit(payload)
    return lines


def _parse_v2_line(
    value: Sequence[object],
    *,
    language: str,
) -> BackendOcrLine | None:
    if len(value) != 2:
        return None
    polygon = _polygon(value[0])
    recognition = _as_sequence(value[1])
    if polygon is None or recognition is None or len(recognition) < 2:
        return None
    text = recognition[0]
    confidence = recognition[1]
    if not isinstance(text, str) or not isinstance(confidence, (int, float)):
        return None
    return BackendOcrLine(
        raw_text=text,
        box=_box_from_polygon(polygon),
        confidence=float(confidence),
        language=language,
    )


def _parse_v3_payload(payload: object, *, language: str) -> list[BackendOcrLine]:
    lines: list[BackendOcrLine] = []
    for result in _as_sequence(payload) or [payload]:
        mapping = _result_mapping(result)
        if mapping is None:
            continue
        nested = mapping.get("res")
        if isinstance(nested, Mapping):
            mapping = cast(Mapping[str, object], nested)
        texts = _as_sequence(mapping.get("rec_texts")) or []
        scores = _as_sequence(mapping.get("rec_scores")) or []
        polygons = (
            _as_sequence(mapping.get("rec_polys")) or _as_sequence(mapping.get("dt_polys")) or []
        )
        for text, score, raw_polygon in zip(texts, scores, polygons, strict=False):
            polygon = _polygon(raw_polygon)
            if not isinstance(text, str) or not isinstance(score, (int, float)) or polygon is None:
                continue
            lines.append(
                BackendOcrLine(
                    raw_text=text,
                    box=_box_from_polygon(polygon),
                    confidence=float(score),
                    language=language,
                )
            )
    return lines


def _result_mapping(value: object) -> Mapping[str, object] | None:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    json_value = getattr(value, "json", None)
    if callable(json_value):
        json_value = json_value()
    if isinstance(json_value, str):
        try:
            decoded = json.loads(json_value)
        except json.JSONDecodeError:
            return None
        return cast(Mapping[str, object], decoded) if isinstance(decoded, Mapping) else None
    if isinstance(json_value, Mapping):
        return cast(Mapping[str, object], json_value)
    result_value = getattr(value, "res", None)
    if isinstance(result_value, Mapping):
        return {"res": cast(Mapping[str, object], result_value)}
    return None


def _polygon(value: object) -> list[tuple[float, float]] | None:
    points = _as_sequence(value)
    if points is None or len(points) < 2:
        return None
    polygon: list[tuple[float, float]] = []
    for point in points:
        coordinates = _as_sequence(point)
        if coordinates is None or len(coordinates) < 2:
            return None
        x, y = coordinates[0], coordinates[1]
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            return None
        polygon.append((float(x), float(y)))
    return polygon


def _box_from_polygon(points: list[tuple[float, float]]) -> OcrBox:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    left, right = min(xs), max(xs)
    top, bottom = min(ys), max(ys)
    if right <= left or bottom <= top:
        raise OcrInferenceError("PaddleOCR returned a degenerate text polygon")
    return OcrBox(x=left, y=top, width=right - left, height=bottom - top)


def _as_sequence(value: object) -> Sequence[object] | None:
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return cast(Sequence[object], value)
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        converted = tolist()
        if isinstance(converted, Sequence):
            return cast(Sequence[object], converted)
    return None
