"""Structured text/vision generation adapters with no secret persistence."""

from .client import (
    AnthropicMessagesBackend,
    EndpointStyle,
    GeminiGenerateContentBackend,
    GeminiInteractionsBackend,
    GenerationError,
    GenerationRequest,
    GenerationResult,
    ImageInput,
    OllamaNativeChatBackend,
    OpenAIChatCompletionsBackend,
    OpenAICompatibleBackend,
    OpenAIResponsesBackend,
    StructuredGenerationBackend,
)

__all__ = [
    "AnthropicMessagesBackend",
    "EndpointStyle",
    "GeminiGenerateContentBackend",
    "GeminiInteractionsBackend",
    "GenerationError",
    "GenerationRequest",
    "GenerationResult",
    "ImageInput",
    "OllamaNativeChatBackend",
    "OpenAIChatCompletionsBackend",
    "OpenAICompatibleBackend",
    "OpenAIResponsesBackend",
    "StructuredGenerationBackend",
]
