"""
MIRA: OpenRouter API Test
Simple hello world test to verify API connectivity.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from simulation.llm_client import OpenRouterClient, ChatMessage


def test_basic_chat():
    """Test basic chat completion."""
    api_key = "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"
    client = OpenRouterClient(api_key)
    
    print("Testing basic chat with glm-5...")
    
    response = client.chat(
        model="glm-5",
        messages=[
            ChatMessage(role="user", content="What is 2+2?"),
        ],
        temperature=0.0,
    )
    
    print(f"Model: {response.model}")
    print(f"Response: {response.content}")
    print(f"Usage: {response.usage}")
    print("✓ Basic chat test passed!")
    return True


def test_structured_output():
    """Test structured JSON output."""
    api_key = "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"
    client = OpenRouterClient(api_key)
    
    print("\nTesting structured output...")
    
    schema = {
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "description": "The answer to the question"
            },
            "explanation": {
                "type": "string", 
                "description": "Explanation of the answer"
            }
        },
        "required": ["answer", "explanation"],
        "additionalProperties": False
    }
    
    result = client.complete_with_schema(
        model="glm-5",
        prompt="What is the capital of France?",
        system_prompt="You are a helpful assistant. Answer in JSON format.",
        schema=schema,
        temperature=0.0,
    )
    
    print(f"Result: {result}")
    assert "answer" in result, "Missing 'answer' field"
    assert "explanation" in result, "Missing 'explanation' field"
    assert "Paris" in result["answer"], f"Expected 'Paris' in answer, got: {result['answer']}"
    print("✓ Structured output test passed!")
    return True


def test_redstone_prompt():
    """Test with actual redstone building prompt."""
    api_key = "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"
    client = OpenRouterClient(api_key)
    
    print("\nTesting redstone building prompt...")
    
    schema = {
        "type": "object",
        "properties": {
            "reasoning": {
                "type": "string",
                "description": "Your design reasoning"
            },
            "blocks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                        "z": {"type": "integer"},
                        "state": {"type": "string"}
                    },
                    "required": ["x", "y", "z", "state"]
                },
                "description": "Blocks to place"
            }
        },
        "required": ["reasoning", "blocks"],
        "additionalProperties": False
    }
    
    system_prompt = """
You are an expert Minecraft Redstone Engineer.
Output JSON with 'reasoning' and 'blocks' array.
Each block has x, y, z (integers) and state (minecraft block state string).
Use format: minecraft:blockname[properties]
"""
    
    result = client.complete_with_schema(
        model="glm-5",
        prompt="Build a simple circuit: lever at 0,0,0 connected to redstone lamp at 2,0,0 with redstone wire in between.",
        system_prompt=system_prompt,
        schema=schema,
        temperature=0.0,
    )
    
    print(f"Reasoning: {result.get('reasoning', '')[:200]}...")
    print(f"Blocks: {result.get('blocks', [])}")
    
    assert "blocks" in result, "Missing 'blocks' field"
    assert len(result["blocks"]) > 0, "No blocks generated"
    print("✓ Redstone prompt test passed!")
    return True


if __name__ == "__main__":
    print("="*60)
    print("OpenRouter API Tests")
    print("="*60)
    
    try:
        test_basic_chat()
        test_structured_output()
        test_redstone_prompt()
        
        print("\n" + "="*60)
        print("All tests passed! ✓")
        print("="*60)
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
