from __future__ import annotations

from typing import Any, Dict, List

from ... import models
from ...ollama_client import chat_stream_typed
from ..ollama_runtime import run_agent_loop
from ..registry import NativeToolProvider, ToolDefinition, ToolExecutionContext


MESSAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "role": {"type": "string"},
        "content": {"type": "string"},
        "images": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["role", "content"],
    "additionalProperties": True,
}


def _usage_dict(usage: Any) -> Dict[str, int]:
    return {
        "prompt_eval_count": usage.prompt_eval_count,
        "eval_count": usage.eval_count,
        "total_duration": usage.total_duration,
        "load_duration": usage.load_duration,
        "prompt_eval_duration": usage.prompt_eval_duration,
        "eval_duration": usage.eval_duration,
    }


def _context_text(blocks: List[Any]) -> str:
    parts: List[str] = []
    for block in blocks or []:
        if isinstance(block, str):
            text = block
        elif isinstance(block, dict):
            text = str(block.get("context_block") or block.get("text") or block.get("content") or "")
        else:
            text = str(block or "")
        if text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts)


def _collect_sources(blocks: List[Any]) -> List[Any]:
    sources: List[Any] = []
    seen = set()
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        for source in block.get("sources") or []:
            key = str(
                (source.get("url") or source.get("source_message_id") or source.get("doc_id") or source)
                if isinstance(source, dict) else source
            )
            if key and key not in seen:
                seen.add(key)
                sources.append(source)
    return sources


def _drop_superseded_trailing_user_rows(rows: List[Any]) -> List[Any]:
    if not rows or getattr(rows[-1], "role", None) != "user":
        return rows
    first_trailing_user = len(rows) - 1
    while first_trailing_user > 0 and getattr(rows[first_trailing_user - 1], "role", None) == "user":
        first_trailing_user -= 1
    if first_trailing_user == len(rows) - 1:
        return rows
    return [*rows[:first_trailing_user], rows[-1]]


def _append_context_to_last_user_message(messages: List[Dict[str, Any]], context_text: str) -> None:
    if not context_text:
        return
    for index in range(len(messages) - 1, -1, -1):
        if str(messages[index].get("role") or "") == "user":
            content = str(messages[index].get("content") or "").strip()
            messages[index]["content"] = f"{content}\n\n{context_text}".strip()
            return


async def chat_handler(arguments: Dict[str, Any], context: ToolExecutionContext) -> Dict[str, Any]:
    messages = [dict(item) for item in arguments.get("messages") or []]
    context_text = _context_text(arguments.get("context_blocks") or [])
    context_applied = False
    if context.session_id:
        db = context.db_factory()
        try:
            session = db.query(models.ChatSession).filter(models.ChatSession.session_id == context.session_id).first()
            rows = [] if not session else (
                db.query(models.ChatMessage)
                .filter(models.ChatMessage.session_pk == session.id)
                .order_by(models.ChatMessage.created_at.asc(), models.ChatMessage.id.asc())
                .all()
            )[-20:]
        finally:
            db.close()
        if rows:
            rows = _drop_superseded_trailing_user_rows(rows)
            from ... import main as main_module
            supports_vision = await main_module._model_supports_vision(arguments["model"])
            override_index = len(rows) - 1 if rows[-1].role == "user" else None
            prompt = rows[-1].content if override_index is not None else ""
            override_content = f"{prompt}\n\n{context_text}".strip() if context_text else None
            context_applied = bool(override_content)
            messages = await main_module._build_ollama_messages_from_rows(
                rows,
                request_model_supports_vision=supports_vision,
                vision_model=arguments.get("vision_model"),
                transcription_model=arguments.get("transcription_model"),
                override_user_row_index=override_index,
                override_user_content=override_content,
            )
    if context_text and not context_applied:
        _append_context_to_last_user_message(messages, context_text)
    system_prompt = str(arguments.get("system_prompt") or "").strip()
    if system_prompt:
        messages.insert(0, {"role": "system", "content": system_prompt})

    full_content = ""
    full_thinking = ""
    usage: Dict[str, int] = {}
    async for chunk in chat_stream_typed(
        arguments["model"],
        messages,
        options=arguments.get("generation_options") or {},
        think=bool(arguments.get("reasoning")),
        cancellation_event=context.cancellation_event,
    ):
        if chunk.thinking:
            full_thinking += chunk.thinking
            if context.show_thinking:
                await context.emit("model_thinking", {"text": chunk.thinking})
        if chunk.content:
            full_content += chunk.content
            await context.emit("model_token", {"text": chunk.content})
        if chunk.done:
            usage = _usage_dict(chunk.usage)
    return {
        "content": full_content,
        "sources": _collect_sources(arguments.get("context_blocks") or []),
        "usage": usage,
        "thinking_available": bool(full_thinking),
    }


def register_chat_tools(registry: NativeToolProvider) -> None:
    registry.register(ToolDefinition(
        name="heimgeist.chat",
        description="Generate an Ollama chat response from conversation history and supplied context blocks.",
        input_schema={
            "type": "object",
            "properties": {
                "model": {"type": "string", "minLength": 1},
                "messages": {"type": "array", "items": MESSAGE_SCHEMA},
                "system_prompt": {"type": "string"},
                "context_blocks": {"type": "array"},
                "attachments": {"type": "array"},
                "vision_model": {"type": ["string", "null"]},
                "transcription_model": {"type": ["string", "null"]},
                "generation_options": {"type": "object"},
                "stream": {"type": "boolean"},
                "reasoning": {"type": "boolean"},
            },
            "required": ["model", "messages"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "sources": {"type": "array"},
                "usage": {"type": "object"},
                "thinking_available": {"type": "boolean"},
            },
            "required": ["content", "sources", "usage", "thinking_available"],
            "additionalProperties": False,
        },
        handler=chat_handler,
        timeout_seconds=600,
        result_size_limit=180_000,
        llm_call=True,
    ))
    registry.register(ToolDefinition(
        name="heimgeist.agent",
        description="Run a bounded Ollama tool-calling loop over an approved native-tool allowlist.",
        input_schema={
            "type": "object",
            "properties": {
                "model": {"type": "string", "minLength": 1},
                "messages": {"type": "array", "items": MESSAGE_SCHEMA},
                "allowed_tool_names": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
                "maximum_rounds": {"type": "integer", "minimum": 1, "maximum": 8},
                "maximum_output_chars": {"type": "integer", "minimum": 1000, "maximum": 120000},
                "reasoning": {"type": "boolean"},
                "generation_options": {"type": "object"},
            },
            "required": ["model", "messages", "allowed_tool_names", "maximum_rounds"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "tool_summary": {"type": "array"},
                "usage": {"type": "object"},
                "rounds": {"type": "integer"},
            },
            "required": ["content", "tool_summary", "usage", "rounds"],
            "additionalProperties": False,
        },
        handler=run_agent_loop,
        timeout_seconds=600,
        result_size_limit=180_000,
    ))
