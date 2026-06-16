from __future__ import annotations

from typing import Any, Dict

from ...local_rag import CreateWebsiteRequest, create_library_website
from ..registry import NativeToolProvider, ToolDefinition, ToolExecutionContext


async def save_message_handler(arguments: Dict[str, Any], context: ToolExecutionContext) -> Dict[str, Any]:
    from ... import main as main_module

    db = context.db_factory()
    try:
        result = await main_module._save_message_to_knowledge_impl(
            arguments["library_slug"],
            arguments["message_id"],
            db,
            title=arguments.get("title"),
            content=arguments.get("edited_content"),
            saved_by="agent" if context.selection_mode == "automatic" else "user",
        )
    finally:
        db.close()
    return {"status": "saved", "confirmed": True, "result": result}


async def save_website_handler(arguments: Dict[str, Any], context: ToolExecutionContext) -> Dict[str, Any]:
    result = await create_library_website(
        arguments["library_slug"],
        CreateWebsiteRequest(url=arguments["url"], title=arguments.get("title")),
    )
    return {"status": "saved", "confirmed": True, "result": result}


SAVE_OUTPUT = {
    "type": "object",
    "properties": {
        "status": {"type": "string"},
        "confirmed": {"type": "boolean"},
        "result": {"type": "object"},
    },
    "required": ["status", "confirmed", "result"],
    "additionalProperties": False,
}


def register_saving_tools(registry: NativeToolProvider) -> None:
    registry.register(ToolDefinition(
        name="heimgeist.save_message_to_knowledge",
        description="Save a stable chat message snapshot to Heimgeist's local Knowledge store.",
        input_schema={
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "minLength": 1},
                "library_slug": {"type": "string", "minLength": 1},
                "title": {"type": ["string", "null"]},
                "edited_content": {"type": ["string", "null"]},
            },
            "required": ["message_id", "library_slug"],
            "additionalProperties": False,
        },
        output_schema=SAVE_OUTPUT,
        handler=save_message_handler,
        risk_level="write",
        requires_confirmation=True,
        timeout_seconds=180,
    ))
    registry.register(ToolDefinition(
        name="heimgeist.save_website_to_knowledge",
        description="Fetch and save a durable website snapshot to Heimgeist's local Knowledge store.",
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "minLength": 1},
                "library_slug": {"type": "string", "minLength": 1},
                "title": {"type": ["string", "null"]},
            },
            "required": ["url", "library_slug"],
            "additionalProperties": False,
        },
        output_schema=SAVE_OUTPUT,
        handler=save_website_handler,
        risk_level="write",
        requires_confirmation=True,
        timeout_seconds=180,
    ))
