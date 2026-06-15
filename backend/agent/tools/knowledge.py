from __future__ import annotations

import asyncio
from typing import Any, Dict

from ...local_rag import LibraryContextRequest, library_context
from ..registry import NativeToolProvider, ToolDefinition, ToolExecutionContext


def _knowledge_source(source: Dict[str, Any], library_slug: str) -> Dict[str, Any]:
    return {
        "type": "knowledge",
        "library_slug": library_slug,
        "doc_id": source.get("doc_id"),
        "title": source.get("title"),
        "url": source.get("url"),
        "record_type": source.get("record_type"),
        "mime": source.get("mime"),
        "lang": source.get("lang"),
        "snippet": source.get("snippet"),
        "scores": source.get("scores") or {},
    }


async def knowledge_search_handler(arguments: Dict[str, Any], _context: ToolExecutionContext) -> Dict[str, Any]:
    request = LibraryContextRequest(
        prompt=arguments["prompt"],
        top_k=arguments.get("top_k", 5),
        embed_model=arguments.get("embedding_model"),
    )
    payload = await asyncio.to_thread(library_context, arguments["library_slug"], request)
    raw_hits = (payload.get("result") or {}).get("sources") or []
    budget = int(arguments.get("context_character_budget") or 12_000)
    context_block = str(payload.get("context_block") or "")[:budget]
    sources = [_knowledge_source(item, arguments["library_slug"]) for item in raw_hits]
    return {
        "hits": sources,
        "context_block": context_block,
        "sources": sources,
        "scores": [item.get("scores") or {} for item in sources],
    }


def register_knowledge_tools(registry: NativeToolProvider) -> None:
    registry.register(ToolDefinition(
        name="heimgeist.knowledge_search",
        description="Retrieve structured evidence from an indexed Heimgeist knowledge library.",
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "minLength": 1},
                "library_slug": {"type": "string", "minLength": 1},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                "context_character_budget": {"type": "integer", "minimum": 500, "maximum": 60000},
                "embedding_model": {"type": ["string", "null"]},
            },
            "required": ["prompt", "library_slug"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "hits": {"type": "array"},
                "context_block": {"type": "string"},
                "sources": {"type": "array"},
                "scores": {"type": "array"},
            },
            "required": ["hits", "context_block", "sources", "scores"],
            "additionalProperties": False,
        },
        handler=knowledge_search_handler,
        timeout_seconds=180,
        result_size_limit=100_000,
    ))
