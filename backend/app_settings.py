from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict


APP_NAME = "Heimgeist"
DEFAULT_BACKEND_API_URL = "http://127.0.0.1:8000"
DEFAULT_OLLAMA_API_URL = "http://127.0.0.1:11434"
DEFAULT_EMBED_MODEL = "nomic-embed-text:latest"
BGE_EMBED_MODEL = "bge-m3:latest"
DEFAULT_SETTINGS: Dict[str, Any] = {
    "backendApiUrl": DEFAULT_BACKEND_API_URL,
    "ollamaApiUrl": DEFAULT_OLLAMA_API_URL,
    "embedModel": DEFAULT_EMBED_MODEL,
}


def _default_settings_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if os.name == "nt":
        appdata = os.getenv("APPDATA")
        if appdata:
            return Path(appdata) / APP_NAME
        return Path.home() / "AppData" / "Roaming" / APP_NAME
    return Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / APP_NAME


def settings_path() -> Path:
    custom_path = os.getenv("HEIMGEIST_SETTINGS_FILE")
    if custom_path:
        return Path(custom_path).expanduser()
    return _default_settings_dir() / "settings.json"


def _looks_like_ollama_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False

    trimmed = value.strip()
    if not trimmed:
        return False

    if ":11434" in trimmed:
        return True

    return trimmed.rstrip("/").endswith("/api")


def _normalize_url(value: Any, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback

    trimmed = value.strip().rstrip("/")
    return trimmed or fallback


def normalize_embed_model(value: Any) -> str:
    if not isinstance(value, str):
        return DEFAULT_EMBED_MODEL

    trimmed = value.strip().lower()
    if trimmed in {"bge", "bge-m3", BGE_EMBED_MODEL}:
        return BGE_EMBED_MODEL
    return DEFAULT_EMBED_MODEL


def load_app_settings() -> Dict[str, Any]:
    path = settings_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw = {}
    except Exception:
        raw = {}

    if not isinstance(raw, dict):
        raw = {}

    settings = {**DEFAULT_SETTINGS, **raw}
    if "backendApiUrl" not in raw and isinstance(raw.get("ollamaApiUrl"), str):
        if _looks_like_ollama_url(raw["ollamaApiUrl"]):
            settings["backendApiUrl"] = DEFAULT_BACKEND_API_URL
            settings["ollamaApiUrl"] = _normalize_url(raw["ollamaApiUrl"], DEFAULT_OLLAMA_API_URL)
        else:
            settings["backendApiUrl"] = _normalize_url(raw["ollamaApiUrl"], DEFAULT_BACKEND_API_URL)
            settings["ollamaApiUrl"] = DEFAULT_OLLAMA_API_URL
    else:
        settings["backendApiUrl"] = _normalize_url(settings.get("backendApiUrl"), DEFAULT_BACKEND_API_URL)
        settings["ollamaApiUrl"] = _normalize_url(settings.get("ollamaApiUrl"), DEFAULT_OLLAMA_API_URL)
    settings["embedModel"] = normalize_embed_model(settings.get("embedModel"))

    return settings


def get_ollama_api_url() -> str:
    settings = load_app_settings()
    return _normalize_url(settings.get("ollamaApiUrl"), DEFAULT_OLLAMA_API_URL)


def get_embed_model_preference() -> str:
    settings = load_app_settings()
    return normalize_embed_model(settings.get("embedModel"))
