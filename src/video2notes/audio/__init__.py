"""Audio extraction, subtitle parsing, and timestamp-calibrated ASR."""

from .asr import (
    ASRBackend,
    ASRDependencyError,
    FasterWhisperBackend,
    FasterWhisperConfig,
    transcribe_to_evidence,
)
from .extract import (
    AudioExtractionError,
    build_audio_extraction_command,
    extract_audio,
    select_audio_stream,
)
from .models import (
    ASREvidenceResult,
    ASRSegment,
    ASRTranscript,
    ASRWord,
    AudioExtractionResult,
    TranscriptTimeline,
)
from .secondary import (
    SecondaryASRDecision,
    SecondaryASRPolicy,
    SecondaryASRReason,
    evaluate_secondary_asr_window,
)
from .subtitles import (
    SubtitleParseError,
    parse_subtitle_file,
    parse_subtitle_text,
    parse_subtitle_timestamp,
)

__all__ = [
    "ASRBackend",
    "ASRDependencyError",
    "ASREvidenceResult",
    "ASRSegment",
    "ASRTranscript",
    "ASRWord",
    "AudioExtractionError",
    "AudioExtractionResult",
    "FasterWhisperBackend",
    "FasterWhisperConfig",
    "SecondaryASRDecision",
    "SecondaryASRPolicy",
    "SecondaryASRReason",
    "SubtitleParseError",
    "TranscriptTimeline",
    "build_audio_extraction_command",
    "evaluate_secondary_asr_window",
    "extract_audio",
    "parse_subtitle_file",
    "parse_subtitle_text",
    "parse_subtitle_timestamp",
    "select_audio_stream",
    "transcribe_to_evidence",
]
