from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Dict, List

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


async def generate_queries_handler(arguments: Dict[str, Any], context: ToolExecutionContext) -> Dict[str, Any]:
    recent = render_recent_context(arguments.get("messages") or [], char_limit=1600)
    prompt = (
        "Generate 3 concise, diverse web-search queries for the current request. "
        "Resolve references using recent conversation. Return only valid JSON matching the schema.\n\n"
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
    cleaned = []
    for query in queries or [arguments["prompt"]]:
        text = " ".join(str(query).split()).strip()
        if text and text.lower() not in {item.lower() for item in cleaned}:
            cleaned.append(text[:300])
    return {"queries": cleaned[:5] or [arguments["prompt"][:300]]}


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
            if not url or url in seen:
                continue
            seen.add(url)
            results.append({"query": query, "url": url, "title": item.get("title") or "", "engine": item.get("engine")})
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
        if url and url not in unique:
            unique.append(url)
    semaphore = asyncio.Semaphore(4)

    async def fetch_one(url: str) -> Dict[str, Any]:
        try:
            async with semaphore:
                snapshot = await fetch_website_snapshot(url)
            text = str(snapshot.get("text") or "")
            return {
                "requested_url": snapshot.get("requested_url") or url,
                "canonical_url": snapshot.get("url") or snapshot.get("final_url") or url,
                "title": snapshot.get("title") or "",
                "cleaned_text": text,
                "fetch_timestamp": datetime.now(timezone.utc).isoformat(),
                "content_hash": hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest(),
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
        docs.append((str(page.get("canonical_url") or page.get("requested_url") or ""), str(page.get("cleaned_text") or "")))
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
                "urls": {"type": "array", "maxItems": 12},
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
