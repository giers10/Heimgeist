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
DEFAULT_RERANK_MODEL = DEFAULT_EMBED_MODEL
DEFAULT_ENRICHMENT_MODEL = "qwen3:4b"
DEFAULT_TRANSCRIPTION_MODEL = "base"
DEFAULT_WORKFLOW_ROUTER_MODEL = ""
DEFAULT_AUTO_DEEP_ENRICHMENT = True
BGE_EMBED_MODEL = "bge-m3:latest"
DEFAULT_SETTINGS: Dict[str, Any] = {
    "backendApiUrl": DEFAULT_BACKEND_API_URL,
    "ollamaApiUrl": DEFAULT_OLLAMA_API_URL,
    "chatModel": "llama3",
    "visionModel": "",
    "embedModel": DEFAULT_EMBED_MODEL,
    "rerankModel": DEFAULT_RERANK_MODEL,
    "enrichmentModel": DEFAULT_ENRICHMENT_MODEL,
    "transcriptionModel": DEFAULT_TRANSCRIPTION_MODEL,
    "workflowRouterModel": DEFAULT_WORKFLOW_ROUTER_MODEL,
    "autoDeepEnrichment": DEFAULT_AUTO_DEEP_ENRICHMENT,
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

    trimmed = value.strip()
    if not trimmed:
        return DEFAULT_EMBED_MODEL

    lowered = trimmed.lower()
    if lowered in {"bge", "bge-m3", BGE_EMBED_MODEL}:
        return BGE_EMBED_MODEL
    if lowered in {"nomic", "nomic-embed-text", DEFAULT_EMBED_MODEL}:
        return DEFAULT_EMBED_MODEL
    return trimmed


def normalize_rerank_model(value: Any) -> str:
    return normalize_embed_model(value)


def normalize_model_name(value: Any, fallback: str = "") -> str:
    if not isinstance(value, str):
        return fallback
    trimmed = value.strip()
    return trimmed or fallback


def normalize_transcription_model(value: Any) -> str:
    return normalize_model_name(value, DEFAULT_TRANSCRIPTION_MODEL)


def normalize_boolean(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off", ""}:
            return False
    return default


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
    if "rerankModel" not in raw:
        settings["rerankModel"] = settings.get("embedModel")
    if "visionModel" not in raw:
        settings["visionModel"] = settings.get("chatModel", "")
    settings["embedModel"] = normalize_embed_model(settings.get("embedModel"))
    settings["rerankModel"] = normalize_rerank_model(settings.get("rerankModel"))
    settings["enrichmentModel"] = normalize_model_name(
        settings.get("enrichmentModel"),
        DEFAULT_ENRICHMENT_MODEL,
    )
    settings["chatModel"] = normalize_model_name(settings.get("chatModel"))
    settings["visionModel"] = normalize_model_name(settings.get("visionModel"))
    settings["transcriptionModel"] = normalize_transcription_model(settings.get("transcriptionModel"))
    settings["workflowRouterModel"] = normalize_model_name(settings.get("workflowRouterModel"))
    settings["autoDeepEnrichment"] = normalize_boolean(
        settings.get("autoDeepEnrichment"),
        DEFAULT_AUTO_DEEP_ENRICHMENT,
    )

    return settings


def get_ollama_api_url() -> str:
    settings = load_app_settings()
    return _normalize_url(settings.get("ollamaApiUrl"), DEFAULT_OLLAMA_API_URL)


def get_embed_model_preference() -> str:
    settings = load_app_settings()
    return normalize_embed_model(settings.get("embedModel"))


def get_rerank_model_preference() -> str:
    settings = load_app_settings()
    return normalize_rerank_model(settings.get("rerankModel"))


def get_enrichment_model_preference() -> str:
    settings = load_app_settings()
    return normalize_model_name(settings.get("enrichmentModel"), DEFAULT_ENRICHMENT_MODEL)


def get_transcription_model_preference() -> str:
    settings = load_app_settings()
    return normalize_transcription_model(settings.get("transcriptionModel"))


def get_workflow_router_model_preference() -> str:
    settings = load_app_settings()
    return normalize_model_name(settings.get("workflowRouterModel"))


def get_auto_deep_enrichment_preference() -> bool:
    settings = load_app_settings()
    return normalize_boolean(
        settings.get("autoDeepEnrichment"),
        DEFAULT_AUTO_DEEP_ENRICHMENT,
    )
