
import httpx
import json
import re
import time
from typing import Dict, Any, List, AsyncGenerator, Tuple

from .app_settings import get_ollama_api_url

_MODEL_DETAILS_CACHE: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}
_MODEL_DETAILS_TTL_S = 15.0

async def list_models() -> Dict[str, Any]:
    ollama_url = get_ollama_api_url()
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{ollama_url}/api/tags")
        r.raise_for_status()
        data = r.json()
        # Normalize to a simple list of names
        models = [m.get('name') for m in data.get('models', [])]
        return {"models": models}

async def show_model(model: str, *, refresh: bool = False) -> Dict[str, Any]:
    ollama_url = get_ollama_api_url()
    cache_key = (ollama_url.rstrip('/'), str(model or '').strip())
    cached = _MODEL_DETAILS_CACHE.get(cache_key)
    now = time.monotonic()
    if not refresh and cached and (now - cached[0]) < _MODEL_DETAILS_TTL_S:
        return cached[1]

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(f"{ollama_url}/api/show", json={"model": model})
        r.raise_for_status()
        data = r.json()
        _MODEL_DETAILS_CACHE[cache_key] = (now, data)
        return data

def supports_vision(model_data: Dict[str, Any]) -> bool:
    capabilities = model_data.get("capabilities") or []
    if any(str(item).strip().lower() == "vision" for item in capabilities):
        return True

    model_info = model_data.get("model_info") or {}
    if isinstance(model_info, dict):
        for key in model_info.keys():
            lowered = str(key).strip().lower()
            if ".vision." in lowered or lowered.endswith(".vision"):
                return True
            if lowered.endswith("tokens_per_image") or re.search(r"\bmm\b", lowered):
                return True

    return False

async def chat(model: str, messages: List[Dict[str, Any]]) -> str:
    ollama_url = get_ollama_api_url()
    payload = {
        "model": model,
        "messages": messages,
        "stream": False
    }
    async with httpx.AsyncClient(timeout=600.0) as client:
        r = await client.post(f"{ollama_url}/api/chat", json=payload)
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

async def chat_stream(model: str, messages: List[Dict[str, Any]]) -> AsyncGenerator[str, None]:
    ollama_url = get_ollama_api_url()
    payload = {
        "model": model,
        "messages": messages,
        "stream": True
    }
    async with httpx.AsyncClient(timeout=600.0) as client:
        async with client.stream("POST", f"{ollama_url}/api/chat", json=payload) as r:
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
