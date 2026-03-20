from __future__ import annotations

import importlib
import importlib.util
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_WHISPER_MODEL = "base"
_WHISPER_DOWNLOAD_LOCK = threading.Lock()


def _default_download_root() -> Path:
    default_cache = Path.home() / ".cache"
    return Path(os.getenv("XDG_CACHE_HOME", str(default_cache))) / "whisper"


def _load_whisper_module():
    try:
        return importlib.import_module("whisper")
    except Exception:
        return None


def whisper_runtime_error() -> Optional[str]:
    if importlib.util.find_spec("whisper") is None:
        return (
            "Audio/video transcription requires the optional 'openai-whisper' package. "
            "Install it in backend/.venv, for example: pip install -U openai-whisper"
        )
    return None


def _official_model_target(whisper_mod: Any, model_name: str) -> Optional[Path]:
    url = str(getattr(whisper_mod, "_MODELS", {}).get(model_name) or "").strip()
    if not url:
        return None
    return _default_download_root() / os.path.basename(url)


def inspect_whisper_model(model_name: str = DEFAULT_WHISPER_MODEL) -> Dict[str, Any]:
    error = whisper_runtime_error()
    if error:
        return {
            "model": model_name,
            "package_available": False,
            "available": False,
            "downloaded": False,
            "path": None,
            "error": error,
        }

    whisper_mod = _load_whisper_module()
    if whisper_mod is None:
        return {
            "model": model_name,
            "package_available": False,
            "available": False,
            "downloaded": False,
            "path": None,
            "error": "Failed to import the Whisper runtime.",
        }

    target = _official_model_target(whisper_mod, model_name)
    if target is not None:
        return {
            "model": model_name,
            "package_available": True,
            "available": target.is_file(),
            "downloaded": False,
            "path": str(target),
            "error": "",
        }

    custom_path = Path(model_name).expanduser()
    return {
        "model": model_name,
        "package_available": True,
        "available": custom_path.is_file(),
        "downloaded": False,
        "path": str(custom_path),
        "error": "",
    }


def ensure_whisper_model_downloaded(model_name: str = DEFAULT_WHISPER_MODEL) -> Dict[str, Any]:
    status = inspect_whisper_model(model_name)
    if status["error"]:
        raise RuntimeError(status["error"])

    whisper_mod = _load_whisper_module()
    if whisper_mod is None:
        raise RuntimeError("Failed to import the Whisper runtime.")

    target = _official_model_target(whisper_mod, model_name)
    if target is None:
        custom_path = Path(model_name).expanduser()
        if custom_path.is_file():
            return {
                **status,
                "available": True,
                "downloaded": False,
                "path": str(custom_path),
            }
        raise RuntimeError(f"Model {model_name} not found; available models = {whisper_mod.available_models()}")

    with _WHISPER_DOWNLOAD_LOCK:
        existed_before = target.is_file()
        download_fn = getattr(whisper_mod, "_download", None)
        if callable(download_fn):
            download_fn(whisper_mod._MODELS[model_name], str(_default_download_root()), False)
        else:
            model = whisper_mod.load_model(model_name, device="cpu")
            del model

    return {
        "model": model_name,
        "package_available": True,
        "available": target.is_file(),
        "downloaded": target.is_file() and not existed_before,
        "path": str(target),
        "error": "",
    }
