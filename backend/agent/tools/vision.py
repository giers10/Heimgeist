from __future__ import annotations

from typing import Any, Dict

from ..registry import NativeToolProvider, ToolDefinition, ToolExecutionContext


async def vision_analyze_handler(arguments: Dict[str, Any], _context: ToolExecutionContext) -> Dict[str, Any]:
    from ... import main as main_module

    prepared, _images, context_block = await main_module._prepare_chat_message_attachments(
        arguments.get("attachments") or [],
        request_model_supports_vision=False,
        vision_model=arguments.get("vision_model"),
        transcription_model=arguments.get("transcription_model"),
        persist_file_text=False,
    )
    return {
        "context_block": context_block,
        "attachments": [main_module._attachment_history_payload(item) for item in prepared],
        "sources": [],
    }


def register_vision_tools(registry: NativeToolProvider) -> None:
    registry.register(ToolDefinition(
        name="heimgeist.vision_analyze",
        description="Analyze chat attachments through Heimgeist's existing vision and extraction path.",
        input_schema={
            "type": "object",
            "properties": {
                "attachments": {"type": "array"},
                "vision_model": {"type": "string", "minLength": 1},
                "transcription_model": {"type": ["string", "null"]},
            },
            "required": ["attachments", "vision_model"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"context_block": {"type": "string"}, "attachments": {"type": "array"}, "sources": {"type": "array"}},
            "required": ["context_block", "attachments", "sources"],
            "additionalProperties": False,
        },
        handler=vision_analyze_handler,
        timeout_seconds=600,
        result_size_limit=140_000,
        llm_call=True,
    ))
