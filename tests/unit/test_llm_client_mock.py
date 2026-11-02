"""
Mock-based unit tests for simulation/llm_client.py OpenRouterClient.

Uses unittest.mock.patch to mock HTTP calls — no real API keys or network needed.
"""
import os
import json
import pytest
from unittest.mock import patch, MagicMock
from simulation.llm_client import OpenRouterClient, ChatMessage, LLMResponse


# ---------------------------------------------------------------------------
# MODELS dict
# ---------------------------------------------------------------------------

class TestModelsDict:
    def test_models_contains_expected_keys(self):
        """MODELS dict has expected keys."""
        assert "glm-5" in OpenRouterClient.MODELS
        assert "kimi-k2.5" in OpenRouterClient.MODELS
        assert "gemini-flash-lite" in OpenRouterClient.MODELS
        assert "claude-sonnet" in OpenRouterClient.MODELS
        assert "gpt-4o" in OpenRouterClient.MODELS

    def test_models_contains_glm5(self):
        assert OpenRouterClient.MODELS["glm-5"] == "z-ai/glm-5"

    def test_models_contains_gemini_flash_lite(self):
        assert OpenRouterClient.MODELS["gemini-flash-lite"] == "google/gemini-3.1-flash-lite"

    def test_models_contains_kimi_k25(self):
        assert OpenRouterClient.MODELS["kimi-k2.5"] == "moonshotai/kimi-k2.5"

    def test_model_name_resolution(self):
        """Short name resolves to full model ID."""
        client = OpenRouterClient(api_key="test-key")
        resolved = client.MODELS.get("gemini-flash-lite")
        assert resolved == "google/gemini-3.1-flash-lite"


# ---------------------------------------------------------------------------
# Client initialization
# ---------------------------------------------------------------------------

class TestClientInit:
    def test_init_with_key(self):
        """Client can be initialized with explicit API key."""
        client = OpenRouterClient(api_key="explicit-key")
        assert client.api_key == "explicit-key"
        assert client.session.headers["Authorization"] == "Bearer explicit-key"

    def test_init_without_key_raises(self):
        """Client without key and without env var raises RuntimeError."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
                OpenRouterClient()

    def test_init_with_env_var(self):
        """Client reads key from env var when not provided."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "env-key"}):
            client = OpenRouterClient()
            assert client.api_key == "env-key"
            assert client.session.headers["Authorization"] == "Bearer env-key"


# ---------------------------------------------------------------------------
# chat() — payload building
# ---------------------------------------------------------------------------

class TestChatPayloadBuilding:
    def test_chat_builds_correct_payload(self):
        """chat() sends correct model, messages, temperature, max_tokens (non-Gemini)."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = OpenRouterClient()

        # Mock the session.post
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Hello!"}}],
            "model": "z-ai/glm-5",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        mock_response.status_code = 200
        client.session.post = MagicMock(return_value=mock_response)

        messages = [ChatMessage(role="user", content="Say hello")]
        client.chat(
            model="glm-5",
            messages=messages,
            temperature=0.5,
            max_tokens=8192,
        )

        # Verify payload
        call_args = client.session.post.call_args
        payload = call_args[1]["json"]
        assert payload["model"] == "z-ai/glm-5"
        assert payload["temperature"] == 0.5
        assert payload["max_tokens"] == 8192
        assert payload["messages"] == [{"role": "user", "content": "Say hello"}]

    def test_chat_with_system_prompt(self):
        """system_prompt is prepended as system message."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = OpenRouterClient()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "OK"}}],
            "model": "test-model",
            "usage": {},
        }
        mock_response.status_code = 200
        client.session.post = MagicMock(return_value=mock_response)

        client.chat(
            model="glm-5",
            messages=[ChatMessage(role="user", content="Hi")],
            system_prompt="You are a helper.",
        )

        payload = client.session.post.call_args[1]["json"]
        assert payload["messages"][0] == {"role": "system", "content": "You are a helper."}
        assert payload["messages"][1] == {"role": "user", "content": "Hi"}

    def test_chat_with_response_format(self):
        """response_format is added to payload when provided."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = OpenRouterClient()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "{}"}}],
            "model": "test-model",
            "usage": {},
        }
        mock_response.status_code = 200
        client.session.post = MagicMock(return_value=mock_response)

        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": "test", "strict": True, "schema": schema},
        }

        client.chat(
            model="glm-5",
            messages=[ChatMessage(role="user", content="test")],
            response_format=response_format,
        )

        payload = client.session.post.call_args[1]["json"]
        assert "response_format" in payload
        assert payload["response_format"] == response_format

    def test_chat_with_reasoning_effort(self):
        """reasoning_effort adds reasoning field to payload (non-Gemini)."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = OpenRouterClient()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "thinking..."}}],
            "model": "test-model",
            "usage": {},
        }
        mock_response.status_code = 200
        client.session.post = MagicMock(return_value=mock_response)

        client.chat(
            model="glm-5",
            messages=[ChatMessage(role="user", content="test")],
            reasoning_effort="high",
        )

        payload = client.session.post.call_args[1]["json"]
        assert "reasoning" in payload
        assert payload["reasoning"] == {"effort": "high"}

    def test_chat_gemini_skips_temperature_and_reasoning(self):
        """Gemini models don't get temperature or reasoning_effort in payload."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = OpenRouterClient()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Hello!"}}],
            "model": "google/gemini-3.1-flash-lite",
            "usage": {},
        }
        mock_response.status_code = 200
        client.session.post = MagicMock(return_value=mock_response)

        client.chat(
            model="gemini-flash-lite",
            messages=[ChatMessage(role="user", content="Hi")],
            temperature=0.5,
            reasoning_effort="none",
        )

        payload = client.session.post.call_args[1]["json"]
        assert "temperature" not in payload
        assert "reasoning" not in payload
        # But max_tokens should still be present
        assert payload["max_tokens"] == 4096

    def test_chat_gemini_uses_response_mime_type(self):
        """Gemini models use responseMimeType + responseSchema instead of response_format."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = OpenRouterClient()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"ok": true}'}}],
            "model": "google/gemini-3.1-flash-lite",
            "usage": {},
        }
        mock_response.status_code = 200
        client.session.post = MagicMock(return_value=mock_response)

        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": "test", "strict": True, "schema": schema},
        }

        client.chat(
            model="gemini-flash-lite",
            messages=[ChatMessage(role="user", content="test")],
            response_format=response_format,
        )

        payload = client.session.post.call_args[1]["json"]
        # Should NOT have standard response_format
        assert "response_format" not in payload
        # Should have Gemini-specific fields
        assert payload["responseMimeType"] == "application/json"
        assert payload["responseSchema"] == schema


# ---------------------------------------------------------------------------
# chat() — return value
# ---------------------------------------------------------------------------

class TestChatReturnValue:
    def test_chat_returns_llm_response(self):
        """chat() returns an LLMResponse with correct fields."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = OpenRouterClient()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Result"}}],
            "model": "google/gemini-3.1-flash-lite",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        mock_response.status_code = 200
        client.session.post = MagicMock(return_value=mock_response)

        result = client.chat(
            model="gemini-flash-lite",
            messages=[ChatMessage(role="user", content="Hi")],
        )

        assert isinstance(result, LLMResponse)
        assert result.content == "Result"
        assert result.model == "google/gemini-3.1-flash-lite"
        assert result.usage == {"prompt_tokens": 10, "completion_tokens": 5}
        assert "choices" in result.raw_response

    def test_chat_direct_model_string(self):
        """Using a full model string (not short name) works."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = OpenRouterClient()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
            "model": "some/custom-model",
            "usage": {},
        }
        mock_response.status_code = 200
        client.session.post = MagicMock(return_value=mock_response)

        result = client.chat(
            model="some/custom-model",
            messages=[ChatMessage(role="user", content="Hi")],
        )

        payload = client.session.post.call_args[1]["json"]
        assert payload["model"] == "some/custom-model"
        assert result.model == "some/custom-model"


# ---------------------------------------------------------------------------
# chat() — error handling
# ---------------------------------------------------------------------------

class TestChatErrorHandling:
    def test_chat_raises_on_network_error(self):
        """chat() raises RuntimeError on requests.RequestException."""
        import requests
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = OpenRouterClient()

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.RequestException("Network error")
        client.session.post = MagicMock(return_value=mock_response)

        with pytest.raises(RuntimeError, match="OpenRouter API error"):
            client.chat(
                model="gemini-flash-lite",
                messages=[ChatMessage(role="user", content="Hi")],
            )


# ---------------------------------------------------------------------------
# complete_with_schema()
# ---------------------------------------------------------------------------

class TestCompleteWithSchema:
    def test_complete_with_schema_returns_parsed_json(self):
        """complete_with_schema returns parsed JSON dict."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = OpenRouterClient()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"name": "test"}'}}],
            "model": "test-model",
            "usage": {},
        }
        mock_response.status_code = 200
        client.session.post = MagicMock(return_value=mock_response)

        schema = {"type": "object", "properties": {"name": {"type": "string"}}}

        result = client.complete_with_schema(
            model="gemini-flash-lite",
            prompt="Make JSON",
            system_prompt="You output JSON",
            schema=schema,
        )

        assert result == {"name": "test"}

    def test_complete_with_schema_raises_on_json_error(self):
        """complete_with_schema raises RuntimeError on JSON decode failure."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = OpenRouterClient()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "NOT JSON"}}],
            "model": "test-model",
            "usage": {},
        }
        mock_response.status_code = 200
        client.session.post = MagicMock(return_value=mock_response)

        schema = {"type": "object", "properties": {}}

        with pytest.raises(RuntimeError, match="Failed to parse JSON"):
            client.complete_with_schema(
                model="gemini-flash-lite",
                prompt="Make JSON",
                system_prompt="You output JSON",
                schema=schema,
            )

    def test_complete_with_schema_handles_none_content(self):
        """complete_with_schema tries reasoning field when content is None."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = OpenRouterClient()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": None,
                    "reasoning": '{"name": "from_reasoning"}',
                }
            }],
            "model": "test-model",
            "usage": {},
        }
        mock_response.status_code = 200
        client.session.post = MagicMock(return_value=mock_response)

        schema = {"type": "object", "properties": {"name": {"type": "string"}}}

        result = client.complete_with_schema(
            model="gemini-flash-lite",
            prompt="Make JSON",
            system_prompt="You output JSON",
            schema=schema,
        )

        assert result == {"name": "from_reasoning"}

    def test_complete_with_schema_raises_on_no_content(self):
        """complete_with_schema raises when both content and reasoning are None."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = OpenRouterClient()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": None,
                    # No reasoning key
                }
            }],
            "model": "test-model",
            "usage": {},
        }
        mock_response.status_code = 200
        client.session.post = MagicMock(return_value=mock_response)

        schema = {"type": "object", "properties": {}}

        with pytest.raises(RuntimeError, match="returned no content"):
            client.complete_with_schema(
                model="gemini-flash-lite",
                prompt="Make JSON",
                system_prompt="You output JSON",
                schema=schema,
            )

    def test_complete_with_schema_gemini_uses_response_mime_type(self):
        """complete_with_schema uses responseMimeType+responseSchema for Gemini."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = OpenRouterClient()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"name": "gemini_test"}'}}],
            "model": "google/gemini-3.5-flash",
            "usage": {},
        }
        mock_response.status_code = 200
        client.session.post = MagicMock(return_value=mock_response)

        schema = {"type": "object", "properties": {"name": {"type": "string"}}}

        result = client.complete_with_schema(
            model="gemini-3.5-flash",
            prompt="Make JSON",
            system_prompt="You output JSON",
            schema=schema,
        )

        # Verify return value
        assert result == {"name": "gemini_test"}

        # Verify payload uses Gemini format
        payload = client.session.post.call_args[1]["json"]
        assert "response_format" not in payload
        assert payload["responseMimeType"] == "application/json"
        assert payload["responseSchema"] == schema
        # Verify no temperature or reasoning for Gemini
        assert "temperature" not in payload
        assert "reasoning" not in payload

    def test_complete_with_schema_passes_reasoning_effort(self):
        """complete_with_schema defaults reasoning_effort to 'none' (non-Gemini)."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = OpenRouterClient()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "{}"}}],
            "model": "test-model",
            "usage": {},
        }
        mock_response.status_code = 200
        client.session.post = MagicMock(return_value=mock_response)

        schema = {"type": "object", "properties": {}}

        client.complete_with_schema(
            model="glm-5",
            prompt="Make JSON",
            system_prompt="You output JSON",
            schema=schema,
        )

        payload = client.session.post.call_args[1]["json"]
        assert payload["reasoning"] == {"effort": "none"}


# ---------------------------------------------------------------------------
# _get_api_key()
# ---------------------------------------------------------------------------

class TestGetApiKey:
    def test_get_api_key_raises_when_not_set(self):
        """_get_api_key raises RuntimeError when env var is not set."""
        from simulation.llm_client import _get_api_key
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
                _get_api_key()

    def test_get_api_key_returns_value(self):
        """_get_api_key returns the env var value when set."""
        from simulation.llm_client import _get_api_key
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "my-test-key"}):
            assert _get_api_key() == "my-test-key"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

class TestDataclasses:
    def test_chat_message_fields(self):
        """ChatMessage has role and content fields."""
        msg = ChatMessage(role="user", content="hello")
        assert msg.role == "user"
        assert msg.content == "hello"

    def test_llm_response_fields(self):
        """LLMResponse has content, model, usage, raw_response fields."""
        resp = LLMResponse(
            content="test",
            model="m1",
            usage={"tokens": 10},
            raw_response={"key": "value"},
        )
        assert resp.content == "test"
        assert resp.model == "m1"
        assert resp.usage == {"tokens": 10}
        assert resp.raw_response == {"key": "value"}

    def test_llm_response_default_usage_empty(self):
        """LLMResponse usage defaults to empty dict from mock."""
        resp = LLMResponse(content="", model="", usage={}, raw_response={})
        assert resp.usage == {}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestChatEdgeCases:
    def test_chat_empty_messages(self):
        """chat with empty messages list sends empty list."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = OpenRouterClient()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": ""}}],
            "model": "test-model",
            "usage": {},
        }
        mock_response.status_code = 200
        client.session.post = MagicMock(return_value=mock_response)

        client.chat(model="glm-5", messages=[])

        payload = client.session.post.call_args[1]["json"]
        assert payload["messages"] == []

    def test_chat_without_reasoning_effort(self):
        """When reasoning_effort is None, reasoning key is not in payload."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = OpenRouterClient()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
            "model": "test-model",
            "usage": {},
        }
        mock_response.status_code = 200
        client.session.post = MagicMock(return_value=mock_response)

        client.chat(
            model="glm-5",
            messages=[ChatMessage(role="user", content="Hi")],
            reasoning_effort=None,
        )

        payload = client.session.post.call_args[1]["json"]
        assert "reasoning" not in payload
