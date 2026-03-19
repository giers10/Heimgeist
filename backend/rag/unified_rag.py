#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
04_unified_rag.py

Hybrid retrieval + (optional) rerank + (optional) answer generation.

Now supports:
- HYBRID: shadow+content indexes (best quality)
- SINGLE-INDEX:
    * legacy pair (--index/--store)            ← back-compat
    * content-only pair (--content-index/--content-store)
    * shadow-only  pair (--shadow-index/--shadow-store)

If you skipped enrichment:
- Build only content + proxy shadow with 03_index_builder.py (raw → content; raw/enhanced→proxy shadow)
- Query with:
    * HYBRID: provide both pairs
    * SINGLE-INDEX: provide only one pair (content OR shadow)

"""
from __future__ import annotations

import argparse, json, os, sys, subprocess, math
from pathlib import Path
from typing import List, Dict, Tuple, Optional

import faiss
import numpy as np
import requests
import threading
from typing import Callable

# -----------------------------
# Utilities
# -----------------------------
def norm_f32(mat: np.ndarray) -> np.ndarray:
    mat = np.asarray(mat, dtype="float32")
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms

def zscore(x: List[float]) -> List[float]:
    if not x:
        return []
    mu = float(np.mean(x))
    sd = float(np.std(x))
    if sd == 0.0:
        return [0.0 for _ in x]
    return [(v - mu) / sd for v in x]

def sanitize(s: Optional[str]) -> str:
    if not s:
        return ""
    import re
    s = re.sub(r"<\s*think\s*>.*?<\s*/\s*think\s*>", "", s, flags=re.S|re.I)
    s = re.sub(r"^\s*```(?:\w+)?\s*|\s*```\s*$", "", s, flags=re.M)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def pick_any_text(rec: Dict) -> str:
    """Use 'text' if present else 'shadow_text' for rerank/answer/pretty."""
    return rec.get("text") or rec.get("shadow_text") or rec.get("content") or rec.get("body") or ""

def embed_query(ollama_url: str, model: str, text: str, timeout_s: int = 60) -> np.ndarray:
    r = requests.post(
        f"{ollama_url.rstrip('/')}/api/embeddings",
        json={"model": model, "prompt": text},
        timeout=timeout_s,
    )
    r.raise_for_status()
    data = r.json()
    vec = data.get("embedding") or (data.get("embeddings") or [None])[0]
    if vec is None:
        raise RuntimeError("Ollama /api/embeddings returned no vector.")
    return np.array(vec, dtype="float32")

def load_meta(store_path: str) -> Dict[int, Dict]:
    id2meta: Dict[int, Dict] = {}
    with open(store_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            id2meta[int(rec["id"])] = rec
    return id2meta

def truncate_text(s: Optional[str], limit: int) -> str:
    if not s:
        return ""
    return s if len(s) <= limit else s[:limit]

def derive_doc_id(rec: Dict) -> str:
    # Prefer explicit doc_id if provided by meta builder
    did = rec.get("doc_id")
    if did:
        return did
    rid = rec.get("record_id") or rec.get("id") or ""
    return rid.split("#", 1)[0]

# -----------------------------
# Rerank (subprocess worker)
# -----------------------------
def sentence_transformers_available() -> bool:
    try:
        import importlib.util as _ilu
        spec = _ilu.find_spec("sentence_transformers")
        return spec is not None
    except Exception:
        return False

def rerank_subprocess(
    query: str,
    docs: List[str],
    *,
    worker_path: Path,
    model: str,
    device: str,
    dtype: str,
    batch: int,
    maxlen: int,
) -> Optional[List[Tuple[int, float]]]:
    """
    Call this same script with --mode rerank-worker via a clean Python subprocess.
    Returns: list of (local_index, score) sorted desc, or None on failure.
    """
    payload = {"query": query, "docs": docs}
    cmd = [
        sys.executable,
        str(worker_path),
        "--mode", "rerank-worker",
        "--rerank-model", model,
        "--rerank-device", device,
        "--rerank-dtype", dtype,
        "--rerank-batch", str(batch),
        "--rerank-maxlen", str(maxlen),
        "--stdio"
    ]
    env = os.environ.copy()
    env.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    env.setdefault("TOKENIZERS_PARALLELISM", "false")
    env.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

    try:
        proc = subprocess.run(
            cmd,
            input=json.dumps(payload).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=env,
        )
    except Exception as e:
        sys.stderr.write(f"[rerank] failed to launch worker: {e}\n")
        return None

    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", errors="ignore") + "\n")
        return None

    try:
        data = json.loads(proc.stdout.decode("utf-8"))
        results = data.get("results") or []
        pairs = [(int(r["index"]), float(r["score"])) for r in results]
        pairs.sort(key=lambda x: x[1], reverse=True)
        return pairs
    except Exception as e:
        sys.stderr.write(f"[rerank] parse error: {e}\n")
        return None

# -----------------------------
# Simple diversity (per-source cap)
# -----------------------------
def apply_per_source_cap(ordered: List[Dict], per_source_limit: int) -> List[Dict]:
    if per_source_limit <= 0:
        return ordered
    counts = {}
    out = []
    for rec in ordered:
        key = rec.get("url") or rec.get("doc_id") or rec.get("title") or str(rec.get("id"))
        c = counts.get(key, 0)
        if c < per_source_limit:
            out.append(rec)
            counts[key] = c + 1
    return out

# -----------------------------
# Generation
# -----------------------------
def generate(
    ollama_url: str,
    model: str,
    prompt: str,
    system: Optional[str] = None,
    temperature: float = 0.2,
    timeout_s: int = 180,
    on_stream=None,
) -> str:
    payload = {"model": model, "prompt": prompt, "options": {"temperature": temperature}}
    if system:
        payload["system"] = system
    r = requests.post(f"{ollama_url.rstrip('/')}/api/generate", json=payload, timeout=timeout_s, stream=True)
    r.raise_for_status()
    out = []
    for chunk in r.iter_lines(decode_unicode=True):
        if not chunk:
            continue
        try:
            obj = json.loads(chunk)
            delta = obj.get("response", "")
            out.append(delta)
            if on_stream:
                on_stream({"delta": delta})
            if obj.get("done"):
                break
        except Exception:
            pass
    return sanitize("".join(out))

# -----------------------------
# Search helpers
# -----------------------------
def faiss_search(index: faiss.Index, qvec: np.ndarray, k: int) -> Tuple[List[int], List[float]]:
    sims, ids = index.search(qvec, k)
    ids = [int(i) for i in ids[0] if i != -1]
    sims = [float(s) for s in sims[0][: len(ids)]]
    return ids, sims

# -----------------------------
# Output / answer
# -----------------------------
def output_or_answer(final: List[Dict], args, on_stream=None):
    if not args.answer:
        # Return top-k results without generating an answer
        return {
            "done": True,
            "sources": [
                {
                    "doc_id": rec.get("doc_id"),
                    "title": rec.get("title"),
                    "url": rec.get("url"),
                    "record_type": rec.get("record_type"),
                    "mime": rec.get("mime"),
                    "lang": rec.get("lang"),
                    "snippet": pick_any_text(rec),
                    "scores": {
                        "final": float(rec.get("_score", 0.0)),
                        "shadow": float(rec.get("_shadow")) if rec.get("_shadow") is not None else None,
                        "content": float(rec.get("_ann", 0.0)),
                        "rerank": float(rec.get("_rerank")) if rec.get("_rerank") is not None else None,
                    },
                }
                for rec in final
            ],
        }

    # Build prompt for answering
    context_blocks, sources = [], []
    for i, rec in enumerate(final, start=1):
        text = pick_any_text(rec)
        title = rec.get("title") or "(untitled)"
        url = rec.get("url") or title
        sources.append(f"[{i}] {url}")
        context_blocks.append(f"[{i}] {title}\n{text}")

    system = (
        "You are a careful researcher. Answer ONLY from the provided sources. "
        "Cite like [1], [2] in-line. If the answer is not in the sources, say you can't find it. "
        "Do not include private chain-of-thought or <think> tags."
    )
    prompt = (
        f"Question: {args.query}\n\n"
        "Use the sources below. If not answerable from them, say so clearly.\n\n"
        "Sources:\n" + "\n\n".join(context_blocks) + "\n\n----\n\n"
        "Remember: only use these sources. Provide a concise answer with citations.\n\n"
        f"And again. The question you need to answer is: {args.query}"
    )

    full_answer = generate(args.ollama, args.gen_model, prompt, system=system, temperature=args.temperature, on_stream=on_stream)
    
    final_result = {
        "done": True,
        "answer": full_answer,
        "sources": [
            {
                "doc_id": rec.get("doc_id"),
                "title": rec.get("title"),
                "url": rec.get("url"),
            }
            for rec in final
        ],
    }
    if on_stream:
        on_stream(final_result)
    
    return final_result

# -----------------------------
# Main CLI (search / answer)
# -----------------------------
def run_cli(args):
    # Determine mode
    hybrid_ok = all([args.shadow_index, args.shadow_store, args.content_index, args.content_store])

    single_pair: Optional[Tuple[str, str]] = None
    single_kind = None
    if not hybrid_ok:
        # Prefer legacy if provided
        if args.index and args.store:
            single_pair = (args.index, args.store)
            single_kind = "legacy"
        elif args.content_index and args.content_store:
            single_pair = (args.content_index, args.content_store)
            single_kind = "content"
        elif args.shadow_index and args.shadow_store:
            single_pair = (args.shadow_index, args.shadow_store)
            single_kind = "shadow"

    # Embed query
    q = norm_f32(embed_query(args.ollama, args.embed_model, args.query).reshape(1, -1))

    if single_pair:
        # SINGLE-INDEX path (works for legacy/content-only/shadow-only)
        index = faiss.read_index(single_pair[0])
        id2meta = load_meta(single_pair[1])

        ids, sims = faiss_search(index, q, min(args.candidates, index.ntotal))
        candidates = []
        for pos, _id in enumerate(ids):
            base = id2meta[_id]
            rec = dict(base)
            rec["_ann"] = sims[pos]
            candidates.append(rec)

        # Optional rerank
        reranked_scores = None
        if not args.no_rerank and sentence_transformers_available():
            docs = [truncate_text(pick_any_text(c), args.max_doc_chars) for c in candidates]
            pairs = rerank_subprocess(
                args.query, docs,
                worker_path=Path(__file__),
                model=args.rerank_model,
                device=args.rerank_device,
                dtype=args.rerank_dtype,
                batch=args.rerank_batch,
                maxlen=args.rerank_maxlen,
            )
            if pairs is not None:
                reranked_scores = [None] * len(candidates)
                for local_idx, score in pairs:
                    if 0 <= local_idx < len(reranked_scores):
                        reranked_scores[local_idx] = float(score)
                min_score = min([s for s in reranked_scores if s is not None], default=0.0)
                reranked_scores = [s if s is not None else min_score for s in reranked_scores]
            else:
                print("[info] rerank disabled (worker failed).", file=sys.stderr)

        # Blend
        if reranked_scores is not None:
            z_ann = zscore([c["_ann"] for c in candidates])
            z_rr = zscore(reranked_scores)
            alpha = float(args.blend)
            final_scores = [(1 - alpha) * a + alpha * r for a, r in zip(z_ann, z_rr)]
            for rec, fs, rr in zip(candidates, final_scores, reranked_scores):
                rec["_score"] = float(fs)
                rec["_rerank"] = float(rr)
            candidates.sort(key=lambda r: r["_score"], reverse=True)
        else:
            for rec in candidates:
                rec["_score"] = rec["_ann"]

        final = candidates[: max(1, min(args.k, len(candidates)))]
        return output_or_answer(final, args)

    # HYBRID path
    shadow_index = faiss.read_index(args.shadow_index)
    shadow_meta = load_meta(args.shadow_store)
    content_index = faiss.read_index(args.content_index)
    content_meta = load_meta(args.content_store)

    # Stage A: Shadow search → doc shortlist
    sid_list, s_sim = faiss_search(shadow_index, q, min(args.shadow_candidates, shadow_index.ntotal))
    s_hits = [{"id": sid, "sim": sim, **shadow_meta[sid]} for sid, sim in zip(sid_list, s_sim)]

    # optional shadow weighting by kind
    kw = {}
    for kv in args.shadow_kind_weights.split(","):
        kv = kv.strip()
        if not kv:
            continue
        if ":" in kv:
            k, v = kv.split(":", 1)
            try:
                kw[k.strip().lower()] = float(v)
            except Exception:
                pass
    if kw:
        for h in s_hits:
            w = kw.get((h.get("kind") or "").lower(), 1.0)
            h["sim"] *= float(w)

    # group to doc_id
    doc_scores: Dict[str, float] = {}
    for h in s_hits:
        did = derive_doc_id(h)
        doc_scores[did] = max(doc_scores.get(did, 0.0), float(h["sim"]))  # max over shadow signals

    # Stage B: Content search (global)
    cid_list, c_sim = faiss_search(content_index, q, min(args.content_candidates, content_index.ntotal))
    c_hits = [{"id": cid, "sim": sim, **content_meta[cid]} for cid, sim in zip(cid_list, c_sim)]

    # Stage C: filter to doc shortlist
    ordered_docs = sorted(doc_scores.items(), key=lambda kv: kv[1], reverse=True)[: args.doc_top]
    if not ordered_docs:
        # Fallback: derive docs from top content hits
        tmp_docs = []
        seen = set()
        for h in c_hits:
            did = derive_doc_id(h)
            if did not in seen:
                seen.add(did)
                tmp_docs.append((did, float(h['sim'])))
            if len(tmp_docs) >= args.doc_top:
                break
        ordered_docs = tmp_docs
    shortlist = set([d for d, _ in ordered_docs])

    # keep content hits belonging to shortlist (fallback to global if empty)
    content_for_docs = [h for h in c_hits if derive_doc_id(h) in shortlist] or c_hits

    # per-doc cap
    per_doc = max(1, args.per_doc_chunks)
    doc_buckets: Dict[str, List[Dict]] = {}
    for h in content_for_docs:
        did = derive_doc_id(h)
        doc_buckets.setdefault(did, []).append(h)
    for did, arr in doc_buckets.items():
        arr.sort(key=lambda r: r["sim"], reverse=True)
        doc_buckets[did] = arr[:per_doc]

    # flatten, compute final score as blend of shadow(doc) + content(chunk)
    out_candidates: List[Dict] = []
    for did, doc_sim in ordered_docs:
        for ch in doc_buckets.get(did, []):
            final = dict(ch)
            final["_shadow"] = float(doc_sim)
            final["_ann"] = float(ch["sim"])
            alpha = float(args.doc_blend)  # weight of shadow
            beta = float(args.chunk_blend) # weight of chunk ann
            final["_score"] = alpha * float(doc_sim) + beta * float(ch["sim"])
            out_candidates.append(final)

    if not out_candidates:
        print("No retrieval results.", file=sys.stderr)
        if args.answer:
            print("No results from retrieval; cannot answer.")
        return

    out_candidates.sort(key=lambda r: r["_score"], reverse=True)

    # Optional rerank of the first pool
    reranked_scores = None
    if not args.no_rerank and sentence_transformers_available():
        pool = out_candidates[: args.candidates]
        docs = [truncate_text(pick_any_text(c), args.max_doc_chars) for c in pool]
        pairs = rerank_subprocess(
            args.query, docs,
            worker_path=Path(__file__),
            model=args.rerank_model,
            device=args.rerank_device,
            dtype=args.rerank_dtype,
            batch=args.rerank_batch,
            maxlen=args.rerank_maxlen,
        )
        if pairs is not None:
            reranked_scores = [None] * len(pool)
            for local_idx, score in pairs:
                if 0 <= local_idx < len(pool):
                    reranked_scores[local_idx] = float(score)
            min_score = min([s for s in reranked_scores if s is not None], default=0.0)
            reranked_scores = [s if s is not None else min_score for s in reranked_scores]
            z_ann = zscore([c["_score"] for c in pool])
            z_rr = zscore(reranked_scores)
            alpha = float(args.blend)
            blended = [(1 - alpha) * a + alpha * r for a, r in zip(z_ann, z_rr)]
            for rec, fs, rr in zip(pool, blended, reranked_scores):
                rec["_score"] = float(fs)
                rec["_rerank"] = float(rr)
            out_candidates[: len(pool)] = sorted(pool, key=lambda r: r["_score"], reverse=True)
        else:
            print("[info] rerank disabled (worker failed).", file=sys.stderr)

    # per-source cap and top-k
    ordered = apply_per_source_cap(out_candidates, args.per_source_limit)
    final = ordered[: max(1, min(args.k, len(ordered)))]
    return output_or_answer(final, args)

# -----------------------------
# Rerank worker mode
# -----------------------------
def run_rerank_worker(args):
    """
    Reads JSON from stdin: {"query": str, "docs": [str, ...]}
    Writes JSON to stdout: {"results": [{"index": int, "score": float}, ...]}
    """
    try:
        import torch
        from sentence_transformers import CrossEncoder
    except Exception as e:
        # Gracefully tell parent we failed by returning empty results
        out = {"results": []}
        json.dump(out, sys.stdout)
        sys.stdout.flush()
        print(f"[worker] sentence_transformers unavailable: {e}", file=sys.stderr)
        return

    try:
        torch.set_num_threads(1)
    except Exception:
        pass

    device = args.rerank_device
    if device == "auto":
        device = "mps" if torch.backends.mps.is_available() else "cpu"

    if args.rerank_dtype == "auto":
        dtype = torch.float16 if device == "mps" else torch.float32
    else:
        dtype = torch.float16 if args.rerank_dtype == "float16" else torch.float32

    model = CrossEncoder(
        args.rerank_model,
        device=device,
        max_length=args.rerank_maxlen,
        automodel_args={"torch_dtype": dtype},
    )

    data = json.load(sys.stdin)
    query = data["query"]
    docs = data["docs"]

    pairs = [(query, d) for d in docs]
    scores = model.predict(
        pairs,
        batch_size=args.rerank_batch,
        convert_to_numpy=True,
        show_progress_bar=False,
    ).tolist()

    ordered = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
    out = {"results": [{"index": int(i), "score": float(s)} for i, s in ordered]}
    json.dump(out, sys.stdout)
    sys.stdout.flush()

# -----------------------------
# Argparse
# -----------------------------
def build_parser():
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--mode", default="cli", choices=["cli", "rerank-worker"])

    # Legacy single-index I/O (kept for back-compat)
    ap.add_argument("--index", help="Single FAISS index (legacy)")
    ap.add_argument("--store", help="Single metadata JSONL (legacy)")
    ap.add_argument("--candidates", type=int, default=200, help="ANN neighbors to fetch (legacy or rerank pool).")

    # Hybrid I/O
    ap.add_argument("--shadow-index", help="FAISS index over shadow_text")
    ap.add_argument("--shadow-store", help="Metadata JSONL for shadow index")
    ap.add_argument("--content-index", help="FAISS index over content chunks")
    ap.add_argument("--content-store", help="Metadata JSONL for content index")

    ap.add_argument("--query", required=False)
    ap.add_argument("--ollama", default="http://localhost:11434")
    ap.add_argument("--embed-model", default="dengcao/Qwen3-Embedding-0.6B:F16")

    # Shadow/content retrieval sizes (hybrid)
    ap.add_argument("--shadow-candidates", type=int, default=400, help="Shadow ANN pool size")
    ap.add_argument("--content-candidates", type=int, default=600, help="Content ANN pool size")
    ap.add_argument("--doc-top", type=int, default=40, help="Top-N documents from shadow shortlist")
    ap.add_argument("--per-doc-chunks", type=int, default=2, help="Max chunks per doc from content pool")
    ap.add_argument("--doc-blend", type=float, default=0.6, help="Weight for shadow score in final blend [0..1]")
    ap.add_argument("--chunk-blend", type=float, default=0.4, help="Weight for content-ANN score in final blend [0..1]")
    ap.add_argument("--shadow-kind-weights", default="image:1.2,code:1.1", help="Comma list 'kind:weight' to bias doc ranking")

    # Rerank knobs
    ap.add_argument("--no-rerank", action="store_true", help="Disable reranking.")
    ap.add_argument("--blend", type=float, default=0.75, help="Blend weight for reranker in normalized score [0..1].")
    ap.add_argument("--rerank-model", default="cross-encoder/ms-marco-MiniLM-L-6-v2")
    ap.add_argument("--rerank-device", default="auto", choices=["auto", "mps", "cpu"])
    ap.add_argument("--rerank-dtype", default="auto", choices=["auto", "float16", "float32"])
    ap.add_argument("--rerank-batch", type=int, default=64)
    ap.add_argument("--rerank-maxlen", type=int, default=256)
    ap.add_argument("--stdio", action="store_true", help=argparse.SUPPRESS)  # worker-only flag

    # Output / answer
    ap.add_argument("--json", action="store_true", help="Print search results as JSON.")
    ap.add_argument("--pretty", action="store_true", help="Pretty-print search results.")
    ap.add_argument("--show-scores", action="store_true", help="Show ANN/rerank scores in pretty output.")
    ap.add_argument("--answer", action="store_true", help="Generate an answer with an LLM using top-k contexts.")
    ap.add_argument("--gen-model", default="qwen3:4b",
                    help="Any chat-capable model in Ollama (e.g., 'qwen2.5:7b-instruct', 'llama3.1:8b-instruct').")
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--k", type=int, default=10, help="Number of final results to return/use.")

    # Misc
    ap.add_argument("--max-doc-chars", type=int, default=4000, help="Truncate each candidate before rerank.")
    ap.add_argument("--per-source-limit", type=int, default=3, help="Max results per source (url/doc) to diversify.")

    return ap

def run_query(shadow_index: Path, shadow_store: Path,
              content_index: Path, content_store: Path,
              query: str, *, answer: bool = False,
              on_stream: Optional[Callable[[Dict], None]] = None, **opts) -> dict:

    # Ensure all paths are strings for argparse.Namespace
    _shadow_index = str(shadow_index) if shadow_index else None
    _shadow_store = str(shadow_store) if shadow_store else None
    _content_index = str(content_index) if content_index else None
    _content_store = str(content_store) if content_store else None

    args = argparse.Namespace(
        shadow_index=_shadow_index,
        shadow_store=_shadow_store,
        content_index=_content_index,
        content_store=_content_store,
        query=query,
        answer=answer,
        ollama=opts.get("ollama", "http://localhost:11434"),
        embed_model=opts.get("embed_model", "dengcao/Qwen3-Embedding-0.6B:F16"),
        shadow_candidates=opts.get("shadow_candidates", 400),
        content_candidates=opts.get("content_candidates", 600),
        doc_top=opts.get("doc_top", 40),
        per_doc_chunks=opts.get("per_doc_chunks", 2),
        doc_blend=opts.get("doc_blend", 0.6),
        chunk_blend=opts.get("chunk_blend", 0.4),
        shadow_kind_weights=opts.get("shadow_kind_weights", "image:1.2,code:1.1"),
        no_rerank=opts.get("no_rerank", True), # Reranker OFF by default
        blend=opts.get("blend", 0.75),
        rerank_model=opts.get("rerank_model", "cross-encoder/ms-marco-MiniLM-L-6-v2"),
        rerank_device=opts.get("rerank_device", "auto"),
        rerank_dtype=opts.get("rerank_dtype", "auto"),
        rerank_batch=opts.get("rerank_batch", 64),
        rerank_maxlen=opts.get("rerank_maxlen", 256),
        gen_model=opts.get("gen_model", "qwen3:4b"),
        temperature=opts.get("temperature", 0.2),
        k=opts.get("k", 10),
        max_doc_chars=opts.get("max_doc_chars", 4000),
        per_source_limit=opts.get("per_source_limit", 3),
        json=True # Force JSON-like output dict
    )

    return run_cli(args, on_stream=on_stream)

def main():
    ap = build_parser()
    args = ap.parse_args()

    if args.mode == "rerank-worker":
        return run_rerank_worker(args)

    if not args.query:
        ap.error("--query is required in cli mode")

    hybrid_ok = all([args.shadow_index, args.shadow_store, args.content_index, args.content_store])
    if not hybrid_ok:
        ap.error("For CLI use, all four index/store paths are required for hybrid retrieval.")

    result = run_query(
        shadow_index=Path(args.shadow_index),
        shadow_store=Path(args.shadow_store),
        content_index=Path(args.content_index),
        content_store=Path(args.content_store),
        query=args.query,
        answer=args.answer,
        on_stream=lambda d: print(json.dumps(d, ensure_ascii=False), flush=True) if args.answer else None,
        **vars(args)
    )
    
    if not args.answer:
        print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()
