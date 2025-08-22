
import httpx
from typing import Dict, Any, List

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
