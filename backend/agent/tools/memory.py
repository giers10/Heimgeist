from __future__ import annotations

from typing import Any, Dict

from ...chat_memory import build_chat_memory_context, ensure_chat_memory_schema
from ..registry import NativeToolProvider, ToolDefinition, ToolExecutionContext


def _int_argument(arguments: Dict[str, Any], key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(arguments.get(key) or default)
    except Exception:
        value = default
    return max(minimum, min(value, maximum))


def _memory_hit(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "session_id": item.get("session_id"),
        "session_name": item.get("session_name"),
        "user_message_id": item.get("user_message_id"),
        "assistant_message_id": item.get("assistant_message_id"),
        "created_at": item.get("created_at"),
        "score": round(float(item.get("_score") or item.get("score") or 0), 4),
        "user_excerpt": item.get("user_content"),
        "assistant_excerpt": item.get("assistant_content"),
    }


async def chat_memory_search_handler(arguments: Dict[str, Any], context: ToolExecutionContext) -> Dict[str, Any]:
    top_k = _int_argument(arguments, "top_k", 6, 1, 20)
    context_character_budget = _int_argument(arguments, "context_character_budget", 10_000, 500, 60_000)
    mode = str(arguments.get("mode") or "search").strip().lower()
    if mode not in {"search", "recent"}:
        mode = "search"
    exclude_current_session = bool(arguments.get("exclude_current_session", True))
    exclude_session_id = context.session_id if exclude_current_session else None

    db = context.db_factory()
    try:
        ensure_chat_memory_schema(db.get_bind())
        payload = build_chat_memory_context(
            db,
            str(arguments.get("prompt") or ""),
            exclude_session_id=exclude_session_id,
            top_k=top_k,
            context_character_budget=context_character_budget,
            mode=mode,
        )
    finally:
        db.close()

    sources = payload.get("sources") or []
    return {
        "hits": [_memory_hit(item) for item in (payload.get("hits") or [])],
        "context_block": str(payload.get("context_block") or ""),
        "sources": sources,
        "scores": [source.get("score") for source in sources if isinstance(source, dict)],
    }


def register_memory_tools(registry: NativeToolProvider) -> None:
    registry.register(ToolDefinition(
        name="heimgeist.chat_memory_search",
        description=(
            "Retrieve relevant completed turns from past Heimgeist chats. Use only when a workflow or agent "
            "decides previous chats are needed, such as questions about earlier conversations, prior decisions, "
            "or remembered discussion context; it does not run automatically."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "minLength": 1},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                "context_character_budget": {"type": "integer", "minimum": 500, "maximum": 60000},
                "mode": {"type": "string", "enum": ["search", "recent"]},
                "exclude_current_session": {"type": "boolean"},
            },
            "required": ["prompt"],
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
        handler=chat_memory_search_handler,
        timeout_seconds=20,
        result_size_limit=100_000,
    ))
