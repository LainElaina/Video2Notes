from __future__ import annotations

import unittest

from pydantic import ValidationError

from video2notes.audio import ASRSegment, ASRTranscript, ASRWord, TranscriptTimeline


def make_word(**updates: object) -> ASRWord:
    values: dict[str, object] = {
        "start_us": 100_000,
        "end_us": 200_000,
        "text": "hello",
        "language": "en",
        "speaker": "speaker-1",
        "raw_confidence": 0.91,
        "calibrated_confidence": 0.87,
        "confidence_method": "fixture-v1",
        "provider": "fake",
        "model": "tiny",
        "version": "1.0",
    }
    values.update(updates)
    return ASRWord.model_validate(values)


def make_segment(**updates: object) -> ASRSegment:
    values: dict[str, object] = {
        "id": "segment-1",
        "start_us": 50_000,
        "end_us": 250_000,
        "text": "hello",
        "words": [make_word()],
        "language": "en",
        "speaker": "speaker-1",
        "raw_confidence": -0.1,
        "calibrated_confidence": 0.84,
        "confidence_method": "fixture-v1",
        "provider": "fake",
        "model": "tiny",
        "version": "1.0",
    }
    values.update(updates)
    return ASRSegment.model_validate(values)


class AudioModelTests(unittest.TestCase):
    def test_word_rejects_unknown_fields_and_invalid_intervals(self) -> None:
        with self.assertRaises(ValidationError):
            make_word(unexpected=True)
        with self.assertRaisesRegex(ValidationError, "end_us cannot be before"):
            make_word(start_us=200_001, end_us=200_000)

    def test_segment_requires_words_to_remain_inside_and_match_provenance(self) -> None:
        with self.assertRaisesRegex(ValidationError, "inside their segment"):
            make_segment(words=[make_word(start_us=20_000)])
        with self.assertRaisesRegex(ValidationError, "provenance must match"):
            make_segment(words=[make_word(provider="different")])

    def test_transcript_rejects_segment_provenance_drift(self) -> None:
        with self.assertRaisesRegex(ValidationError, "provenance must match"):
            ASRTranscript(
                provider="fake",
                model="other",
                version="1.0",
                language="en",
                language_probability=0.9,
                timeline=TranscriptTimeline.AUDIO_FILE,
                segments=[make_segment()],
            )

    def test_model_assignment_is_validated(self) -> None:
        word = make_word()
        with self.assertRaises(ValidationError):
            word.calibrated_confidence = 2.0


if __name__ == "__main__":
    unittest.main()
