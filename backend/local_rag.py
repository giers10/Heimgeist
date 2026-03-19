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


def library_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    paths = _collect_library_paths(data["slug"])
    files = list(data.get("files", []))
    stages = {
        "has_files": len(files) > 0,
        "has_corpus": paths["corpus"].exists(),
        "is_enriched": paths["enhanced"].exists() and paths["shadow"].exists(),
        "is_indexed": paths["shadow_index"].exists() and paths["content_index"].exists(),
    }
    artifacts = {
        "corpus_records": _line_count(paths["corpus"]),
        "enhanced_records": _line_count(paths["enhanced"]),
        "shadow_records": _line_count(paths["shadow"]),
    }
    return {
        **data,
        "files": files,
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


async def _run_job(job_id: str, fn_name: str, **kwargs):
    loop = asyncio.get_running_loop()
    job = JOBS[job_id]

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
        else:
            raise RuntimeError(f"Unknown job type: {fn_name}")

        call = functools.partial(runner, on_progress=on_progress, **kwargs)
        result = await loop.run_in_executor(JOB_EXECUTOR, call)
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
def register_paths(slug: str, req: RegisterPathsRequest):
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

    write_library(slug, data)
    return {
        "added": added,
        "skipped": skipped,
        "library": library_payload(data),
    }


@router.delete("/libraries/{slug}/files")
def remove_file(slug: str, req: RemoveFileRequest):
    data = read_library(slug)
    before = len(data.get("files", []))
    data["files"] = [entry for entry in data.get("files", []) if entry.get("rel") != req.rel]
    symlink_path = stage_dir(slug) / req.rel
    if symlink_path.exists():
        symlink_path.unlink()
    write_library(slug, data)
    if len(data["files"]) == before:
        raise HTTPException(status_code=404, detail="File not found")
    return {"ok": True, "library": library_payload(data)}


@router.post("/libraries/{slug}/jobs/build")
async def build_library(slug: str):
    data = read_library(slug)
    if not data.get("files"):
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
        )
    return {"job_id": job_id}


@router.post("/libraries/{slug}/jobs/enrich")
async def enrich_library(slug: str):
    paths = _collect_library_paths(slug)
    if not paths["corpus"].exists():
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
        )
    return {"job_id": job_id}


@router.post("/libraries/{slug}/jobs/embed")
async def embed_library(slug: str, req: EmbedLibraryRequest):
    paths = _collect_library_paths(slug)
    if not paths["corpus"].exists():
        raise HTTPException(status_code=400, detail="Build the corpus before indexing.")
    lock = LIB_LOCKS.setdefault(slug, asyncio.Lock())
    async with lock:
        if _has_active_job(slug):
            raise HTTPException(status_code=409, detail="This library already has an active job.")
        job_id = _start_job(
            slug,
            "embed",
            raw=paths["corpus"],
            enhanced=paths["enhanced"] if paths["enhanced"].exists() else None,
            shadow=paths["shadow"] if paths["shadow"].exists() else None,
            out_dir=paths["indexes"],
            embed_model=req.embed_model,
            ollama=req.ollama,
            target_chars=req.target_chars,
            overlap_chars=req.overlap_chars,
            concurrency=req.concurrency,
        )
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
    paths = _collect_library_paths(slug)
    if not paths["shadow_index"].exists() or not paths["content_index"].exists():
        raise HTTPException(status_code=400, detail="Index the library before using it in chat.")
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
