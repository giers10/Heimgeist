from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Dict, List
from urllib.parse import unquote, urlparse
import unicodedata

import httpx

from ...ollama_client import chat_typed
from ...websearch import (
    DEFAULT_HEADERS,
    HTTP_LIMITS,
    HTTP_TIMEOUT,
    fetch_website_snapshot,
    render_recent_context,
    rerank,
    searx_search,
)
from ..registry import NativeToolProvider, ToolDefinition, ToolExecutionContext


QUERY_FORMAT = {
    "type": "object",
    "properties": {"queries": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5}},
    "required": ["queries"],
    "additionalProperties": False,
}

FETCH_TEXT_LIMIT = 12_000

GENERIC_QUERY_TERMS = {
    "a",
    "about",
    "actual",
    "aktuell",
    "aktuelle",
    "aktuellen",
    "an",
    "and",
    "are",
    "article",
    "articles",
    "auf",
    "bericht",
    "berichte",
    "current",
    "das",
    "dem",
    "den",
    "der",
    "describe",
    "described",
    "deutsch",
    "developments",
    "die",
    "end",
    "ending",
    "english",
    "ereignisse",
    "event",
    "events",
    "for",
    "from",
    "heute",
    "headline",
    "headlines",
    "in",
    "ist",
    "latest",
    "look",
    "media",
    "mean",
    "meaning",
    "meant",
    "nachrichten",
    "nah",
    "news",
    "neueste",
    "of",
    "on",
    "online",
    "outlet",
    "outlets",
    "recent",
    "said",
    "say",
    "search",
    "story",
    "stories",
    "the",
    "this",
    "today",
    "told",
    "top",
    "ubersetzung",
    "uebersetzung",
    "und",
    "was",
    "weather",
    "what",
    "whats",
    "wie",
    "you",
    "your",
    "zur",
}

CURRENT_LOOKUP_TERMS = {
    "actual",
    "aktuell",
    "aktuelle",
    "aktuellen",
    "breaking",
    "current",
    "forecast",
    "headline",
    "headlines",
    "heute",
    "latest",
    "nachrichten",
    "news",
    "neueste",
    "recent",
    "today",
    "weather",
}

CONSENT_OR_UNUSABLE_HOSTS = (
    "consent.google.",
    "accounts.google.",
)

CURRENT_LOOKUP_NOISE_HOSTS = (
    "collinsdictionary.com",
    "de.langenscheidt.com",
    "dict.cc",
    "dict.leo.org",
    "dictionary.cambridge.org",
    "duden.de",
    "dwds.de",
    "langenscheidt.com",
    "leo.org",
    "linguee.",
    "news.google.",
)

CURRENT_LOOKUP_NOISE_TITLE_PATTERNS = (
    "deutsch-ubersetzung",
    "deutsch ubersetzung",
    "deutsch-uebersetzung",
    "deutsch uebersetzung",
    "dictionary",
    "englisch-deutsch",
    "english-german",
    "meaning",
    "what does",
    "worterbuch",
)


def _normalize_text(value: Any) -> str:
    text = unquote(str(value or ""))
    text = unicodedata.normalize("NFKD", text)
    return text.encode("ascii", "ignore").decode("ascii").lower()


def _word_tokens(value: Any) -> List[str]:
    return [item for item in re.findall(r"[a-z0-9]+", _normalize_text(value)) if len(item) >= 2]


def _topic_terms(prompt: str) -> List[str]:
    seen = set()
    terms: List[str] = []
    for token in _word_tokens(prompt):
        if token in GENERIC_QUERY_TERMS or len(token) < 3:
            continue
        if token not in seen:
            seen.add(token)
            terms.append(token)
    return terms[:8]


def _term_aliases(term: str) -> List[str]:
    aliases = [term]
    if term.endswith("ian") and len(term) > 5:
        aliases.append(term[:-3])
    if term.endswith("isch") and len(term) > 6:
        aliases.append(term[:-4])
    if term == "libanon":
        aliases.append("lebanon")
    if term == "lebanese":
        aliases.append("lebanon")
    if term == "agreement":
        aliases.extend(["deal", "accord", "framework"])
    if term == "war":
        aliases.extend(["fighting", "conflict", "ceasefire", "cessation"])
    return [alias for alias in aliases if len(alias) >= 3]


def _matches_topic(prompt: str, value: str) -> bool:
    terms = _topic_terms(prompt)
    if not terms:
        return True
    haystack = _normalize_text(value)
    matches = 0
    for term in terms:
        if any(alias in haystack for alias in _term_aliases(term)):
            matches += 1
    required = 1 if len(terms) == 1 else min(2, len(terms))
    return matches >= required


def _is_current_lookup(value: str) -> bool:
    tokens = set(_word_tokens(value))
    return bool(tokens & CURRENT_LOOKUP_TERMS)


def _host_for(url: str) -> str:
    try:
        return _normalize_text(urlparse(url).netloc)
    except Exception:
        return ""


def _is_noise_result(url: str, title: str = "", topic_text: str = "") -> bool:
    host = _host_for(url)
    title_norm = _normalize_text(title)
    if any(marker in host for marker in CONSENT_OR_UNUSABLE_HOSTS):
        return True
    if "before you continue" in title_norm or "google consent" in title_norm:
        return True
    if not _is_current_lookup(topic_text):
        return False
    if any(marker in host for marker in CURRENT_LOOKUP_NOISE_HOSTS):
        return True
    return any(pattern in title_norm for pattern in CURRENT_LOOKUP_NOISE_TITLE_PATTERNS)


def _fallback_queries(prompt: str) -> List[str]:
    cleaned_prompt = " ".join(str(prompt or "").split()).strip()
    terms = _topic_terms(cleaned_prompt)
    topic = " ".join(terms[:4])
    queries: List[str] = []
    if topic and any(token in set(_word_tokens(cleaned_prompt)) for token in {"news", "nachrichten", "headlines"}):
        queries.extend([f"{topic} news today", f"{topic} latest news", f"{topic} current events"])
    elif topic and any(token in set(_word_tokens(cleaned_prompt)) for token in {"weather", "forecast"}):
        queries.extend([f"{topic} weather today", f"{topic} current weather", f"{topic} weather forecast"])
    if cleaned_prompt:
        queries.append(cleaned_prompt)
    return queries


def _query_is_too_generic(query: str, prompt: str) -> bool:
    query_terms = _topic_terms(query)
    prompt_terms = _topic_terms(prompt)
    if not query_terms:
        return True
    if prompt_terms and not _matches_topic(prompt, query):
        return True
    return False


def _clean_queries(prompt: str, generated: List[Any]) -> List[str]:
    cleaned: List[str] = []
    seen = set()
    for query in [*generated, *_fallback_queries(prompt)]:
        text = " ".join(str(query or "").split()).strip()
        if not text or _query_is_too_generic(text, prompt):
            continue
        key = _normalize_text(text)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text[:300])
        if len(cleaned) >= 5:
            break
    return cleaned or [str(prompt or "")[:300]]


async def generate_queries_handler(arguments: Dict[str, Any], context: ToolExecutionContext) -> Dict[str, Any]:
    recent = render_recent_context(arguments.get("messages") or [], char_limit=1600)
    prompt = (
        "Generate 3 concise, diverse web-search queries for the current request. "
        "Resolve references using recent conversation. Every query must include the concrete topic, "
        "entity, or location from the request. Never output standalone generic terms such as latest, "
        "current, top, today, news, or weather. Return only valid JSON matching the schema.\n\n"
        f"Current request:\n{arguments['prompt']}\n\nRecent conversation:\n{recent}"
    )
    result = await chat_typed(
        arguments["model"],
        [{"role": "user", "content": prompt}],
        format=QUERY_FORMAT,
        options={"temperature": 0.1},
        cancellation_event=context.cancellation_event,
    )
    try:
        parsed = json.loads(result.content)
        queries = parsed.get("queries") if isinstance(parsed, dict) else None
    except Exception:
        queries = None
    return {"queries": _clean_queries(arguments["prompt"], queries or [])}


async def web_search_handler(arguments: Dict[str, Any], _context: ToolExecutionContext) -> Dict[str, Any]:
    queries = arguments.get("queries") or []
    if arguments.get("query"):
        queries = [arguments["query"], *queries]
    unique_queries = []
    for query in queries:
        text = str(query or "").strip()
        if text and text not in unique_queries:
            unique_queries.append(text)
    maximum = max(1, min(int(arguments.get("maximum_results") or 12), 32))
    async with httpx.AsyncClient(
        headers=DEFAULT_HEADERS,
        follow_redirects=True,
        http2=True,
        limits=HTTP_LIMITS,
        timeout=HTTP_TIMEOUT,
    ) as client:
        responses = await asyncio.gather(*[
            searx_search(
                client,
                query,
                max_results=min(8, maximum),
                searx_url=arguments.get("searx_url"),
                engines=arguments.get("engines") or None,
            )
            for query in unique_queries[:5]
        ])
    results: List[Dict[str, Any]] = []
    seen = set()
    for query, items in zip(unique_queries, responses):
        for item in items:
            url = str(item.get("url") or "")
            title = str(item.get("title") or "")
            result_blob = f"{title} {url}"
            if not url or url in seen:
                continue
            if _is_noise_result(url, title, query):
                continue
            if not _matches_topic(query, result_blob):
                continue
            seen.add(url)
            results.append({"query": query, "url": url, "title": title, "engine": item.get("engine")})
            if len(results) >= maximum:
                break
    return {"queries": unique_queries, "results": results, "unavailable": not results}


async def web_fetch_handler(arguments: Dict[str, Any], _context: ToolExecutionContext) -> Dict[str, Any]:
    urls = arguments.get("urls") or []
    if arguments.get("url"):
        urls = [arguments["url"], *urls]
    maximum = max(1, min(int(arguments.get("maximum_pages") or 6), 12))
    unique = []
    for item in urls:
        url = item.get("url") if isinstance(item, dict) else item
        url = str(url or "").strip()
        title = str(item.get("title") or "") if isinstance(item, dict) else ""
        query = str(item.get("query") or "") if isinstance(item, dict) else ""
        if url and url not in unique and not _is_noise_result(url, title, query):
            unique.append(url)
    semaphore = asyncio.Semaphore(4)

    async def fetch_one(url: str) -> Dict[str, Any]:
        try:
            async with semaphore:
                snapshot = await fetch_website_snapshot(url)
            full_text = str(snapshot.get("text") or "")
            text = full_text[:FETCH_TEXT_LIMIT]
            canonical_url = snapshot.get("url") or snapshot.get("final_url") or url
            title = snapshot.get("title") or ""
            if _is_noise_result(str(canonical_url), str(title), ""):
                return {
                    "requested_url": snapshot.get("requested_url") or url,
                    "canonical_url": canonical_url,
                    "title": title,
                    "cleaned_text": "",
                    "fetch_timestamp": datetime.now(timezone.utc).isoformat(),
                    "content_hash": hashlib.sha256(full_text.encode("utf-8", errors="ignore")).hexdigest(),
                    "error": "filtered_noise_page",
                }
            return {
                "requested_url": snapshot.get("requested_url") or url,
                "canonical_url": canonical_url,
                "title": title,
                "cleaned_text": text,
                "fetch_timestamp": datetime.now(timezone.utc).isoformat(),
                "content_hash": hashlib.sha256(full_text.encode("utf-8", errors="ignore")).hexdigest(),
                "error": None,
            }
        except Exception as exc:
            return {
                "requested_url": url,
                "canonical_url": url,
                "title": "",
                "cleaned_text": "",
                "fetch_timestamp": datetime.now(timezone.utc).isoformat(),
                "content_hash": "",
                "error": f"{type(exc).__name__}: {exc}",
            }

    pages = await asyncio.gather(*(fetch_one(url) for url in unique[:maximum]))
    return {"pages": pages}


async def web_rerank_handler(arguments: Dict[str, Any], _context: ToolExecutionContext) -> Dict[str, Any]:
    docs = []
    for page in arguments.get("pages") or []:
        if page.get("error") or not str(page.get("cleaned_text") or "").strip():
            continue
        url = str(page.get("canonical_url") or page.get("requested_url") or "")
        title = str(page.get("title") or "")
        text = str(page.get("cleaned_text") or "")
        if _is_noise_result(url, title, arguments["prompt"]):
            continue
        if not _matches_topic(arguments["prompt"], f"{title} {url} {text[:2400]}"):
            continue
        docs.append((url, text))
    ranked = await rerank(
        arguments["prompt"],
        docs,
        model=arguments.get("model") or "",
        context_excerpt=arguments.get("context_excerpt") or "",
        embed_model=arguments.get("rerank_model"),
    )
    maximum = max(1, min(int(arguments.get("maximum_results") or 6), 12))
    minimum_score = float(arguments.get("minimum_score") or 0)
    selected = []
    page_by_url = {
        str(page.get("canonical_url") or page.get("requested_url") or ""): page
        for page in arguments.get("pages") or []
    }
    for url, text, score in ranked:
        if score < minimum_score:
            continue
        page = page_by_url.get(url) or {}
        title = str(page.get("title") or "")
        if _is_noise_result(url, title, arguments["prompt"]):
            continue
        if not _matches_topic(arguments["prompt"], f"{title} {url} {text[:2400]}"):
            continue
        selected.append({
            "type": "web",
            "url": url,
            "title": page.get("title") or url,
            "snippet": text[:1600],
            "score": score,
        })
        if len(selected) >= maximum:
            break
    context_lines = ["<websearch_context>"]
    if not selected:
        context_lines.append("Web search completed but no suitable readable pages were available.")
    for index, item in enumerate(selected, 1):
        context_lines.append(f"[W{index}] {item['title']}\n{item['snippet']}\nSource: {item['url']}")
    context_lines.append("</websearch_context>")
    return {"results": selected, "context_block": "\n".join(context_lines), "sources": selected}


def register_web_tools(registry: NativeToolProvider) -> None:
    registry.register(ToolDefinition(
        name="heimgeist.web_generate_queries",
        description="Generate validated web-search queries from a request and recent conversation.",
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "minLength": 1},
                "model": {"type": "string", "minLength": 1},
                "messages": {"type": "array"},
            },
            "required": ["prompt", "model"],
            "additionalProperties": False,
        },
        output_schema=QUERY_FORMAT,
        handler=generate_queries_handler,
        timeout_seconds=120,
        llm_call=True,
    ))
    registry.register(ToolDefinition(
        name="heimgeist.web_search",
        description="Search the configured SearXNG service and return structured results without answering.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": ["string", "null"]},
                "queries": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                "engines": {"type": "array", "items": {"type": "string"}},
                "maximum_results": {"type": "integer", "minimum": 1, "maximum": 32},
                "searx_url": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"queries": {"type": "array"}, "results": {"type": "array"}, "unavailable": {"type": "boolean"}},
            "required": ["queries", "results", "unavailable"],
            "additionalProperties": False,
        },
        handler=web_search_handler,
        timeout_seconds=45,
    ))
    registry.register(ToolDefinition(
        name="heimgeist.web_fetch",
        description="Fetch and extract selected web pages using Heimgeist's bounded website policy.",
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": ["string", "null"]},
                "urls": {"type": "array", "maxItems": 32},
                "maximum_pages": {"type": "integer", "minimum": 1, "maximum": 12},
            },
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"pages": {"type": "array"}},
            "required": ["pages"],
            "additionalProperties": False,
        },
        handler=web_fetch_handler,
        timeout_seconds=90,
        result_size_limit=180_000,
    ))
    registry.register(ToolDefinition(
        name="heimgeist.web_rerank",
        description="Rank extracted web pages using Heimgeist's embedding-based reranker.",
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "minLength": 1},
                "pages": {"type": "array"},
                "model": {"type": ["string", "null"]},
                "rerank_model": {"type": ["string", "null"]},
                "context_excerpt": {"type": "string"},
                "maximum_results": {"type": "integer", "minimum": 1, "maximum": 12},
                "minimum_score": {"type": "number", "minimum": 0, "maximum": 100},
            },
            "required": ["prompt", "pages"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"results": {"type": "array"}, "context_block": {"type": "string"}, "sources": {"type": "array"}},
            "required": ["results", "context_block", "sources"],
            "additionalProperties": False,
        },
        handler=web_rerank_handler,
        timeout_seconds=180,
        result_size_limit=120_000,
    ))
