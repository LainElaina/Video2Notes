"""Structured text/vision generation adapters with no secret persistence."""

from .client import (
    EndpointStyle,
    GenerationError,
    GenerationRequest,
    GenerationResult,
    ImageInput,
    OpenAICompatibleBackend,
    StructuredGenerationBackend,
)

__all__ = [
    "EndpointStyle",
    "GenerationError",
    "GenerationRequest",
    "GenerationResult",
    "ImageInput",
    "OpenAICompatibleBackend",
    "StructuredGenerationBackend",
]
