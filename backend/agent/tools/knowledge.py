from __future__ import annotations

import asyncio
import multiprocessing
import time
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


def _knowledge_search_worker(connection, arguments: Dict[str, Any]) -> None:
    try:
        request = LibraryContextRequest(
            prompt=arguments["prompt"],
            top_k=arguments.get("top_k", 5),
            embed_model=arguments.get("embedding_model"),
        )
        connection.send(("ok", library_context(arguments["library_slug"], request)))
    except BaseException as exc:
        detail = getattr(exc, "detail", None) or str(exc)
        connection.send(("error", f"{type(exc).__name__}: {detail}"))
    finally:
        connection.close()


async def _isolated_library_context(arguments: Dict[str, Any], context: ToolExecutionContext) -> Dict[str, Any]:
    process_context = multiprocessing.get_context("spawn")
    parent_connection, child_connection = process_context.Pipe(duplex=False)
    process = process_context.Process(target=_knowledge_search_worker, args=(child_connection, arguments), daemon=True)
    process.start()
    child_connection.close()
    deadline = time.monotonic() + 170
    try:
        while True:
            if context.cancellation_event.is_set():
                raise asyncio.CancelledError()
            if parent_connection.poll():
                status, value = parent_connection.recv()
                if status == "ok":
                    return value
                raise RuntimeError(value)
            if not process.is_alive():
                raise RuntimeError(f"Local retrieval process exited unexpectedly (exit code {process.exitcode}).")
            if time.monotonic() >= deadline:
                raise TimeoutError("Local retrieval exceeded its process timeout.")
            await asyncio.sleep(0.05)
    finally:
        parent_connection.close()
        if process.is_alive():
            process.terminate()
        process.join(timeout=2)


async def knowledge_search_handler(arguments: Dict[str, Any], context: ToolExecutionContext) -> Dict[str, Any]:
    payload = await _isolated_library_context(arguments, context)
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
        description="Retrieve structured evidence from Heimgeist's indexed local Knowledge store.",
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
