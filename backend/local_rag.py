from __future__ import annotations

import asyncio
import functools
import hashlib
import importlib
import json
import os
import re
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .app_settings import (
    DEFAULT_EMBED_MODEL as DEFAULT_EMBED_MODEL_SETTING,
    DEFAULT_ENRICHMENT_MODEL,
    get_auto_deep_enrichment_preference,
    get_embed_model_preference,
    get_enrichment_model_preference,
    get_ollama_api_url,
)
from .paths import library_root
from .video_ingest import (
    UnsupportedVideoUrl,
    ingest_video_url,
    is_likely_media_url,
    probe_video_url,
)
from .websearch import fetch_website_snapshot

router = APIRouter(tags=["local-rag"])

LIB_ROOT = library_root()

GLOBAL_LIBRARY_SLUG = "_global"
GLOBAL_LIBRARY_NAME = "Knowledge"
MIGRATED_LIBRARY_ARCHIVE = "_migrated_libraries"
RAW_CORPUS_PROFILE = "per-file-default-v1"
PREPARE_PROFILE = "selective-enrich-v2"
DEFAULT_EMBED_MODEL = DEFAULT_EMBED_MODEL_SETTING
DEFAULT_ENRICH_MODEL = DEFAULT_ENRICHMENT_MODEL
DEFAULT_ENRICH_MIN_CHARS = 240
DEFAULT_ENRICH_MAX_TEXT = 6000
DEFAULT_ENRICH_CONCURRENCY = max(1, min(4, (os.cpu_count() or 4) // 2))
ITEM_METADATA_VERSION = 2
AUTO_DEEP_ENRICHMENT_DISABLED_KEY = "auto_deep_enrichment_disabled"

JOB_EXECUTOR = ThreadPoolExecutor(max_workers=2)
JOBS: Dict[str, Dict[str, Any]] = {}
LIB_LOCKS: Dict[str, asyncio.Lock] = {}
QUERY_LOCKS: Dict[str, threading.Lock] = {}


class CreateLibraryRequest(BaseModel):
    name: str


class RenameLibraryRequest(BaseModel):
    name: str


class RegisterPathsRequest(BaseModel):
    paths: List[str]


class CreateTextRequest(BaseModel):
    title: str
    content: str


class UpdateTextRequest(BaseModel):
    title: str
    content: str


class CreateWebsiteRequest(BaseModel):
    url: str
    title: Optional[str] = None


class RemoveFileRequest(BaseModel):
    rel: str


class UpdateFileEnrichmentRequest(BaseModel):
    rel: str
    enabled: bool


class EmbedLibraryRequest(BaseModel):
    embed_model: Optional[str] = None
    ollama: Optional[str] = None
    target_chars: int = 2000
    overlap_chars: int = 200
    concurrency: int = 6


class LibraryContextRequest(BaseModel):
    prompt: str
    top_k: int = 5
    ollama: Optional[str] = None
    embed_model: Optional[str] = None
    gen_model: str = "qwen3:4b"


def _default_ollama_url() -> str:
    return get_ollama_api_url()


def _default_embed_model() -> str:
    return get_embed_model_preference()


def _default_enrichment_model() -> str:
    return get_enrichment_model_preference()


def _resolve_ollama_url(value: Optional[str] = None) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip().rstrip("/")
    return _default_ollama_url()


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def slugify(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9\- ]+", "", name).strip().lower()
    cleaned = re.sub(r"\s+", "-", cleaned)
    return cleaned or f"lib-{uuid.uuid4().hex[:8]}"


def lib_dir(slug: str) -> Path:
    return LIB_ROOT / slug


def lib_json(slug: str) -> Path:
    return lib_dir(slug) / "library.json"


def global_library_slug() -> str:
    return GLOBAL_LIBRARY_SLUG


def stage_dir(slug: str) -> Path:
    path = lib_dir(slug) / "stage"
    path.mkdir(parents=True, exist_ok=True)
    return path


def sources_dir(slug: str) -> Path:
    path = lib_dir(slug) / "sources"
    path.mkdir(parents=True, exist_ok=True)
    return path


def indexes_dir(slug: str) -> Path:
    path = lib_dir(slug) / "indexes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_library_data(name: str, slug: str) -> Dict[str, Any]:
    return {
        "id": uuid.uuid4().hex,
        "name": name,
        "slug": slug,
        "created_at": now_iso(),
        "files": [],
        "pipeline": {},
    }


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_library(slug: str) -> Dict[str, Any]:
    path = lib_json(slug)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Library not found")
    data = _read_json(path)
    needs_metadata_upgrade = any(
        isinstance(entry.get("metadata"), dict)
        and entry["metadata"].get("status") in {"ready", "fallback"}
        and int(entry["metadata"].get("version") or 0) < ITEM_METADATA_VERSION
        for entry in data.get("files", [])
    )
    enhanced_path = lib_dir(slug) / "corpus.enhanced.jsonl"
    if needs_metadata_upgrade and enhanced_path.exists():
        _persist_item_metadata(slug, enhanced_path)
        data = _read_json(path)
    return data


def write_library(slug: str, data: Dict[str, Any]) -> None:
    path = lib_json(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        return sum(1 for line in handle if line.strip())


def _file_uri(path_value: str) -> str:
    return f"file://{quote(path_value)}"


def _pipeline_meta(data: Dict[str, Any]) -> Dict[str, Any]:
    pipeline = data.get("pipeline")
    if isinstance(pipeline, dict):
        return pipeline
    return {}


def _source_signature(files: List[Dict[str, Any]]) -> Optional[str]:
    if not files:
        return None
    digest = hashlib.sha256()
    ordered = sorted(
        files,
        key=lambda entry: (
            str(entry.get("sha256") or ""),
            str(entry.get("path") or ""),
            str(entry.get("rel") or ""),
        ),
    )
    for entry in ordered:
        payload = {
            "sha256": entry.get("sha256") or "",
            "path": entry.get("path") or "",
            "rel": entry.get("rel") or "",
            "size": int(entry.get("size") or 0),
        }
        if entry.get("managed") or entry.get("kind") in {"text", "website", "video", "chat_message"}:
            payload.update({
                "item_id": entry.get("item_id") or "",
                "kind": entry.get("kind") or "file",
                "title": entry.get("title") or entry.get("name") or "",
                "url": entry.get("url") or "",
            })
        digest.update(json.dumps(payload, sort_keys=True).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _file_enrich_enabled(entry: Dict[str, Any]) -> bool:
    value = entry.get("enrich_enabled")
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _versioned_signature(*parts: Optional[str]) -> Optional[str]:
    clean_parts = [str(part) for part in parts if part]
    if not clean_parts:
        return None
    digest = hashlib.sha256()
    for part in clean_parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _corpus_signature(files: List[Dict[str, Any]]) -> Optional[str]:
    return _versioned_signature("corpus", RAW_CORPUS_PROFILE, _source_signature(files))


def _prepare_signature(files: List[Dict[str, Any]]) -> Optional[str]:
    source_signature = _source_signature(files)
    if not source_signature:
        return None

    digest = hashlib.sha256()
    digest.update(f"prepare\n{RAW_CORPUS_PROFILE}\n{PREPARE_PROFILE}\n{source_signature}\n".encode("utf-8"))
    for entry in sorted(files, key=lambda item: str(item.get("rel") or item.get("path") or item.get("sha256") or "")):
        rel = str(entry.get("rel") or "")
        enabled = "1" if _file_enrich_enabled(entry) else "0"
        digest.update(f"{rel}\t{enabled}\n".encode("utf-8"))
    return digest.hexdigest()


def _collect_library_paths(slug: str) -> Dict[str, Path]:
    base = lib_dir(slug)
    return {
        "base": base,
        "stage": stage_dir(slug),
        "corpus": base / "corpus.jsonl",
        "enrich_input": base / "corpus.enrich-input.jsonl",
        "enhanced": base / "corpus.enhanced.jsonl",
        "shadow": base / "corpus.shadow.jsonl",
        "shadow_partial": base / "corpus.shadow.partial.jsonl",
        "indexes": indexes_dir(slug),
        "shadow_index": indexes_dir(slug) / "shadow.index.faiss",
        "shadow_store": indexes_dir(slug) / "shadow.meta.jsonl",
        "content_index": indexes_dir(slug) / "content.index.faiss",
        "content_store": indexes_dir(slug) / "content.meta.jsonl",
    }


def _cleanup_enrichment_artifacts(slug: str) -> None:
    paths = _collect_library_paths(slug)
    for key in ("enrich_input", "enhanced", "shadow", "shadow_partial"):
        target = paths[key]
        if target.exists():
            target.unlink()


def _cleanup_generated_artifacts(slug: str) -> None:
    paths = _collect_library_paths(slug)
    for key in (
        "corpus",
        "enrich_input",
        "enhanced",
        "shadow",
        "shadow_partial",
        "shadow_index",
        "shadow_store",
        "content_index",
        "content_store",
    ):
        target = paths[key]
        if target.exists():
            target.unlink()


def _latest_library_job(slug: str, *, statuses: Optional[set[str]] = None) -> Optional[Dict[str, Any]]:
    matches = [
        job for job in JOBS.values()
        if job["slug"] == slug and (statuses is None or job["status"] in statuses)
    ]
    if not matches:
        return None
    matches.sort(key=lambda job: (str(job.get("created_at") or ""), job["id"]), reverse=True)
    return matches[0]


def _build_file_sync_payload(
    slug: str,
    files: List[Dict[str, Any]],
    pipeline: Dict[str, Any],
) -> List[Dict[str, Any]]:
    active_job = _latest_library_job(slug, statuses={"queued", "running"})
    failed_job = _latest_library_job(slug, statuses={"failed"})
    pending_signature = pipeline.get("pending_prepare_signature")
    out: List[Dict[str, Any]] = []

    for entry in files:
        file_entry = dict(entry)
        file_entry["item_id"] = str(file_entry.get("item_id") or file_entry.get("sha256") or file_entry.get("rel") or "")
        file_entry["kind"] = str(file_entry.get("kind") or "file")
        file_entry["title"] = str(file_entry.get("title") or file_entry.get("name") or "")
        file_entry["enrich_enabled"] = _file_enrich_enabled(file_entry)
        stored_status = str(file_entry.get("sync_status") or "pending")
        sync_status = stored_status
        sync_progress = 100.0 if stored_status == "ready" else 0.0
        sync_detail = ""
        sync_error = file_entry.get("sync_error")

        if stored_status != "ready":
            if active_job:
                sync_status = "syncing"
                sync_progress = float(active_job.get("progress") or 0.0)
                sync_detail = active_job.get("detail") or ""
                sync_error = None
            elif failed_job and pending_signature:
                sync_status = "failed"
                sync_progress = 0.0
                sync_detail = failed_job.get("detail") or ""
                sync_error = failed_job.get("error")
            elif pending_signature:
                sync_status = "pending"
                sync_progress = 0.0

        file_entry["sync"] = {
            "status": sync_status,
            "progress": round(sync_progress, 1),
            "detail": sync_detail,
            "error": sync_error,
            "ready": sync_status == "ready",
        }
        out.append(file_entry)

    return out


def library_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    paths = _collect_library_paths(data["slug"])
    pipeline = _pipeline_meta(data)
    raw_files = list(data.get("files", []))
    files = _build_file_sync_payload(data["slug"], raw_files, pipeline)
    source_signature = _source_signature(files)
    corpus_signature = _corpus_signature(files)
    prepare_signature = _prepare_signature(files)
    enrichment_enabled_files = sum(1 for entry in files if _file_enrich_enabled(entry))
    has_corpus = bool(corpus_signature) and pipeline.get("corpus_signature") == corpus_signature and paths["corpus"].exists()
    has_metadata = (
        bool(prepare_signature)
        and pipeline.get("enriched_signature") == prepare_signature
        and paths["enhanced"].exists()
        and paths["shadow_partial"].exists()
    )
    is_enriched = has_metadata and enrichment_enabled_files > 0
    is_indexed = (
        bool(prepare_signature)
        and pipeline.get("indexed_signature") == prepare_signature
        and paths["shadow_index"].exists()
        and paths["shadow_store"].exists()
        and paths["content_index"].exists()
        and paths["content_store"].exists()
    )
    stages = {
        "has_files": len(files) > 0,
        "has_corpus": has_corpus,
        "has_metadata": has_metadata,
        "is_enriched": is_enriched,
        "is_indexed": is_indexed,
        "is_ready_for_chat": is_indexed,
        "needs_prepare": bool(files) and not is_indexed,
        "enrichment_enabled_files": enrichment_enabled_files,
    }
    artifacts = {
        "corpus_records": _line_count(paths["corpus"]) if has_corpus else 0,
        "enhanced_records": _line_count(paths["enhanced"]) if has_metadata else 0,
        "shadow_records": _line_count(paths["shadow_partial"]) if has_metadata else 0,
    }
    return {
        **data,
        "files": files,
        "pipeline": pipeline,
        "source_signature": source_signature,
        "corpus_signature": corpus_signature,
        "prepare_signature": prepare_signature,
        "states": stages,
        "artifacts": artifacts,
    }


def _walk_input_paths(paths: List[str]) -> List[Path]:
    out: List[Path] = []
    for raw in paths:
        current = Path(raw).expanduser().resolve()
        if not current.exists():
            continue
        if current.is_file():
            out.append(current)
            continue
        for child in current.rglob("*"):
            if child.is_file():
                out.append(child.resolve())
    return out


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _managed_source_path(slug: str, item_id: str, suffix: str = ".md") -> Path:
    return sources_dir(slug) / f"{item_id}{suffix}"


def _managed_stage_rel(item_id: str, suffix: str = ".md") -> str:
    return f"managed-{item_id}{suffix}"


def _ensure_stage_link(slug: str, source_path: Path, rel: str) -> None:
    staged = stage_dir(slug) / rel
    if staged.is_symlink() or staged.exists():
        staged.unlink()
    staged.symlink_to(source_path)


def _find_item(data: Dict[str, Any], item_id: str) -> Optional[Dict[str, Any]]:
    return next(
        (
            entry for entry in data.get("files", [])
            if str(entry.get("item_id") or entry.get("sha256") or entry.get("rel") or "") == item_id
        ),
        None,
    )


def _read_indexed_file_text(slug: str, entry: Dict[str, Any], max_chars: int = 200_000) -> tuple[str, bool]:
    corpus_path = _collect_library_paths(slug)["corpus"]
    if not corpus_path.exists():
        return "", False

    raw_path = str(entry.get("path") or "")
    try:
        source_key = str(Path(raw_path).expanduser().resolve())
    except Exception:
        source_key = raw_path
    item_id = str(entry.get("item_id") or entry.get("sha256") or entry.get("rel") or "")
    parts: List[str] = []
    total = 0
    truncated = False

    with corpus_path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            record_source = str(record.get("source_path") or "")
            try:
                record_source_key = str(Path(record_source).expanduser().resolve())
            except Exception:
                record_source_key = record_source
            record_item_id = str((record.get("meta") or {}).get("item_id") or record.get("id") or "")
            if record_source_key != source_key and (not item_id or record_item_id != item_id):
                continue
            text = str(record.get("text") or "").strip()
            if not text:
                continue
            remaining = max_chars - total
            if remaining <= 0:
                truncated = True
                break
            if len(text) > remaining:
                parts.append(text[:remaining].rstrip())
                truncated = True
                break
            parts.append(text)
            total += len(text) + 2

    return "\n\n".join(parts), truncated


def _ensure_global_library() -> Dict[str, Any]:
    if lib_json(GLOBAL_LIBRARY_SLUG).exists():
        return read_library(GLOBAL_LIBRARY_SLUG)
    data = default_library_data(GLOBAL_LIBRARY_NAME, GLOBAL_LIBRARY_SLUG)
    data["system_managed"] = True
    stage_dir(GLOBAL_LIBRARY_SLUG)
    indexes_dir(GLOBAL_LIBRARY_SLUG)
    sources_dir(GLOBAL_LIBRARY_SLUG)
    write_library(GLOBAL_LIBRARY_SLUG, data)
    return data


def _source_library_dirs() -> List[Path]:
    out: List[Path] = []
    if not LIB_ROOT.exists():
        return out
    for path in LIB_ROOT.iterdir():
        if not path.is_dir() or path.name in {GLOBAL_LIBRARY_SLUG, MIGRATED_LIBRARY_ARCHIVE}:
            continue
        if (path / "library.json").exists():
            out.append(path)
    return out


def _migration_archive_root() -> Path:
    path = LIB_ROOT / MIGRATED_LIBRARY_ARCHIVE
    path.mkdir(parents=True, exist_ok=True)
    return path


def _archive_library_dir(path: Path) -> None:
    archive_root = _migration_archive_root()
    target = archive_root / path.name
    if target.exists():
        stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        target = archive_root / f"{path.name}-{stamp}"
        index = 2
        while target.exists():
            target = archive_root / f"{path.name}-{stamp}-{index}"
            index += 1
    shutil.move(str(path), str(target))


def _entry_dedupe_key(entry: Dict[str, Any]) -> Optional[tuple[str, str]]:
    kind = str(entry.get("kind") or "file")
    source_message_id = str(entry.get("source_message_id") or "").strip()
    if kind == "chat_message" and source_message_id:
        return ("chat_message", source_message_id)
    url = str(entry.get("url") or entry.get("final_url") or entry.get("requested_url") or "").strip()
    if kind in {"website", "video"} and url:
        return (kind, url)
    sha = str(entry.get("sha256") or "").strip()
    path = str(entry.get("path") or "").strip()
    if sha and path:
        return (kind, f"{sha}:{path}")
    return None


def _merge_entry_origin(entry: Dict[str, Any], source_library: Dict[str, Any]) -> None:
    slug = str(source_library.get("slug") or "").strip()
    name = str(source_library.get("name") or slug or "Imported collection").strip()
    if not slug:
        return
    entry.setdefault("origin_library_slug", slug)
    entry.setdefault("origin_library_name", name)

    source_libraries = entry.get("source_libraries")
    if not isinstance(source_libraries, list):
        source_libraries = []
    if not any(isinstance(item, dict) and item.get("slug") == slug for item in source_libraries):
        source_libraries.append({"slug": slug, "name": name})
    entry["source_libraries"] = source_libraries

    collections = entry.get("collections")
    if not isinstance(collections, list):
        collections = []
    if name and name not in collections:
        collections.append(name)
    entry["collections"] = collections


def _existing_entry_indexes(files: List[Dict[str, Any]]) -> tuple[set[str], set[str], Dict[tuple[str, str], Dict[str, Any]]]:
    item_ids = {str(entry.get("item_id") or "") for entry in files if str(entry.get("item_id") or "").strip()}
    rels = {str(entry.get("rel") or "") for entry in files if str(entry.get("rel") or "").strip()}
    dedupe: Dict[tuple[str, str], Dict[str, Any]] = {}
    for entry in files:
        key = _entry_dedupe_key(entry)
        if key:
            dedupe[key] = entry
    return item_ids, rels, dedupe


def _unique_item_id(existing_ids: set[str], preferred: Optional[str] = None) -> str:
    value = str(preferred or "").strip() or uuid.uuid4().hex
    while value in existing_ids:
        value = uuid.uuid4().hex
    existing_ids.add(value)
    return value


def _unique_rel(existing_rels: set[str], preferred: str) -> str:
    rel = re.sub(r"[/\\]+", "_", str(preferred or "").strip()) or f"managed-{uuid.uuid4().hex}.md"
    if rel not in existing_rels and not (stage_dir(GLOBAL_LIBRARY_SLUG) / rel).exists():
        existing_rels.add(rel)
        return rel
    stem = Path(rel).stem or "source"
    suffix = Path(rel).suffix
    index = 2
    while True:
        candidate = f"{stem}-{index}{suffix}"
        if candidate not in existing_rels and not (stage_dir(GLOBAL_LIBRARY_SLUG) / candidate).exists():
            existing_rels.add(candidate)
            return candidate
        index += 1


def _is_managed_source_from_library(source_slug: str, source_path: Path) -> bool:
    try:
        source_path.resolve().relative_to(sources_dir(source_slug).resolve())
        return True
    except Exception:
        return False


def _fallback_source_content(source_slug: str, entry: Dict[str, Any]) -> str:
    kind = str(entry.get("kind") or "")
    raw_path = Path(str(entry.get("path") or ""))
    if kind in {"text", "website", "video", "chat_message"} and raw_path.exists():
        try:
            return raw_path.read_text(encoding="utf-8")
        except Exception:
            pass
    content, _truncated = _read_indexed_file_text(source_slug, entry)
    return content


def _migrate_entry_to_global(
    source_slug: str,
    source_library: Dict[str, Any],
    entry: Dict[str, Any],
    global_files: List[Dict[str, Any]],
    existing_ids: set[str],
    existing_rels: set[str],
    dedupe: Dict[tuple[str, str], Dict[str, Any]],
) -> bool:
    key = _entry_dedupe_key(entry)
    if key and key in dedupe:
        _merge_entry_origin(dedupe[key], source_library)
        return True

    migrated = dict(entry)
    original_item_id = str(migrated.get("item_id") or migrated.get("sha256") or migrated.get("rel") or "").strip()
    item_id = _unique_item_id(existing_ids, original_item_id)
    if original_item_id and original_item_id != item_id:
        migrated["source_item_id"] = original_item_id
    migrated["item_id"] = item_id

    source_path = Path(str(migrated.get("path") or ""))
    source_exists = source_path.exists()
    managed_source = bool(migrated.get("managed")) or (source_exists and _is_managed_source_from_library(source_slug, source_path))
    fallback_content = "" if source_exists else _fallback_source_content(source_slug, migrated)

    if managed_source or fallback_content:
        suffix = source_path.suffix or ".md"
        target_path = _managed_source_path(GLOBAL_LIBRARY_SLUG, item_id, suffix)
        if source_exists:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
        elif fallback_content.strip():
            _write_text_atomic(target_path, fallback_content)
        else:
            return False
        rel = _unique_rel(existing_rels, _managed_stage_rel(item_id, suffix))
        _ensure_stage_link(GLOBAL_LIBRARY_SLUG, target_path, rel)
        migrated.update({
            "path": str(target_path),
            "rel": rel,
            "managed": True,
            "sha256": _sha256_file(target_path),
            "size": target_path.stat().st_size,
        })
        if fallback_content and not source_exists:
            migrated["migration_content_fallback"] = True
    else:
        if not source_exists:
            return False
        rel = _unique_rel(existing_rels, str(migrated.get("rel") or _stage_name(str(migrated.get("sha256") or ""), source_path)))
        _ensure_stage_link(GLOBAL_LIBRARY_SLUG, source_path, rel)
        migrated["rel"] = rel

    _merge_entry_origin(migrated, source_library)
    migrated["migrated_at"] = now_iso()
    migrated["sync_status"] = "pending"
    migrated.pop("sync_error", None)
    migrated.pop("synced_at", None)
    global_files.append(migrated)
    if key:
        dedupe[key] = migrated
    return True


def ensure_global_knowledge_store() -> Dict[str, Any]:
    global_data = _ensure_global_library()
    source_dirs = _source_library_dirs()
    if not source_dirs:
        return global_data

    global_data = read_library(GLOBAL_LIBRARY_SLUG)
    global_files = list(global_data.get("files", []))
    existing_ids, existing_rels, dedupe = _existing_entry_indexes(global_files)
    changed = False

    for path in source_dirs:
        try:
            source_library = _read_json(path / "library.json")
            source_slug = str(source_library.get("slug") or path.name)
            source_library["slug"] = source_slug
            source_library.setdefault("name", path.name)
            for entry in source_library.get("files", []):
                if isinstance(entry, dict):
                    changed = _migrate_entry_to_global(
                        source_slug,
                        source_library,
                        entry,
                        global_files,
                        existing_ids,
                        existing_rels,
                        dedupe,
                    ) or changed
            _archive_library_dir(path)
        except Exception as exc:
            print(f"[knowledge] failed to migrate library {path.name}: {exc}")

    if changed:
        global_data["files"] = global_files
        global_data["updated_at"] = now_iso()
        _set_pending_prepare_signature(global_data, _prepare_signature(global_files))
        write_library(GLOBAL_LIBRARY_SLUG, global_data)
        _cleanup_generated_artifacts(GLOBAL_LIBRARY_SLUG)
    return read_library(GLOBAL_LIBRARY_SLUG)


def _mark_entry_pending(entry: Dict[str, Any]) -> None:
    entry["sync_status"] = "pending"
    entry.pop("sync_error", None)
    entry.pop("synced_at", None)
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    entry["metadata"] = {**metadata, "status": "pending"}
    entry["metadata"].pop("error", None)


def _enable_automatic_deep_enrichment(slug: str, data: Optional[Dict[str, Any]] = None) -> bool:
    if not get_auto_deep_enrichment_preference():
        return False

    library = data if data is not None else read_library(slug)
    changed = False
    for entry in library.get("files", []):
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        if metadata.get("status") not in {"ready", "fallback"}:
            continue
        if _file_enrich_enabled(entry) or bool(entry.get(AUTO_DEEP_ENRICHMENT_DISABLED_KEY)):
            continue
        entry["enrich_enabled"] = True
        _mark_entry_pending(entry)
        changed = True

    if not changed:
        return False

    _set_pending_prepare_signature(library, _prepare_signature(library.get("files", [])))
    write_library(slug, library)
    return True


async def _save_library_change(slug: str, data: Dict[str, Any]) -> Optional[str]:
    signature = _prepare_signature(data.get("files", []))
    _set_pending_prepare_signature(data, signature)
    write_library(slug, data)
    if signature:
        return await _ensure_prepare_job(slug)
    _cleanup_generated_artifacts(slug)
    return None


def _apply_source_metadata(corpus_path: Path, files: List[Dict[str, Any]]) -> None:
    if not corpus_path.exists():
        return

    by_path: Dict[str, Dict[str, Any]] = {}
    for entry in files:
        raw_path = str(entry.get("path") or "").strip()
        if not raw_path:
            continue
        try:
            by_path[str(Path(raw_path).expanduser().resolve())] = entry
        except Exception:
            by_path[raw_path] = entry

    rewritten: List[str] = []
    changed = False
    with corpus_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            rec = json.loads(line)
            raw_source = str(rec.get("source_path") or "")
            try:
                source_key = str(Path(raw_source).expanduser().resolve())
            except Exception:
                source_key = raw_source
            entry = by_path.get(source_key)
            if entry and entry.get("item_id"):
                item_id = str(entry["item_id"])
                rec["id"] = item_id
                rec["parent_id"] = None
                rec["title"] = entry.get("title") or entry.get("name") or rec.get("title")
                rec["url"] = entry.get("url") or rec.get("url")
                rec["record_type"] = entry.get("kind") or rec.get("record_type")
                meta = rec.get("meta") if isinstance(rec.get("meta"), dict) else {}
                rec["meta"] = {**meta, "item_id": item_id, "source_kind": entry.get("kind") or "file"}
                changed = True
            rewritten.append(json.dumps(rec, ensure_ascii=False) + "\n")

    if changed:
        tmp = corpus_path.with_suffix(".metadata.tmp")
        tmp.write_text("".join(rewritten), encoding="utf-8")
        tmp.replace(corpus_path)


def _persist_item_metadata(slug: str, enhanced_path: Path) -> None:
    if not enhanced_path.exists():
        return

    path = lib_json(slug)
    if not path.exists():
        return
    data = _read_json(path)
    by_path: Dict[str, Dict[str, Any]] = {}
    for entry in data.get("files", []):
        raw_path = str(entry.get("path") or "")
        try:
            key = str(Path(raw_path).expanduser().resolve())
        except Exception:
            key = raw_path
        if key:
            by_path[key] = entry

    changed = False
    with enhanced_path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            raw_source = str(record.get("source_path") or "")
            try:
                source_key = str(Path(raw_source).expanduser().resolve())
            except Exception:
                source_key = raw_source
            entry = by_path.get(source_key)
            if not entry:
                continue
            enrichment_meta = record.get("enrichment_meta") if isinstance(record.get("enrichment_meta"), dict) else {}
            ok = bool(enrichment_meta.get("ok", True))
            entry["metadata"] = {
                "version": ITEM_METADATA_VERSION,
                "status": "ready" if ok else "fallback",
                "headline": str(record.get("headline") or ""),
                "summary": str(record.get("summary") or ""),
                "keywords": list(record.get("keywords") or []),
                "entities": list(record.get("entities") or []),
                "qa": list(record.get("qa") or []),
                "language": record.get("lang"),
                "level": str(enrichment_meta.get("level") or "standard"),
                "model": enrichment_meta.get("model"),
                "strategy": enrichment_meta.get("strategy"),
                "qa_count": len(record.get("qa") or []),
                "quality_flags": list(enrichment_meta.get("quality_flags") or []),
                "updated_at": now_iso(),
                "error": enrichment_meta.get("error") if not ok else None,
            }
            changed = True
    if changed:
        write_library(slug, data)


def _stage_name(sha: str, path: Path) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", path.name).strip("._") or "file"
    return f"{sha}--{safe_name}"


def _job_public(job: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": job["id"],
        "slug": job["slug"],
        "type": job["type"],
        "status": job["status"],
        "phase": job.get("phase"),
        "progress": job.get("progress", 0.0),
        "detail": job.get("detail", ""),
        "error": job.get("error"),
        "result": job.get("result"),
        "created_at": job["created_at"],
        "finished_at": job.get("finished_at"),
    }


def _has_active_job(slug: str) -> bool:
    return any(
        job["slug"] == slug and job["status"] in {"queued", "running"}
        for job in JOBS.values()
    )


def _load_pipeline_fn(module_name: str, attr: str):
    try:
        module = importlib.import_module(f"backend.rag.{module_name}")
    except ModuleNotFoundError:
        module = importlib.import_module(f".rag.{module_name}", package=__package__)
    return getattr(module, attr)


def _mark_pipeline_stage(slug: str, stage: str, source_signature: Optional[str]) -> None:
    path = lib_json(slug)
    if not path.exists():
        return

    data = _read_json(path)
    pipeline = data.get("pipeline")
    if not isinstance(pipeline, dict):
        pipeline = {}
        data["pipeline"] = pipeline

    stamp = now_iso()
    if stage == "build":
        pipeline["corpus_signature"] = source_signature
        pipeline["corpus_updated_at"] = stamp
        pipeline.pop("enriched_signature", None)
        pipeline.pop("enriched_updated_at", None)
        pipeline.pop("indexed_signature", None)
        pipeline.pop("indexed_updated_at", None)
    elif stage == "enrich":
        pipeline["enriched_signature"] = source_signature
        pipeline["enriched_updated_at"] = stamp
        pipeline.pop("indexed_signature", None)
        pipeline.pop("indexed_updated_at", None)
    elif stage == "embed":
        pipeline["indexed_signature"] = source_signature
        pipeline["indexed_updated_at"] = stamp
    else:
        raise ValueError(f"Unknown pipeline stage: {stage}")

    write_library(slug, data)


def _set_pipeline_embed_model(slug: str, embed_model: Optional[str]) -> None:
    if not embed_model:
        return

    path = lib_json(slug)
    if not path.exists():
        return

    data = _read_json(path)
    pipeline = data.get("pipeline")
    if not isinstance(pipeline, dict):
        pipeline = {}
        data["pipeline"] = pipeline
    pipeline["embed_model"] = embed_model
    pipeline["embed_model_updated_at"] = now_iso()
    write_library(slug, data)


def _set_pending_prepare_signature(data: Dict[str, Any], source_signature: Optional[str]) -> None:
    pipeline = data.get("pipeline")
    if not isinstance(pipeline, dict):
        pipeline = {}
        data["pipeline"] = pipeline

    if source_signature:
        pipeline["pending_prepare_signature"] = source_signature
        pipeline["pending_prepare_updated_at"] = now_iso()
    else:
        pipeline.pop("pending_prepare_signature", None)
        pipeline.pop("pending_prepare_updated_at", None)


def _clear_pending_prepare(slug: str) -> None:
    path = lib_json(slug)
    if not path.exists():
        return

    data = _read_json(path)
    _set_pending_prepare_signature(data, None)
    write_library(slug, data)


def _mark_all_files_ready(slug: str) -> None:
    path = lib_json(slug)
    if not path.exists():
        return

    data = _read_json(path)
    stamp = now_iso()
    changed = False
    for entry in data.get("files", []):
        if entry.get("sync_status") != "ready" or entry.get("synced_at") != stamp:
            changed = True
        entry["sync_status"] = "ready"
        entry["synced_at"] = stamp
        entry.pop("sync_error", None)
    if changed:
        write_library(slug, data)


def _mark_pending_files_failed(slug: str, error: Optional[str]) -> None:
    path = lib_json(slug)
    if not path.exists():
        return

    data = _read_json(path)
    changed = False
    for entry in data.get("files", []):
        if entry.get("sync_status") == "ready":
            continue
        entry["sync_status"] = "failed"
        if error:
            entry["sync_error"] = error
        else:
            entry.pop("sync_error", None)
        changed = True
    if changed:
        write_library(slug, data)


def _mark_unfinished_metadata_failed(slug: str, error: Optional[str]) -> None:
    path = lib_json(slug)
    if not path.exists():
        return
    data = _read_json(path)
    changed = False
    for entry in data.get("files", []):
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        if metadata.get("status") in {"ready", "fallback"}:
            continue
        entry["metadata"] = {**metadata, "status": "failed", "error": error or "Metadata generation failed."}
        changed = True
    if changed:
        write_library(slug, data)


async def _ensure_prepare_job(slug: str) -> Optional[str]:
    path = lib_json(slug)
    if not path.exists():
        return None

    lock = LIB_LOCKS.setdefault(slug, asyncio.Lock())
    async with lock:
        if _has_active_job(slug):
            return None

        data = read_library(slug)
        payload = library_payload(data)
        pipeline = _pipeline_meta(data)
        pending_signature = pipeline.get("pending_prepare_signature")

        if not payload["states"].get("has_files") or not pending_signature:
            return None
        if payload["states"].get("is_indexed"):
            _mark_all_files_ready(slug)
            _clear_pending_prepare(slug)
            return None

        return _start_job(slug, "prepare")


async def _handle_post_job_state(slug: str, job_type: str, status: str) -> None:
    path = lib_json(slug)
    if not path.exists():
        return

    data = read_library(slug)
    payload = library_payload(data)
    pipeline = _pipeline_meta(data)
    pending_signature = pipeline.get("pending_prepare_signature")

    if not payload["states"].get("has_files"):
        _clear_pending_prepare(slug)
        _cleanup_generated_artifacts(slug)
        return

    if payload["states"].get("is_indexed"):
        if job_type == "prepare" and status == "succeeded" and _enable_automatic_deep_enrichment(slug, data):
            await _ensure_prepare_job(slug)
            return
        _mark_all_files_ready(slug)
        _clear_pending_prepare(slug)
        return

    if status == "failed":
        failed_job = _latest_library_job(slug, statuses={"failed"})
        _mark_pending_files_failed(slug, failed_job.get("error") if failed_job else None)
        return

    if status == "succeeded" and pending_signature and not payload["states"].get("is_indexed"):
        await _ensure_prepare_job(slug)


def _scaled_progress(on_progress, start: float, end: float, prefix: str):
    def wrapped(phase: str, pct: float, detail: str):
        clamped = min(1.0, max(0.0, float(pct)))
        span = max(0.0, end - start)
        message = f"{prefix}: {detail}" if detail else prefix
        on_progress(phase, start + (span * clamped), message)

    return wrapped


def _selected_enrichment_paths(files: List[Dict[str, Any]]) -> set[str]:
    return {
        str(entry.get("path") or "")
        for entry in files
        if _file_enrich_enabled(entry) and str(entry.get("path") or "").strip()
    }


def _shadow_size_metrics(text: str) -> Dict[str, int]:
    return {
        "char_count": len(text),
        "word_count": len(text.split()),
        "line_count": len(text.splitlines()),
    }


def _build_raw_shadow_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    synth_shadow_from_raw = _load_pipeline_fn("index_builder", "synth_shadow_from_raw")
    shadow_text = synth_shadow_from_raw(rec)
    return {
        "id": rec.get("id"),
        "parent_id": rec.get("parent_id"),
        "source_path": rec.get("source_path"),
        "url": rec.get("url"),
        "title": rec.get("title"),
        "record_type": rec.get("record_type"),
        "mime": rec.get("mime"),
        "lang": rec.get("lang"),
        "span": rec.get("span") if isinstance(rec.get("span"), dict) else None,
        "shadow_text": shadow_text,
        "shadow_meta": {
            "prompt_version": "raw-fallback",
            "size": _shadow_size_metrics(shadow_text),
        },
    }


def _write_selected_corpus(corpus_path: Path, out_path: Path, selected_paths: set[str]) -> int:
    if not selected_paths or not corpus_path.exists():
        if out_path.exists():
            out_path.unlink()
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with corpus_path.open("r", encoding="utf-8") as src, out_path.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            source_path = str(rec.get("source_path") or "")
            if source_path not in selected_paths:
                continue
            dst.write(json.dumps(rec, ensure_ascii=False) + "\n")
            written += 1

    if written == 0 and out_path.exists():
        out_path.unlink()
    return written


def _build_merged_shadow_corpus(corpus_path: Path, partial_shadow_path: Path, out_path: Path) -> int:
    if not corpus_path.exists():
        if out_path.exists():
            out_path.unlink()
        return 0

    selected_shadow: Dict[str, Dict[str, Any]] = {}
    if partial_shadow_path.exists():
        with partial_shadow_path.open("r", encoding="utf-8") as src:
            for line in src:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                rec_id = str(rec.get("id") or rec.get("record_id") or "")
                if rec_id:
                    selected_shadow[rec_id] = rec

    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with corpus_path.open("r", encoding="utf-8") as src, out_path.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            rec_id = str(rec.get("id") or rec.get("record_id") or "")
            shadow_record = selected_shadow.get(rec_id) if rec_id else None
            if shadow_record is None:
                shadow_record = _build_raw_shadow_record(rec)
            dst.write(json.dumps(shadow_record, ensure_ascii=False) + "\n")
            written += 1

    if written == 0 and out_path.exists():
        out_path.unlink()
    return written


def _run_selected_enrichment(slug: str, on_progress=None, **opts) -> Dict[str, Any]:
    data = read_library(slug)
    files = list(data.get("files", []))
    deep_paths = _selected_enrichment_paths(files)
    paths = _collect_library_paths(slug)

    if not paths["corpus"].exists():
        raise RuntimeError("Build the corpus before generating metadata.")

    enrich_runner = _load_pipeline_fn("corpus_enricher", "run_enrich")
    result = enrich_runner(
        inp=paths["corpus"],
        out=paths["enhanced"],
        shadow_out=paths["shadow_partial"],
        on_progress=on_progress,
        ollama=_resolve_ollama_url(opts.get("ollama")),
        model=opts.get("enrich_model") or _default_enrichment_model() or DEFAULT_ENRICH_MODEL,
        summary_lang=opts.get("summary_lang", "auto"),
        concurrency=opts.get("enrich_concurrency", DEFAULT_ENRICH_CONCURRENCY),
        min_chars=opts.get("min_chars", DEFAULT_ENRICH_MIN_CHARS),
        max_text=opts.get("max_text", DEFAULT_ENRICH_MAX_TEXT),
        cache_dir=opts.get("cache_dir", str(paths["base"] / ".rag_cache")),
        deep_source_paths=deep_paths,
    )
    _persist_item_metadata(slug, paths["enhanced"])
    result["metadata_records"] = _line_count(paths["enhanced"])
    result["deep_files"] = len(deep_paths)
    result["shadow_records"] = _build_merged_shadow_corpus(paths["corpus"], paths["shadow_partial"], paths["shadow"])
    return result


def _run_prepare_pipeline(slug: str, on_progress=None, **opts):
    data = read_library(slug)
    payload = library_payload(data)
    pipeline = _pipeline_meta(data)
    files = list(data.get("files", []))
    source_signature = payload.get("source_signature")
    corpus_signature = payload.get("corpus_signature")
    prepare_signature = payload.get("prepare_signature")
    if not files or not source_signature or not corpus_signature or not prepare_signature:
        raise RuntimeError("Add files before preparing Knowledge.")

    paths = _collect_library_paths(slug)
    states = dict(payload.get("states") or {})
    results: Dict[str, Any] = {}

    build_runner = _load_pipeline_fn("corpus_builder", "run_build")
    index_runner = _load_pipeline_fn("index_builder", "run_index")
    embed_model = opts.get("embed_model") or _default_embed_model() or pipeline.get("embed_model") or DEFAULT_EMBED_MODEL

    if on_progress:
        on_progress("prepare", 0.01, "Preparing Knowledge for chat...")

    if not states.get("has_corpus"):
        build_progress = _scaled_progress(on_progress, 0.02, 0.34, "Reading files") if on_progress else None
        results["build"] = build_runner(
            root=stage_dir(slug),
            out=paths["corpus"],
            on_progress=build_progress,
            emit="per-file",
            lang_detect=False,
        )
        _apply_source_metadata(paths["corpus"], files)
        build_errors = list((results.get("build") or {}).get("errors") or [])
        if not paths["corpus"].exists() or _line_count(paths["corpus"]) == 0:
            if build_errors:
                raise RuntimeError(build_errors[0])
            raise RuntimeError("Corpus build produced no usable records.")
        _mark_pipeline_stage(slug, "build", corpus_signature)
        states["has_corpus"] = True
        states["is_enriched"] = False
        states["is_indexed"] = False

    if not states.get("has_metadata"):
        enrich_progress = _scaled_progress(on_progress, 0.34, 0.69, "Generating metadata") if on_progress else None
        results["enrich"] = _run_selected_enrichment(
            slug,
            on_progress=enrich_progress,
            ollama=_resolve_ollama_url(opts.get("ollama")),
            enrich_model=opts.get("enrich_model") or _default_enrichment_model() or DEFAULT_ENRICH_MODEL,
            summary_lang=opts.get("summary_lang", "auto"),
            enrich_concurrency=opts.get("enrich_concurrency", DEFAULT_ENRICH_CONCURRENCY),
            min_chars=opts.get("min_chars", DEFAULT_ENRICH_MIN_CHARS),
            max_text=opts.get("max_text", DEFAULT_ENRICH_MAX_TEXT),
        )
        _mark_pipeline_stage(slug, "enrich", prepare_signature)
        states["has_metadata"] = True
        states["is_enriched"] = int(states.get("enrichment_enabled_files") or 0) > 0
        states["is_indexed"] = False

    if not states.get("is_indexed"):
        index_progress = _scaled_progress(on_progress, 0.69, 1.0, "Building search indexes") if on_progress else None
        results["embed"] = index_runner(
            raw=paths["corpus"],
            enhanced=None,
            shadow=paths["shadow"] if paths["shadow"].exists() else None,
            out_dir=paths["indexes"],
            on_progress=index_progress,
            embed_model=embed_model,
            ollama=_resolve_ollama_url(opts.get("ollama")),
            target_chars=opts.get("target_chars", 2000),
            overlap_chars=opts.get("overlap_chars", 200),
            concurrency=opts.get("concurrency", 6),
        )
        _set_pipeline_embed_model(slug, results["embed"].get("embed_model") if isinstance(results.get("embed"), dict) else None)
        _mark_pipeline_stage(slug, "embed", prepare_signature)

    if on_progress:
        on_progress("done", 1.0, "Knowledge is ready for chat.")

    return {
        "status": "ok",
        "results": results,
        "source_signature": source_signature,
        "embed_model": (results.get("embed") or {}).get("embed_model") if isinstance(results.get("embed"), dict) else None,
    }


async def _run_job(job_id: str, fn_name: str, **kwargs):
    loop = asyncio.get_running_loop()
    job = JOBS[job_id]
    stage_signature = kwargs.pop("stage_signature", None)

    def on_progress(phase: str, pct: float, detail: str):
        job["phase"] = phase
        job["progress"] = round(float(pct) * 100.0, 1)
        job["detail"] = detail

    job["status"] = "running"
    try:
        if fn_name == "build":
            runner = _load_pipeline_fn("corpus_builder", "run_build")
        elif fn_name == "enrich":
            runner = functools.partial(_run_selected_enrichment, job["slug"])
        elif fn_name == "embed":
            runner = _load_pipeline_fn("index_builder", "run_index")
        elif fn_name == "prepare":
            runner = functools.partial(_run_prepare_pipeline, job["slug"])
        else:
            raise RuntimeError(f"Unknown job type: {fn_name}")

        call = functools.partial(runner, on_progress=on_progress, **kwargs)
        result = await loop.run_in_executor(JOB_EXECUTOR, call)
        if fn_name in {"embed", "prepare"} and isinstance(result, dict):
            embed_model = result.get("embed_model")
            if embed_model:
                _set_pipeline_embed_model(job["slug"], embed_model)
        if fn_name in {"build", "enrich", "embed"} and stage_signature:
            _mark_pipeline_stage(job["slug"], fn_name, stage_signature)
        job["status"] = "succeeded"
        job["progress"] = 100.0
        job["phase"] = "done"
        job["detail"] = "Completed."
        job["result"] = result
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = f"{type(exc).__name__}: {exc}"
        if fn_name in {"enrich", "prepare"} and job.get("phase") in {"load", "enrich"}:
            _mark_unfinished_metadata_failed(job["slug"], job["error"])
    finally:
        job["finished_at"] = now_iso()
        try:
            await _handle_post_job_state(job["slug"], fn_name, job["status"])
        except Exception:
            pass


def _start_job(slug: str, job_type: str, **kwargs) -> str:
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {
        "id": job_id,
        "slug": slug,
        "type": job_type,
        "status": "queued",
        "phase": "queued",
        "progress": 0.0,
        "detail": "",
        "created_at": now_iso(),
        "finished_at": None,
        "result": None,
        "error": None,
    }
    asyncio.create_task(_run_job(job_id, job_type, **kwargs))
    return job_id


def _build_local_context(prompt: str, results: Dict[str, Any], top_k: int = 5) -> Dict[str, Any]:
    sources = results.get("sources") or []
    selected = sources[: max(1, top_k)]
    if not selected:
        context_block = (
            "<local_rag_context>\n"
            "No useful results were found in the selected local knowledge base.\n"
            "</local_rag_context>"
        )
        return {"context_block": context_block, "sources": []}

    blocks: List[str] = ["<local_rag_context>"]
    file_sources: List[str] = []
    seen_sources: set[str] = set()
    for idx, source in enumerate(selected, start=1):
        title = (source.get("title") or Path(source.get("url") or source.get("doc_id") or f"Source {idx}").name).strip()
        snippet = re.sub(r"\s+", " ", (source.get("snippet") or "")).strip()
        if len(snippet) > 1400:
            snippet = snippet[:1400].rstrip() + "..."
        raw_path = source.get("url") or source.get("doc_id") or ""
        if raw_path:
            source_uri = ""
            if os.path.isabs(raw_path):
                source_uri = _file_uri(raw_path)
            elif str(raw_path).startswith(("http://", "https://")):
                source_uri = str(raw_path)
            if source_uri and source_uri not in seen_sources:
                seen_sources.add(source_uri)
                file_sources.append(source_uri)
        blocks.append(f"[L{idx}] {title}\n{snippet}")
    blocks.append("</local_rag_context>")
    blocks.append(
        "Use the local knowledge base context when it is relevant. "
        "If it does not answer the question, say so clearly instead of inventing details."
    )
    return {"context_block": "\n".join(blocks), "sources": file_sources}


async def _synced_library_payload(slug: str) -> Dict[str, Any]:
    data = read_library(slug)
    payload = library_payload(data)
    if (
        payload["states"].get("is_indexed")
        and not _has_active_job(data["slug"])
        and _enable_automatic_deep_enrichment(data["slug"], data)
    ):
        await _ensure_prepare_job(data["slug"])
        data = read_library(data["slug"])
        payload = library_payload(data)
    has_failed_item = any(
        str(entry.get("sync_status") or "") == "failed"
        for entry in data.get("files", [])
    )
    if (
        _pipeline_meta(data).get("pending_prepare_signature")
        and not _has_active_job(data["slug"])
        and (payload["states"].get("is_indexed") or not has_failed_item)
    ):
        await _ensure_prepare_job(data["slug"])
    return library_payload(read_library(slug))


async def _global_library_payload() -> Dict[str, Any]:
    ensure_global_knowledge_store()
    return await _synced_library_payload(GLOBAL_LIBRARY_SLUG)


@router.get("/libraries")
async def list_libraries():
    return {"libraries": [await _global_library_payload()]}


@router.get("/knowledge")
async def get_knowledge():
    return await _global_library_payload()


@router.post("/knowledge/files/register")
async def register_knowledge_paths(req: RegisterPathsRequest):
    ensure_global_knowledge_store()
    return await register_paths(GLOBAL_LIBRARY_SLUG, req)


@router.get("/knowledge/items/{item_id}")
def get_knowledge_item(item_id: str):
    ensure_global_knowledge_store()
    return get_library_item(GLOBAL_LIBRARY_SLUG, item_id)


@router.post("/knowledge/texts")
async def create_knowledge_text(req: CreateTextRequest):
    ensure_global_knowledge_store()
    return await create_library_text(GLOBAL_LIBRARY_SLUG, req)


@router.patch("/knowledge/texts/{item_id}")
async def update_knowledge_text(item_id: str, req: UpdateTextRequest):
    ensure_global_knowledge_store()
    return await update_library_text(GLOBAL_LIBRARY_SLUG, item_id, req)


@router.post("/knowledge/websites")
async def create_knowledge_website(req: CreateWebsiteRequest):
    ensure_global_knowledge_store()
    return await create_library_website(GLOBAL_LIBRARY_SLUG, req)


@router.post("/knowledge/videos/{item_id}/refresh")
async def refresh_knowledge_video(item_id: str):
    ensure_global_knowledge_store()
    return await refresh_library_video(GLOBAL_LIBRARY_SLUG, item_id)


@router.post("/knowledge/websites/{item_id}/refresh")
async def refresh_knowledge_website(item_id: str):
    ensure_global_knowledge_store()
    return await refresh_library_website(GLOBAL_LIBRARY_SLUG, item_id)


@router.delete("/knowledge/files")
async def remove_knowledge_file(req: RemoveFileRequest):
    ensure_global_knowledge_store()
    return await remove_file(GLOBAL_LIBRARY_SLUG, req)


@router.patch("/knowledge/files/enrichment")
async def update_knowledge_file_enrichment(req: UpdateFileEnrichmentRequest):
    ensure_global_knowledge_store()
    return await update_file_enrichment(GLOBAL_LIBRARY_SLUG, req)


@router.post("/knowledge/jobs/prepare")
async def prepare_knowledge():
    ensure_global_knowledge_store()
    return await prepare_library(GLOBAL_LIBRARY_SLUG)


@router.post("/knowledge/context")
def knowledge_context(req: LibraryContextRequest):
    ensure_global_knowledge_store()
    return library_context(GLOBAL_LIBRARY_SLUG, req)


@router.post("/knowledge/purge")
def purge_knowledge():
    return purge_libraries()


@router.post("/libraries")
def create_library(req: CreateLibraryRequest):
    slug = slugify(req.name)
    base_slug = slug
    idx = 2
    while lib_dir(slug).exists():
        slug = f"{base_slug}-{idx}"
        idx += 1
    data = default_library_data(req.name, slug)
    stage_dir(slug)
    indexes_dir(slug)
    write_library(slug, data)
    return library_payload(data)


@router.post("/libraries/purge")
def purge_libraries():
    active_jobs = [
        job for job in JOBS.values()
        if job["status"] in {"queued", "running"}
    ]
    if active_jobs:
        active_slugs = sorted({str(job.get("slug") or "") for job in active_jobs if job.get("slug")})
        detail = "Cannot purge Knowledge while sync jobs are still running."
        if active_slugs:
            detail = f"{detail} Active jobs: {', '.join(active_slugs)}."
        raise HTTPException(status_code=409, detail=detail)

    removed: List[str] = []
    failures: List[str] = []

    for path in list(LIB_ROOT.iterdir()):
        if not path.is_dir():
            continue
        try:
            shutil.rmtree(path)
            removed.append(path.name)
        except Exception as exc:
            failures.append(f"{path.name}: {type(exc).__name__}: {exc}")

    LIB_ROOT.mkdir(parents=True, exist_ok=True)
    JOBS.clear()
    LIB_LOCKS.clear()
    QUERY_LOCKS.clear()

    if failures:
        preview = "; ".join(failures[:3])
        if len(failures) > 3:
            preview = f"{preview}; ..."
        raise HTTPException(status_code=500, detail=f"Failed to purge some Knowledge data. {preview}")

    return {
        "ok": True,
        "count": len(removed),
        "removed": removed,
    }


@router.get("/libraries/{slug}")
def get_library(slug: str):
    return library_payload(read_library(slug))


@router.patch("/libraries/{slug}")
def rename_library(slug: str, req: RenameLibraryRequest):
    data = read_library(slug)
    data["name"] = req.name.strip() or data["name"]
    write_library(slug, data)
    return library_payload(data)


@router.delete("/libraries/{slug}")
def delete_library(slug: str):
    path = lib_dir(slug)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Library not found")
    shutil.rmtree(path)
    LIB_LOCKS.pop(slug, None)
    QUERY_LOCKS.pop(slug, None)
    return {"ok": True}


@router.post("/libraries/{slug}/files/register")
async def register_paths(slug: str, req: RegisterPathsRequest):
    data = read_library(slug)
    stage = stage_dir(slug)
    existing = {entry.get("sha256"): entry for entry in data.get("files", [])}
    added: List[Dict[str, Any]] = []
    skipped: List[str] = []

    for file_path in _walk_input_paths(req.paths):
        sha = _sha256_file(file_path)
        if sha in existing:
            skipped.append(str(file_path))
            continue
        stage_name = _stage_name(sha, file_path)
        symlink_path = stage / stage_name
        if symlink_path.exists():
            symlink_path.unlink()
        symlink_path.symlink_to(file_path)
        entry = {
            "item_id": uuid.uuid4().hex,
            "kind": "file",
            "sha256": sha,
            "path": str(file_path),
            "rel": stage_name,
            "name": file_path.name,
            "size": file_path.stat().st_size,
            "added_at": now_iso(),
            "sync_status": "pending",
            "enrich_enabled": False,
            "metadata": {"status": "pending"},
        }
        data.setdefault("files", []).append(entry)
        added.append(entry)
        existing[sha] = entry

    job_id = None
    if added:
        _set_pending_prepare_signature(data, _prepare_signature(data.get("files", [])))
    write_library(slug, data)
    if added:
        job_id = await _ensure_prepare_job(slug)
    return {
        "added": added,
        "skipped": skipped,
        "job_id": job_id,
        "library": library_payload(data),
    }


@router.get("/libraries/{slug}/items/{item_id}")
def get_library_item(slug: str, item_id: str):
    data = read_library(slug)
    entry = _find_item(data, item_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Content item not found")

    payload = dict(entry)
    if entry.get("kind") in {"text", "website", "video", "chat_message"}:
        path = Path(str(entry.get("path") or ""))
        payload["content"] = path.read_text(encoding="utf-8") if path.exists() else ""
        if entry.get("kind") == "website":
            payload["content_origin"] = "stored_snapshot"
        elif entry.get("kind") == "video":
            payload["content_origin"] = "stored_video_transcript"
        elif entry.get("kind") == "chat_message":
            payload["content_origin"] = "stored_chat_message"
        else:
            payload["content_origin"] = "stored_text"
        payload["content_truncated"] = False
    else:
        content, truncated = _read_indexed_file_text(slug, entry)
        payload["content"] = content
        payload["content_origin"] = "extracted_text"
        payload["content_truncated"] = truncated
    return payload


@router.post("/libraries/{slug}/texts")
async def create_library_text(slug: str, req: CreateTextRequest):
    data = read_library(slug)
    title = str(req.title or "").strip()
    content = str(req.content or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Text title is required.")
    if not content:
        raise HTTPException(status_code=400, detail="Text content is required.")

    item_id = uuid.uuid4().hex
    source_path = _managed_source_path(slug, item_id)
    rel = _managed_stage_rel(item_id)
    _write_text_atomic(source_path, content)
    _ensure_stage_link(slug, source_path, rel)
    entry = {
        "item_id": item_id,
        "kind": "text",
        "title": title[:240],
        "name": title[:240],
        "path": str(source_path),
        "rel": rel,
        "sha256": _sha256_file(source_path),
        "size": source_path.stat().st_size,
        "added_at": now_iso(),
        "updated_at": now_iso(),
        "sync_status": "pending",
        "enrich_enabled": False,
        "metadata": {"status": "pending"},
        "managed": True,
    }
    data.setdefault("files", []).append(entry)
    job_id = await _save_library_change(slug, data)
    return {"item": entry, "job_id": job_id, "library": library_payload(data)}


async def save_chat_message_snapshot(
    slug: str,
    *,
    title: str,
    content: str,
    source_message_id: str,
    source_session_id: str,
    source_session_title: str,
    source_role: str,
    source_created_at: Optional[str],
    sources: Optional[List[str]] = None,
    saved_by: str = "user",
) -> Dict[str, Any]:
    data = read_library(slug)
    existing = next(
        (
            entry for entry in data.get("files", [])
            if entry.get("kind") == "chat_message"
            and entry.get("source_message_id") == source_message_id
        ),
        None,
    )
    if existing:
        return {
            "item": existing,
            "job_id": None,
            "already_saved": True,
            "library": library_payload(data),
        }

    clean_title = str(title or "").strip()
    clean_content = str(content or "").strip()
    if not clean_title:
        raise HTTPException(status_code=400, detail="A title is required.")
    if not clean_content:
        raise HTTPException(status_code=400, detail="Message content is required.")

    item_id = uuid.uuid4().hex
    source_path = _managed_source_path(slug, item_id)
    rel = _managed_stage_rel(item_id)
    _write_text_atomic(source_path, clean_content)
    _ensure_stage_link(slug, source_path, rel)
    timestamp = now_iso()
    entry = {
        "item_id": item_id,
        "kind": "chat_message",
        "title": clean_title[:240],
        "name": clean_title[:240],
        "path": str(source_path),
        "rel": rel,
        "sha256": _sha256_file(source_path),
        "size": source_path.stat().st_size,
        "added_at": timestamp,
        "updated_at": timestamp,
        "sync_status": "pending",
        "enrich_enabled": False,
        "metadata": {"status": "pending"},
        "managed": True,
        "source_role": source_role,
        "source_message_id": source_message_id,
        "source_session_id": source_session_id,
        "source_session_title": source_session_title,
        "source_created_at": source_created_at,
        "sources": list(sources or []),
        "saved_by": saved_by,
    }
    data.setdefault("files", []).append(entry)
    job_id = await _save_library_change(slug, data)
    return {
        "item": entry,
        "job_id": job_id,
        "already_saved": False,
        "library": library_payload(data),
    }


@router.patch("/libraries/{slug}/texts/{item_id}")
async def update_library_text(slug: str, item_id: str, req: UpdateTextRequest):
    data = read_library(slug)
    entry = _find_item(data, item_id)
    if not entry or entry.get("kind") not in {"text", "chat_message"}:
        raise HTTPException(status_code=404, detail="Text item not found")

    title = str(req.title or "").strip()
    content = str(req.content or "").strip()
    if not title or not content:
        raise HTTPException(status_code=400, detail="Text title and content are required.")

    source_path = Path(str(entry.get("path") or ""))
    _write_text_atomic(source_path, content)
    _ensure_stage_link(slug, source_path, str(entry.get("rel") or _managed_stage_rel(item_id)))
    entry.update({
        "title": title[:240],
        "name": title[:240],
        "sha256": _sha256_file(source_path),
        "size": source_path.stat().st_size,
        "updated_at": now_iso(),
    })
    _mark_entry_pending(entry)
    job_id = await _save_library_change(slug, data)
    return {"item": entry, "job_id": job_id, "library": library_payload(data)}


@router.post("/libraries/{slug}/websites")
async def create_library_website(slug: str, req: CreateWebsiteRequest):
    requested_url = str(req.url or "").strip()
    parsed_url = urlparse(requested_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise HTTPException(status_code=400, detail="Enter a valid HTTP or HTTPS URL.")
    try:
        video_metadata = await asyncio.to_thread(probe_video_url, requested_url)
    except UnsupportedVideoUrl:
        video_metadata = None
    except Exception as exc:
        if is_likely_media_url(requested_url):
            raise HTTPException(status_code=400, detail=f"Could not read video URL: {exc}") from exc
        video_metadata = None

    if video_metadata:
        try:
            video = await ingest_video_url(requested_url, video_metadata)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not process video: {exc}") from exc

        data = read_library(slug)
        canonical_url = str(video["url"])
        video_id = str(video.get("video_id") or "")
        extractor = str(video.get("extractor") or "")
        duplicate = next(
            (
                entry for entry in data.get("files", [])
                if entry.get("kind") == "video"
                and (
                    entry.get("url") == canonical_url
                    or (
                        video_id
                        and entry.get("video_id") == video_id
                        and str(entry.get("extractor") or "") == extractor
                    )
                )
            ),
            None,
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="This video is already saved in Knowledge.")

        item_id = uuid.uuid4().hex
        source_path = _managed_source_path(slug, item_id)
        rel = _managed_stage_rel(item_id)
        _write_text_atomic(source_path, str(video["document"]))
        _ensure_stage_link(slug, source_path, rel)
        custom_title = str(req.title or "").strip()
        title = custom_title or str(video.get("title") or canonical_url)
        entry = {
            "item_id": item_id,
            "kind": "video",
            "title": title[:240],
            "title_locked": bool(custom_title),
            "name": title[:240],
            "url": canonical_url,
            "requested_url": requested_url,
            "video_id": video.get("video_id"),
            "extractor": video.get("extractor"),
            "channel": video.get("channel"),
            "duration": video.get("duration"),
            "duration_text": video.get("duration_text"),
            "upload_date": video.get("upload_date"),
            "thumbnail_url": video.get("thumbnail_url"),
            "video_summary": video.get("summary"),
            "transcript_chars": len(str(video.get("transcript") or "")),
            "transcription_model": video.get("transcription_model"),
            "transcription_workers": video.get("transcription_workers"),
            "transcription_slices": video.get("transcription_slices"),
            "summary_model": video.get("summary_model"),
            "yt_dlp_version": video.get("yt_dlp_version"),
            "fetched_at": video.get("fetched_at") or now_iso(),
            "path": str(source_path),
            "rel": rel,
            "sha256": _sha256_file(source_path),
            "size": source_path.stat().st_size,
            "added_at": now_iso(),
            "updated_at": now_iso(),
            "sync_status": "pending",
            "enrich_enabled": False,
            "metadata": {"status": "pending"},
            "managed": True,
        }
        data.setdefault("files", []).append(entry)
        job_id = await _save_library_change(slug, data)
        return {
            "item": entry,
            "job_id": job_id,
            "content_kind": "video",
            "library": library_payload(data),
        }

    data = read_library(slug)
    try:
        snapshot = await fetch_website_snapshot(requested_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=400, detail=f"Website returned HTTP {exc.response.status_code}.") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not fetch website: {exc}") from exc

    canonical_url = str(snapshot["url"])
    duplicate = next(
        (entry for entry in data.get("files", []) if entry.get("kind") == "website" and entry.get("url") == canonical_url),
        None,
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="This website is already saved in Knowledge.")

    item_id = uuid.uuid4().hex
    source_path = _managed_source_path(slug, item_id)
    rel = _managed_stage_rel(item_id)
    _write_text_atomic(source_path, str(snapshot["text"]))
    _ensure_stage_link(slug, source_path, rel)
    custom_title = str(req.title or "").strip()
    title = custom_title or str(snapshot.get("title") or canonical_url)
    entry = {
        "item_id": item_id,
        "kind": "website",
        "title": title[:240],
        "title_locked": bool(custom_title),
        "name": title[:240],
        "url": canonical_url,
        "requested_url": str(snapshot.get("requested_url") or requested_url),
        "final_url": str(snapshot.get("final_url") or canonical_url),
        "content_type": snapshot.get("content_type"),
        "etag": snapshot.get("etag"),
        "last_modified": snapshot.get("last_modified"),
        "fetched_at": now_iso(),
        "path": str(source_path),
        "rel": rel,
        "sha256": _sha256_file(source_path),
        "size": source_path.stat().st_size,
        "added_at": now_iso(),
        "updated_at": now_iso(),
        "sync_status": "pending",
        "enrich_enabled": False,
        "metadata": {"status": "pending"},
        "managed": True,
    }
    data.setdefault("files", []).append(entry)
    job_id = await _save_library_change(slug, data)
    return {"item": entry, "job_id": job_id, "content_kind": "website", "library": library_payload(data)}


@router.post("/libraries/{slug}/videos/{item_id}/refresh")
async def refresh_library_video(slug: str, item_id: str):
    data = read_library(slug)
    entry = _find_item(data, item_id)
    if not entry or entry.get("kind") != "video":
        raise HTTPException(status_code=404, detail="Video item not found")

    url = str(entry.get("url") or entry.get("requested_url") or "").strip()
    try:
        metadata = await asyncio.to_thread(probe_video_url, url)
        if not metadata:
            raise RuntimeError("yt-dlp no longer reports playable media for this URL.")
        video = await ingest_video_url(url, metadata)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not reprocess video: {exc}") from exc

    data = read_library(slug)
    entry = _find_item(data, item_id)
    if not entry or entry.get("kind") != "video":
        raise HTTPException(status_code=404, detail="Video item not found")
    source_path = Path(str(entry.get("path") or ""))
    previous_hash = str(entry.get("sha256") or "")
    _write_text_atomic(source_path, str(video["document"]))
    _ensure_stage_link(slug, source_path, str(entry.get("rel") or _managed_stage_rel(item_id)))
    new_hash = _sha256_file(source_path)
    if not entry.get("title_locked"):
        entry["title"] = str(video.get("title") or entry.get("title") or url)[:240]
        entry["name"] = entry["title"]
    entry.update({
        "url": video.get("url") or url,
        "video_id": video.get("video_id"),
        "extractor": video.get("extractor"),
        "channel": video.get("channel"),
        "duration": video.get("duration"),
        "duration_text": video.get("duration_text"),
        "upload_date": video.get("upload_date"),
        "thumbnail_url": video.get("thumbnail_url"),
        "video_summary": video.get("summary"),
        "transcript_chars": len(str(video.get("transcript") or "")),
        "transcription_model": video.get("transcription_model"),
        "transcription_workers": video.get("transcription_workers"),
        "transcription_slices": video.get("transcription_slices"),
        "summary_model": video.get("summary_model"),
        "yt_dlp_version": video.get("yt_dlp_version"),
        "fetched_at": video.get("fetched_at") or now_iso(),
        "sha256": new_hash,
        "size": source_path.stat().st_size,
        "updated_at": now_iso(),
    })
    _mark_entry_pending(entry)
    job_id = await _save_library_change(slug, data)
    return {
        "changed": previous_hash != new_hash,
        "item": entry,
        "job_id": job_id,
        "library": library_payload(data),
    }


@router.post("/libraries/{slug}/websites/{item_id}/refresh")
async def refresh_library_website(slug: str, item_id: str):
    data = read_library(slug)
    entry = _find_item(data, item_id)
    if not entry or entry.get("kind") != "website":
        raise HTTPException(status_code=404, detail="Website item not found")

    try:
        snapshot = await fetch_website_snapshot(str(entry.get("url") or entry.get("requested_url") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=400, detail=f"Website returned HTTP {exc.response.status_code}.") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not refresh website: {exc}") from exc

    source_path = Path(str(entry.get("path") or ""))
    previous_hash = str(entry.get("sha256") or "")
    previous_title = str(entry.get("title") or "")
    previous_url = str(entry.get("url") or "")
    _write_text_atomic(source_path, str(snapshot["text"]))
    next_hash = _sha256_file(source_path)
    entry.update({
        "url": str(snapshot.get("url") or entry.get("url") or ""),
        "final_url": str(snapshot.get("final_url") or entry.get("final_url") or ""),
        "content_type": snapshot.get("content_type"),
        "etag": snapshot.get("etag"),
        "last_modified": snapshot.get("last_modified"),
        "fetched_at": now_iso(),
        "size": source_path.stat().st_size,
        "sha256": next_hash,
        "updated_at": now_iso(),
    })
    if not entry.get("title_locked"):
        title = str(snapshot.get("title") or entry.get("title") or entry.get("url") or "")[:240]
        entry["title"] = title
        entry["name"] = title

    job_id = None
    changed = next_hash != previous_hash or str(entry.get("title") or "") != previous_title or str(entry.get("url") or "") != previous_url
    if changed:
        _mark_entry_pending(entry)
        job_id = await _save_library_change(slug, data)
    else:
        write_library(slug, data)
    return {"changed": changed, "item": entry, "job_id": job_id, "library": library_payload(data)}


@router.delete("/libraries/{slug}/files")
async def remove_file(slug: str, req: RemoveFileRequest):
    data = read_library(slug)
    files = list(data.get("files", []))
    removed = next((entry for entry in files if entry.get("rel") == req.rel), None)
    if not removed:
        raise HTTPException(status_code=404, detail="File not found")

    data["files"] = [entry for entry in files if entry.get("rel") != req.rel]
    symlink_path = stage_dir(slug) / req.rel
    if symlink_path.is_symlink() or symlink_path.exists():
        symlink_path.unlink()
    if removed.get("managed"):
        managed_path = Path(str(removed.get("path") or ""))
        if managed_path.exists() and managed_path.is_file():
            managed_path.unlink()

    prepare_signature = _prepare_signature(data.get("files", []))
    _set_pending_prepare_signature(data, prepare_signature)
    write_library(slug, data)

    job_id = None
    if prepare_signature:
        job_id = await _ensure_prepare_job(slug)
    else:
        _cleanup_generated_artifacts(slug)

    return {"ok": True, "job_id": job_id, "library": library_payload(data)}


@router.patch("/libraries/{slug}/files/enrichment")
async def update_file_enrichment(slug: str, req: UpdateFileEnrichmentRequest):
    data = read_library(slug)
    files = list(data.get("files", []))
    target = next((entry for entry in files if entry.get("rel") == req.rel), None)
    if not target:
        raise HTTPException(status_code=404, detail="File not found")

    desired = bool(req.enabled)
    if _file_enrich_enabled(target) == desired:
        return {"job_id": None, "library": library_payload(data)}

    target["enrich_enabled"] = desired
    if desired:
        target.pop(AUTO_DEEP_ENRICHMENT_DISABLED_KEY, None)
    else:
        target[AUTO_DEEP_ENRICHMENT_DISABLED_KEY] = True
    target["sync_status"] = "pending"
    target.pop("sync_error", None)
    target.pop("synced_at", None)
    metadata = target.get("metadata") if isinstance(target.get("metadata"), dict) else {}
    target["metadata"] = {**metadata, "status": "pending"}
    target["metadata"].pop("error", None)

    prepare_signature = _prepare_signature(files)
    _set_pending_prepare_signature(data, prepare_signature)
    write_library(slug, data)

    job_id = await _ensure_prepare_job(slug)
    return {"job_id": job_id, "library": library_payload(data)}


@router.post("/libraries/{slug}/jobs/build")
async def build_library(slug: str):
    data = read_library(slug)
    payload = library_payload(data)
    if not payload["states"].get("has_files"):
        raise HTTPException(status_code=400, detail="Add files before building a library.")
    lock = LIB_LOCKS.setdefault(slug, asyncio.Lock())
    async with lock:
        if _has_active_job(slug):
            raise HTTPException(status_code=409, detail="This library already has an active job.")
        job_id = _start_job(
            slug,
            "build",
            root=stage_dir(slug),
            out=_collect_library_paths(slug)["corpus"],
            emit="per-file",
            lang_detect=False,
            stage_signature=payload.get("corpus_signature"),
        )
    return {"job_id": job_id}


@router.post("/libraries/{slug}/jobs/enrich")
async def enrich_library(slug: str):
    data = read_library(slug)
    payload = library_payload(data)
    if not payload["states"].get("has_corpus"):
        raise HTTPException(status_code=400, detail="Build the corpus before enrichment.")
    lock = LIB_LOCKS.setdefault(slug, asyncio.Lock())
    async with lock:
        if _has_active_job(slug):
            raise HTTPException(status_code=409, detail="This library already has an active job.")
        job_id = _start_job(slug, "enrich", stage_signature=payload.get("prepare_signature"))
    return {"job_id": job_id}


@router.post("/libraries/{slug}/jobs/embed")
async def embed_library(slug: str, req: EmbedLibraryRequest):
    data = read_library(slug)
    payload = library_payload(data)
    pipeline = _pipeline_meta(data)
    paths = _collect_library_paths(slug)
    if not payload["states"].get("has_corpus"):
        raise HTTPException(status_code=400, detail="Build the corpus before indexing.")
    embed_model = req.embed_model or _default_embed_model() or pipeline.get("embed_model") or DEFAULT_EMBED_MODEL
    lock = LIB_LOCKS.setdefault(slug, asyncio.Lock())
    async with lock:
        if _has_active_job(slug):
            raise HTTPException(status_code=409, detail="This library already has an active job.")
        job_id = _start_job(
            slug,
            "embed",
            raw=paths["corpus"],
            enhanced=None,
            shadow=paths["shadow"] if paths["shadow"].exists() else None,
            out_dir=paths["indexes"],
            embed_model=embed_model,
            ollama=_resolve_ollama_url(req.ollama),
            target_chars=req.target_chars,
            overlap_chars=req.overlap_chars,
            concurrency=req.concurrency,
            stage_signature=payload.get("prepare_signature"),
        )
    return {"job_id": job_id}


@router.post("/libraries/{slug}/jobs/prepare")
async def prepare_library(slug: str):
    data = read_library(slug)
    payload = library_payload(data)
    if not payload["states"].get("has_files"):
        raise HTTPException(status_code=400, detail="Add files before preparing Knowledge.")
    lock = LIB_LOCKS.setdefault(slug, asyncio.Lock())
    async with lock:
        if _has_active_job(slug):
            raise HTTPException(status_code=409, detail="This library already has an active job.")
        job_id = _start_job(slug, "prepare")
    return {"job_id": job_id}


@router.get("/jobs")
def list_jobs(slug: Optional[str] = None):
    jobs = [_job_public(job) for job in JOBS.values() if slug is None or job["slug"] == slug]
    jobs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return {"jobs": jobs}


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_public(job)


@router.post("/libraries/{slug}/context")
def library_context(slug: str, req: LibraryContextRequest):
    payload = library_payload(read_library(slug))
    pipeline = payload.get("pipeline") or {}
    paths = _collect_library_paths(slug)
    if not payload["states"].get("is_indexed"):
        raise HTTPException(status_code=400, detail="Prepare the library before using it in chat.")
    embed_model = req.embed_model or pipeline.get("embed_model") or _default_embed_model() or DEFAULT_EMBED_MODEL
    try:
        run_query = _load_pipeline_fn("unified_rag", "run_query")
        query_lock = QUERY_LOCKS.setdefault(slug, threading.Lock())
        with query_lock:
            result = run_query(
                shadow_index=paths["shadow_index"],
                shadow_store=paths["shadow_store"],
                content_index=paths["content_index"],
                content_store=paths["content_store"],
                query=req.prompt,
                answer=False,
                ollama=_resolve_ollama_url(req.ollama),
                embed_model=embed_model,
                gen_model=req.gen_model,
                no_rerank=True,
                k=max(1, req.top_k),
            )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Local retrieval failed: {type(exc).__name__}: {exc}") from exc

    context = _build_local_context(req.prompt, result, top_k=req.top_k)
    return {
        "context_block": context["context_block"],
        "sources": context["sources"],
        "result": result,
    }
