
import httpx
import json
from typing import Dict, Any, List, AsyncGenerator

OLLAMA_URL = "http://127.0.0.1:11434"

async def list_models() -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{OLLAMA_URL}/api/tags")
        r.raise_for_status()
        data = r.json()
        # Normalize to a simple list of names
        models = [m.get('name') for m in data.get('models', [])]
        return {"models": models}

async def chat(model: str, messages: List[Dict[str, str]]) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "stream": False
    }
    async with httpx.AsyncClient(timeout=600.0) as client:
        r = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
        r.raise_for_status()
        data = r.json()
        # Ollama returns full conversation; pick last message content
        try:
            return data["message"]["content"]
        except Exception:
            # Newer Ollama formats may return messages list
            msgs = data.get("messages") or []
            if msgs:
                return msgs[-1].get("content", "")
            return data.get("content", "")

async def chat_stream(model: str, messages: List[Dict[str, str]]) -> AsyncGenerator[str, None]:
    payload = {
        "model": model,
        "messages": messages,
        "stream": True
    }
    async with httpx.AsyncClient(timeout=600.0) as client:
        async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json=payload) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if line:
                    try:
                        chunk = json.loads(line)
                        if "content" in chunk: # Newer Ollama format
                             yield chunk["content"]
                        elif "message" in chunk and "content" in chunk["message"]: # Older format
                            yield chunk["message"]["content"]
                    except json.JSONDecodeError:
                        pass # Ignore invalid JSON lines
