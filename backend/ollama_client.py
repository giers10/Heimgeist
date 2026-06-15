import httpx
import json
import re
import time
from dataclasses import dataclass, field
from typing import Dict, Any, List, AsyncGenerator, Optional, Tuple

from .app_settings import get_ollama_api_url

_MODEL_DETAILS_CACHE: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}
_MODEL_DETAILS_TTL_S = 15.0


@dataclass
class OllamaToolCall:
    name: str
    arguments: Dict[str, Any]
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OllamaUsage:
    prompt_eval_count: int = 0
    eval_count: int = 0
    total_duration: int = 0
    load_duration: int = 0
    prompt_eval_duration: int = 0
    eval_duration: int = 0


@dataclass
class OllamaChatResult:
    content: str = ""
    thinking: str = ""
    tool_calls: List[OllamaToolCall] = field(default_factory=list)
    usage: OllamaUsage = field(default_factory=OllamaUsage)
    done_reason: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OllamaStreamChunk:
    content: str = ""
    thinking: str = ""
    tool_calls: List[OllamaToolCall] = field(default_factory=list)
    done: bool = False
    usage: OllamaUsage = field(default_factory=OllamaUsage)
    raw: Dict[str, Any] = field(default_factory=dict)


def _cache_key(model: str) -> Tuple[str, str]:
    ollama_url = get_ollama_api_url()
    return (ollama_url.rstrip('/'), str(model or '').strip())


def _get_cached_model_details(model: str) -> Dict[str, Any]:
    cached = _MODEL_DETAILS_CACHE.get(_cache_key(model))
    return cached[1] if cached else {}


def _string_tokens(value: Any) -> List[str]:
    if isinstance(value, str):
        trimmed = value.strip()
        return [trimmed] if trimmed else []
    if isinstance(value, dict):
        out: List[str] = []
        for key, item in value.items():
            out.extend(_string_tokens(key))
            out.extend(_string_tokens(item))
        return out
    if isinstance(value, (list, tuple, set)):
        out: List[str] = []
        for item in value:
            out.extend(_string_tokens(item))
        return out
    return []


def _normalize_capabilities(model_data: Dict[str, Any]) -> List[str]:
    out = []
    for item in model_data.get("capabilities") or []:
        text = str(item).strip().lower()
        if text and text not in out:
            out.append(text)
    return out


def _combined_model_tokens(name: str, model_data: Dict[str, Any], tag_item: Dict[str, Any]) -> str:
    return " ".join(
        token.lower()
        for token in _string_tokens(name) + _string_tokens(tag_item.get("details")) + _string_tokens(model_data)
    )


def _is_embedding_model(name: str, model_data: Dict[str, Any], tag_item: Dict[str, Any]) -> bool:
    capabilities = set(_normalize_capabilities(model_data))
    if "embedding" in capabilities or "embeddings" in capabilities:
        return True

    lowered_tokens = _combined_model_tokens(name, model_data, tag_item)
    return any(
        marker in lowered_tokens
        for marker in (
            " embed ",
            " embedding ",
            "embed-",
            "-embed",
            "nomic-embed",
            "mxbai-embed",
            "snowflake-arctic-embed",
            "bge-m3",
            "bge ",
        )
    ) or lowered_tokens.startswith("bge")


def _is_rerank_model(name: str, model_data: Dict[str, Any], tag_item: Dict[str, Any]) -> bool:
    lowered_tokens = _combined_model_tokens(name, model_data, tag_item)
    return (
        _is_embedding_model(name, model_data, tag_item)
        or "rerank" in lowered_tokens
        or "cross-encoder" in lowered_tokens
    )


def _supports_vision_fast(name: str, model_data: Dict[str, Any], tag_item: Dict[str, Any]) -> bool:
    if supports_vision(model_data):
        return True

    lowered_tokens = _combined_model_tokens(name, model_data, tag_item)
    return any(
        marker in lowered_tokens
        for marker in (
            " vision ",
            "-vision",
            " vision-",
            "vision:",
            "llava",
            "bakllava",
            "moondream",
            "minicpm-v",
            "minicpmv",
            "pixtral",
            "qwen-vl",
            "qwen2vl",
            "qwen2.5vl",
            "qwen2.5-omni",
            "granite3.2-vision",
            "llama3.2-vision",
            "gemma3",
            "gemma4",
            "-vl",
            " vl ",
        )
    )


def _build_model_catalog_entry(tag_item: Dict[str, Any], model_data: Dict[str, Any]) -> Dict[str, Any]:
    name = str((tag_item or {}).get("name") or "").strip()
    capabilities = _normalize_capabilities(model_data)
    is_embedding = _is_embedding_model(name, model_data, tag_item)
    is_rerank = _is_rerank_model(name, model_data, tag_item)
    has_vision = _supports_vision_fast(name, model_data, tag_item)
    lowered_tokens = _combined_model_tokens(name, model_data, tag_item)
    is_non_chat = is_embedding or "rerank" in lowered_tokens or "cross-encoder" in lowered_tokens
    return {
        "name": name,
        "capabilities": capabilities,
        "supports_vision": has_vision,
        "is_embedding": is_embedding,
        "can_chat": not is_non_chat,
        "can_rerank": is_rerank,
    }


async def list_models() -> Dict[str, Any]:
    ollama_url = get_ollama_api_url()
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{ollama_url}/api/tags")
        r.raise_for_status()
        data = r.json()
        # Normalize to a simple list of names
        models = [m.get('name') for m in data.get('models', [])]
        return {"models": models}


async def list_model_catalog() -> Dict[str, Any]:
    ollama_url = get_ollama_api_url()
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{ollama_url}/api/tags")
        r.raise_for_status()
        payload = r.json()

    raw_models = payload.get("models", []) or []
    models = [
        _build_model_catalog_entry(
            item or {},
            _get_cached_model_details(str((item or {}).get("name") or "").strip()),
        )
        for item in raw_models
        if str((item or {}).get("name") or "").strip()
    ]

    return {
        "models": models,
        "chat_models": [model["name"] for model in models if model["can_chat"]],
        "embedding_models": [model["name"] for model in models if model["is_embedding"]],
        "vision_models": [model["name"] for model in models if model["supports_vision"]],
        "reranking_models": [model["name"] for model in models if model["can_rerank"]],
    }

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

def _parse_tool_calls(value: Any) -> List[OllamaToolCall]:
    calls: List[OllamaToolCall] = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        function = item.get("function") if isinstance(item.get("function"), dict) else item
        name = str(function.get("name") or "").strip()
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except Exception:
                arguments = {}
        if name and isinstance(arguments, dict):
            calls.append(OllamaToolCall(name=name, arguments=arguments, raw=item))
    return calls


def _usage_from_payload(data: Dict[str, Any]) -> OllamaUsage:
    return OllamaUsage(
        prompt_eval_count=int(data.get("prompt_eval_count") or 0),
        eval_count=int(data.get("eval_count") or 0),
        total_duration=int(data.get("total_duration") or 0),
        load_duration=int(data.get("load_duration") or 0),
        prompt_eval_duration=int(data.get("prompt_eval_duration") or 0),
        eval_duration=int(data.get("eval_duration") or 0),
    )


def _chat_payload(
    model: str,
    messages: List[Dict[str, Any]],
    *,
    stream: bool,
    options: Optional[Dict[str, Any]] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    format: Optional[Any] = None,
    think: Optional[bool] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"model": model, "messages": messages, "stream": stream}
    if options:
        payload["options"] = options
    if tools:
        payload["tools"] = tools
    if format is not None:
        payload["format"] = format
    if think is not None:
        payload["think"] = bool(think)
    return payload


async def chat_typed(
    model: str,
    messages: List[Dict[str, Any]],
    *,
    options: Dict[str, Any] | None = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    format: Optional[Any] = None,
    think: Optional[bool] = None,
    cancellation_event: Optional[Any] = None,
) -> OllamaChatResult:
    ollama_url = get_ollama_api_url()
    if cancellation_event is not None and cancellation_event.is_set():
        raise asyncio.CancelledError()
    payload = _chat_payload(model, messages, stream=False, options=options, tools=tools, format=format, think=think)
    async with httpx.AsyncClient(timeout=600.0) as client:
        r = await client.post(f"{ollama_url}/api/chat", json=payload)
        r.raise_for_status()
        data = r.json()
    message = data.get("message") if isinstance(data.get("message"), dict) else {}
    if not message and data.get("messages"):
        message = data["messages"][-1] or {}
    return OllamaChatResult(
        content=str(message.get("content") or data.get("content") or ""),
        thinking=str(message.get("thinking") or data.get("thinking") or ""),
        tool_calls=_parse_tool_calls(message.get("tool_calls") or data.get("tool_calls")),
        usage=_usage_from_payload(data),
        done_reason=data.get("done_reason"),
        raw=data,
    )


async def chat_stream_typed(
    model: str,
    messages: List[Dict[str, Any]],
    *,
    options: Optional[Dict[str, Any]] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    format: Optional[Any] = None,
    think: Optional[bool] = None,
    cancellation_event: Optional[Any] = None,
) -> AsyncGenerator[OllamaStreamChunk, None]:
    ollama_url = get_ollama_api_url()
    payload = _chat_payload(model, messages, stream=True, options=options, tools=tools, format=format, think=think)
    async with httpx.AsyncClient(timeout=600.0) as client:
        async with client.stream("POST", f"{ollama_url}/api/chat", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if cancellation_event is not None and cancellation_event.is_set():
                    raise asyncio.CancelledError()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                message = data.get("message") if isinstance(data.get("message"), dict) else {}
                yield OllamaStreamChunk(
                    content=str(message.get("content") or data.get("content") or ""),
                    thinking=str(message.get("thinking") or data.get("thinking") or ""),
                    tool_calls=_parse_tool_calls(message.get("tool_calls") or data.get("tool_calls")),
                    done=bool(data.get("done")),
                    usage=_usage_from_payload(data),
                    raw=data,
                )


async def chat(
    model: str,
    messages: List[Dict[str, Any]],
    *,
    options: Dict[str, Any] | None = None,
) -> str:
    return (await chat_typed(model, messages, options=options)).content

async def chat_stream(model: str, messages: List[Dict[str, Any]]) -> AsyncGenerator[str, None]:
    async for chunk in chat_stream_typed(model, messages):
        if chunk.content:
            yield chunk.content
