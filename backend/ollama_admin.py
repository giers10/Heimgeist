from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from .app_settings import get_embed_model_preference, get_ollama_api_url, normalize_embed_model
from .whisper_admin import DEFAULT_WHISPER_MODEL, ensure_whisper_model_downloaded, inspect_whisper_model


LOCAL_OLLAMA_HOSTS = {"127.0.0.1", "localhost", "::1"}
_OLLAMA_PULL_LOCK = asyncio.Lock()


def _ollama_binary() -> Optional[str]:
    return shutil.which("ollama")


def _is_local_ollama_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return (parsed.hostname or "").strip().lower() in LOCAL_OLLAMA_HOSTS


def _model_aliases(model: str) -> set[str]:
    normalized = normalize_embed_model(model)
    aliases = {normalized}
    if normalized.endswith(":latest"):
        aliases.add(normalized[:-7])
    else:
        aliases.add(f"{normalized}:latest")
    return aliases


async def _list_model_names(ollama_url: str, *, timeout: float = 5.0) -> List[str]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(f"{ollama_url.rstrip('/')}/api/tags")
        response.raise_for_status()
        payload = response.json()

    out: List[str] = []
    for item in payload.get("models", []) or []:
        name = str((item or {}).get("name") or "").strip()
        if name:
            out.append(name)
    return out


async def inspect_ollama_startup() -> Dict[str, Any]:
    ollama_url = get_ollama_api_url()
    embed_model = get_embed_model_preference()
    ollama_bin = _ollama_binary()
    is_local = _is_local_ollama_url(ollama_url)
    whisper_status = inspect_whisper_model(DEFAULT_WHISPER_MODEL)
    available_models: List[str] = []
    error = ""
    running = False

    try:
        available_models = await _list_model_names(ollama_url)
        running = True
    except Exception as exc:
        error = str(exc)

    available = bool(set(available_models) & _model_aliases(embed_model))
    return {
        "ollama_url": ollama_url,
        "ollama_running": running,
        "ollama_binary_found": bool(ollama_bin),
        "can_manage_locally": bool(ollama_bin) and is_local,
        "selected_embed_model": embed_model,
        "embedding_model_available": available,
        "available_models": available_models,
        "whisper_model": whisper_status["model"],
        "whisper_model_available": bool(whisper_status["available"]),
        "whisper_error": whisper_status["error"],
        "error": error,
    }


async def start_local_ollama() -> Dict[str, Any]:
    status = await inspect_ollama_startup()
    if status["ollama_running"]:
        return status
    if not status["can_manage_locally"]:
        raise RuntimeError("Ollama can only be started automatically when the configured Ollama URL points to this machine.")

    ollama_bin = _ollama_binary()
    if not ollama_bin:
        raise FileNotFoundError("Could not find the 'ollama' executable in PATH.")

    subprocess.Popen(
        [ollama_bin, "serve"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=os.environ.copy(),
    )

    for _ in range(20):
        await asyncio.sleep(0.5)
        status = await inspect_ollama_startup()
        if status["ollama_running"]:
            return status

    raise RuntimeError("Started 'ollama serve', but Ollama did not become reachable in time.")


async def pull_local_model(model: Optional[str] = None) -> Dict[str, Any]:
    async with _OLLAMA_PULL_LOCK:
        status = await inspect_ollama_startup()
        if not status["can_manage_locally"]:
            raise RuntimeError("Heimgeist can only pull models automatically when the configured Ollama URL points to this machine.")
        if not status["ollama_running"]:
            raise RuntimeError("Ollama must be running before Heimgeist can pull a model.")

        ollama_bin = _ollama_binary()
        if not ollama_bin:
            raise FileNotFoundError("Could not find the 'ollama' executable in PATH.")

        model_name = normalize_embed_model(model or status["selected_embed_model"])
        if bool(set(status["available_models"]) & _model_aliases(model_name)):
            return {
                "model": model_name,
                "downloaded": False,
                "status": status,
            }

        process = await asyncio.create_subprocess_exec(
            ollama_bin,
            "pull",
            model_name,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await process.communicate()
        if process.returncode != 0:
            detail = (stderr or b"").decode("utf-8", errors="ignore").strip()
            raise RuntimeError(detail or f"'ollama pull {model_name}' failed with exit code {process.returncode}.")

        status = await inspect_ollama_startup()
        return {
            "model": model_name,
            "downloaded": True,
            "status": status,
        }


async def prepare_startup_models() -> Dict[str, Any]:
    status = await inspect_ollama_startup()
    whisper_result = await asyncio.to_thread(ensure_whisper_model_downloaded, status["whisper_model"])
    status = await inspect_ollama_startup()

    embedding_result: Dict[str, Any] = {
        "model": status["selected_embed_model"],
        "available": bool(status["embedding_model_available"]),
        "downloaded": False,
        "skipped": False,
        "reason": "",
    }

    if not status["ollama_running"]:
        embedding_result["skipped"] = True
        embedding_result["reason"] = "Ollama is not running."
    elif not status["can_manage_locally"]:
        embedding_result["skipped"] = True
        embedding_result["reason"] = "Automatic model pulls are only available for local Ollama."
    elif not status["embedding_model_available"]:
        pulled = await pull_local_model(status["selected_embed_model"])
        status = pulled["status"]
        embedding_result = {
            "model": pulled["model"],
            "available": bool(status["embedding_model_available"]),
            "downloaded": bool(pulled.get("downloaded")),
            "skipped": False,
            "reason": "",
        }

    return {
        "ollama": status,
        "whisper": whisper_result,
        "embedding_model": embedding_result,
    }
