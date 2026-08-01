from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from typing import cast
from unittest import mock

from video2notes.audio import (
    ASRDependencyError,
    ASRSegment,
    ASRTranscript,
    ASRWord,
    AudioExtractionResult,
    FasterWhisperBackend,
    FasterWhisperConfig,
    TranscriptTimeline,
    transcribe_to_evidence,
)
from video2notes.domain import EvidenceModality


class FakeBackend:
    def __init__(self) -> None:
        self.requested_language: str | None = None

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
    ) -> ASRTranscript:
        if not audio_path.is_file():
            raise AssertionError("fake backend expected an existing WAV")
        self.requested_language = language
        word = ASRWord(
            start_us=120_000,
            end_us=300_000,
            text=" 你好",
            language="zh",
            raw_confidence=0.93,
            calibrated_confidence=0.90,
            confidence_method="fake-calibration-v1",
            provider="fake-asr",
            model="fixture",
            version="1",
        )
        segment = ASRSegment(
            id="fake-0",
            start_us=100_000,
            end_us=500_000,
            text=" 你好",
            words=[word],
            language="zh",
            language_probability=0.94,
            language_detection_method="fake-language-v1",
            raw_confidence=-0.2,
            calibrated_confidence=0.81,
            confidence_method="fake-calibration-v1",
            provider="fake-asr",
            model="fixture",
            version="1",
        )
        return ASRTranscript(
            provider="fake-asr",
            model="fixture",
            version="1",
            language="zh",
            language_probability=0.96,
            timeline=TranscriptTimeline.AUDIO_FILE,
            segments=[segment],
        )


class ASRExecutionTests(unittest.TestCase):
    def test_injected_backend_is_shifted_to_canonical_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            wav = Path(temporary) / "speech.wav"
            wav.write_bytes(b"RIFF-fixture")
            extraction = AudioExtractionResult(
                input_path=str(Path(temporary) / "input.mkv"),
                output_path=str(wav),
                audio_stream_index=1,
                source_stream_start_us=2_250_000,
                timeline_origin_us=2_000_000,
                output_time_zero_canonical_us=250_000,
                duration_us=1_000_000,
            )
            backend = FakeBackend()
            result = transcribe_to_evidence(
                extraction,
                backend,
                run_id="run-asr",
                language_hints=["zh-Hans"],
            )

        self.assertEqual(backend.requested_language, "zh")
        self.assertEqual(result.transcript.timeline, TranscriptTimeline.CANONICAL_MEDIA)
        self.assertEqual(result.transcript.timeline_offset_us, 250_000)
        segment = result.transcript.segments[0]
        self.assertEqual((segment.start_us, segment.end_us), (350_000, 750_000))
        self.assertEqual((segment.words[0].start_us, segment.words[0].end_us), (370_000, 550_000))
        evidence = result.evidence[0]
        self.assertEqual(evidence.modality, EvidenceModality.ASR)
        self.assertEqual((evidence.start_us, evidence.end_us), (350_000, 750_000))
        self.assertEqual(evidence.provider, "fake-asr")
        self.assertEqual(evidence.confidence, 0.81)
        self.assertEqual(evidence.provenance["words"][0]["start_us"], 370_000)
        self.assertEqual(evidence.provenance["language_probability"], 0.94)
        self.assertEqual(
            evidence.provenance["language_detection_method"],
            "fake-language-v1",
        )

    def test_faster_whisper_is_lazy_and_has_clear_optional_dependency_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            wav = Path(temporary) / "speech.wav"
            wav.write_bytes(b"RIFF-fixture")
            backend = FasterWhisperBackend(FasterWhisperConfig(model_path="local/model"))
            with (
                mock.patch(
                    "video2notes.audio.asr.import_module",
                    side_effect=ImportError("not installed"),
                ),
                self.assertRaisesRegex(
                    ASRDependencyError,
                    r"full Video2Notes portable build|repair the packaged runtime",
                ),
            ):
                backend.transcribe(wav)

    def test_faster_whisper_requests_words_vad_and_local_files_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            wav = Path(temporary) / "speech.wav"
            wav.write_bytes(b"RIFF-fixture")
            constructor_calls: list[dict[str, object]] = []
            transcription_calls: list[dict[str, object]] = []

            class FakeWhisperModel:
                def transcribe(
                    self,
                    audio: str,
                    **kwargs: object,
                ) -> tuple[list[object], object]:
                    self_audio = audio
                    if self_audio != str(wav.resolve()):
                        raise AssertionError("adapter supplied the wrong audio path")
                    transcription_calls.append(kwargs)
                    word = types.SimpleNamespace(
                        start=0.11,
                        end=0.42,
                        word=" hello",
                        probability=0.88,
                    )
                    segment = types.SimpleNamespace(
                        id=7,
                        start=0.1,
                        end=0.5,
                        text=" hello",
                        avg_logprob=-0.2,
                        no_speech_prob=0.05,
                        words=[word],
                    )
                    info = types.SimpleNamespace(language="en", language_probability=0.97)
                    return [segment], info

            def factory(model_path: str, **kwargs: object) -> FakeWhisperModel:
                constructor_calls.append({"model_path": model_path, **kwargs})
                return FakeWhisperModel()

            fake_module = types.SimpleNamespace(
                WhisperModel=factory,
                __version__="fixture-version",
            )
            backend = FasterWhisperBackend(
                FasterWhisperConfig(
                    model_path="C:/models/whisper",
                    vad_parameters={"min_silence_duration_ms": 300},
                )
            )
            self.assertEqual(constructor_calls, [], "backend construction must remain lazy")
            with (
                mock.patch("video2notes.audio.asr.import_module", return_value=fake_module),
                mock.patch(
                    "video2notes.audio.asr.package_version",
                    return_value="1.2.3",
                ),
            ):
                transcript = backend.transcribe(wav)

        self.assertTrue(constructor_calls[0]["local_files_only"])
        self.assertEqual(constructor_calls[0]["model_path"], "C:/models/whisper")
        self.assertTrue(transcription_calls[0]["word_timestamps"])
        self.assertTrue(transcription_calls[0]["vad_filter"])
        self.assertFalse(transcription_calls[0]["multilingual"])
        self.assertTrue(transcription_calls[0]["condition_on_previous_text"])
        self.assertEqual(transcription_calls[0]["language_detection_segments"], 3)
        self.assertEqual(transcription_calls[0]["language_detection_threshold"], 0.70)
        self.assertEqual(
            transcription_calls[0]["vad_parameters"],
            {"min_silence_duration_ms": 300},
        )
        self.assertEqual(transcript.language, "en")
        self.assertEqual(transcript.language_probability, 0.97)
        self.assertEqual(transcript.segments[0].version, "1.2.3")
        self.assertEqual(transcript.segments[0].words[0].start_us, 110_000)

    def test_default_vad_is_phrase_sensitive_but_padded(self) -> None:
        config = FasterWhisperConfig(model_path="local/model")

        self.assertTrue(config.vad_filter)
        self.assertFalse(
            config.multilingual,
            "multi-window language detection stays opt-in unless multiple hints are supplied",
        )
        self.assertEqual(
            config.vad_parameters,
            {
                "threshold": 0.5,
                "min_speech_duration_ms": 100,
                "min_silence_duration_ms": 500,
                "speech_pad_ms": 200,
            },
        )

    def test_multiple_hints_enable_codeswitch_and_segment_language_detection(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            wav = Path(temporary) / "speech.wav"
            wav.write_bytes(b"RIFF-fixture")
            transcription_calls: list[dict[str, object]] = []
            detection_calls: list[int] = []
            detection_results = iter(
                [
                    ("en", 0.80, [("en", 0.80), ("es", 0.20)]),
                    ("fr", 0.70, [("fr", 0.70), ("es", 0.65), ("en", 0.25)]),
                ]
            )

            class FakeWhisperModel:
                def transcribe(
                    self,
                    audio: str,
                    **kwargs: object,
                ) -> tuple[list[object], object]:
                    self_audio = audio
                    if self_audio != str(wav.resolve()):
                        raise AssertionError("adapter supplied the wrong audio path")
                    transcription_calls.append(kwargs)
                    first_word = types.SimpleNamespace(
                        start=0.10,
                        end=0.70,
                        word=" hello",
                        probability=0.91,
                    )
                    second_word = types.SimpleNamespace(
                        start=4.10,
                        end=4.80,
                        word=" hola",
                        probability=0.89,
                    )
                    segments: list[object] = [
                        types.SimpleNamespace(
                            id=1,
                            start=0.0,
                            end=1.0,
                            text=" hello",
                            avg_logprob=-0.1,
                            no_speech_prob=0.01,
                            words=[first_word],
                        ),
                        types.SimpleNamespace(
                            id=2,
                            start=4.0,
                            end=5.0,
                            text=" hola",
                            avg_logprob=-0.2,
                            no_speech_prob=0.02,
                            words=[second_word],
                        ),
                    ]
                    info = types.SimpleNamespace(
                        language="en",
                        language_probability=0.75,
                        all_language_probs=[("en", 0.75), ("es", 0.20)],
                    )
                    return segments, info

                def detect_language(
                    self,
                    **kwargs: object,
                ) -> tuple[str, float, list[tuple[str, float]]]:
                    audio = cast(list[float], kwargs["audio"])
                    detection_calls.append(len(audio))
                    return next(detection_results)

            def factory(model_path: str, **kwargs: object) -> FakeWhisperModel:
                del model_path, kwargs
                return FakeWhisperModel()

            fake_module = types.SimpleNamespace(
                WhisperModel=factory,
                __version__="fixture-version",
            )
            fake_audio_module = types.SimpleNamespace(
                decode_audio=lambda path, sampling_rate: [0.0] * (sampling_rate * 10),
            )

            def load_module(name: str) -> object:
                if name == "faster_whisper":
                    return fake_module
                if name == "faster_whisper.audio":
                    return fake_audio_module
                raise ImportError(name)

            backend = FasterWhisperBackend(FasterWhisperConfig(model_path="C:/models/whisper"))
            extraction = AudioExtractionResult(
                input_path=str(Path(temporary) / "input.mkv"),
                output_path=str(wav),
                audio_stream_index=0,
                source_stream_start_us=0,
                timeline_origin_us=0,
                output_time_zero_canonical_us=0,
                duration_us=10_000_000,
            )
            with (
                mock.patch(
                    "video2notes.audio.asr.import_module",
                    side_effect=load_module,
                ),
                mock.patch(
                    "video2notes.audio.asr.package_version",
                    return_value="1.2.1",
                ),
            ):
                result = transcribe_to_evidence(
                    extraction,
                    backend,
                    run_id="run-multilingual",
                    language_hints=["en-US", "es"],
                )

        self.assertIsNone(transcription_calls[0]["language"])
        self.assertTrue(transcription_calls[0]["multilingual"])
        self.assertEqual(transcription_calls[0]["language_detection_segments"], 3)
        self.assertEqual(len(detection_calls), 2)
        self.assertEqual(
            [segment.language for segment in result.transcript.segments],
            ["en", "es"],
        )
        self.assertEqual(
            [segment.language_probability for segment in result.transcript.segments],
            [0.80, 0.65],
        )
        self.assertEqual(
            result.transcript.segments[1].language_detection_method,
            "faster_whisper_segment_detection_v1_hint_constrained",
        )
        self.assertEqual(
            (
                result.transcript.segments[1].words[0].start_us,
                result.transcript.segments[1].words[0].end_us,
            ),
            (4_100_000, 4_800_000),
        )
        self.assertEqual(result.evidence[1].language, "es")
        self.assertEqual(
            result.evidence[1].provenance["language_probability"],
            0.65,
        )


if __name__ == "__main__":
    unittest.main()
