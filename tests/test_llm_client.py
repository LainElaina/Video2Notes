from __future__ import annotations

import unittest
from collections.abc import Mapping
from typing import Any

from video2notes.llm import (
    EndpointStyle,
    GenerationError,
    GenerationRequest,
    OpenAICompatibleBackend,
)


def request() -> GenerationRequest:
    return GenerationRequest(
        role="notes.fact_extractor",
        system_prompt="Only use supplied evidence.",
        user_prompt="Evidence: ev-1",
        schema_name="fact_cards",
        json_schema={
            "type": "object",
            "properties": {"facts": {"type": "array"}},
            "required": ["facts"],
            "additionalProperties": False,
        },
        temperature=0,
    )


class LlmClientTests(unittest.TestCase):
    def test_responses_payload_uses_strict_schema_and_store_false(self) -> None:
        captured: dict[str, Any] = {}

        def transport(
            url: str,
            headers: Mapping[str, str],
            payload: Mapping[str, Any],
            timeout: float,
        ) -> Mapping[str, Any]:
            captured.update(
                url=url,
                headers=dict(headers),
                payload=dict(payload),
                timeout=timeout,
            )
            return {
                "id": "resp-1",
                "output_text": '{"facts":[]}',
                "usage": {"input_tokens": 10, "output_tokens": 4},
            }

        backend = OpenAICompatibleBackend(
            provider_id="openai",
            model_id="configured-model",
            base_url="https://api.example.test/v1",
            endpoint_style=EndpointStyle.RESPONSES,
            api_key="secret-value",
            transport=transport,
        )
        result = backend.generate(request())

        self.assertEqual(captured["url"], "https://api.example.test/v1/responses")
        payload = captured["payload"]
        self.assertFalse(payload["store"])
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertEqual(result.parsed, {"facts": []})
        self.assertEqual(result.input_tokens, 10)
        self.assertNotIn("secret-value", result.model_dump_json())

    def test_chat_payload_and_fenced_json_compatibility(self) -> None:
        captured: dict[str, Any] = {}

        def transport(
            url: str,
            headers: Mapping[str, str],
            payload: Mapping[str, Any],
            timeout: float,
        ) -> Mapping[str, Any]:
            del headers, timeout
            captured.update(url=url, payload=dict(payload))
            return {
                "choices": [{"message": {"content": '```json\n{"facts": []}\n```'}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            }

        backend = OpenAICompatibleBackend(
            provider_id="ollama",
            model_id="local-model",
            base_url="http://127.0.0.1:11434/v1/",
            endpoint_style=EndpointStyle.CHAT_COMPLETIONS,
            transport=transport,
        )
        result = backend.generate(request())
        self.assertEqual(
            captured["url"],
            "http://127.0.0.1:11434/v1/chat/completions",
        )
        self.assertEqual(
            captured["payload"]["response_format"]["type"],
            "json_schema",
        )
        self.assertEqual(result.output_tokens, 2)

    def test_provider_error_does_not_echo_secret_or_prompt(self) -> None:
        def transport(
            url: str,
            headers: Mapping[str, str],
            payload: Mapping[str, Any],
            timeout: float,
        ) -> Mapping[str, Any]:
            del url, headers, payload, timeout
            raise RuntimeError("authorization secret-value Evidence: ev-1")

        backend = OpenAICompatibleBackend(
            provider_id="cloud",
            model_id="model",
            base_url="https://example.test/v1",
            endpoint_style=EndpointStyle.RESPONSES,
            api_key="secret-value",
            transport=transport,
        )
        with self.assertRaises(GenerationError) as caught:
            backend.generate(request())
        self.assertNotIn("secret-value", str(caught.exception))
        self.assertNotIn("Evidence: ev-1", str(caught.exception))
