"""Injectable ASR execution and a lazily loaded faster-whisper backend."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Iterable
from decimal import ROUND_HALF_UP, Decimal
from importlib import import_module
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Protocol, cast

from pydantic import Field

from video2notes.domain import EvidenceModality, EvidenceSpan

from .models import (
    ASREvidenceResult,
    ASRSegment,
    ASRTranscript,
    ASRWord,
    AudioExtractionResult,
    AudioModel,
    TranscriptTimeline,
)


class ASRDependencyError(RuntimeError):
    """Raised when an explicitly selected ASR backend is unavailable."""


class ASRBackend(Protocol):
    """Substitutable ASR backend; fake implementations need only this method."""

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
    ) -> ASRTranscript:
        """Return timestamps relative to sample zero in the supplied audio file."""


class FasterWhisperConfig(AudioModel):
    model_path: str
    device: str = "auto"
    compute_type: str = "default"
    cpu_threads: int = Field(default=0, ge=0)
    beam_size: int = Field(default=5, ge=1)
    vad_filter: bool = True
    vad_parameters: dict[str, float | int | bool] = Field(default_factory=dict)


class _WhisperWord(Protocol):
    start: float
    end: float
    word: str
    probability: float


class _WhisperSegment(Protocol):
    id: int
    start: float
    end: float
    text: str
    avg_logprob: float
    no_speech_prob: float
    words: list[_WhisperWord] | None


class _WhisperInfo(Protocol):
    language: str
    language_probability: float


class _WhisperModel(Protocol):
    def transcribe(
        self,
        audio: str,
        **kwargs: object,
    ) -> tuple[Iterable[_WhisperSegment], _WhisperInfo]: ...


WhisperModelFactory = Callable[..., _WhisperModel]


class FasterWhisperBackend:
    """Offline adapter that never imports or initializes faster-whisper early."""

    def __init__(self, config: FasterWhisperConfig) -> None:
        self.config = config
        self._model: _WhisperModel | None = None
        self._version: str | None = None

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
    ) -> ASRTranscript:
        path = audio_path.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"audio does not exist: {path}")
        model = self._load_model()
        segments_iterable, info = model.transcribe(
            str(path),
            language=language,
            beam_size=self.config.beam_size,
            vad_filter=self.config.vad_filter,
            vad_parameters=dict(self.config.vad_parameters),
            word_timestamps=True,
        )
        detected_language = info.language or language
        provider = "faster-whisper"
        model_name = self.config.model_path
        version = self._version or "unknown"
        segments: list[ASRSegment] = []
        for raw_segment in segments_iterable:
            if not raw_segment.text.strip():
                continue
            start_us = max(0, _seconds_to_us(raw_segment.start))
            end_us = max(start_us, _seconds_to_us(raw_segment.end))
            words: list[ASRWord] = []
            for raw_word in raw_segment.words or []:
                if not raw_word.word.strip():
                    continue
                word_start = min(end_us, max(start_us, _seconds_to_us(raw_word.start)))
                word_end = min(end_us, max(word_start, _seconds_to_us(raw_word.end)))
                probability = _clamp_probability(raw_word.probability)
                words.append(
                    ASRWord(
                        start_us=word_start,
                        end_us=word_end,
                        text=raw_word.word,
                        language=detected_language,
                        raw_confidence=raw_word.probability,
                        calibrated_confidence=probability,
                        confidence_method="provider_probability_clamped_v1",
                        provider=provider,
                        model=model_name,
                        version=version,
                    )
                )
            segment_confidence = _segment_confidence(
                raw_segment.avg_logprob,
                raw_segment.no_speech_prob,
            )
            segments.append(
                ASRSegment(
                    id=f"fw-{raw_segment.id}",
                    start_us=start_us,
                    end_us=end_us,
                    text=raw_segment.text,
                    words=words,
                    language=detected_language,
                    raw_confidence=raw_segment.avg_logprob,
                    calibrated_confidence=segment_confidence,
                    confidence_method="exp_logprob_times_speech_probability_v1",
                    provider=provider,
                    model=model_name,
                    version=version,
                )
            )
        return ASRTranscript(
            provider=provider,
            model=model_name,
            version=version,
            language=detected_language,
            language_probability=_clamp_probability(info.language_probability),
            timeline=TranscriptTimeline.AUDIO_FILE,
            timeline_offset_us=0,
            segments=segments,
        )

    def _load_model(self) -> _WhisperModel:
        if self._model is not None:
            return self._model
        try:
            module = import_module("faster_whisper")
        except ImportError as error:
            raise ASRDependencyError(
                "faster-whisper is not installed; install the local ASR extra with "
                "`pip install 'video2notes[asr]'`"
            ) from error
        factory = cast(WhisperModelFactory, module.WhisperModel)
        try:
            self._version = package_version("faster-whisper")
        except PackageNotFoundError:
            raw_version = getattr(module, "__version__", "unknown")
            self._version = str(raw_version)
        self._model = factory(
            self.config.model_path,
            device=self.config.device,
            compute_type=self.config.compute_type,
            cpu_threads=self.config.cpu_threads,
            local_files_only=True,
        )
        return self._model


def transcribe_to_evidence(
    extraction: AudioExtractionResult,
    backend: ASRBackend,
    *,
    run_id: str,
    language: str | None = None,
) -> ASREvidenceResult:
    """Run an injected backend and shift all hypotheses to canonical media time."""

    relative = backend.transcribe(Path(extraction.output_path), language=language)
    if relative.timeline is not TranscriptTimeline.AUDIO_FILE:
        raise ValueError("ASR backend must return timestamps relative to the audio file")
    if relative.timeline_offset_us != 0:
        raise ValueError("audio-file-relative ASR output must have a zero timeline offset")
    canonical = _shift_to_canonical(
        relative,
        offset_us=extraction.output_time_zero_canonical_us,
    )
    evidence = [
        _segment_to_evidence(segment, run_id=run_id, segment_index=index)
        for index, segment in enumerate(canonical.segments)
    ]
    return ASREvidenceResult(run_id=run_id, transcript=canonical, evidence=evidence)


def _shift_to_canonical(transcript: ASRTranscript, *, offset_us: int) -> ASRTranscript:
    shifted_segments: list[ASRSegment] = []
    for segment in transcript.segments:
        shifted_words = [
            ASRWord.model_validate(
                {
                    **word.model_dump(),
                    "start_us": word.start_us + offset_us,
                    "end_us": word.end_us + offset_us,
                }
            )
            for word in segment.words
        ]
        shifted_segments.append(
            ASRSegment.model_validate(
                {
                    **segment.model_dump(exclude={"words"}),
                    "start_us": segment.start_us + offset_us,
                    "end_us": segment.end_us + offset_us,
                    "words": shifted_words,
                }
            )
        )
    return ASRTranscript.model_validate(
        {
            **transcript.model_dump(exclude={"segments"}),
            "timeline": TranscriptTimeline.CANONICAL_MEDIA,
            "timeline_offset_us": offset_us,
            "segments": shifted_segments,
        }
    )


def _segment_to_evidence(
    segment: ASRSegment,
    *,
    run_id: str,
    segment_index: int,
) -> EvidenceSpan:
    payload = (
        f"{run_id}\0{segment_index}\0{segment.start_us}\0{segment.end_us}\0{segment.text}"
    ).encode()
    return EvidenceSpan(
        id=f"asr-{hashlib.sha256(payload).hexdigest()[:20]}",
        run_id=run_id,
        modality=EvidenceModality.ASR,
        start_us=segment.start_us,
        end_us=segment.end_us,
        language=segment.language,
        raw_text=segment.text,
        normalized_text=" ".join(segment.text.split()),
        confidence=segment.calibrated_confidence,
        confidence_kind=segment.confidence_method,
        provider=segment.provider,
        model=segment.model,
        version=segment.version,
        speaker=segment.speaker,
        provenance={
            "segment_id": segment.id,
            "segment_index": segment_index,
            "raw_confidence": segment.raw_confidence,
            "calibrated_confidence": segment.calibrated_confidence,
            "words": [word.model_dump(mode="json") for word in segment.words],
        },
    )


def _seconds_to_us(value: float) -> int:
    return int((Decimal(str(value)) * Decimal(1_000_000)).to_integral_value(rounding=ROUND_HALF_UP))


def _clamp_probability(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return min(1.0, max(0.0, value))


def _segment_confidence(avg_logprob: float, no_speech_probability: float) -> float:
    if not math.isfinite(avg_logprob):
        return 0.0
    token_probability = math.exp(min(0.0, avg_logprob))
    speech_probability = 1.0 - _clamp_probability(no_speech_probability)
    return _clamp_probability(token_probability * speech_probability)
