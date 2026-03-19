from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

import requests


DEFAULT_EMBED_CANDIDATES = (
    "bge-m3:latest",
    "nomic-embed-text:latest",
    "dengcao/Qwen3-Embedding-0.6B:F16",
)

_MODEL_CACHE: Dict[Tuple[str, str], str] = {}


def _cache_key(ollama_url: str, preferred_model: Optional[str]) -> Tuple[str, str]:
    return (ollama_url.rstrip("/"), str(preferred_model or "").strip())


def _candidate_models(preferred_model: Optional[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for model in [preferred_model, *DEFAULT_EMBED_CANDIDATES]:
        name = str(model or "").strip()
        if not name or name in seen:
            continue
        out.append(name)
        seen.add(name)
    return out


def _response_error_text(response: requests.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        message = str(payload.get("error") or "").strip()
        if message:
            return message
    return (response.text or f"HTTP {response.status_code}").strip()


def request_embedding(ollama_url: str, model: str, text: str, *, timeout: int = 120) -> List[float]:
    response = requests.post(
        f"{ollama_url.rstrip('/')}/api/embeddings",
        json={"model": model, "prompt": text},
        timeout=timeout,
    )
    if not response.ok:
        raise RuntimeError(f"{model}: {_response_error_text(response)}")

    data = response.json()
    vec = data.get("embedding") or (data.get("embeddings") or [None])[0]
    if vec is None:
        raise RuntimeError(f"{model}: Ollama returned no embedding vector")
    return vec


def resolve_embed_model(
    ollama_url: str,
    preferred_model: Optional[str],
    *,
    probe_text: str = "embedding probe",
    timeout: int = 120,
) -> Tuple[str, List[float]]:
    key = _cache_key(ollama_url, preferred_model)
    cached = _MODEL_CACHE.get(key)
    if cached:
        try:
            return cached, request_embedding(ollama_url, cached, probe_text, timeout=timeout)
        except Exception:
            _MODEL_CACHE.pop(key, None)

    errors: List[str] = []
    for model in _candidate_models(preferred_model):
        try:
            vector = request_embedding(ollama_url, model, probe_text, timeout=timeout)
            _MODEL_CACHE[key] = model
            return model, vector
        except Exception as exc:
            errors.append(str(exc))

    tried = ", ".join(_candidate_models(preferred_model)) or "(none)"
    detail = "; ".join(errors) if errors else "no candidate models were available"
    raise RuntimeError(f"No working Ollama embedding model found. Tried: {tried}. {detail}")
