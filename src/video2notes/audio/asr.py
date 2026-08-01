"""Injectable ASR execution and a lazily loaded faster-whisper backend."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from importlib import import_module
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Protocol, cast, runtime_checkable

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


@runtime_checkable
class MultilingualASRBackend(Protocol):
    """Optional extension for backends that can switch language inside one file."""

    def transcribe_multilingual(
        self,
        audio_path: Path,
        *,
        language_hints: Sequence[str] = (),
    ) -> ASRTranscript:
        """Detect language repeatedly while preserving one physical timeline."""


def _default_vad_parameters() -> dict[str, float | int | bool]:
    # faster-whisper's conservative default waits for two seconds of silence.
    # Notes benefit from natural phrase boundaries while retaining padding.
    return {
        "threshold": 0.5,
        "min_speech_duration_ms": 100,
        "min_silence_duration_ms": 500,
        "speech_pad_ms": 200,
    }


class FasterWhisperConfig(AudioModel):
    model_path: str
    device: str = "auto"
    compute_type: str = "default"
    cpu_threads: int = Field(default=0, ge=0)
    beam_size: int = Field(default=5, ge=1)
    vad_filter: bool = True
    vad_parameters: dict[str, float | int | bool] = Field(default_factory=_default_vad_parameters)
    multilingual: bool = False
    language_detection_threshold: float = Field(default=0.70, ge=0, le=1)
    language_detection_segments: int = Field(default=3, ge=1, le=10)
    language_window_seconds: float = Field(default=30.0, ge=5.0, le=30.0)


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
    all_language_probs: list[tuple[str, float]] | None


class _AudioWaveform(Protocol):
    def __len__(self) -> int: ...

    def __getitem__(self, key: slice) -> _AudioWaveform: ...


class _DecodeAudio(Protocol):
    def __call__(
        self,
        input_file: str,
        *,
        sampling_rate: int,
    ) -> _AudioWaveform: ...


class _WhisperModel(Protocol):
    def transcribe(
        self,
        audio: str,
        **kwargs: object,
    ) -> tuple[Iterable[_WhisperSegment], _WhisperInfo]: ...

    def detect_language(
        self,
        *,
        audio: _AudioWaveform,
        vad_filter: bool,
        vad_parameters: dict[str, float | int | bool],
        language_detection_segments: int,
        language_detection_threshold: float,
    ) -> tuple[str, float, list[tuple[str, float]]]: ...


WhisperModelFactory = Callable[..., _WhisperModel]


@dataclass(frozen=True, slots=True)
class _LanguageLabel:
    language: str | None
    probability: float | None
    method: str


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
        normalized_language = _normalize_language_hint(language) if language is not None else None
        return self._transcribe(
            audio_path,
            language=normalized_language,
            language_hints=(),
            multilingual=self.config.multilingual and normalized_language is None,
        )

    def transcribe_multilingual(
        self,
        audio_path: Path,
        *,
        language_hints: Sequence[str] = (),
    ) -> ASRTranscript:
        hints = _normalize_language_hints(language_hints)
        return self._transcribe(
            audio_path,
            language=None,
            language_hints=hints,
            multilingual=True,
        )

    def _transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None,
        language_hints: Sequence[str],
        multilingual: bool,
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
            multilingual=multilingual,
            language_detection_threshold=self.config.language_detection_threshold,
            language_detection_segments=self.config.language_detection_segments,
            condition_on_previous_text=not multilingual,
        )
        raw_segments = [item for item in segments_iterable if item.text.strip()]
        detected_language = _normalize_optional_language(info.language or language)
        global_label = _select_detected_language(
            detected_language,
            info.language_probability,
            getattr(info, "all_language_probs", None),
            language_hints,
            method="faster_whisper_file_detection_v1",
        )
        labels = (
            self._resolve_segment_languages(
                path,
                model,
                raw_segments,
                language_hints=language_hints,
                fallback=global_label,
            )
            if multilingual
            else [global_label] * len(raw_segments)
        )
        provider = "faster-whisper"
        model_name = self.config.model_path
        version = self._version or "unknown"
        segments: list[ASRSegment] = []
        for raw_segment, label in zip(raw_segments, labels, strict=True):
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
                        language=label.language,
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
                    language=label.language,
                    language_probability=label.probability,
                    language_detection_method=label.method,
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
            language=global_label.language,
            language_probability=global_label.probability,
            timeline=TranscriptTimeline.AUDIO_FILE,
            timeline_offset_us=0,
            segments=segments,
        )

    def _resolve_segment_languages(
        self,
        audio_path: Path,
        model: _WhisperModel,
        segments: Sequence[_WhisperSegment],
        *,
        language_hints: Sequence[str],
        fallback: _LanguageLabel,
    ) -> list[_LanguageLabel]:
        labels: list[_LanguageLabel | None] = []
        unresolved: list[int] = []
        for index, segment in enumerate(segments):
            explicit = _explicit_segment_language(segment)
            scripted = _script_language(segment.text, language_hints)
            label = explicit or scripted
            labels.append(label)
            if label is None:
                unresolved.append(index)
        if not unresolved:
            return cast(list[_LanguageLabel], labels)

        try:
            decoder = self._load_audio_decoder()
            waveform = decoder(str(audio_path), sampling_rate=16_000)
        except (ASRDependencyError, OSError, RuntimeError, ValueError):
            return [item or fallback for item in labels]

        cache: dict[tuple[int, int], _LanguageLabel] = {}
        same_script_hints = _has_same_script_language_choices(language_hints)
        for index in unresolved:
            segment = segments[index]
            start_seconds, end_seconds = _language_detection_interval(
                segment,
                waveform_duration_seconds=len(waveform) / 16_000,
                per_segment=same_script_hints,
                window_seconds=self.config.language_window_seconds,
            )
            key = (
                _seconds_to_us(start_seconds),
                _seconds_to_us(end_seconds),
            )
            label = cache.get(key)
            if label is None:
                start_sample = max(0, round(start_seconds * 16_000))
                end_sample = min(len(waveform), round(end_seconds * 16_000))
                if end_sample <= start_sample:
                    label = fallback
                else:
                    clip = waveform[start_sample:end_sample]
                    try:
                        detected, probability, all_probabilities = model.detect_language(
                            audio=clip,
                            vad_filter=False,
                            vad_parameters=dict(self.config.vad_parameters),
                            language_detection_segments=(self.config.language_detection_segments),
                            language_detection_threshold=(self.config.language_detection_threshold),
                        )
                    except (RuntimeError, ValueError):
                        label = fallback
                    else:
                        label = _select_detected_language(
                            detected,
                            probability,
                            all_probabilities,
                            language_hints,
                            method="faster_whisper_segment_detection_v1",
                        )
                cache[key] = label
            labels[index] = label
        return [item or fallback for item in labels]

    def _load_audio_decoder(self) -> _DecodeAudio:
        try:
            module = import_module("faster_whisper.audio")
            decoder = module.decode_audio
        except ImportError as error:
            raise ASRDependencyError("faster-whisper audio decoding is unavailable") from error
        except AttributeError as error:
            raise ASRDependencyError("faster-whisper audio decoder is unavailable") from error
        return cast(_DecodeAudio, decoder)

    def _load_model(self) -> _WhisperModel:
        if self._model is not None:
            return self._model
        try:
            module = import_module("faster_whisper")
        except ImportError as error:
            raise ASRDependencyError(
                "The bundled faster-whisper runtime is unavailable. Use the full "
                "Video2Notes portable build or repair the packaged runtime."
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


_SCRIPT_LANGUAGES: dict[str, frozenset[str]] = {
    "arabic": frozenset({"ar", "fa", "ps", "sd", "ur"}),
    "cyrillic": frozenset({"be", "bg", "kk", "mk", "mn", "ru", "sr", "tg", "uk", "uz"}),
    "devanagari": frozenset({"hi", "mr", "ne", "sa"}),
    "greek": frozenset({"el"}),
    "han": frozenset({"ja", "yue", "zh"}),
    "hangul": frozenset({"ko"}),
    "hebrew": frozenset({"he", "yi"}),
    "kana": frozenset({"ja"}),
    "latin": frozenset(
        {
            "af",
            "az",
            "bs",
            "ca",
            "cs",
            "cy",
            "da",
            "de",
            "en",
            "es",
            "et",
            "eu",
            "fi",
            "fr",
            "ga",
            "gl",
            "hr",
            "hu",
            "id",
            "is",
            "it",
            "la",
            "lt",
            "lv",
            "ms",
            "mt",
            "nl",
            "no",
            "pl",
            "pt",
            "ro",
            "sk",
            "sl",
            "sq",
            "sv",
            "sw",
            "tl",
            "tr",
            "vi",
        }
    ),
    "thai": frozenset({"th"}),
}


def _normalize_language_hint(value: str) -> str:
    normalized = value.strip().replace("_", "-").casefold()
    if not normalized:
        raise ValueError("language hint cannot be blank")
    return normalized.split("-", 1)[0]


def _normalize_optional_language(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return _normalize_language_hint(value)


def _normalize_language_hints(values: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        hint = _normalize_language_hint(value)
        if hint not in normalized:
            normalized.append(hint)
    return tuple(normalized)


def _explicit_segment_language(segment: _WhisperSegment) -> _LanguageLabel | None:
    language = _normalize_optional_language(cast(str | None, getattr(segment, "language", None)))
    if language is None:
        return None
    probability = _optional_probability(getattr(segment, "language_probability", None))
    method = getattr(segment, "language_detection_method", None)
    return _LanguageLabel(
        language=language,
        probability=probability,
        method=(method if isinstance(method, str) and method else "provider_segment_language_v1"),
    )


def _script_language(
    text: str,
    language_hints: Sequence[str],
) -> _LanguageLabel | None:
    script = _detect_script(text)
    if script is None:
        return None
    script_languages = _SCRIPT_LANGUAGES[script]
    hints = set(language_hints)
    candidates = script_languages & hints if hints else script_languages
    if len(candidates) != 1:
        return None
    return _LanguageLabel(
        language=next(iter(candidates)),
        probability=0.98,
        method="unicode_script_hint_v1",
    )


def _detect_script(text: str) -> str | None:
    codepoints = [ord(character) for character in text if character.isalpha()]
    if not codepoints:
        return None
    checks: tuple[tuple[str, Callable[[int], bool]], ...] = (
        (
            "kana",
            lambda value: 0x3040 <= value <= 0x30FF or 0x31F0 <= value <= 0x31FF,
        ),
        (
            "hangul",
            lambda value: 0x1100 <= value <= 0x11FF or 0xAC00 <= value <= 0xD7AF,
        ),
        ("thai", lambda value: 0x0E00 <= value <= 0x0E7F),
        ("greek", lambda value: 0x0370 <= value <= 0x03FF),
        ("cyrillic", lambda value: 0x0400 <= value <= 0x052F),
        ("hebrew", lambda value: 0x0590 <= value <= 0x05FF),
        ("arabic", lambda value: 0x0600 <= value <= 0x06FF),
        ("devanagari", lambda value: 0x0900 <= value <= 0x097F),
        (
            "han",
            lambda value: 0x3400 <= value <= 0x4DBF or 0x4E00 <= value <= 0x9FFF,
        ),
        (
            "latin",
            lambda value: 0x0041 <= value <= 0x024F or 0x1E00 <= value <= 0x1EFF,
        ),
    )
    counts = {script: sum(check(value) for value in codepoints) for script, check in checks}
    script, count = max(counts.items(), key=lambda item: item[1])
    return script if count > 0 else None


def _has_same_script_language_choices(language_hints: Sequence[str]) -> bool:
    if len(language_hints) < 2:
        return False
    counts: dict[str, int] = {}
    for hint in language_hints:
        script = next(
            (name for name, languages in _SCRIPT_LANGUAGES.items() if hint in languages),
            "unknown",
        )
        counts[script] = counts.get(script, 0) + 1
    return any(count > 1 for count in counts.values())


def _language_detection_interval(
    segment: _WhisperSegment,
    *,
    waveform_duration_seconds: float,
    per_segment: bool,
    window_seconds: float,
) -> tuple[float, float]:
    duration = max(0.0, waveform_duration_seconds)
    if per_segment:
        center = (max(0.0, segment.start) + max(segment.start, segment.end)) / 2
        interval_duration = max(2.0, segment.end - segment.start + 0.5)
        start = max(0.0, center - interval_duration / 2)
        end = min(duration, start + interval_duration)
        start = max(0.0, end - interval_duration)
        return start, max(start, end)
    midpoint = max(0.0, (segment.start + segment.end) / 2)
    start = math.floor(midpoint / window_seconds) * window_seconds
    end = min(duration, start + window_seconds)
    return start, max(start, end)


def _select_detected_language(
    detected: str | None,
    probability: float | None,
    all_probabilities: Sequence[tuple[str, float]] | None,
    language_hints: Sequence[str],
    *,
    method: str,
) -> _LanguageLabel:
    normalized_detected = _normalize_optional_language(detected)
    selected_language = normalized_detected
    selected_probability = _optional_probability(probability)
    if language_hints and all_probabilities:
        allowed = set(language_hints)
        candidates = [
            (_normalize_language_hint(language), _clamp_probability(score))
            for language, score in all_probabilities
            if _normalize_language_hint(language) in allowed
        ]
        if candidates:
            selected_language, selected_probability = max(
                candidates,
                key=lambda item: item[1],
            )
            method = f"{method}_hint_constrained"
    return _LanguageLabel(
        language=selected_language,
        probability=selected_probability,
        method=method,
    )


def _optional_probability(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return _clamp_probability(float(value))


def transcribe_to_evidence(
    extraction: AudioExtractionResult,
    backend: ASRBackend,
    *,
    run_id: str,
    language: str | None = None,
    language_hints: Sequence[str] = (),
) -> ASREvidenceResult:
    """Run an injected backend and shift all hypotheses to canonical media time."""

    hints = _normalize_language_hints(language_hints)
    selected_language = (
        _normalize_language_hint(language)
        if language is not None
        else (hints[0] if len(hints) == 1 else None)
    )
    if len(hints) > 1 and isinstance(backend, MultilingualASRBackend):
        relative = backend.transcribe_multilingual(
            Path(extraction.output_path),
            language_hints=hints,
        )
    else:
        relative = backend.transcribe(
            Path(extraction.output_path),
            language=selected_language,
        )
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
            "language_probability": segment.language_probability,
            "language_detection_method": segment.language_detection_method,
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
