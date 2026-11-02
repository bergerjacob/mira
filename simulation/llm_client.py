"""
MIRA: OpenRouter LLM Client
Provides unified interface to multiple LLM providers via OpenRouter API.
Supports structured outputs (JSON schema enforcement).
"""

import os
import re
import requests
import json
from typing import Optional, Dict, Any, List
from dataclasses import dataclass


def repair_json(raw_text: str) -> Optional[dict]:
    """
    Attempt to repair malformed JSON from LLM output.
    
    Common issues handled:
    - Markdown code block wrappers (```json ... ```)
    - Extra text before/after JSON
    - Trailing commas before } or ]
    - Missing closing brackets
    
    Returns parsed dict on success, None on failure.
    """
    if not raw_text or not raw_text.strip():
        return None
    
    text = raw_text.strip()
    
    # 1. Strip markdown code block wrappers
    text = re.sub(r'^```(?:json)?\s*\n?', '', text)
    text = re.sub(r'\n?```\s*$', '', text)
    
    # 2. Extract JSON object (find first { and matching last })
    first_brace = text.find('{')
    if first_brace == -1:
        return None
    
    # Try to find matching closing brace via depth tracking
    depth = 0
    end_idx = -1
    for i in range(first_brace, len(text)):
        ch = text[i]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                end_idx = i + 1
                break
    
    if end_idx == -1:
        # Unmatched braces — try adding missing closing brackets
        text = text[first_brace:]
    else:
        text = text[first_brace:end_idx]
    
    # 3. Remove trailing commas before } or ]
    text = re.sub(r',\s*([}\]])', r'\1', text)
    
    # 4. Try to parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    # 5. More aggressive repair: balance brackets
    open_braces = text.count('{')
    close_braces = text.count('}')
    open_brackets = text.count('[')
    close_brackets = text.count(']')
    
    if open_braces > close_braces:
        text += '}' * (open_braces - close_braces)
    if open_brackets > close_brackets:
        text += ']' * (open_brackets - close_brackets)
    
    text = re.sub(r',\s*([}\]])', r'\1', text)
    
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _get_api_key() -> str:
    """Get API key from environment variable. Raises error if not set."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY environment variable not set.\n"
            "Please set it before running:\n"
            "  export OPENROUTER_API_KEY='your-key-here'\n"
            "Or add it to your .bashrc/.zshrc file."
        )
    return api_key


@dataclass
class ChatMessage:
    role: str
    content: str


@dataclass
class LLMResponse:
    content: str
    model: str
    usage: Dict[str, int]
    raw_response: Dict[str, Any]


class OpenRouterClient:
    """
    Client for OpenRouter API with structured output support.
    """
    
    BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
    
    # Recommended models for code/spatial reasoning (July 2026 pricing)
    # Tier 1: Best value workhorses
    MODELS = {
        # Tier 1: Primary workhorses (best bang-for-buck, newest)
        "deepseek-v4-flash": "deepseek/deepseek-v4-flash",        # $0.09/$0.19, 1M ctx, SWE-bench 80.6%, MIT
        "gemma-4-31b": "google/gemma-4-31b-it",                   # $0.12/$0.37, 262K ctx, LiveCodeBench 80.0%, Apache 2.0
        "gemma-4-26b": "google/gemma-4-26b-a4b-it",               # $0.07/$0.34, 262K ctx, LiveCodeBench 77.1%, MoE
        "minimax-m3": "minimax/minimax-m3",                       # $0.30/$1.20, 1M ctx, SWE-bench Pro 59.0%, open weights
        "qwen3.7-plus": "qwen/qwen3.7-plus",                     # $0.32/$1.28, 1M ctx, agentic coding
        # Tier 2: High-quality
        "deepseek-v4-pro": "deepseek/deepseek-v4-pro",            # $0.44/$0.87, 1M ctx, SWE-bench 80.6%, MIT
        "qwen3.6-plus": "qwen/qwen3.6-plus",                      # $0.33/$1.95, 1M ctx, SWE-bench 78.8%
        "kimi-k2.6": "moonshotai/kimi-k2.6",                     # $0.68/$3.42, 262K ctx, SWE-bench Pro 58.6%, MIT
        "glm-5.2": "z-ai/glm-5.2",                               # $0.94/$2.95, 1M ctx, SWE-bench Pro 62.1%, MIT
        # Tier 3: Premium
        "gemini-3.5-flash": "google/gemini-3.5-flash",            # $1.50/$9.00, 1M ctx, TB 2.1 76.2%
        "claude-sonnet-5": "anthropic/claude-sonnet-5",          # $2.00/$10.00, 1M ctx, SWE-bench Pro 63.2%
        "qwen3.7-max": "qwen/qwen3.7-max",                       # $1.48/$4.43, 1M ctx, agentic flagship
        # Legacy (kept for backwards compat)
        "gemini-flash-lite": "google/gemini-3.1-flash-lite",      # $0.25/$1.50, 1M ctx (GA, not preview)
        "qwen3-coder-30b": "qwen/qwen3-coder-30b-a3b-instruct",  # $0.07/$0.27, 160K ctx (legacy)
        "llama-3.3-70b": "meta-llama/llama-3.3-70b-instruct",    # $0.13/$0.40, 131K ctx (legacy)
        "glm-5": "z-ai/glm-5",                                    # $0.95/$3.15, 202K ctx (legacy)
        "kimi-k3": "moonshotai/kimi-k3",                          # $3.00/$15.00, 1M ctx (legacy alias)
        "kimi-k2.5": "moonshotai/kimi-k2.5",                      # $0.57/$2.85, 256K ctx (legacy)
        "claude-sonnet": "anthropic/claude-sonnet-4.6",            # $3.00/$15.00, 1M ctx (legacy)
        "gpt-4o": "openai/gpt-4o",                                # $2.50/$10.00, 128K ctx (legacy)
    }
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or _get_api_key()
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })
    
    @staticmethod
    def _is_gemini_model(model: str) -> bool:
        """Check if the resolved model ID is a Google Gemini model.
        
        Gemini models (e.g., google/gemini-3.5-flash) use a different structured
        output format (responseMimeType/responseSchema) instead of the standard
        OpenAI response_format.  Other Google models like Gemma (google/gemma-*)
        use the standard format and should NOT be matched.
        """
        return model.startswith("google/gemini-")
    
    def chat(
        self,
        model: str,
        messages: List[ChatMessage],
        system_prompt: Optional[str] = None,
        temperature: float = 0.0,
        response_format: Optional[Dict[str, Any]] = None,
        max_tokens: int = 4096,
        reasoning_effort: Optional[str] = None,
    ) -> LLMResponse:
        """
        Send a chat completion request to OpenRouter.
        
        Args:
            model: Model identifier (use MODELS dict keys or full model string)
            messages: List of ChatMessage objects
            system_prompt: Optional system prompt (prepended to messages)
            temperature: Sampling temperature (0.0 for deterministic)
            response_format: Optional structured output schema
            max_tokens: Maximum tokens to generate
            reasoning_effort: Optional reasoning effort for thinking models.
                "none" = disable reasoning tokens (recommended for structured output)
                "low" / "medium" / "high" = control reasoning depth
            
        Returns:
            LLMResponse with content, model, usage stats, and raw response
        """
        # Resolve model name
        if model in self.MODELS:
            model = self.MODELS[model]
        
        is_gemini = self._is_gemini_model(model)
        
        # Build messages list
        messages_list = []
        if system_prompt:
            messages_list.append({"role": "system", "content": system_prompt})
        
        for msg in messages:
            messages_list.append({"role": msg.role, "content": msg.content})
        
        # Build request — Gemini models use a different API format
        # (responseMimeType/responseSchema instead of response_format,
        # and don't support temperature or reasoning_effort)
        payload = {
            "model": model,
            "messages": messages_list,
            "max_tokens": max_tokens,
        }
        
        if is_gemini:
            # Gemini models: use responseMimeType + responseSchema at top level
            if response_format:
                payload["responseMimeType"] = "application/json"
                payload["responseSchema"] = response_format["json_schema"]["schema"]
            # Note: Do NOT send temperature or reasoning for Gemini —
            # they use a different parameter structure and may reject unknown fields.
        else:
            # Standard OpenAI-compatible format for all other providers
            payload["temperature"] = temperature
            
            if response_format:
                payload["response_format"] = response_format
            
            # Add reasoning effort control (for Qwen, DeepSeek, and other thinking models)
            # "none" disables reasoning tokens — essential for structured output
            # Only send if model supports it; some providers 404 on unknown parameters
            if reasoning_effort is not None:
                payload["reasoning"] = {"effort": reasoning_effort}
        
        # Note: Do NOT set provider.require_parameters=True — it causes 404s for models
        # that don't support the reasoning parameter. Let OpenRouter route freely.
        
        try:
            response = self.session.post(self.BASE_URL, json=payload, timeout=120)
            response.raise_for_status()
            
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            
            return LLMResponse(
                content=content,
                model=data["model"],
                usage=data.get("usage", {}),
                raw_response=data,
            )
        except requests.RequestException as e:
            raise RuntimeError(f"OpenRouter API error: {e}")
    
    def complete_with_schema(
        self,
        model: str,
        prompt: str,
        system_prompt: str,
        schema: Dict[str, Any],
        temperature: float = 0.0,
        reasoning_effort: Optional[str] = "none",
        max_tokens: int = 8192,
    ) -> Dict[str, Any]:
        """
        Send a request with structured JSON schema output.
        Returns parsed JSON object.
        
        Args:
            model: Model identifier
            prompt: User prompt
            system_prompt: System instructions
            schema: JSON schema for structured output
            temperature: Sampling temperature
            reasoning_effort: Control for thinking models. Default "none" disables
                reasoning tokens (essential for structured output to work correctly).
                Set to "low"/"medium"/"high" to enable reasoning, but note this may
                cause issues with json_schema enforcement on some models.
            max_tokens: Maximum output tokens (default 8192). Circuits with many
                blocks may need more. Gemini models are particularly sensitive to
                truncation — increase if JSON is cut off.
            
        Returns:
            Parsed JSON response matching the schema
        """
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "structured_output",
                "strict": True,
                "schema": schema,
            }
        }
        
        response = self.chat(
            model=model,
            messages=[ChatMessage(role="user", content=prompt)],
            system_prompt=system_prompt,
            temperature=temperature,
            response_format=response_format,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
        )
        
        # Parse JSON response - some models put JSON in 'reasoning' field
        content_to_parse = response.content
        
        # Check if content is None and try reasoning field
        if content_to_parse is None:
            reasoning = response.raw_response.get("choices", [{}])[0].get("message", {}).get("reasoning")
            if reasoning:
                content_to_parse = reasoning
        
        if content_to_parse is None:
            raise RuntimeError(f"Model returned no content. Raw response: {response.raw_response}")
        
        # Parse JSON response — attempt repair if initial parse fails
        try:
            return json.loads(content_to_parse)
        except json.JSONDecodeError as e:
            repaired = repair_json(content_to_parse)
            if repaired is not None:
                return repaired
            raise RuntimeError(f"Failed to parse JSON response: {e}. Raw: {content_to_parse}")
