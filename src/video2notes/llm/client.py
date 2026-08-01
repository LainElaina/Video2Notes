"""Protocol-specific non-streaming structured-generation adapters.

The serializable request and result models contain no credentials.  Every
adapter owns its protocol's URL, authentication headers, request shape,
response extraction, and usage mapping; the shared layer only normalizes the
project-level generation contract and safe failures.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, field_validator

JsonTransport = Callable[
    [str, Mapping[str, str], Mapping[str, Any], float],
    Mapping[str, Any],
]


class ClientModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EndpointStyle(StrEnum):
    """Deprecated schema-v1 OpenAI endpoint selector."""

    RESPONSES = "responses"
    CHAT_COMPLETIONS = "chat_completions"


class ImageInput(ClientModel):
    data_url: str
    detail: str = "high"

    @field_validator("data_url")
    @classmethod
    def require_data_url(cls, value: str) -> str:
        if not value.startswith("data:image/"):
            raise ValueError("image input must be a data:image data URL")
        if ";base64," not in value:
            raise ValueError("image input must be a base64 data URL")
        return value


class GenerationRequest(ClientModel):
    role: str
    system_prompt: str
    user_prompt: str
    schema_name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    json_schema: dict[str, Any]
    images: list[ImageInput] = Field(default_factory=list)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int = Field(default=8_192, gt=0)
    reasoning_effort: str | None = None


class GenerationResult(ClientModel):
    provider: str
    model: str
    role: str
    parsed: dict[str, Any]
    raw_text: str
    latency_seconds: float = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    request_id: str | None = None


class GenerationError(RuntimeError):
    """Safe provider failure that never includes authorization or prompt data."""


class StructuredGenerationBackend(Protocol):
    provider_id: str
    model_id: str

    def generate(self, request: GenerationRequest) -> GenerationResult: ...


class _JsonStructuredBackend:
    def __init__(
        self,
        *,
        provider_id: str,
        model_id: str,
        base_url: str,
        timeout_seconds: float = 180,
        transport: JsonTransport | None = None,
    ):
        if not base_url.strip():
            raise ValueError("base_url cannot be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.provider_id = provider_id
        self.model_id = model_id
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._transport = transport or _json_transport

    def generate(self, request: GenerationRequest) -> GenerationResult:
        started = time.perf_counter()
        try:
            response = self._transport(
                self._endpoint(),
                self._headers(),
                self._payload(request),
                self.timeout_seconds,
            )
            raw_text = self._output_text(response)
            parsed = _parse_json_object(raw_text)
            input_tokens, output_tokens = self._usage(response)
            request_id = self._request_id(response)
        except GenerationError:
            raise
        except Exception as error:
            raise GenerationError(
                f"{self.provider_id}/{self.model_id} structured generation failed: "
                f"{type(error).__name__}"
            ) from None
        return GenerationResult(
            provider=self.provider_id,
            model=self.model_id,
            role=request.role,
            parsed=parsed,
            raw_text=raw_text,
            latency_seconds=max(0, time.perf_counter() - started),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            request_id=request_id,
        )

    def _endpoint(self) -> str:
        raise NotImplementedError

    def _headers(self) -> Mapping[str, str]:
        raise NotImplementedError

    def _payload(self, request: GenerationRequest) -> dict[str, Any]:
        raise NotImplementedError

    def _output_text(self, response: Mapping[str, Any]) -> str:
        raise NotImplementedError

    def _usage(self, response: Mapping[str, Any]) -> tuple[int | None, int | None]:
        return _usage_tokens(response.get("usage"))

    def _request_id(self, response: Mapping[str, Any]) -> str | None:
        return _optional_string(response.get("id"))


class OpenAICompatibleBackend(_JsonStructuredBackend):
    """OpenAI Responses or Chat Completions, retained as the v1 compatibility API."""

    def __init__(
        self,
        *,
        provider_id: str,
        model_id: str,
        base_url: str,
        endpoint_style: EndpointStyle,
        api_key: str | None = None,
        auth_headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 180,
        transport: JsonTransport | None = None,
        legacy_max_tokens: bool = False,
    ):
        super().__init__(
            provider_id=provider_id,
            model_id=model_id,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )
        self.endpoint_style = endpoint_style
        self._api_key = api_key
        self._auth_headers = dict(auth_headers or {})
        self.legacy_max_tokens = legacy_max_tokens

    def _endpoint(self) -> str:
        path = (
            "/responses"
            if self.endpoint_style is EndpointStyle.RESPONSES
            else "/chat/completions"
        )
        return _join_url(self.base_url, path)

    def _headers(self) -> Mapping[str, str]:
        headers = {"Content-Type": "application/json"}
        headers.update(self._auth_headers)
        if self._api_key and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _payload(self, request: GenerationRequest) -> dict[str, Any]:
        if self.endpoint_style is EndpointStyle.RESPONSES:
            return _openai_responses_payload(self.model_id, request)
        return _openai_chat_payload(
            self.model_id,
            request,
            legacy_max_tokens=self.legacy_max_tokens,
        )

    def _output_text(self, response: Mapping[str, Any]) -> str:
        if self.endpoint_style is EndpointStyle.RESPONSES:
            return _responses_output_text(response)
        return _chat_output_text(response)


class OpenAIResponsesBackend(OpenAICompatibleBackend):
    def __init__(
        self,
        *,
        provider_id: str,
        model_id: str,
        base_url: str,
        api_key: str | None = None,
        auth_headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 180,
        transport: JsonTransport | None = None,
    ):
        super().__init__(
            provider_id=provider_id,
            model_id=model_id,
            base_url=base_url,
            endpoint_style=EndpointStyle.RESPONSES,
            api_key=api_key,
            auth_headers=auth_headers,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )


class OpenAIChatCompletionsBackend(OpenAICompatibleBackend):
    def __init__(
        self,
        *,
        provider_id: str,
        model_id: str,
        base_url: str,
        api_key: str | None = None,
        auth_headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 180,
        transport: JsonTransport | None = None,
        legacy_max_tokens: bool = False,
    ):
        super().__init__(
            provider_id=provider_id,
            model_id=model_id,
            base_url=base_url,
            endpoint_style=EndpointStyle.CHAT_COMPLETIONS,
            api_key=api_key,
            auth_headers=auth_headers,
            timeout_seconds=timeout_seconds,
            transport=transport,
            legacy_max_tokens=legacy_max_tokens,
        )


class AnthropicMessagesBackend(_JsonStructuredBackend):
    def __init__(
        self,
        *,
        provider_id: str,
        model_id: str,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 180,
        transport: JsonTransport | None = None,
        anthropic_version: str = "2023-06-01",
    ):
        if not api_key:
            raise ValueError("Anthropic Messages requires an API key")
        super().__init__(
            provider_id=provider_id,
            model_id=model_id,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )
        self._api_key = api_key
        self.anthropic_version = anthropic_version

    def _endpoint(self) -> str:
        return _join_url(self.base_url, "/messages")

    def _headers(self) -> Mapping[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": self.anthropic_version,
        }

    def _payload(self, request: GenerationRequest) -> dict[str, Any]:
        content: list[dict[str, Any]] = [{"type": "text", "text": request.user_prompt}]
        for image in request.images:
            media_type, data = _split_image_data_url(image.data_url)
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": data,
                    },
                }
            )
        # Sampling parameters are intentionally omitted. Current Claude models
        # reject non-default temperature/top_p/top_k; callers can use prompts.
        return {
            "model": self.model_id,
            "system": request.system_prompt,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": request.max_output_tokens,
            "output_config": {
                "format": {
                    "type": "json_schema",
                    "schema": request.json_schema,
                }
            },
        }

    def _output_text(self, response: Mapping[str, Any]) -> str:
        stop_reason = response.get("stop_reason")
        if stop_reason in {"refusal", "max_tokens", "model_context_window_exceeded"}:
            raise GenerationError(f"Anthropic generation stopped: {stop_reason}")
        content = response.get("content")
        if not isinstance(content, list):
            raise GenerationError("Anthropic response has no content")
        chunks = [
            item["text"]
            for item in content
            if isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ]
        if not chunks:
            raise GenerationError("Anthropic response has no text output")
        return "".join(chunks)


class GeminiGenerateContentBackend(_JsonStructuredBackend):
    def __init__(
        self,
        *,
        provider_id: str,
        model_id: str,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 180,
        transport: JsonTransport | None = None,
    ):
        if not api_key:
            raise ValueError("Gemini generateContent requires an API key")
        super().__init__(
            provider_id=provider_id,
            model_id=model_id,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )
        self._api_key = api_key

    def _endpoint(self) -> str:
        model = _gemini_model_path_segment(self.model_id)
        return _join_url(self.base_url, f"/models/{model}:generateContent")

    def _headers(self) -> Mapping[str, str]:
        return {
            "Content-Type": "application/json",
            "x-goog-api-key": self._api_key,
        }

    def _payload(self, request: GenerationRequest) -> dict[str, Any]:
        parts: list[dict[str, Any]] = [{"text": request.user_prompt}]
        for image in request.images:
            media_type, data = _split_image_data_url(image.data_url)
            parts.append(
                {
                    "inlineData": {
                        "mimeType": media_type,
                        "data": data,
                    }
                }
            )
        generation_config: dict[str, Any] = {
            "responseMimeType": "application/json",
            "responseJsonSchema": request.json_schema,
            "maxOutputTokens": request.max_output_tokens,
        }
        if request.temperature is not None:
            generation_config["temperature"] = request.temperature
        if request.reasoning_effort is not None:
            generation_config["thinkingConfig"] = {
                "thinkingLevel": request.reasoning_effort
            }
        return {
            "contents": [{"role": "user", "parts": parts}],
            "systemInstruction": {"parts": [{"text": request.system_prompt}]},
            "generationConfig": generation_config,
            "store": False,
        }

    def _output_text(self, response: Mapping[str, Any]) -> str:
        candidates = response.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise GenerationError("Gemini generateContent response has no candidates")
        first = candidates[0]
        if not isinstance(first, dict):
            raise GenerationError("Gemini generateContent candidate is malformed")
        finish_reason = first.get("finishReason")
        if finish_reason not in {None, "STOP"}:
            raise GenerationError(f"Gemini generation stopped: {finish_reason}")
        content = first.get("content")
        if not isinstance(content, dict):
            raise GenerationError("Gemini generateContent candidate has no content")
        parts = content.get("parts")
        chunks = _text_parts(parts)
        if not chunks:
            raise GenerationError("Gemini generateContent response has no text output")
        return "".join(chunks)

    def _usage(self, response: Mapping[str, Any]) -> tuple[int | None, int | None]:
        usage = response.get("usageMetadata")
        if not isinstance(usage, dict):
            return None, None
        return (
            _optional_nonnegative_int(usage.get("promptTokenCount")),
            _optional_nonnegative_int(usage.get("candidatesTokenCount")),
        )

    def _request_id(self, response: Mapping[str, Any]) -> str | None:
        return _optional_string(response.get("responseId"))


class GeminiInteractionsBackend(_JsonStructuredBackend):
    def __init__(
        self,
        *,
        provider_id: str,
        model_id: str,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 180,
        transport: JsonTransport | None = None,
    ):
        if not api_key:
            raise ValueError("Gemini Interactions requires an API key")
        super().__init__(
            provider_id=provider_id,
            model_id=model_id,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )
        self._api_key = api_key

    def _endpoint(self) -> str:
        return _join_url(self.base_url, "/interactions")

    def _headers(self) -> Mapping[str, str]:
        return {
            "Content-Type": "application/json",
            "x-goog-api-key": self._api_key,
        }

    def _payload(self, request: GenerationRequest) -> dict[str, Any]:
        interaction_input: str | list[dict[str, Any]]
        if request.images:
            items: list[dict[str, Any]] = [{"type": "text", "text": request.user_prompt}]
            for image in request.images:
                media_type, data = _split_image_data_url(image.data_url)
                items.append(
                    {
                        "type": "image",
                        "mime_type": media_type,
                        "data": data,
                    }
                )
            interaction_input = items
        else:
            interaction_input = request.user_prompt
        generation_config: dict[str, Any] = {
            "max_output_tokens": request.max_output_tokens,
        }
        if request.temperature is not None:
            generation_config["temperature"] = request.temperature
        if request.reasoning_effort is not None:
            generation_config["thinking_level"] = request.reasoning_effort
        return {
            "model": self.model_id,
            "input": interaction_input,
            "system_instruction": request.system_prompt,
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": request.json_schema,
            },
            "generation_config": generation_config,
            "store": False,
        }

    def _output_text(self, response: Mapping[str, Any]) -> str:
        status = response.get("status")
        if status not in {None, "completed"}:
            raise GenerationError(f"Gemini interaction did not complete: {status}")
        steps = response.get("steps")
        if not isinstance(steps, list):
            raise GenerationError("Gemini interaction has no steps")
        chunks: list[str] = []
        for step in steps:
            if not isinstance(step, dict) or step.get("type") != "model_output":
                continue
            chunks.extend(_text_parts(step.get("content")))
        if not chunks:
            raise GenerationError("Gemini interaction has no text output")
        return "".join(chunks)

    def _usage(self, response: Mapping[str, Any]) -> tuple[int | None, int | None]:
        usage = response.get("usage")
        if not isinstance(usage, dict):
            return None, None
        return (
            _optional_nonnegative_int(usage.get("total_input_tokens")),
            _optional_nonnegative_int(usage.get("total_output_tokens")),
        )


class OllamaNativeChatBackend(_JsonStructuredBackend):
    def _endpoint(self) -> str:
        return _join_url(self.base_url, "/api/chat")

    def _headers(self) -> Mapping[str, str]:
        return {"Content-Type": "application/json"}

    def _payload(self, request: GenerationRequest) -> dict[str, Any]:
        user_message: dict[str, Any] = {
            "role": "user",
            "content": request.user_prompt,
        }
        if request.images:
            user_message["images"] = [
                _split_image_data_url(image.data_url)[1] for image in request.images
            ]
        options: dict[str, Any] = {"num_predict": request.max_output_tokens}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        payload: dict[str, Any] = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                user_message,
            ],
            "format": request.json_schema,
            "stream": False,
            "options": options,
        }
        if request.reasoning_effort is not None:
            payload["think"] = request.reasoning_effort
        return payload

    def _output_text(self, response: Mapping[str, Any]) -> str:
        if "error" in response:
            raise GenerationError("Ollama returned a generation error")
        if response.get("done") is False:
            raise GenerationError("Ollama non-streaming response is incomplete")
        message = response.get("message")
        if not isinstance(message, dict):
            raise GenerationError("Ollama response has no message")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise GenerationError("Ollama response has no text output")
        return content

    def _usage(self, response: Mapping[str, Any]) -> tuple[int | None, int | None]:
        return (
            _optional_nonnegative_int(response.get("prompt_eval_count")),
            _optional_nonnegative_int(response.get("eval_count")),
        )

    def _request_id(self, response: Mapping[str, Any]) -> str | None:
        return None


def _openai_responses_payload(
    model_id: str,
    request: GenerationRequest,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "input_text", "text": request.user_prompt}]
    content.extend(
        {
            "type": "input_image",
            "image_url": image.data_url,
            "detail": image.detail,
        }
        for image in request.images
    )
    payload: dict[str, Any] = {
        "model": model_id,
        "instructions": request.system_prompt,
        "input": [{"role": "user", "content": content}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": request.schema_name,
                "strict": True,
                "schema": request.json_schema,
            }
        },
        "max_output_tokens": request.max_output_tokens,
        "store": False,
    }
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.reasoning_effort is not None:
        payload["reasoning"] = {"effort": request.reasoning_effort}
    return payload


def _openai_chat_payload(
    model_id: str,
    request: GenerationRequest,
    *,
    legacy_max_tokens: bool,
) -> dict[str, Any]:
    user_content: str | list[dict[str, Any]]
    if request.images:
        user_parts: list[dict[str, Any]] = [{"type": "text", "text": request.user_prompt}]
        user_parts.extend(
            {
                "type": "image_url",
                "image_url": {
                    "url": image.data_url,
                    "detail": image.detail,
                },
            }
            for image in request.images
        )
        user_content = user_parts
    else:
        user_content = request.user_prompt
    payload: dict[str, Any] = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": user_content},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": request.schema_name,
                "strict": True,
                "schema": request.json_schema,
            },
        },
        (
            "max_tokens" if legacy_max_tokens else "max_completion_tokens"
        ): request.max_output_tokens,
    }
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.reasoning_effort is not None:
        payload["reasoning_effort"] = request.reasoning_effort
    return payload


def _json_transport(
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any],
    timeout: float,
) -> Mapping[str, Any]:
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers=dict(headers),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        raise GenerationError(f"provider returned HTTP {error.code}") from None
    except (urllib.error.URLError, TimeoutError):
        raise GenerationError("provider request failed or timed out") from None
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise GenerationError("provider returned invalid JSON") from None
    if not isinstance(decoded, dict):
        raise GenerationError("provider response must be a JSON object")
    return decoded


def _responses_output_text(response: Mapping[str, Any]) -> str:
    status = response.get("status")
    if status in {"failed", "cancelled", "incomplete"}:
        raise GenerationError(f"Responses generation did not complete: {status}")
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    output = response.get("output")
    if not isinstance(output, list):
        raise GenerationError("Responses payload has no output")
    chunks: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "refusal":
                raise GenerationError("Responses model refused the request")
            if part.get("type") in {"output_text", "text"}:
                text = part.get("text")
                if isinstance(text, str):
                    chunks.append(text)
    if not chunks:
        raise GenerationError("Responses payload has no text output")
    return "".join(chunks)


def _chat_output_text(response: Mapping[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise GenerationError("Chat Completions payload has no choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise GenerationError("Chat Completions choice is malformed")
    finish_reason = first.get("finish_reason")
    if finish_reason in {"length", "content_filter"}:
        raise GenerationError(f"Chat Completions generation stopped: {finish_reason}")
    message = first.get("message")
    if not isinstance(message, dict):
        raise GenerationError("Chat Completions choice has no message")
    if message.get("refusal"):
        raise GenerationError("Chat Completions model refused the request")
    content = message.get("content")
    if isinstance(content, str):
        return content
    chunks = _text_parts(content)
    if chunks:
        return "".join(chunks)
    raise GenerationError("Chat Completions message has no text content")


def _text_parts(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        item["text"]
        for item in value
        if isinstance(item, dict)
        and item.get("type") in {None, "text", "output_text"}
        and isinstance(item.get("text"), str)
    ]


def _split_image_data_url(data_url: str) -> tuple[str, str]:
    try:
        header, data = data_url.split(",", 1)
        media_type = header.removeprefix("data:").split(";", 1)[0]
    except ValueError:
        raise GenerationError("image data URL is malformed") from None
    if not media_type.startswith("image/") or not data:
        raise GenerationError("image data URL is malformed")
    return media_type, data


def _gemini_model_path_segment(model_id: str) -> str:
    normalized = model_id.removeprefix("models/")
    if not normalized:
        raise GenerationError("Gemini model ID is empty")
    return quote(normalized, safe="-._~")


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _parse_json_object(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        last_fence = text.rfind("```")
        if first_newline >= 0 and last_fence > first_newline:
            text = text[first_newline + 1 : last_fence].strip()
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise GenerationError("model output is not a JSON object") from None
        try:
            decoded = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            raise GenerationError("model output contains invalid JSON") from None
    if not isinstance(decoded, dict):
        raise GenerationError("model output must be a JSON object")
    return decoded


def _usage_tokens(usage: object) -> tuple[int | None, int | None]:
    if not isinstance(usage, dict):
        return None, None
    raw_input = usage.get("input_tokens", usage.get("prompt_tokens"))
    raw_output = usage.get("output_tokens", usage.get("completion_tokens"))
    return _optional_nonnegative_int(raw_input), _optional_nonnegative_int(raw_output)


def _optional_nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
