"""Small OpenAI-compatible structured-output client.

The engine supports both the Responses API and Chat Completions because local
servers frequently implement only the latter. API keys are accepted only by
the live backend instance and are never included in serializable request/result
models.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

JsonTransport = Callable[
    [str, Mapping[str, str], Mapping[str, Any], float],
    Mapping[str, Any],
]


class ClientModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EndpointStyle(StrEnum):
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
    """Safe provider failure that never includes authorization material."""


class StructuredGenerationBackend(Protocol):
    provider_id: str
    model_id: str

    def generate(self, request: GenerationRequest) -> GenerationResult: ...


class OpenAICompatibleBackend:
    """Call a configured endpoint using Responses or Chat Completions."""

    def __init__(
        self,
        *,
        provider_id: str,
        model_id: str,
        base_url: str,
        endpoint_style: EndpointStyle,
        api_key: str | None = None,
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
        self.endpoint_style = endpoint_style
        self._api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._transport = transport or _json_transport

    def generate(self, request: GenerationRequest) -> GenerationResult:
        started = time.perf_counter()
        payload = self._payload(request)
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        endpoint = (
            f"{self.base_url}/responses"
            if self.endpoint_style is EndpointStyle.RESPONSES
            else f"{self.base_url}/chat/completions"
        )
        try:
            response = self._transport(
                endpoint,
                headers,
                payload,
                self.timeout_seconds,
            )
            raw_text = (
                _responses_output_text(response)
                if self.endpoint_style is EndpointStyle.RESPONSES
                else _chat_output_text(response)
            )
            parsed = _parse_json_object(raw_text)
            usage = response.get("usage")
            input_tokens, output_tokens = _usage_tokens(usage)
            request_id = _optional_string(response.get("id"))
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

    def _payload(self, request: GenerationRequest) -> dict[str, Any]:
        if self.endpoint_style is EndpointStyle.RESPONSES:
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
                "model": self.model_id,
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
        chat_payload: dict[str, Any] = {
            "model": self.model_id,
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
            "max_tokens": request.max_output_tokens,
        }
        if request.temperature is not None:
            chat_payload["temperature"] = request.temperature
        if request.reasoning_effort is not None:
            chat_payload["reasoning_effort"] = request.reasoning_effort
        return chat_payload


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
    message = first.get("message")
    if not isinstance(message, dict):
        raise GenerationError("Chat Completions choice has no message")
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = [
            str(item.get("text"))
            for item in content
            if isinstance(item, dict)
            and item.get("type") in {"text", "output_text"}
            and isinstance(item.get("text"), str)
        ]
        if chunks:
            return "".join(chunks)
    raise GenerationError("Chat Completions message has no text content")


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
