from __future__ import annotations

import unittest
from collections.abc import Mapping
from typing import Any

from video2notes.llm import (
    AnthropicMessagesBackend,
    EndpointStyle,
    GeminiGenerateContentBackend,
    GeminiInteractionsBackend,
    GenerationError,
    GenerationRequest,
    ImageInput,
    OllamaNativeChatBackend,
    OpenAIChatCompletionsBackend,
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
        self.assertEqual(captured["payload"]["max_completion_tokens"], 8_192)
        self.assertNotIn("max_tokens", captured["payload"])
        self.assertEqual(result.output_tokens, 2)

    def test_chat_legacy_max_tokens_is_an_explicit_opt_in(self) -> None:
        captured: dict[str, Any] = {}

        def transport(
            url: str,
            headers: Mapping[str, str],
            payload: Mapping[str, Any],
            timeout: float,
        ) -> Mapping[str, Any]:
            del url, headers, timeout
            captured.update(payload=dict(payload))
            return {"choices": [{"message": {"content": '{"facts":[]}'}}]}

        backend = OpenAIChatCompletionsBackend(
            provider_id="legacy-proxy",
            model_id="legacy-model",
            base_url="https://legacy.example/v1",
            legacy_max_tokens=True,
            transport=transport,
        )
        backend.generate(request())

        self.assertEqual(captured["payload"]["max_tokens"], 8_192)
        self.assertNotIn("max_completion_tokens", captured["payload"])

    def test_anthropic_messages_contract(self) -> None:
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
                "id": "msg-1",
                "content": [{"type": "text", "text": '{"facts":[]}'}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 21, "output_tokens": 5},
            }

        backend = AnthropicMessagesBackend(
            provider_id="anthropic",
            model_id="configured-model",
            base_url="https://api.anthropic.test/v1/",
            api_key="anthropic-secret",
            transport=transport,
        )
        result = backend.generate(request())

        self.assertEqual(captured["url"], "https://api.anthropic.test/v1/messages")
        self.assertEqual(captured["headers"]["x-api-key"], "anthropic-secret")
        self.assertEqual(captured["headers"]["anthropic-version"], "2023-06-01")
        payload = captured["payload"]
        self.assertEqual(payload["system"], "Only use supplied evidence.")
        self.assertNotEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(
            payload["output_config"]["format"]["type"],
            "json_schema",
        )
        self.assertNotIn("temperature", payload)
        self.assertEqual(result.input_tokens, 21)
        self.assertNotIn("anthropic-secret", result.model_dump_json())

    def test_gemini_generate_content_contract_with_inline_image(self) -> None:
        captured: dict[str, Any] = {}

        def transport(
            url: str,
            headers: Mapping[str, str],
            payload: Mapping[str, Any],
            timeout: float,
        ) -> Mapping[str, Any]:
            del timeout
            captured.update(url=url, headers=dict(headers), payload=dict(payload))
            return {
                "responseId": "gemini-response",
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {
                            "parts": [{"type": "text", "text": '{"facts":[]}'}]
                        },
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 17,
                    "candidatesTokenCount": 6,
                },
            }

        generation_request = request().model_copy(
            update={
                "images": [ImageInput(data_url="data:image/png;base64,AA==")],
                "reasoning_effort": "low",
            }
        )
        backend = GeminiGenerateContentBackend(
            provider_id="google",
            model_id="models/user-selected-model",
            base_url="https://generativelanguage.example/v1beta",
            api_key="google-secret",
            transport=transport,
        )
        result = backend.generate(generation_request)

        self.assertEqual(
            captured["url"],
            "https://generativelanguage.example/v1beta/models/user-selected-model:generateContent",
        )
        self.assertEqual(captured["headers"]["x-goog-api-key"], "google-secret")
        payload = captured["payload"]
        self.assertEqual(
            payload["generationConfig"]["responseMimeType"],
            "application/json",
        )
        self.assertEqual(
            payload["generationConfig"]["responseJsonSchema"],
            generation_request.json_schema,
        )
        self.assertEqual(
            payload["contents"][0]["parts"][1]["inlineData"]["data"],
            "AA==",
        )
        self.assertEqual(result.request_id, "gemini-response")
        self.assertEqual(result.output_tokens, 6)

    def test_gemini_interactions_contract(self) -> None:
        captured: dict[str, Any] = {}

        def transport(
            url: str,
            headers: Mapping[str, str],
            payload: Mapping[str, Any],
            timeout: float,
        ) -> Mapping[str, Any]:
            del timeout
            captured.update(url=url, headers=dict(headers), payload=dict(payload))
            return {
                "id": "interaction-1",
                "status": "completed",
                "steps": [
                    {
                        "type": "model_output",
                        "content": [{"type": "text", "text": '{"facts":[]}'}],
                    }
                ],
                "usage": {"total_input_tokens": 33, "total_output_tokens": 9},
            }

        backend = GeminiInteractionsBackend(
            provider_id="google",
            model_id="user-selected-model",
            base_url="https://generativelanguage.example/v1beta",
            api_key="google-secret",
            transport=transport,
        )
        result = backend.generate(request())

        self.assertEqual(
            captured["url"],
            "https://generativelanguage.example/v1beta/interactions",
        )
        payload = captured["payload"]
        self.assertEqual(payload["model"], "user-selected-model")
        self.assertEqual(payload["system_instruction"], "Only use supplied evidence.")
        self.assertFalse(payload["store"])
        self.assertEqual(payload["response_format"]["type"], "text")
        self.assertEqual(
            payload["response_format"]["mime_type"],
            "application/json",
        )
        self.assertEqual(payload["response_format"]["schema"], request().json_schema)
        self.assertEqual(result.input_tokens, 33)
        self.assertEqual(result.output_tokens, 9)

    def test_ollama_native_chat_contract_disables_ndjson_streaming(self) -> None:
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
                "message": {"role": "assistant", "content": '{"facts":[]}'},
                "done": True,
                "prompt_eval_count": 12,
                "eval_count": 4,
            }

        backend = OllamaNativeChatBackend(
            provider_id="ollama",
            model_id="user-installed-model",
            base_url="http://127.0.0.1:11434/",
            transport=transport,
        )
        result = backend.generate(request())

        self.assertEqual(captured["url"], "http://127.0.0.1:11434/api/chat")
        payload = captured["payload"]
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["format"], request().json_schema)
        self.assertEqual(payload["options"]["num_predict"], 8_192)
        self.assertEqual(result.input_tokens, 12)
        self.assertEqual(result.output_tokens, 4)

    def test_protocol_stop_status_is_not_accepted_as_structured_output(self) -> None:
        def transport(
            url: str,
            headers: Mapping[str, str],
            payload: Mapping[str, Any],
            timeout: float,
        ) -> Mapping[str, Any]:
            del url, headers, payload, timeout
            return {
                "status": "incomplete",
                "output_text": '{"facts":[]}',
            }

        backend = OpenAICompatibleBackend(
            provider_id="openai",
            model_id="model",
            base_url="https://example.test/v1",
            endpoint_style=EndpointStyle.RESPONSES,
            transport=transport,
        )
        with self.assertRaisesRegex(GenerationError, "did not complete"):
            backend.generate(request())

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
