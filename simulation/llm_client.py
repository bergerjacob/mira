"""
MIRA: OpenRouter LLM Client
Provides unified interface to multiple LLM providers via OpenRouter API.
Supports structured outputs (JSON schema enforcement).
"""

import requests
import json
from typing import Optional, Dict, Any, List
from dataclasses import dataclass


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
    
    # Recommended models for code/spatial reasoning
    MODELS = {
        "glm-5": "z-ai/glm-5",
        "kimi-k2.5": "moonshotai/kimi-k2.5", 
        "gemini-flash-lite": "google/gemini-3.1-flash-lite-preview",
        "claude-sonnet": "anthropic/claude-sonnet-4.6",
        "gpt-4o": "openai/gpt-4o",
    }
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
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
        
        # Add provider preferences for structured outputs
        payload["provider"] = {
            "require_parameters": True
        }
        
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
