#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
03_index_builder.py

Flexible FAISS index builder for hybrid RAG.

Supports these inputs (any subset):
- --raw       : corpus.jsonl from 01_corpus_builder.py (no enrichment)
- --enhanced  : corpus.enhanced.jsonl from 02_corpus_enricher.py
- --shadow    : corpus.shadow.jsonl   from 02_corpus_enricher.py

Outputs (by default into ./indexes):
- shadow.index.faiss  : FAISS IP index over vectors of "shadow_text"
- shadow.meta.jsonl   : metadata for each FAISS id (id, doc_id, record_id, title, url, record_type, mime, lang, kind, shadow_text)
- content.index.faiss : FAISS IP index over vectors of chunked "text"
- content.meta.jsonl  : metadata for each FAISS id (id, doc_id, record_id, chunk_no, title, url, text, record_type, mime, lang)

Behavior
- If you provide --shadow → build shadow from it.
- Else if you provide --enhanced → synthesize shadow from enriched fields (headline+summary+keywords+entities+qa).
- Else if you provide --raw → synthesize shadow from raw (title + first sentences + hints).
- If you provide --enhanced → build content from it.
- Else if you provide --raw → build content from raw text (chunking).
- You can disable either side with --no-shadow or --no-content.

Embedding
- Uses Ollama /api/embeddings with cosine similarity (L2-normalize then IP).

Examples:

# Full hybrid from enriched+shadow
python 03_index_builder.py \
  --enhanced corpus.enhanced.jsonl \
  --shadow corpus.shadow.jsonl \
  --out-dir indexes \
  --embed-model "dengcao/Qwen3-Embedding-0.6B:F16" \
  --target-chars 2500 --overlap-chars 200 \
  --concurrency 6

# Raw-only (no enricher) → builds content from raw text and a proxy shadow
python 03_index_builder.py \
  --raw corpus.jsonl \
  --out-dir indexes \
  --embed-model "dengcao/Qwen3-Embedding-0.6B:F16"

"""
from __future__ import annotations

import argparse, json, sys, uuid, os, re, math
from pathlib import Path
from typing import Dict, Any, Iterable, List, Tuple, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import numpy as np
import requests
import faiss

try:
    from backend.rag.ollama_embeddings import resolve_embed_model, request_embedding
except ModuleNotFoundError:
    from .ollama_embeddings import resolve_embed_model, request_embedding

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

# -----------------------------
# IO
# -----------------------------
def read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    yield json.loads(line)
                except Exception:
                    continue

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

# -----------------------------
# Text helpers
# -----------------------------
def pick_text(rec: Dict[str, Any]) -> str:
    return rec.get("text") or rec.get("content") or rec.get("body") or ""

def first_sentences(s: str, max_chars: int = 500) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    # cheap sentence-ish split
    parts = re.split(r"(?<=[\.\!\?])\s+", s)
    out = []
    total = 0
    for p in parts:
        if not p:
            continue
        out.append(p)
        total += len(p) + 1
        if total >= max_chars:
            break
    joined = " ".join(out).strip()
    return joined[:max_chars].rstrip()

def chunk_text(txt: str, target_chars: int = 2500, overlap_chars: int = 200) -> Iterable[str]:
    # paragraph-first greedy pack
    paras = [p.strip() for p in (txt or "").split("\n\n") if p.strip()]
    if not paras:
        if txt.strip():
            yield txt.strip()
        return
    buf, size = [], 0
    for p in paras:
        if size + len(p) + 2 > target_chars and buf:
            chunk = "\n\n".join(buf)
            yield chunk
            if overlap_chars > 0 and len(chunk) > overlap_chars:
                tail = chunk[-overlap_chars:]
                buf, size = [tail], len(tail)
            else:
                buf, size = [], 0
        buf.append(p)
        size += len(p) + 2
    if buf:
        yield "\n\n".join(buf)

def clamp_int(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))

def sample_evenly_spaced_chunks(chunks: List[str], target_count: int) -> List[str]:
    if target_count <= 0 or len(chunks) <= target_count:
        return chunks
    if target_count == 1:
        return [chunks[len(chunks) // 2]]

    last = len(chunks) - 1
    step = last / float(target_count - 1)
    picked: List[str] = []
    seen = set()
    for idx in range(target_count):
        source_idx = int(round(idx * step))
        source_idx = clamp_int(source_idx, 0, last)
        while source_idx in seen and source_idx < last:
            source_idx += 1
        if source_idx in seen:
            source_idx = max(i for i in range(last + 1) if i not in seen)
        seen.add(source_idx)
        picked.append(chunks[source_idx])
    return picked

def plan_content_chunking(txt: str, target_chars: int, overlap_chars: int) -> Dict[str, int | bool | str]:
    clean = (txt or "").strip()
    text_len = len(clean)
    base_target = max(400, int(target_chars))
    base_overlap = max(0, int(overlap_chars))
    approx_raw_chunks = max(1, math.ceil(text_len / max(1, base_target))) if text_len else 1

    adaptive = approx_raw_chunks > 24
    chunk_budget = approx_raw_chunks
    planned_target = base_target
    sampling_fallback = False

    if adaptive:
        chunk_budget = min(
            approx_raw_chunks,
            max(24, math.ceil(3.5 * math.sqrt(approx_raw_chunks))),
        )
        planned_target = math.ceil(text_len / max(1, chunk_budget)) if text_len else base_target

    max_target = max(base_target, base_target * 8)
    if planned_target > max_target:
        planned_target = max_target
        sampling_fallback = adaptive

    planned_target = clamp_int(planned_target, base_target, max_target)
    planned_overlap = min(base_overlap, max(0, planned_target // 12))

    return {
        "doc_chars": text_len,
        "baseline_chunks": approx_raw_chunks,
        "chunk_budget": chunk_budget,
        "target_chars": planned_target,
        "overlap_chars": planned_overlap,
        "adaptive": adaptive,
        "sampling_fallback": sampling_fallback,
        "mode": "adaptive" if adaptive else "fixed",
    }

def chunk_text_for_index(txt: str, target_chars: int, overlap_chars: int) -> Tuple[List[str], Dict[str, int | bool | str]]:
    clean = (txt or "").strip()
    if not clean:
        return [], {
            "doc_chars": 0,
            "baseline_chunks": 0,
            "chunk_budget": 0,
            "target_chars": max(400, int(target_chars)),
            "overlap_chars": max(0, int(overlap_chars)),
            "adaptive": False,
            "sampling_fallback": False,
            "mode": "fixed",
            "chunks_indexed": 0,
            "chunks_saved": 0,
        }

    plan = plan_content_chunking(clean, target_chars, overlap_chars)
    chunks = list(
        chunk_text(
            clean,
            int(plan["target_chars"]),
            int(plan["overlap_chars"]),
        )
    )

    if plan["adaptive"] and len(chunks) > int(plan["chunk_budget"]):
        chunks = sample_evenly_spaced_chunks(chunks, int(plan["chunk_budget"]))
        plan["mode"] = "adaptive-sampled"

    plan["chunks_indexed"] = len(chunks)
    plan["chunks_saved"] = max(0, int(plan["baseline_chunks"]) - len(chunks))
    return chunks, plan

def norm_f32(mat: np.ndarray) -> np.ndarray:
    mat = np.asarray(mat, dtype="float32")
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms

# -----------------------------
# Embedding
# -----------------------------
def embed_many(ollama_url: str, model: str, texts: List[str], *, concurrency: int = 4, timeout: int = 120, on_progress=None) -> List[np.ndarray]:
    if not texts:
        return []

    resolved_model, first_vec = resolve_embed_model(
        ollama_url,
        model,
        probe_text=texts[0],
        timeout=timeout,
    )

    def _embed_one(t: str) -> np.ndarray:
        vec = request_embedding(ollama_url, resolved_model, t, timeout=timeout)
        return np.array(vec, dtype="float32")

    out: List[Optional[np.ndarray]] = [None] * len(texts)
    out[0] = np.array(first_vec, dtype="float32")
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        futures = {ex.submit(_embed_one, t): i for i, t in enumerate(texts[1:], start=1)}
        
        progress_bar = None
        if on_progress is None and 'tqdm' in globals() and tqdm is not None:
            progress_bar = tqdm(as_completed(futures), total=len(futures), desc=f"embed:{resolved_model}")
        
        iterator = progress_bar if progress_bar else as_completed(futures)
        
        count = 1
        for fut in iterator:
            i = futures[fut]
            out[i] = fut.result()
            count += 1
            if on_progress:
                on_progress("embed", count / len(texts), f"Embedding {count}/{len(texts)}")

    # type: ignore
    return out  # List[np.ndarray]

# -----------------------------
# Meta helpers
# -----------------------------
def derive_doc_id_from_any(any_id: Optional[str], parent_id: Optional[str]) -> str:
    """Prefer parent_id if present (file-level), else base of 'id' before '#...'."""
    if parent_id:
        return str(parent_id)
    if not any_id:
        return ""
    return any_id.split("#", 1)[0]

def kind_from_rec(rec: Dict[str, Any]) -> str:
    rt = (rec.get("record_type") or "").lower()
    mime = (rec.get("mime") or "").lower()
    if rt == "image" or (mime.startswith("image/")):
        return "image"
    if rt == "av" or mime.startswith(("audio/", "video/")):
        return "av"
    if "html" in mime or rt in {"html-section"}:
        return "html"
    if "pdf" in mime or rt == "page":
        return "pdf"
    if rt == "code-summary" or mime.startswith("text/x-code"):
        return "code"
    return rt or "file"

# -----------------------------
# Shadow text synthesis (fallbacks)
# -----------------------------
def synth_shadow_from_enhanced(rec: Dict[str, Any]) -> str:
    """
    Build a compact shadow_text from enriched fields if present.
    """
    parts: List[str] = []
    h = (rec.get("headline") or rec.get("title") or "").strip()
    s = (rec.get("summary") or "").strip()
    kws = rec.get("keywords") or []
    ents = rec.get("entities") or []
    qas = rec.get("qa") or []

    if h:
        parts.append(f"headline: {h}")
    if s:
        parts.append(f"summary: {s}")
    if kws:
        parts.append("keywords: " + ", ".join([str(k).strip() for k in kws if str(k).strip()]))
    if ents:
        uniq = {}
        for e in ents:
            if not isinstance(e, dict):
                continue
            name = (e.get("name") or "").strip()
            typ = (e.get("type") or "OTHER").strip().upper()
            if name and name.lower() not in uniq:
                uniq[name.lower()] = (name, typ)
        if uniq:
            parts.append("entities: " + "; ".join(f"{n} [{t}]" for n, t in uniq.values()))
    if qas:
        qa_lines = []
        for qa in qas[:4]:
            if not isinstance(qa, dict):
                continue
            q = (qa.get("q") or "").strip()
            a = (qa.get("a") or "").strip()
            if q and a:
                qa_lines.append(f"Q: {q}\nA: {a}")
        if qa_lines:
            parts.append("qa:\n" + "\n".join(qa_lines))
    return "\n".join(parts).strip()

def synth_shadow_from_raw(rec: Dict[str, Any]) -> str:
    """
    Build a proxy shadow_text without any LLM: title + first sentences + light hints.
    """
    title = (rec.get("title") or "").strip()
    text = pick_text(rec)
    kind = kind_from_rec(rec)
    url = rec.get("url") or rec.get("source_path") or ""
    head = f"headline: {title}" if title else ""
    summary = first_sentences(text, 500)
    parts = []
    if head:
        parts.append(head)
    if summary:
        parts.append(f"summary: {summary}")
    hints = []
    if kind:
        hints.append(kind)
    if rec.get("mime"):
        hints.append(rec.get("mime").split(";")[0])
    if url:
        hints.append(Path(url).name)
    if hints:
        parts.append("keywords: " + ", ".join(hints))
    return "\n".join(parts).strip()

# -----------------------------
# Builders
# -----------------------------
def build_shadow_any(
    shadow_jsonl: Optional[Path],
    enhanced_jsonl: Optional[Path],
    raw_jsonl: Optional[Path],
    out_index: Path,
    out_meta: Path,
    *,
    ollama: str,
    model: str,
    concurrency: int
) -> Tuple[int, int, int]:
    """
    Build FAISS over shadow_text from best available source.
    Priority: shadow_jsonl > enhanced_jsonl (synth) > raw_jsonl (synth).
    Returns (n_input_records, n_indexed, dim)
    """
    src_records: List[Dict[str, Any]] = []
    mode = ""
    if shadow_jsonl and shadow_jsonl.exists():
        src_records = list(read_jsonl(shadow_jsonl))
        mode = "shadow"
    elif enhanced_jsonl and enhanced_jsonl.exists():
        src_records = list(read_jsonl(enhanced_jsonl))
        mode = "enhanced->shadow"
    elif raw_jsonl and raw_jsonl.exists():
        src_records = list(read_jsonl(raw_jsonl))
        mode = "raw->shadow"
    else:
        raise SystemExit("[ERR] No input for shadow index (need --shadow OR --enhanced OR --raw).")

    if not src_records:
        raise SystemExit("[ERR] Empty input for shadow index.")

    texts: List[str] = []
    metas: List[Dict[str, Any]] = []
    for rec in src_records:
        if mode == "shadow":
            st = rec.get("shadow_text") or ""
        elif mode == "enhanced->shadow":
            st = synth_shadow_from_enhanced(rec)
        else:
            st = synth_shadow_from_raw(rec)

        if not st.strip():
            continue

        record_id = rec.get("id") or rec.get("record_id") or str(uuid.uuid4())
        doc_id = derive_doc_id_from_any(record_id, rec.get("parent_id"))

        meta = {
            "id": None,  # numeric FAISS id later
            "record_id": record_id,
            "doc_id": doc_id,
            "title": rec.get("title"),
            "url": rec.get("url") or rec.get("source_path"),
            "record_type": rec.get("record_type"),
            "mime": rec.get("mime"),
            "lang": rec.get("lang"),
            "kind": kind_from_rec(rec),
            "shadow_text": st,
        }
        metas.append(meta)
        texts.append(st)

    if not texts:
        raise SystemExit("[ERR] no shadow_text to embed")

    vecs = embed_many(ollama, model, texts, concurrency=concurrency)
    d = len(vecs[0])
    mat = norm_f32(np.vstack(vecs))

    base = faiss.IndexFlatIP(d)
    index = faiss.IndexIDMap2(base)

    out_meta.parent.mkdir(parents=True, exist_ok=True)
    with open(out_meta, "w", encoding="utf-8") as mf:
        buf_vecs, buf_ids = [], []
        next_id = 0
        for m, v in zip(metas, mat):
            m["id"] = next_id
            mf.write(json.dumps(m, ensure_ascii=False) + "\n")
            buf_vecs.append(v)
            buf_ids.append(next_id)
            next_id += 1
            if len(buf_vecs) >= 512:
                index.add_with_ids(np.vstack(buf_vecs), np.array(buf_ids, dtype="int64"))
                buf_vecs, buf_ids = [], []
        if buf_vecs:
            index.add_with_ids(np.vstack(buf_vecs), np.array(buf_ids, dtype="int64"))

    faiss.write_index(index, str(out_index))
    return (len(src_records), index.ntotal, d)

def build_content_any(
    enhanced_jsonl: Optional[Path],
    raw_jsonl: Optional[Path],
    out_index: Path,
    out_meta: Path,
    *,
    ollama: str,
    model: str,
    target_chars: int,
    overlap_chars: int,
    concurrency: int
) -> Tuple[int, int, int, Dict[str, Any]]:
    """
    Build FAISS over chunked 'text' from best available source.
    Priority: enhanced_jsonl > raw_jsonl.
    Returns (n_input_records, n_chunks, dim)
    """
    src_records: List[Dict[str, Any]] = []
    mode = ""
    if enhanced_jsonl and enhanced_jsonl.exists():
        src_records = list(read_jsonl(enhanced_jsonl))
        mode = "enhanced"
    elif raw_jsonl and raw_jsonl.exists():
        src_records = list(read_jsonl(raw_jsonl))
        mode = "raw"
    else:
        raise SystemExit("[ERR] No input for content index (need --enhanced OR --raw).")

    metas: List[Dict[str, Any]] = []
    texts: List[str] = []
    stats: Dict[str, Any] = {
        "docs_with_text": 0,
        "adaptive_docs": 0,
        "sampled_docs": 0,
        "baseline_chunks": 0,
        "chunks_indexed": 0,
        "chunks_saved": 0,
        "max_doc_chunks": 0,
        "max_doc_chars": 0,
    }
    for rec in src_records:
        base_text = pick_text(rec)
        if not base_text.strip():
            continue
        record_id = rec.get("id") or rec.get("record_id") or str(uuid.uuid4())
        doc_id = derive_doc_id_from_any(record_id, rec.get("parent_id"))
        title = rec.get("title")
        url = rec.get("url") or rec.get("source_path")

        chunks, chunk_plan = chunk_text_for_index(base_text, target_chars, overlap_chars)
        if not chunks:
            continue
        stats["docs_with_text"] += 1
        stats["baseline_chunks"] += int(chunk_plan["baseline_chunks"])
        stats["chunks_indexed"] += len(chunks)
        stats["chunks_saved"] += int(chunk_plan["chunks_saved"])
        stats["max_doc_chunks"] = max(stats["max_doc_chunks"], len(chunks))
        stats["max_doc_chars"] = max(stats["max_doc_chars"], int(chunk_plan["doc_chars"]))
        if chunk_plan["adaptive"]:
            stats["adaptive_docs"] += 1
        if chunk_plan["mode"] == "adaptive-sampled":
            stats["sampled_docs"] += 1
        for ci, chunk in enumerate(chunks):
            meta = {
                "id": None,  # numeric FAISS id later
                "doc_id": doc_id,
                "record_id": record_id,
                "chunk_no": ci,
                "doc_chunk_count": len(chunks),
                "doc_chars": int(chunk_plan["doc_chars"]),
                "chunk_target_chars": int(chunk_plan["target_chars"]),
                "chunk_overlap_chars": int(chunk_plan["overlap_chars"]),
                "chunking_mode": chunk_plan["mode"],
                "title": title,
                "url": url,
                "text": chunk,
                "record_type": rec.get("record_type"),
                "mime": rec.get("mime"),
                "lang": rec.get("lang"),
            }
            metas.append(meta)
            texts.append(chunk)

    if not texts:
        raise SystemExit("[ERR] no content chunks to embed")

    vecs = embed_many(ollama, model, texts, concurrency=concurrency)
    d = len(vecs[0])
    mat = norm_f32(np.vstack(vecs))

    base = faiss.IndexFlatIP(d)
    index = faiss.IndexIDMap2(base)

    out_meta.parent.mkdir(parents=True, exist_ok=True)
    with open(out_meta, "w", encoding="utf-8") as mf:
        buf_vecs, buf_ids = [], []
        next_id = 0
        for m, v in zip(metas, mat):
            m["id"] = next_id
            mf.write(json.dumps(m, ensure_ascii=False) + "\n")
            buf_vecs.append(v)
            buf_ids.append(next_id)
            next_id += 1
            if len(buf_vecs) >= 512:
                index.add_with_ids(np.vstack(buf_vecs), np.array(buf_ids, dtype="int64"))
                buf_vecs, buf_ids = [], []
        if buf_vecs:
            index.add_with_ids(np.vstack(buf_vecs), np.array(buf_ids, dtype="int64"))

    faiss.write_index(index, str(out_index))
    return (len(src_records), index.ntotal, d, stats)

# -----------------------------
# CLI
# -----------------------------
def run_index(raw: Path|None, enhanced: Path|None, shadow: Path|None, out_dir: Path, *,
              on_progress=None, **opts) -> dict:
    
    args = argparse.Namespace(
        raw=raw,
        enhanced=enhanced,
        shadow=shadow,
        out_dir=out_dir,
        embed_model=opts.get("embed_model", "bge-m3:latest"),
        ollama=opts.get("ollama", "http://localhost:11434"),
        target_chars=opts.get("target_chars", 2500),
        overlap_chars=opts.get("overlap_chars", 200),
        concurrency=opts.get("concurrency", 6),
        no_shadow=opts.get("no_shadow", False),
        no_content=opts.get("no_content", False),
    )

    ensure_dir(out_dir)
    resolved_model, _ = resolve_embed_model(args.ollama, args.embed_model)

    shadow_index_path = out_dir / "shadow.index.faiss"
    shadow_meta_path  = out_dir / "shadow.meta.jsonl"
    content_index_path = out_dir / "content.index.faiss"
    content_meta_path  = out_dir / "content.meta.jsonl"

    results = {}
    built_any = False

    if not args.no_shadow:
        if on_progress: on_progress("shadow", 0.1, "Building shadow index...")
        s_tot, s_ix, s_dim = build_shadow_any(
            args.shadow, args.enhanced, args.raw,
            shadow_index_path, shadow_meta_path,
            ollama=args.ollama, model=resolved_model, concurrency=args.concurrency
        )
        results["shadow"] = {"records": s_tot, "indexed": s_ix, "dim": s_dim}
        if on_progress: on_progress("shadow", 0.5, "Shadow index complete.")
        built_any = True

    if not args.no_content:
        if on_progress: on_progress("content", 0.6, "Building content index...")
        c_tot, c_ix, c_dim, c_stats = build_content_any(
            args.enhanced, args.raw,
            content_index_path, content_meta_path,
            ollama=args.ollama, model=resolved_model,
            target_chars=args.target_chars, overlap_chars=args.overlap_chars,
            concurrency=args.concurrency
        )
        results["content"] = {"records": c_tot, "chunks": c_ix, "dim": c_dim, **c_stats}
        if on_progress: on_progress("content", 0.9, "Content index complete.")
        built_any = True

    if not built_any:
        return {"status": "warning", "message": "Nothing built."}

    if on_progress: on_progress("done", 1.0, "Indexing complete.")
    return {"status": "ok", "results": results, "embed_model": resolved_model}

def main():
    ap = argparse.ArgumentParser(description="Build FAISS indexes (shadow + content) for hybrid RAG with or without enrichment.")
    ap.add_argument("--raw", help="Raw corpus JSONL (from 01_corpus_builder.py)")
    ap.add_argument("--enhanced", help="Enhanced corpus JSONL (from 02_corpus_enricher.py)")
    ap.add_argument("--shadow", help="Shadow corpus JSONL (from 02_corpus_enricher.py)")
    ap.add_argument("--out-dir", default="indexes", help="Output directory for indexes + metadata")
    ap.add_argument("--embed-model", default="bge-m3:latest", help="Ollama embedding model")
    ap.add_argument("--ollama", default="http://localhost:11434", help="Ollama base URL")
    ap.add_argument("--target-chars", type=int, default=2500, help="Chunk size for content index")
    ap.add_argument("--overlap-chars", type=int, default=200, help="Overlap size for content index")
    ap.add_argument("--concurrency", type=int, default=6, help="Parallel HTTP workers for embeddings")
    ap.add_argument("--no-shadow", action="store_true", help="Do not build shadow index")
    ap.add_argument("--no-content", action="store_true", help="Do not build content index")
    args = ap.parse_args()

    run_index(
        Path(args.raw) if args.raw else None,
        Path(args.enhanced) if args.enhanced else None,
        Path(args.shadow) if args.shadow else None,
        Path(args.out_dir),
        on_progress=lambda p, pct, d: print(f"[{p}] {pct*100:.1f}%: {d}"),
        **vars(args)
    )

if __name__ == "__main__":
    main()
