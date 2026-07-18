"""
MIRA: OpenRouter LLM Client
Provides unified interface to multiple LLM providers via OpenRouter API.
Supports structured outputs (JSON schema enforcement).
"""

import os
import requests
import json
from typing import Optional, Dict, Any, List
from dataclasses import dataclass


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
        # Tier 1: Primary workhorses (best bang-for-buck)
        "deepseek-v4-flash": "deepseek/deepseek-v4-flash",        # $0.09/$0.18, 1M ctx, SWE-bench 79%
        "qwen3-coder-30b": "qwen/qwen3-coder-30b-a3b-instruct",   # $0.07/$0.27, 160K ctx, code MoE
        # Tier 2: High-quality
        "deepseek-v4-pro": "deepseek/deepseek-v4-pro",            # $0.71/$1.43, 1M ctx, SWE-bench 80.6%
        "llama-3.3-70b": "meta-llama/llama-3.3-70b-instruct",     # $0.13/$0.40, 131K ctx, reliable 70B
        # Tier 3: Specialized
        "glm-5.2": "z-ai/glm-5.2",                                # $0.30/$0.94, 1M ctx, engineering focus
        "kimi-k3": "moonshotai/kimi-k3",                          # $3.00/$15.00, 1M ctx, newest flagship
        # Legacy (kept for backwards compat; gemini-flash-lite-preview was shut down May 2026)
        "gemini-flash-lite": "google/gemini-3.1-flash-lite",       # $0.25/$1.50, 1M ctx (GA, not preview)
        "glm-5": "z-ai/glm-5",                                    # $0.95/$3.15, 202K ctx (legacy)
        "kimi-k2.5": "moonshotai/kimi-k2.5",                      # $0.57/$2.85, 256K ctx (legacy)
        "claude-sonnet": "anthropic/claude-sonnet-4.6",            # $3.00/$15.00, 1M ctx
        "gpt-4o": "openai/gpt-4o",                                # $2.50/$10.00, 128K ctx
    }
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or _get_api_key()
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })
    
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
        
        # Build messages list
        messages_list = []
        if system_prompt:
            messages_list.append({"role": "system", "content": system_prompt})
        
        for msg in messages:
            messages_list.append({"role": msg.role, "content": msg.content})
        
        # Build request
        payload = {
            "model": model,
            "messages": messages_list,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        # Add structured output format if provided
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
        
        # Parse JSON response
        try:
            return json.loads(content_to_parse)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Failed to parse JSON response: {e}. Raw: {content_to_parse}")
