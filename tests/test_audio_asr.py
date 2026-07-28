from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
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
                language="zh",
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
                    r"video2notes\[asr\]",
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
        self.assertEqual(
            transcription_calls[0]["vad_parameters"],
            {"min_silence_duration_ms": 300},
        )
        self.assertEqual(transcript.language, "en")
        self.assertEqual(transcript.language_probability, 0.97)
        self.assertEqual(transcript.segments[0].version, "1.2.3")
        self.assertEqual(transcript.segments[0].words[0].start_us, 110_000)


if __name__ == "__main__":
    unittest.main()
