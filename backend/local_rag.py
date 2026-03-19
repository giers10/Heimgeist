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
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


router = APIRouter(tags=["local-rag"])

LIB_ROOT = Path(__file__).parent / "libraries"
LIB_ROOT.mkdir(parents=True, exist_ok=True)

JOB_EXECUTOR = ThreadPoolExecutor(max_workers=2)
JOBS: Dict[str, Dict[str, Any]] = {}
LIB_LOCKS: Dict[str, asyncio.Lock] = {}


class CreateLibraryRequest(BaseModel):
    name: str


class RenameLibraryRequest(BaseModel):
    name: str


class RegisterPathsRequest(BaseModel):
    paths: List[str]


class RemoveFileRequest(BaseModel):
    rel: str


class EmbedLibraryRequest(BaseModel):
    embed_model: str = "dengcao/Qwen3-Embedding-0.6B:F16"
    ollama: str = "http://localhost:11434"
    target_chars: int = 2000
    overlap_chars: int = 200
    concurrency: int = 6


class LibraryContextRequest(BaseModel):
    prompt: str
    top_k: int = 5
    ollama: str = "http://localhost:11434"
    embed_model: str = "dengcao/Qwen3-Embedding-0.6B:F16"
    gen_model: str = "qwen3:4b"


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


def stage_dir(slug: str) -> Path:
    path = lib_dir(slug) / "stage"
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
    return _read_json(path)


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
        digest.update(json.dumps(payload, sort_keys=True).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _collect_library_paths(slug: str) -> Dict[str, Path]:
    base = lib_dir(slug)
    return {
        "base": base,
        "stage": stage_dir(slug),
        "corpus": base / "corpus.jsonl",
        "enhanced": base / "corpus.enhanced.jsonl",
        "shadow": base / "corpus.shadow.jsonl",
        "indexes": indexes_dir(slug),
        "shadow_index": indexes_dir(slug) / "shadow.index.faiss",
        "shadow_store": indexes_dir(slug) / "shadow.meta.jsonl",
        "content_index": indexes_dir(slug) / "content.index.faiss",
        "content_store": indexes_dir(slug) / "content.meta.jsonl",
    }


def _cleanup_generated_artifacts(slug: str) -> None:
    paths = _collect_library_paths(slug)
    for key in (
        "corpus",
        "enhanced",
        "shadow",
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
    has_corpus = bool(source_signature) and pipeline.get("corpus_signature") == source_signature and paths["corpus"].exists()
    is_enriched = (
        bool(source_signature)
        and pipeline.get("enriched_signature") == source_signature
        and paths["enhanced"].exists()
        and paths["shadow"].exists()
    )
    is_indexed = (
        bool(source_signature)
        and pipeline.get("indexed_signature") == source_signature
        and paths["shadow_index"].exists()
        and paths["shadow_store"].exists()
        and paths["content_index"].exists()
        and paths["content_store"].exists()
    )
    stages = {
        "has_files": len(files) > 0,
        "has_corpus": has_corpus,
        "is_enriched": is_enriched,
        "is_indexed": is_indexed,
        "is_ready_for_chat": is_indexed,
        "needs_prepare": bool(files) and not is_indexed,
    }
    artifacts = {
        "corpus_records": _line_count(paths["corpus"]) if has_corpus else 0,
        "enhanced_records": _line_count(paths["enhanced"]) if is_enriched else 0,
        "shadow_records": _line_count(paths["shadow"]) if is_enriched else 0,
    }
    return {
        **data,
        "files": files,
        "pipeline": pipeline,
        "source_signature": source_signature,
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
        if payload["states"].get("is_indexed") and payload.get("source_signature") == pending_signature:
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

    if pending_signature and payload["states"].get("is_indexed") and payload.get("source_signature") == pending_signature:
        _clear_pending_prepare(slug)
        return

    if status != "succeeded":
        return

    if pending_signature and not payload["states"].get("is_indexed"):
        await _ensure_prepare_job(slug)


def _scaled_progress(on_progress, start: float, end: float, prefix: str):
    def wrapped(phase: str, pct: float, detail: str):
        clamped = min(1.0, max(0.0, float(pct)))
        span = max(0.0, end - start)
        message = f"{prefix}: {detail}" if detail else prefix
        on_progress(phase, start + (span * clamped), message)

    return wrapped


def _run_prepare_pipeline(slug: str, on_progress=None, **opts):
    data = read_library(slug)
    payload = library_payload(data)
    files = list(data.get("files", []))
    source_signature = payload.get("source_signature")
    if not files or not source_signature:
        raise RuntimeError("Add files before preparing this database.")

    paths = _collect_library_paths(slug)
    states = dict(payload.get("states") or {})
    results: Dict[str, Any] = {}

    build_runner = _load_pipeline_fn("corpus_builder", "run_build")
    enrich_runner = _load_pipeline_fn("corpus_enricher", "run_enrich")
    index_runner = _load_pipeline_fn("index_builder", "run_index")

    if on_progress:
        on_progress("prepare", 0.01, "Preparing database for chat...")

    if not states.get("has_corpus"):
        build_progress = _scaled_progress(on_progress, 0.02, 0.34, "Reading files") if on_progress else None
        results["build"] = build_runner(
            root=stage_dir(slug),
            out=paths["corpus"],
            on_progress=build_progress,
        )
        _mark_pipeline_stage(slug, "build", source_signature)
        states["has_corpus"] = True
        states["is_enriched"] = False
        states["is_indexed"] = False

    if not states.get("is_enriched"):
        enrich_progress = _scaled_progress(on_progress, 0.34, 0.69, "Enriching content") if on_progress else None
        results["enrich"] = enrich_runner(
            inp=paths["corpus"],
            out=paths["enhanced"],
            shadow_out=paths["shadow"],
            on_progress=enrich_progress,
        )
        _mark_pipeline_stage(slug, "enrich", source_signature)
        states["is_enriched"] = True
        states["is_indexed"] = False

    if not states.get("is_indexed"):
        index_progress = _scaled_progress(on_progress, 0.69, 1.0, "Building search indexes") if on_progress else None
        results["embed"] = index_runner(
            raw=paths["corpus"],
            enhanced=paths["enhanced"] if states.get("is_enriched") and paths["enhanced"].exists() else None,
            shadow=paths["shadow"] if states.get("is_enriched") and paths["shadow"].exists() else None,
            out_dir=paths["indexes"],
            on_progress=index_progress,
            embed_model=opts.get("embed_model", "dengcao/Qwen3-Embedding-0.6B:F16"),
            ollama=opts.get("ollama", "http://localhost:11434"),
            target_chars=opts.get("target_chars", 2000),
            overlap_chars=opts.get("overlap_chars", 200),
            concurrency=opts.get("concurrency", 6),
        )
        _mark_pipeline_stage(slug, "embed", source_signature)

    if on_progress:
        on_progress("done", 1.0, "Database is ready for chat.")

    return {
        "status": "ok",
        "results": results,
        "source_signature": source_signature,
    }


async def _run_job(job_id: str, fn_name: str, **kwargs):
    loop = asyncio.get_running_loop()
    job = JOBS[job_id]
    source_signature = kwargs.pop("source_signature", None)

    def on_progress(phase: str, pct: float, detail: str):
        job["phase"] = phase
        job["progress"] = round(float(pct) * 100.0, 1)
        job["detail"] = detail

    job["status"] = "running"
    try:
        if fn_name == "build":
            runner = _load_pipeline_fn("corpus_builder", "run_build")
        elif fn_name == "enrich":
            runner = _load_pipeline_fn("corpus_enricher", "run_enrich")
        elif fn_name == "embed":
            runner = _load_pipeline_fn("index_builder", "run_index")
        elif fn_name == "prepare":
            runner = functools.partial(_run_prepare_pipeline, job["slug"])
        else:
            raise RuntimeError(f"Unknown job type: {fn_name}")

        call = functools.partial(runner, on_progress=on_progress, **kwargs)
        result = await loop.run_in_executor(JOB_EXECUTOR, call)
        if fn_name in {"build", "enrich", "embed"} and source_signature:
            _mark_pipeline_stage(job["slug"], fn_name, source_signature)
        job["status"] = "succeeded"
        job["progress"] = 100.0
        job["phase"] = "done"
        job["detail"] = "Completed."
        job["result"] = result
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = f"{type(exc).__name__}: {exc}"
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
    for idx, source in enumerate(selected, start=1):
        title = (source.get("title") or Path(source.get("url") or source.get("doc_id") or f"Source {idx}").name).strip()
        snippet = re.sub(r"\s+", " ", (source.get("snippet") or "")).strip()
        if len(snippet) > 1400:
            snippet = snippet[:1400].rstrip() + "..."
        raw_path = source.get("url") or source.get("doc_id") or ""
        if raw_path and os.path.isabs(raw_path):
            file_sources.append(_file_uri(raw_path))
        blocks.append(f"[L{idx}] {title}\n{snippet}")
    blocks.append("</local_rag_context>")
    blocks.append(
        "Use the local knowledge base context when it is relevant. "
        "If it does not answer the question, say so clearly instead of inventing details."
    )
    return {"context_block": "\n".join(blocks), "sources": file_sources}


@router.get("/libraries")
def list_libraries():
    libraries: List[Dict[str, Any]] = []
    for path in LIB_ROOT.iterdir():
        if not path.is_dir():
            continue
        meta = path / "library.json"
        if not meta.exists():
            continue
        try:
            libraries.append(library_payload(_read_json(meta)))
        except Exception:
            continue
    libraries.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return {"libraries": libraries}


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
            "sha256": sha,
            "path": str(file_path),
            "rel": stage_name,
            "name": file_path.name,
            "size": file_path.stat().st_size,
            "added_at": now_iso(),
        }
        data.setdefault("files", []).append(entry)
        added.append(entry)
        existing[sha] = entry

    job_id = None
    if added:
        _set_pending_prepare_signature(data, _source_signature(data.get("files", [])))
    write_library(slug, data)
    if added:
        job_id = await _ensure_prepare_job(slug)
    return {
        "added": added,
        "skipped": skipped,
        "job_id": job_id,
        "library": library_payload(data),
    }


@router.delete("/libraries/{slug}/files")
async def remove_file(slug: str, req: RemoveFileRequest):
    data = read_library(slug)
    files = list(data.get("files", []))
    removed = next((entry for entry in files if entry.get("rel") == req.rel), None)
    if not removed:
        raise HTTPException(status_code=404, detail="File not found")

    data["files"] = [entry for entry in files if entry.get("rel") != req.rel]
    symlink_path = stage_dir(slug) / req.rel
    if symlink_path.exists():
        symlink_path.unlink()

    source_signature = _source_signature(data.get("files", []))
    _set_pending_prepare_signature(data, source_signature)
    write_library(slug, data)

    job_id = None
    if source_signature:
        job_id = await _ensure_prepare_job(slug)
    else:
        _cleanup_generated_artifacts(slug)

    return {"ok": True, "job_id": job_id, "library": library_payload(data)}


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
            source_signature=payload.get("source_signature"),
        )
    return {"job_id": job_id}


@router.post("/libraries/{slug}/jobs/enrich")
async def enrich_library(slug: str):
    data = read_library(slug)
    payload = library_payload(data)
    paths = _collect_library_paths(slug)
    if not payload["states"].get("has_corpus"):
        raise HTTPException(status_code=400, detail="Build the corpus before enrichment.")
    lock = LIB_LOCKS.setdefault(slug, asyncio.Lock())
    async with lock:
        if _has_active_job(slug):
            raise HTTPException(status_code=409, detail="This library already has an active job.")
        job_id = _start_job(
            slug,
            "enrich",
            inp=paths["corpus"],
            out=paths["enhanced"],
            shadow_out=paths["shadow"],
            source_signature=payload.get("source_signature"),
        )
    return {"job_id": job_id}


@router.post("/libraries/{slug}/jobs/embed")
async def embed_library(slug: str, req: EmbedLibraryRequest):
    data = read_library(slug)
    payload = library_payload(data)
    paths = _collect_library_paths(slug)
    if not payload["states"].get("has_corpus"):
        raise HTTPException(status_code=400, detail="Build the corpus before indexing.")
    lock = LIB_LOCKS.setdefault(slug, asyncio.Lock())
    async with lock:
        if _has_active_job(slug):
            raise HTTPException(status_code=409, detail="This library already has an active job.")
        job_id = _start_job(
            slug,
            "embed",
            raw=paths["corpus"],
            enhanced=paths["enhanced"] if payload["states"].get("is_enriched") and paths["enhanced"].exists() else None,
            shadow=paths["shadow"] if payload["states"].get("is_enriched") and paths["shadow"].exists() else None,
            out_dir=paths["indexes"],
            embed_model=req.embed_model,
            ollama=req.ollama,
            target_chars=req.target_chars,
            overlap_chars=req.overlap_chars,
            concurrency=req.concurrency,
            source_signature=payload.get("source_signature"),
        )
    return {"job_id": job_id}


@router.post("/libraries/{slug}/jobs/prepare")
async def prepare_library(slug: str):
    data = read_library(slug)
    payload = library_payload(data)
    if not payload["states"].get("has_files"):
        raise HTTPException(status_code=400, detail="Add files before preparing this database.")
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
    paths = _collect_library_paths(slug)
    if not payload["states"].get("is_indexed"):
        raise HTTPException(status_code=400, detail="Prepare the library before using it in chat.")
    try:
        run_query = _load_pipeline_fn("unified_rag", "run_query")
        result = run_query(
            shadow_index=paths["shadow_index"],
            shadow_store=paths["shadow_store"],
            content_index=paths["content_index"],
            content_store=paths["content_store"],
            query=req.prompt,
            answer=False,
            ollama=req.ollama,
            embed_model=req.embed_model,
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
