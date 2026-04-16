from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_WHISPER_MODEL = "base"
_WHISPER_DOWNLOAD_LOCK = threading.Lock()
_WHISPER_MODEL_LOCK = threading.Lock()
_WHISPER_MODELS: Dict[tuple[str, str], Any] = {}
_AUDIO_SUFFIXES = {
    "audio/aac": ".aac",
    "audio/flac": ".flac",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/mpga": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/wave": ".wav",
    "audio/webm": ".webm",
    "audio/x-wav": ".wav",
}


def _default_download_root() -> Path:
    default_cache = Path.home() / ".cache"
    return Path(os.getenv("XDG_CACHE_HOME", str(default_cache))) / "whisper"


def _load_whisper_module():
    try:
        return importlib.import_module("whisper")
    except Exception:
        return None


def _load_torch_module():
    try:
        return importlib.import_module("torch")
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


def _resolve_whisper_device() -> str:
    try:
        torch_mod = _load_torch_module()
        if torch_mod is not None and getattr(torch_mod.cuda, "is_available", lambda: False)():
            return "cuda"

        backends = getattr(torch_mod, "backends", None)
        mps_backend = getattr(backends, "mps", None)
        if mps_backend is not None and getattr(mps_backend, "is_available", lambda: False)():
            return "mps"
    except Exception:
        pass

    return "cpu"


def _resolve_ffmpeg_binary() -> Optional[str]:
    candidate = os.getenv("HEIMGEIST_FFMPEG_PATH") or shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
    if not candidate:
        return None
    if Path(candidate).exists() or shutil.which(candidate):
        return str(candidate)
    return None


def _audio_suffix_for_mime_type(mime_type: str) -> str:
    base_mime = str(mime_type or "").split(";", 1)[0].strip().lower()
    return _AUDIO_SUFFIXES.get(base_mime, ".webm")


def _load_transcription_model(model_name: str = DEFAULT_WHISPER_MODEL) -> tuple[Any, str]:
    error = whisper_runtime_error()
    if error:
        raise RuntimeError(error)

    whisper_mod = _load_whisper_module()
    if whisper_mod is None:
        raise RuntimeError("Failed to import the Whisper runtime.")

    ensure_whisper_model_downloaded(model_name)
    device = _resolve_whisper_device()
    cache_key = (model_name, device)

    with _WHISPER_MODEL_LOCK:
        model = _WHISPER_MODELS.get(cache_key)
        if model is None:
            try:
                model = whisper_mod.load_model(model_name, device=device)
            except TypeError:
                model = whisper_mod.load_model(model_name)
            _WHISPER_MODELS[cache_key] = model

    return model, device


def _convert_audio_to_wav(input_path: Path, output_path: Path) -> None:
    ffmpeg_bin = _resolve_ffmpeg_binary()
    if not ffmpeg_bin:
        raise RuntimeError(
            "Audio transcription requires ffmpeg. Heimgeist could not resolve it from HEIMGEIST_FFMPEG_PATH or PATH."
        )

    process = subprocess.run(
        [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "wav",
            str(output_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if process.returncode != 0:
        detail = (process.stderr or process.stdout or "ffmpeg audio conversion failed").strip()
        raise RuntimeError(detail)


def transcribe_audio_bytes(
    audio_bytes: bytes,
    mime_type: str,
    model_name: str = DEFAULT_WHISPER_MODEL,
) -> Dict[str, Any]:
    if not audio_bytes:
        raise RuntimeError("Recorded audio was empty.")

    model, device = _load_transcription_model(model_name)
    input_path = Path(tempfile.mkstemp(suffix=_audio_suffix_for_mime_type(mime_type))[1])
    wav_path = Path(tempfile.mkstemp(suffix=".wav")[1])

    try:
        input_path.write_bytes(audio_bytes)
        source_path = input_path
        if input_path.suffix.lower() != ".wav":
            _convert_audio_to_wav(input_path, wav_path)
            source_path = wav_path

        result = model.transcribe(str(source_path), task="transcribe", fp16=device == "cuda")
        return {
            "model": model_name,
            "device": device,
            "language": str(result.get("language") or "").strip(),
            "text": str(result.get("text") or "").strip(),
        }
    finally:
        for path in (input_path, wav_path):
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass
