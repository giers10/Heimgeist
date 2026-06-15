from __future__ import annotations

import json
import math
import multiprocessing as mp
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from .app_settings import (
    get_enrichment_model_preference,
    get_ollama_api_url,
    get_transcription_model_preference,
)
from .ollama_client import chat as ollama_chat
from .paths import tool_root
from .rag.corpus_builder import (
    _transcribe_slice,
    _whisper_pool_init,
    merge_transcripts,
    slice_audio,
)
from .whisper_admin import ensure_whisper_model_downloaded


YTDLP_RELEASE_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download"
YTDLP_UPDATE_INTERVAL_SECONDS = 6 * 60 * 60
YTDLP_STATUS_FILE = "update.json"
SUMMARY_PROMPT_TEMPLATE = """You are an expert summarizer. Summarize the following video concisely:

Title: {title}

Transcript:
{transcript}

Summary:"""
OLLAMA_CHARS_PER_TOKEN = 3.5
OLLAMA_OUTPUT_TOKEN_BUDGET = 2048
OLLAMA_CONTEXT_BUCKETS = (4096, 8192, 16384, 32768, 65536)
NUM_SLICES = 8
OVERLAP_SECONDS = 1.0
MAX_OVERLAP_WORDS = 7
MEDIA_HOST_MARKERS = (
    "youtube.com",
    "youtu.be",
    "vimeo.com",
    "tiktok.com",
    "instagram.com",
    "twitch.tv",
    "soundcloud.com",
    "facebook.com",
    "fb.watch",
    "dailymotion.com",
)

_YTDLP_LOCK = threading.Lock()


class UnsupportedVideoUrl(RuntimeError):
    pass


def _yt_dlp_asset_name() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "windows":
        return "yt-dlp_arm64.exe" if machine in {"arm64", "aarch64"} else "yt-dlp.exe"
    if system == "darwin":
        return "yt-dlp_macos"
    if system == "linux":
        if machine in {"arm64", "aarch64"}:
            return "yt-dlp_linux_aarch64"
        if machine.startswith("armv7"):
            return "yt-dlp_linux_armv7l"
        return "yt-dlp_linux"
    return "yt-dlp"


def _yt_dlp_binary_name() -> str:
    return "yt-dlp.exe" if os.name == "nt" else "yt-dlp"


def _executable_version(path: Path) -> Optional[str]:
    try:
        result = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or result.stderr).strip().splitlines()[0].strip() or None


def _read_update_status(status_path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _update_due(binary_path: Path, status_path: Path) -> bool:
    if not binary_path.exists() or not _executable_version(binary_path):
        return True
    last_check = _read_update_status(status_path).get("last_check")
    if not isinstance(last_check, (int, float)):
        return True
    return (time.time() - float(last_check)) >= YTDLP_UPDATE_INTERVAL_SECONDS


def _download_latest_yt_dlp(binary_path: Path, status_path: Path) -> Optional[str]:
    url = f"{YTDLP_RELEASE_URL}/{_yt_dlp_asset_name()}"
    tmp_path = binary_path.with_suffix(binary_path.suffix + ".download")
    try:
        with requests.get(url, stream=True, timeout=(10, 120)) as response:
            response.raise_for_status()
            with tmp_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        if os.name != "nt":
            tmp_path.chmod(0o755)
        version = _executable_version(tmp_path)
        if not version:
            raise RuntimeError("Downloaded yt-dlp executable did not run.")
        os.replace(tmp_path, binary_path)
        if os.name != "nt":
            binary_path.chmod(0o755)
        status_path.write_text(
            json.dumps({"last_check": time.time(), "version": version, "source": url}, indent=2),
            encoding="utf-8",
        )
        return version
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        return None


def resolve_yt_dlp() -> Tuple[str, str]:
    override = str(os.getenv("HEIMGEIST_YTDLP_PATH") or "").strip()
    if override:
        path = Path(override).expanduser()
        version = _executable_version(path)
        if not version:
            raise RuntimeError(f"Configured yt-dlp executable is unavailable: {path}")
        return str(path), version

    with _YTDLP_LOCK:
        target_dir = tool_root() / "yt-dlp"
        target_dir.mkdir(parents=True, exist_ok=True)
        binary_path = target_dir / _yt_dlp_binary_name()
        status_path = target_dir / YTDLP_STATUS_FILE
        version = _executable_version(binary_path)
        if _update_due(binary_path, status_path):
            updated = _download_latest_yt_dlp(binary_path, status_path)
            if updated:
                return str(binary_path), updated
        if version := _executable_version(binary_path):
            return str(binary_path), version

    system_path = shutil.which("yt-dlp")
    if system_path and (version := _executable_version(Path(system_path))):
        return system_path, version
    raise RuntimeError("Heimgeist could not download or locate yt-dlp.")


def _ffmpeg_binary() -> str:
    candidate = str(os.getenv("HEIMGEIST_FFMPEG_PATH") or shutil.which("ffmpeg") or "").strip()
    if not candidate:
        raise RuntimeError("Video ingestion requires ffmpeg.")
    return candidate


def _ffprobe_binary() -> str:
    candidate = str(os.getenv("HEIMGEIST_FFPROBE_PATH") or shutil.which("ffprobe") or "").strip()
    if not candidate:
        raise RuntimeError("Video ingestion requires ffprobe.")
    return candidate


def _run_yt_dlp(args: List[str], *, timeout: Optional[float] = None) -> subprocess.CompletedProcess:
    executable, _version = resolve_yt_dlp()
    env = os.environ.copy()
    ffmpeg_dir = str(Path(_ffmpeg_binary()).parent)
    path_entries = [entry for entry in env.get("PATH", "").split(os.pathsep) if entry]
    if ffmpeg_dir and ffmpeg_dir not in path_entries:
        path_entries.insert(0, ffmpeg_dir)
    env["PATH"] = os.pathsep.join(path_entries)
    result = subprocess.run(
        [executable, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "yt-dlp failed").strip()
        if "Unsupported URL" in detail:
            raise UnsupportedVideoUrl(detail)
        raise RuntimeError(detail)
    return result


def _parse_metadata(stdout: str) -> Dict[str, Any]:
    for line in reversed([line.strip() for line in stdout.splitlines() if line.strip()]):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise RuntimeError("yt-dlp returned no usable metadata.")


def _looks_like_video_metadata(metadata: Dict[str, Any]) -> bool:
    formats = metadata.get("formats")
    if not isinstance(formats, list) or not formats:
        return False
    return bool(
        metadata.get("duration")
        or metadata.get("acodec") not in {None, "none"}
        or metadata.get("vcodec") not in {None, "none"}
    )


def is_likely_media_url(url: str) -> bool:
    lowered = str(url or "").lower()
    return any(marker in lowered for marker in MEDIA_HOST_MARKERS)


def probe_video_url(url: str) -> Optional[Dict[str, Any]]:
    result = _run_yt_dlp(
        ["--no-playlist", "--skip-download", "--dump-single-json", "--no-warnings", url],
        timeout=180,
    )
    metadata = _parse_metadata(result.stdout)
    return metadata if _looks_like_video_metadata(metadata) else None


def _download_audio(url: str, temp_dir: Path) -> Path:
    output_template = str(temp_dir / "audio.%(ext)s")
    args = [
        "--no-playlist",
        "--format", "bestaudio/best",
        "--output", output_template,
        "--no-progress",
        "--no-part",
        "--no-continue",
        "--force-overwrites",
        "--retries", "3",
        "--fragment-retries", "3",
        "--extract-audio",
        "--audio-format", "wav",
        "--ffmpeg-location", str(Path(_ffmpeg_binary()).parent),
        url,
    ]
    _run_yt_dlp(args, timeout=60 * 60)
    expected = temp_dir / "audio.wav"
    if expected.exists():
        return expected
    matches = list(temp_dir.glob("audio*.wav"))
    if matches:
        return matches[0]
    raise RuntimeError("yt-dlp completed without producing an audio file.")


def _transcription_workers(model_name: str, slice_count: int) -> int:
    override = str(os.getenv("HEIMGEIST_VIDEO_WHISPER_WORKERS") or "").strip()
    if override:
        try:
            return max(1, min(slice_count, int(override)))
        except ValueError:
            pass
    if any(marker in model_name.lower() for marker in ("medium", "large")):
        return 1
    return max(1, min(slice_count, 2, max(1, (os.cpu_count() or 2) // 2)))


def _transcribe_audio(audio_path: Path, temp_dir: Path, model_name: str) -> Tuple[str, Dict[str, Any]]:
    ensure_whisper_model_downloaded(model_name)
    slice_dir = temp_dir / "slices"
    slice_dir.mkdir(parents=True, exist_ok=True)
    slices = slice_audio(
        audio_path,
        slice_dir,
        NUM_SLICES,
        OVERLAP_SECONDS,
        _ffprobe_binary(),
        _ffmpeg_binary(),
    )
    workers = _transcription_workers(model_name, len(slices))
    context = mp.get_context("spawn")
    pool = context.Pool(
        processes=workers,
        initializer=_whisper_pool_init,
        initargs=(model_name, "cpu"),
    )
    try:
        jobs = [(path, index, audio_path.stem) for index, (path, _start, _end) in enumerate(slices)]
        results = pool.starmap(_transcribe_slice, [("transcribe", job) for job in jobs])
    except BaseException:
        pool.terminate()
        pool.join()
        raise
    else:
        pool.close()
        pool.join()
    transcript = merge_transcripts(results, MAX_OVERLAP_WORDS).strip()
    if not transcript:
        raise RuntimeError("Whisper returned an empty transcript.")
    return transcript, {"model": model_name, "workers": workers, "slices": len(slices)}


def _choose_num_ctx(prompt: str) -> int:
    estimated_input_tokens = math.ceil(len(prompt) / OLLAMA_CHARS_PER_TOKEN)
    needed = estimated_input_tokens + OLLAMA_OUTPUT_TOKEN_BUDGET
    for bucket in OLLAMA_CONTEXT_BUCKETS:
        if needed <= bucket:
            return bucket
    return OLLAMA_CONTEXT_BUCKETS[-1]


async def _summarize(title: str, transcript: str, model_name: str) -> str:
    prompt = SUMMARY_PROMPT_TEMPLATE.replace("{title}", title).replace("{transcript}", transcript)
    summary = await ollama_chat(
        model_name,
        [
            {"role": "system", "content": "You are an intelligent summarizer."},
            {"role": "user", "content": prompt},
        ],
        options={"num_ctx": _choose_num_ctx(prompt)},
    )
    clean = str(summary or "").strip()
    if not clean:
        raise RuntimeError("Ollama returned an empty video summary.")
    return clean


def _format_duration(value: Any) -> str:
    try:
        total = max(0, int(float(value)))
    except Exception:
        return ""
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:d}:{seconds:02d}"


def build_video_document(metadata: Dict[str, Any], summary: str, transcript: str) -> str:
    title = str(metadata.get("title") or "Video").strip()
    url = str(metadata.get("webpage_url") or metadata.get("original_url") or "").strip()
    uploader = str(metadata.get("channel") or metadata.get("uploader") or "").strip()
    duration = _format_duration(metadata.get("duration"))
    lines = [f"# {title}"]
    if url:
        lines.append(f"Source: {url}")
    if uploader:
        lines.append(f"Channel: {uploader}")
    if duration:
        lines.append(f"Duration: {duration}")
    lines.extend(["", "## Summary", summary.strip(), "", "## Transcript", transcript.strip()])
    return "\n".join(lines).strip() + "\n"


async def ingest_video_url(
    url: str,
    metadata: Dict[str, Any],
    *,
    progress: Optional[Callable[[str, float, str], None]] = None,
) -> Dict[str, Any]:
    def report(phase: str, pct: float, detail: str) -> None:
        if progress:
            progress(phase, pct, detail)

    title = str(metadata.get("title") or metadata.get("id") or "Video").strip()
    transcription_model = get_transcription_model_preference()
    summary_model = get_enrichment_model_preference()
    _executable, yt_dlp_version = await _to_thread(resolve_yt_dlp)

    with tempfile.TemporaryDirectory(prefix="heimgeist-video-") as raw_temp_dir:
        temp_dir = Path(raw_temp_dir)
        report("download", 0.08, "Downloading video audio...")
        audio_path = await _to_thread(_download_audio, url, temp_dir)
        report("transcribe", 0.25, f"Transcribing audio with Whisper {transcription_model}...")
        transcript, transcription = await _to_thread(
            _transcribe_audio,
            audio_path,
            temp_dir,
            transcription_model,
        )
        report("summarize", 0.72, f"Summarizing transcript with {summary_model}...")
        summary = await _summarize(title, transcript, summary_model)

    report("save", 0.9, "Saving transcript and metadata...")
    canonical_url = str(metadata.get("webpage_url") or metadata.get("original_url") or url).strip()
    return {
        "title": title,
        "url": canonical_url,
        "requested_url": url,
        "video_id": str(metadata.get("id") or "").strip() or None,
        "extractor": str(metadata.get("extractor_key") or metadata.get("extractor") or "").strip() or None,
        "channel": str(metadata.get("channel") or metadata.get("uploader") or "").strip() or None,
        "duration": metadata.get("duration"),
        "duration_text": _format_duration(metadata.get("duration")),
        "upload_date": metadata.get("upload_date"),
        "thumbnail_url": metadata.get("thumbnail"),
        "transcript": transcript,
        "summary": summary,
        "document": build_video_document(metadata, summary, transcript),
        "transcription_model": transcription["model"],
        "transcription_workers": transcription["workers"],
        "transcription_slices": transcription["slices"],
        "summary_model": summary_model,
        "yt_dlp_version": yt_dlp_version,
        "fetched_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }


async def _to_thread(fn, *args):
    import asyncio

    return await asyncio.to_thread(fn, *args)
