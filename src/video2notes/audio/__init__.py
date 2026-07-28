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
    extract_audio_window,
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
    build_secondary_asr_decisions,
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
    "build_secondary_asr_decisions",
    "evaluate_secondary_asr_window",
    "extract_audio",
    "extract_audio_window",
    "parse_subtitle_file",
    "parse_subtitle_text",
    "parse_subtitle_timestamp",
    "select_audio_stream",
    "transcribe_to_evidence",
]
