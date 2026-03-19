#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
RAG Corpus Enrichment (second pass)

What this does
- Reads a JSONL corpus (one record per line) from build_corpus.py and adds LLM-generated enrichment:
  * headline (<= 12 words, language = --summary-lang)
  * summary (2–4 sentences, language = --summary-lang)
  * keywords (5–12, normalized & deduped)
  * entities (name + canonical type: PERSON|ORG|PRODUCT|WORK|PLACE|EVENT|DATE|OTHER)
  * 2–4 likely Q/A pairs (language = --summary-lang)

- Writes two outputs:
  1) --out        : original record + enrichment fields + embedding_text_hint (what your indexer should embed)
  2) --shadow-out : compact “shadow” record for retrieval with normalized shadow_text and useful metadata
       (includes: parent_id, span, size metrics, quality_flags)

Design for speed & robustness
- Default local model: phi4:latest (good balance on Apple/M1 Max). Swap with --model if desired.
- Pooled HTTP via requests.Session (one per thread), bounded semaphore on Ollama calls.
- JSON mode with strict schema + robust repair if the model returns non-JSON.
- Head/Mid/Tail sampling for long texts to stay within context quickly.
- Caching:
    * Main enrichment cache keyed by (prompt_version + model + lang + sampled_text + record_id + record_type)
    * Translation cache keyed by (model + target_lang + field_text)
- Post-enforcement:
    * clamp headline to <=12 words, summary to 2–4 sentences
    * ensure 5–12 keywords, dedup & normalize
    * canonicalize entity types and dedup by name
    * top-up Q/A to required count with a tiny follow-up call (cheap)
    * verify/translate fields to --summary-lang if needed

CLI example
  python rag_enhance_corpus.py \
    --in corpus.jsonl \
    --out corpus.enhanced.jsonl \
    --shadow-out corpus.shadow.jsonl \
    --summary-lang en \
    --ollama http://localhost:11434 \
    --model phi4:latest \
    --concurrency 8 \
    --keep-alive 15m \
    --min-chars 120 \
    --max-text 12000 \
    --timeout 120

Requires: requests, tqdm, (optional) langid, (optional) orjson
"""

from __future__ import annotations
import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Callable
import multiprocessing as mp_context

# Optional faster JSON if available
try:
    import orjson as _orjson
    def json_dumps(obj) -> str:
        return _orjson.dumps(obj, option=_orjson.OPT_NON_STR_KEYS | _orjson.OPT_SERIALIZABLE).decode("utf-8")
    def json_loads(s: str) -> Any:
        return _orjson.loads(s)
except Exception:
    def json_dumps(obj) -> str:
        return json.dumps(obj, ensure_ascii=False)
    def json_loads(s: str) -> Any:
        return json.loads(s)

try:
    import langid  # optional language detection
except Exception:
    langid = None

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

# -------------------------
# Constants & helpers
# -------------------------

PROMPT_VERSION = "v3.0"

ENTITY_CANON = {
    "PERSON": "PERSON",
    "ORG": "ORG", "ORGANIZATION": "ORG", "COMPANY": "ORG", "INSTITUTION": "ORG", "COUNTRY": "ORG",
    "PRODUCT": "PRODUCT", "TOOL": "PRODUCT", "LIBRARY": "PRODUCT",
    "WORK": "WORK", "BOOK": "WORK", "PAPER": "WORK", "ARTICLE": "WORK", "MOVIE": "WORK",
    "PLACE": "PLACE", "LOCATION": "PLACE", "CITY": "PLACE", "REGION": "PLACE", "ADDRESS": "PLACE",
    "EVENT": "EVENT", "CONFERENCE": "EVENT", "MEETING": "EVENT",
    "DATE": "DATE", "TIME": "DATE", "YEAR": "DATE",
    "OTHER": "OTHER",
}

QA_TARGET_DEFAULT = 3      # aim for 3 Q/A pairs for normal docs
QA_TARGET_SHORT = 2        # short docs can have 2

# thread-local session pool
_TLS = threading.local()

def get_session():
    import requests
    s = getattr(_TLS, "session", None)
    if s is None:
        s = requests.Session()
        setattr(_TLS, "session", s)
    return s

def log(msg: str, *, verbose: bool = True):
    if verbose:
        print(msg, flush=True)

def strip_think(s: str) -> str:
    return re.sub(r"<\s*think\s*>.*?<\s*/\s*think\s*>", "", s, flags=re.S | re.I)

def sanitize_text(s: str) -> str:
    if not s:
        return ""
    s = strip_think(s)
    s = re.sub(r"^\s*```(?:\w+)?\s*|\s*```\s*$", "", s, flags=re.M)  # strip stray code fences
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def detect_lang_quick(s: str) -> Optional[str]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        if langid is not None:
            lang, _ = langid.classify(s[:4000])
            return lang
    except Exception:
        pass
    return None

def sentence_split(text: str) -> List[str]:
    # very light heuristic splitter
    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ0-9\"'])", text.strip())
    # fall back if we ended up with nothing
    if len(parts) == 1 and len(parts[0]) > 0:
        return [parts[0]]
    return [p.strip() for p in parts if p.strip()]

def clamp_sentences(text: str, min_s: int = 2, max_s: int = 4) -> str:
    sents = sentence_split(text)
    if not sents:
        return ""
    sents = sents[:max_s]
    # if after clamp we have < min_s and original had more, pad; else keep as is
    return " ".join(sents)

def clamp_words(text: str, max_words: int) -> str:
    words = text.strip().split()
    if len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words])

def normalize_keywords(items: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for x in items or []:
        s = sanitize_text(str(x))
        s = re.sub(r"^[,;:.\-–—\s]+|[,;:.\-–—\s]+$", "", s)
        s = re.sub(r"\s+", " ", s)
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    # enforce 5–12 by trimming if needed
    if len(out) > 12:
        out = out[:12]
    return out

def canonicalize_entities(ents: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen = set()
    for e in ents or []:
        if not isinstance(e, dict): continue
        name = sanitize_text(str(e.get("name", "")))
        if not name: continue
        typ_raw = sanitize_text(str(e.get("type", ""))).upper()
        typ = ENTITY_CANON.get(typ_raw, ENTITY_CANON.get(typ_raw.split("_")[0], "OTHER"))
        key = name.lower()
        if key in seen: continue
        seen.add(key)
        out.append({"name": name, "type": typ})
    return out

def text_size_metrics(text: str) -> Dict[str, int]:
    text = text or ""
    return {
        "char_count": len(text),
        "word_count": len(text.split()),
        "line_count": len([ln for ln in text.splitlines()]),
    }

def head_mid_tail_sample(s: str, max_chars: int) -> str:
    if len(s) <= max_chars:
        return s
    third = max_chars // 3
    head = s[:third]
    mid_start = max(0, len(s)//2 - third//2)
    mid = s[mid_start:mid_start + third]
    tail = s[-third:]
    return f"{head}\n\n[...] (sample)\n\n{mid}\n\n[...] (sample)\n\n{tail}"

def looks_like_ocr_noise(s: str) -> bool:
    s = s or ""
    if not s.strip():
        return False
    letters = sum(ch.isalpha() for ch in s)
    punct = sum(ch in "!@#$%^&*()[]{}<>/\\|~`" for ch in s)
    ratio_letters = letters / max(1, len(s))
    ratio_punct = punct / max(1, len(s))
    return ratio_letters < 0.45 and ratio_punct > 0.08

def build_doc_hint(rec: Dict[str, Any]) -> str:
    rt = rec.get("record_type") or ""
    mime = rec.get("mime") or ""
    title = rec.get("title") or ""
    if rt == "image":
        return "This record is derived from an IMAGE. If text exists, it may be OCR; otherwise it is an image description. Summaries should read like quality alt-text and include short visible text only if clearly legible."
    if rt == "av":
        return "This record is derived from an AUDIO/VIDEO transcript. Focus on the main points, speakers (if known), and concrete facts. Q&A should target answerable details from the transcript."
    if rt == "code-summary":
        return "This record summarizes a code file. Keywords should emphasize APIs, functions, modules, and side effects. Q&A should focus on how to use or extend the code."
    # PDFs/HTML/TXT/etc.
    if "pdf" in mime:
        return "This record is a PDF page or document content."
    if "html" in mime:
        return "This record is HTML/webpage content."
    if "text" in mime:
        return "This record is plain text content."
    return f"This record is of type '{rt}' with mime '{mime}'. Title (if any): {title}"

def pick_text(d: Dict[str, Any]) -> str:
    return d.get("text") or d.get("content") or d.get("body") or ""

def stable_hash(text: str, model: str, lang: str, rec_id: str, rec_type: str) -> str:
    h = hashlib.sha1()
    for part in (PROMPT_VERSION, model, lang, rec_id or "", rec_type or "", text):
        h.update(part.encode("utf-8", errors="ignore"))
        h.update(b"\x00")
    return h.hexdigest()

# -------------------------
# Ollama calls
# -------------------------

def ollama_generate_json(
    host: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    keep_alive: str = "15m",
    timeout: int = 120,
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Call Ollama /api/generate in JSON mode (format='json').
    Robust JSON repair if needed.
    """
    session = get_session()
    payload = {
        "model": model,
        "system": system_prompt,
        "prompt": user_prompt,
        "format": "json",
        "stream": False,
        "keep_alive": keep_alive,
    }
    if options:
        payload["options"] = options
    r = session.post(f"{host.rstrip('/')}/api/generate", json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    raw = sanitize_text(data.get("response", ""))
    try:
        return json_loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, flags=re.S)
        if m:
            try:
                return json_loads(m.group(0))
            except Exception:
                pass
        # last resort minimal structure
        return {"headline": "", "summary": raw, "keywords": [], "entities": [], "qa": []}

def ollama_generate_text(
    host: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    keep_alive: str = "15m",
    timeout: int = 120,
    options: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Plain text response (no enforced JSON). Used for tiny follow-ups if desired.
    """
    session = get_session()
    payload = {
        "model": model,
        "system": system_prompt,
        "prompt": user_prompt,
        "stream": False,
        "keep_alive": keep_alive,
    }
    if options:
        payload["options"] = options
    r = session.post(f"{host.rstrip('/')}/api/generate", json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return sanitize_text(data.get("response", ""))

# -------------------------
# Prompts
# -------------------------

def build_system(summary_lang: str) -> str:
    return (
        "You are a precise, concise, multilingual document tagger for retrieval-augmented generation (RAG). "
        "Return ONLY JSON matching the schema. Avoid markdown. No extra commentary.\n"
        f"Output language for headline/summary/keywords/Q&A must be '{summary_lang}'."
    )

def build_user_main(text: str, summary_lang: str, doc_hint: str, want_qa: int) -> str:
    want_qa = max(2, min(4, int(want_qa)))
    # Fixed internal instruction for style/tone
    fixed_instruction = (
        "Produce concise headlines (≤12 words) and 2–4 sentence summaries; "
        "5–12 normalized keywords (kebab-case); named entities with types; 2–4 useful QA pairs. "
        "Keep strictly grounded in the source."
    )
    return (
        f"{doc_hint}\n\n"
        "You will receive a document TEXT. Produce JSON matching this schema strictly:\n"
        "{\n"
        '  "headline": string,               # <= 12 words\n'
        '  "summary": string,                # 2-4 sentences, faithful and specific\n'
        '  "keywords": [string, ...],        # 5-12 salient terms; multi-word allowed; no hashtags\n'
        '  "entities": [                     # up to ~12 unique items\n'
        '     {"name": string, "type": "PERSON|ORG|PRODUCT|WORK|PLACE|EVENT|DATE|OTHER"}\n'
        "  ],\n"
        f'  "qa": [                           # exactly {want_qa} Q&A pairs\n'
        '     {"q": string, "a": string}\n'
        "  ]\n"
        "}\n\n"
        f"Style Instruction: {fixed_instruction}\n\n"
        f"Constraints:\n"
        f"- Headline and summary MUST be in {summary_lang}.\n"
        "- Extract proper nouns and salient terms as entities; deduplicate by name.\n"
        "- Q&A must be answerable ONLY from the TEXT; keep questions <= 16 words; answers concise (<= ~80 words).\n"
        "- Be terse and informative; no filler.\n\n"
        "TEXT:\n" + text
    )

def build_user_qa_topup(text: str, summary_lang: str, need: int) -> str:
    need = max(1, min(3, int(need)))
    return (
        "We have a document TEXT and need ONLY additional Q&A pairs for retrieval. "
        "Return STRICT JSON of the form: {\n"
        '  "qa": [ {"q": string, "a": string}, ... ]\n'
        "}\n"
        f"Output language: {summary_lang}. Provide exactly {need} pairs. "
        "Questions <= 16 words; answers concise (<= ~80 words).\n\n"
        "TEXT:\n" + text
    )

def build_system_translate(target_lang: str) -> str:
    return (
        "You are a translator. Return ONLY JSON of the form {\"text\": \"...\"}. "
        "Do not add commentary."
    )

def build_user_translate(text: str, target_lang: str) -> str:
    return (
        f"Translate into {target_lang} preserving meaning and tone.\n"
        "Return: {\"text\": \"...\"} only.\n\n"
        "TEXT:\n" + text
    )

# -------------------------
# Shadow rendering
# -------------------------

def render_shadow(rec: Dict[str, Any], enrichment: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a compact record for retrieval. 'shadow_text' concatenates fields in a stable order.
    Adds: parent_id, span, size metrics, quality_flags.
    """
    parts: List[str] = []
    h = enrichment.get("headline") or rec.get("title") or ""
    s = enrichment.get("summary") or ""
    kws = enrichment.get("keywords") or []
    ents = enrichment.get("entities") or []
    qas = enrichment.get("qa") or []

    if h: parts.append(f"headline: {h}")
    if s: parts.append(f"summary: {s}")
    if kws:
        kw_line = ", ".join(str(k).strip() for k in kws if str(k).strip())
        parts.append("keywords: " + kw_line)
        # tiny topical boost (helps small embedders)
        parts.append("keywords_boost: " + kw_line)
    if ents:
        uniq = {}
        for e in ents:
            name = (e.get("name") or "").strip()
            et = (e.get("type") or "OTHER").strip().upper()
            if name and name.lower() not in uniq:
                uniq[name.lower()] = (name, et)
        if uniq:
            parts.append("entities: " + "; ".join(f"{n} [{t}]" for n, t in uniq.values()))
    if qas:
        qas_strs = []
        for qa in qas[:4]:
            q = (qa.get("q") or "").strip()
            a = (qa.get("a") or "").strip()
            if q and a:
                qas_strs.append(f"Q: {q}\nA: {a}")
        if qas_strs:
            parts.append("qa:\n" + "\n".join(qas_strs))

    shadow_text = "\n".join(parts).strip()
    meta = {
        "prompt_version": PROMPT_VERSION,
        "size": text_size_metrics(shadow_text),
    }
    parent_id = rec.get("parent_id")
    span = rec.get("span") if isinstance(rec.get("span"), dict) else None

    out = {
        "id": rec.get("id"),
        "parent_id": parent_id,
        "source_path": rec.get("source_path"),
        "url": rec.get("url"),
        "title": rec.get("title"),
        "record_type": rec.get("record_type"),
        "mime": rec.get("mime"),
        "lang": rec.get("lang"),
        "span": span,
        "shadow_text": shadow_text,
        "shadow_meta": meta,
    }
    return out

# -------------------------
# Cache
# -------------------------

class Cache:
    def __init__(self, root: Path, prefix: str = ""):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.prefix = prefix

    def _path(self, key: str) -> Path:
        k = (self.prefix + key)
        sub = self.root / k[:2] / (k + ".json")
        sub.parent.mkdir(parents=True, exist_ok=True)
        return sub

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        p = self._path(key)
        if not p.exists():
            return None
        try:
            return json_loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def put(self, key: str, value: Dict[str, Any]):
        p = self._path(key)
        with self.lock:
            p.write_text(json_dumps(value), encoding="utf-8")

# -------------------------
# Post-process + translation guards
# -------------------------

def enforce_schema_and_language(
    out: Dict[str, Any],
    *,
    target_lang: str,
    rec_text_sample: str,
    rec_is_short: bool,
    perform_translate,
    stats: Dict[str, int],
) -> Dict[str, Any]:
    quality_flags: List[str] = []

    # headline
    headline = sanitize_text(str(out.get("headline", "")))
    if headline:
        hd = clamp_words(headline, 12)
        if hd != headline:
            quality_flags.append("headline_clamped")
        headline = hd

    # summary
    summary = sanitize_text(str(out.get("summary", "")))
    if summary:
        sm = clamp_sentences(summary, 2, 4)
        if sm != summary:
            quality_flags.append("summary_clamped")
        summary = sm

    # keywords
    kws = out.get("keywords", [])
    if isinstance(kws, list):
        kws = normalize_keywords(kws)
        if len(kws) < 5 and headline:
            # augment from headline tokens if we’re short
            extra = [w for w in re.split(r"[,\s]+", headline) if len(w) > 3]
            kws = normalize_keywords((kws or []) + extra)
        if len(kws) < 5 and summary:
            extra = [w for w in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ\-]{2,}", summary)]
            kws = normalize_keywords((kws or []) + extra)
        if len(kws) > 12:
            kws = kws[:12]
    else:
        kws = []

    # entities
    ents = canonicalize_entities(out.get("entities", []))

    # QA
    qas = []
    for qa in out.get("qa", []) or []:
        if not isinstance(qa, dict): continue
        q = clamp_words(sanitize_text(str(qa.get("q", ""))), 16)
        a = sanitize_text(str(qa.get("a", "")))
        if q and a and len(a) >= 30:
            qas.append({"q": q, "a": a})
    # ensure minimum count target
    target = QA_TARGET_SHORT if rec_is_short else QA_TARGET_DEFAULT
    if len(qas) < target:
        need = target - len(qas)
        # ask for a top-up
        add = perform_translate("__QATOPUP__", rec_text_sample, need)  # overloaded: returns dict {"qa":[...]}
        extra = []
        if isinstance(add, dict):
            for qa in add.get("qa", []) or []:
                if not isinstance(qa, dict): continue
                q = clamp_words(sanitize_text(str(qa.get("q", ""))), 16)
                a = sanitize_text(str(qa.get("a", "")))
                if q and a and len(a) >= 30:
                    extra.append({"q": q, "a": a})
        if extra:
            qas.extend(extra[:need])
            quality_flags.append("qa_topped_up")
            stats["qa_topped_up"] += 1

    # Language guard (per-field)
    def _guard_lang(field_value: str, field_name: str) -> str:
        if not field_value:
            return field_value
        detected = detect_lang_quick(field_value)
        if detected and target_lang and detected != target_lang:
            tr = perform_translate(field_name, field_value, 0)  # 0 = translate exactly this string
            if isinstance(tr, dict):
                txt = sanitize_text(str(tr.get("text", "")))
            else:
                txt = sanitize_text(str(tr) if tr else "")
            if txt:
                quality_flags.append(f"{field_name}_translated")
                stats["translated_fields"] += 1
                return txt
        return field_value

    headline = _guard_lang(headline, "headline")
    summary  = _guard_lang(summary, "summary")
    # translate Q&A fields if needed
    fixed_qas = []
    for qa in qas:
        q = _guard_lang(qa["q"], "qa_q")
        a = _guard_lang(qa["a"], "qa_a")
        fixed_qas.append({"q": q, "a": a})
    qas = fixed_qas

    return {
        "headline": headline,
        "summary": summary,
        "keywords": kws,
        "entities": ents,
        "qa": qas,
        "quality_flags": quality_flags,
    }

# -------------------------
# Worker
# -------------------------

@dataclass
class Args:
    inp: str
    out: str
    shadow_out: str
    ollama: str
    model: str
    summary_lang: str
    concurrency: int
    keep_alive: str
    timeout: int
    min_chars: int
    max_text: int
    force: bool
    cache_dir: str
    verbose: bool

def enrich_one(
    rec: Dict[str, Any],
    *,
    args: Args,
    cache_main: Cache,
    cache_tr: Cache,
    sem: threading.BoundedSemaphore,
    stats: Dict[str, int],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Return (enriched_record, shadow_record)
    """
    base_text = sanitize_text(pick_text(rec))
    rec_id = str(rec.get("id") or "")
    rec_type = str(rec.get("record_type") or "")
    doc_hint = build_doc_hint(rec)

    is_short = len(base_text) < args.min_chars
    sampled = base_text if len(base_text) <= args.max_text else head_mid_tail_sample(base_text, args.max_text)
    target_lang = args.summary_lang
    if target_lang == "auto":
        target_lang = str(rec.get("lang") or "").strip().lower() or (detect_lang_quick(sampled) or "en")
    qa_target = QA_TARGET_SHORT if is_short else QA_TARGET_DEFAULT

    # short fast-path (no LLM)
    if is_short:
        enrichment = {
            "headline": (rec.get("title") or base_text[:80]).strip(),
            "summary": clamp_sentences(base_text[:400], 1, 3),
            "keywords": [],
            "entities": [],
            "qa": [],
            "model": None,
            "prompt_version": PROMPT_VERSION,
            "cached": True,
            "strategy": "short-fastpath",
        }
        enr = dict(rec)
        enr.update({
            "headline": enrichment["headline"],
            "summary": enrichment["summary"],
            "keywords": enrichment["keywords"],
            "entities": enrichment["entities"],
            "qa": enrichment["qa"],
            "enrichment_meta": {
                "model": None,
                "prompt_version": PROMPT_VERSION,
                "cached": True,
                "strategy": "short-fastpath",
                "ok": True,
                "error": None,
            }
        })
        shadow = render_shadow(rec, enrichment)
        # embedding hint prefers shadow_text
        enr["embedding_text_hint"] = shadow["shadow_text"]
        return enr, shadow

    # OCR noise guard: nudge the prompt to produce a descriptive summary
    if looks_like_ocr_noise(sampled):
        doc_hint += " The TEXT appears noisy/garbled (possibly OCR). Summarize what the document likely conveys and any clearly legible details; avoid copying garbled strings."

    # caching
    key = stable_hash(sampled, args.model, target_lang, rec_id, rec_type)
    if not args.force:
        hit = cache_main.get(key)
        if hit is not None:
            enriched = dict(rec)
            enriched.update({
                "headline": hit.get("headline"),
                "summary": hit.get("summary"),
                "keywords": hit.get("keywords"),
                "entities": hit.get("entities"),
                "qa": hit.get("qa"),
                "enrichment_meta": {
                    "model": hit.get("model"),
                    "prompt_version": hit.get("prompt_version"),
                    "cached": True,
                    "strategy": hit.get("strategy"),
                    "ok": True,
                    "error": None,
                }
            })
            shadow = render_shadow(rec, hit)
            enriched["embedding_text_hint"] = shadow["shadow_text"]
            stats["cache_hits"] += 1
            return enriched, shadow

    # tiny helper: translation or QA top-up calls (cached for translations)
    def perform_translate(kind: str, payload: str, need_pairs: int) -> Dict[str, Any] | str:
        if kind == "__QATOPUP__":
            # request exactly need_pairs additional pairs
            sys_prompt = build_system(target_lang)
            usr_prompt = build_user_qa_topup(sampled, target_lang, need_pairs)
            opts = {"temperature": 0.2, "repeat_penalty": 1.1, "top_p": 0.9, "num_predict": 280}
            with sem:
                tries, backoff, last = 2, 1.5, None
                for i in range(tries):
                    try:
                        return ollama_generate_json(args.ollama, args.model, sys_prompt, usr_prompt,
                                                    keep_alive=args.keep_alive, timeout=args.timeout, options=opts)
                    except Exception as e:
                        last = e
                        time.sleep(backoff ** (i+1))
                # failure → empty result
                return {"qa": []}
        else:
            # per-field translation caching
            tr_key = stable_hash(payload, args.model, target_lang, kind, "translate")
            if not args.force:
                tr_hit = cache_tr.get(tr_key)
                if tr_hit is not None:
                    return tr_hit
            sys_prompt = build_system_translate(target_lang)
            usr_prompt = build_user_translate(payload, target_lang)
            opts = {"temperature": 0.2, "repeat_penalty": 1.05, "top_p": 0.9, "num_predict": 200}
            with sem:
                tries, backoff, last = 2, 1.5, None
                for i in range(tries):
                    try:
                        out = ollama_generate_json(args.ollama, args.model, sys_prompt, usr_prompt,
                                                   keep_alive=args.keep_alive, timeout=args.timeout, options=opts)
                        # normalize
                        if not isinstance(out, dict):
                            out = {"text": sanitize_text(str(out))}
                        else:
                            out["text"] = sanitize_text(str(out.get("text", "")))
                        cache_tr.put(tr_key, out)
                        return out
                    except Exception as e:
                        last = e
                        time.sleep(backoff ** (i+1))
                return {"text": payload}  # give up: return original

    # main call
    system = build_system(target_lang)
    user = build_user_main(sampled, target_lang, doc_hint, qa_target)
    options = {"temperature": 0.2, "repeat_penalty": 1.1, "top_p": 0.9, "num_predict": 320}

    with sem:
        tries, backoff, last_exc = 3, 1.5, None
        for i in range(tries):
            try:
                out = ollama_generate_json(args.ollama, args.model, system, user,
                                           keep_alive=args.keep_alive, timeout=args.timeout, options=options)
                # sanitize + normalize structure
                if not isinstance(out, dict):
                    out = {"headline": "", "summary": sanitize_text(str(out)), "keywords": [], "entities": [], "qa": []}
                else:
                    for k in ("headline", "summary"):
                        if k in out and isinstance(out[k], str):
                            out[k] = sanitize_text(out[k])

                    # normalize arrays to expected types
                    out["keywords"] = [sanitize_text(str(x)) for x in out.get("keywords", []) if str(x).strip()]
                    ents = []
                    for e in out.get("entities", []) or []:
                        if isinstance(e, dict):
                            name = sanitize_text(str(e.get("name", "")))
                            typ = sanitize_text(str(e.get("type", "OTHER")))
                            if name:
                                ents.append({"name": name, "type": typ})
                    out["entities"] = ents

                    qas = []
                    for qa in out.get("qa", []) or []:
                        if isinstance(qa, dict):
                            q = sanitize_text(str(qa.get("q", "")))
                            a = sanitize_text(str(qa.get("a", "")))
                            if q and a:
                                qas.append({"q": q, "a": a})
                    out["qa"] = qas

                # post-enforce schema + language
                fixed = enforce_schema_and_language(
                    out,
                    target_lang=target_lang,
                    rec_text_sample=sampled,
                    rec_is_short=is_short,
                    perform_translate=perform_translate,
                    stats=stats,
                )

                result = {
                    "headline": fixed["headline"],
                    "summary": fixed["summary"],
                    "keywords": fixed["keywords"],
                    "entities": fixed["entities"],
                    "qa": fixed["qa"],
                    "quality_flags": fixed["quality_flags"],
                    "model": args.model,
                    "prompt_version": PROMPT_VERSION,
                    "cached": False,
                    "strategy": "sampled" if len(base_text) > args.max_text else "full",
                }

                # save to cache
                cache_main.put(key, result)

                enriched = dict(rec)
                enriched.update({
                    "headline": result["headline"],
                    "summary": result["summary"],
                    "keywords": result["keywords"],
                    "entities": result["entities"],
                    "qa": result["qa"],
                    "enrichment_meta": {
                        "model": args.model,
                        "prompt_version": PROMPT_VERSION,
                        "cached": False,
                        "strategy": result["strategy"],
                        "ok": True,
                        "error": None,
                        "quality_flags": result["quality_flags"],
                    }
                })
                shadow = render_shadow(rec, result)
                enriched["embedding_text_hint"] = shadow["shadow_text"]
                return enriched, shadow

            except Exception as e:
                last_exc = e
                time.sleep(backoff ** (i+1))

    # fallback if everything failed
    stats["fallbacks"] += 1
    fallback_summary = clamp_sentences(sampled[:1000], 2, 4)
    fallback = {
        "headline": (rec.get("title") or sampled.split("\n", 1)[0][:80]).strip(),
        "summary": fallback_summary,
        "keywords": [],
        "entities": [],
        "qa": [],
        "model": None,
        "prompt_version": PROMPT_VERSION,
        "cached": False,
        "strategy": f"fallback:{type(last_exc).__name__ if last_exc else 'error'}",
        "quality_flags": ["fallback"],
    }
    enriched = dict(rec)
    enriched.update({
        "headline": fallback["headline"],
        "summary": fallback["summary"],
        "keywords": [],
        "entities": [],
        "qa": [],
        "enrichment_meta": {
            "model": None,
            "prompt_version": PROMPT_VERSION,
            "cached": False,
            "strategy": fallback["strategy"],
            "ok": False,
            "error": str(last_exc) if last_exc else "unknown",
            "quality_flags": ["fallback"],
        }
    })
    shadow = render_shadow(rec, fallback)
    enriched["embedding_text_hint"] = shadow["shadow_text"]
    return enriched, shadow

# -------------------------
# IO
# -------------------------

def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                yield json_loads(line)
            except Exception:
                continue

def write_line(path: Path, obj: Dict[str, Any], lock: threading.Lock, *, dry_run: bool = False):
    if dry_run:
        return
    line = json_dumps(obj) + "\n"
    with lock:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()

# -------------------------
# CLI
# -------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Enrich a JSONL corpus with small-LLM generated summaries, keywords, entities and Q&A.")
    p.add_argument("--in", dest="inp", required=True, help="Input JSONL (from build_corpus.py)")
    p.add_argument("--out", required=True, help="Output JSONL with enrichment fields merged into each record")
    p.add_argument("--shadow-out", required=True, help="Output JSONL of compact 'shadow' records for retrieval")
    p.add_argument("--ollama", default="http://localhost:11434", help="Ollama base URL")
    p.add_argument("--model", default="phi4:latest", help="Local model (e.g., 'phi4:latest' or 'llama3.2:3b')")
    p.add_argument("--summary-lang", default="en", help="Language of headline/summary/keywords/Q&A")
    p.add_argument("--concurrency", type=int, default=max(2, (os.cpu_count() or 4)//2), help="Parallel HTTP workers")
    p.add_argument("--keep-alive", default="15m", help="Ollama keep_alive value (e.g., '15m', '-1' for forever)")
    p.add_argument("--timeout", type=int, default=120, help="HTTP timeout per request (seconds)")
    p.add_argument("--min-chars", type=int, default=120, help="Skip LLM when text shorter than this (fast-path heuristic)")
    p.add_argument("--max-text", type=int, default=12000, help="If text is longer, sample head/mid/tail to this many chars")
    p.add_argument("--force", action="store_true", help="Ignore cache and regenerate everything")
    p.add_argument("--cache-dir", default=".rag_cache", help="Directory for per-record JSON cache")
    p.add_argument("--dry-run", action="store_true", help="Do the work but do not write outputs")
    p.add_argument("--verbose", action="store_true", help="Verbose logging")
    return p.parse_args()

# -------------------------
# Main
# -------------------------

def run_enrich(inp: Path, out: Path, shadow_out: Path, *,
               summary_lang: str = "auto",
               on_progress: Optional[Callable[[str, float, str], None]] = None,
               cancellation_event: Optional[threading.Event] = None, **opts) -> dict:
    args = Args(
        inp=str(inp),
        out=str(out),
        shadow_out=str(shadow_out),
        ollama=opts.get("ollama", "http://localhost:11434"),
        model=opts.get("model", "phi4:latest"),
        summary_lang=summary_lang,
        concurrency=opts.get("concurrency", max(2, (os.cpu_count() or 4)//2)),
        keep_alive=opts.get("keep_alive", "15m"),
        timeout=opts.get("timeout", 120),
        min_chars=opts.get("min_chars", 120),
        max_text=opts.get("max_text", 12000),
        force=opts.get("force", False),
        cache_dir=opts.get("cache_dir", ".rag_cache"),
        verbose=opts.get("verbose", False),
    )

    src = Path(args.inp).expanduser().resolve()
    if not src.exists():
        return {"status": "error", "message": f"Input not found: {src}"}

    out_path = Path(args.out).expanduser().resolve()
    shadow_path = Path(args.shadow_out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shadow_path.parent.mkdir(parents=True, exist_ok=True)

    out_path.write_text("", encoding="utf-8")
    shadow_path.write_text("", encoding="utf-8")

    cache_main = Cache(Path(args.cache_dir), prefix="enrich_")
    cache_tr   = Cache(Path(args.cache_dir), prefix="trans_")

    sem = threading.BoundedSemaphore(max(1, args.concurrency))
    lock_out = threading.Lock()
    lock_sh = threading.Lock()

    if on_progress:
        on_progress("load", 0.05, "Loading records...")
    records = list(iter_jsonl(src))
    total = len(records)
    if total == 0:
        if on_progress:
            on_progress("done", 1.0, "Empty input.")
        return {"status": "warning", "message": "Empty input."}

    stats = {
        "cache_hits": 0,
        "fallbacks": 0,
        "qa_topped_up": 0,
        "translated_fields": 0,
        "processed": 0,
    }

    def _worker(rec: Dict[str, Any]) -> None:
        if cancellation_event and cancellation_event.is_set():
            return # Exit early if cancelled
        try:
            enriched, shadow = enrich_one(
                rec, args=args, cache_main=cache_main, cache_tr=cache_tr,
                sem=sem, stats=stats
            )
            write_line(out_path, enriched, lock_out)
            write_line(shadow_path, shadow, lock_sh)
        except Exception as e:
            passthru = dict(rec)
            passthru["enrichment_meta"] = {
                "model": None, "prompt_version": PROMPT_VERSION, "cached": False,
                "strategy": "error", "ok": False, "error": f"{type(e).__name__}: {e}",
                "quality_flags": ["error"],
            }
            write_line(out_path, passthru, lock_out)
        finally:
            stats["processed"] += 1
            if on_progress:
                pct = 0.1 + 0.8 * (stats["processed"] / total)
                on_progress("enrich", pct, f"Processed {stats['processed']}/{total}")

    if on_progress:
        on_progress("enrich", 0.1, f"Enriching {total} records...")
    with cf.ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as ex:
        futs = [ex.submit(_worker, r) for r in records]
        for fut in cf.as_completed(futs):
            if cancellation_event and cancellation_event.is_set():
                for f in futs:
                    f.cancel() # Attempt to cancel remaining futures
                if on_progress:
                    on_progress("done", 1.0, "Enrichment cancelled.")
                return {"status": "cancelled", "message": "Enrichment cancelled."}
            _ = fut.result()

    if on_progress:
        on_progress("done", 1.0, "Enrichment complete.")
    
    return {"status": "ok", "stats": stats}

def main():
    a = parse_args()
    run_enrich(
        Path(a.inp), Path(a.out), Path(a.shadow_out),
        summary_lang=a.summary_lang,
        on_progress=lambda p, pct, d: print(f"[{p}] {pct*100:.1f}%: {d}"),
        **vars(a)
    )

if __name__ == "__main__":
    main()
