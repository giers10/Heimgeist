from __future__ import annotations

import json
from typing import Any, Dict, List

from ..ollama_client import chat_typed
from .registry import NativeToolProvider, ToolExecutionContext


def _usage_dict(usage: Any) -> Dict[str, int]:
    return {
        "prompt_eval_count": usage.prompt_eval_count,
        "eval_count": usage.eval_count,
        "total_duration": usage.total_duration,
        "load_duration": usage.load_duration,
        "prompt_eval_duration": usage.prompt_eval_duration,
        "eval_duration": usage.eval_duration,
    }


async def run_agent_loop(arguments: Dict[str, Any], context: ToolExecutionContext) -> Dict[str, Any]:
    registry = context.registry
    if registry is None:
        raise RuntimeError("Agent tool registry is unavailable.")

    allowed_names = []
    for name in arguments.get("allowed_tool_names") or []:
        if name == "heimgeist.agent":
            continue
        registry.get_tool(name)
        allowed_names.append(name)
    tools = [registry.get_tool(name).ollama_manifest() for name in allowed_names]
    messages: List[Dict[str, Any]] = [dict(item) for item in arguments.get("messages") or []]
    maximum_rounds = max(1, min(int(arguments.get("maximum_rounds") or 4), 8))
    maximum_output_chars = max(1000, min(int(arguments.get("maximum_output_chars") or 60_000), 120_000))
    tool_summary: List[Dict[str, Any]] = []
    usage_totals: Dict[str, int] = {}

    for round_index in range(maximum_rounds):
        if context.cancellation_event.is_set():
            raise RuntimeError("Agent execution was cancelled.")
        if context.consume_llm_call:
            context.consume_llm_call()
        result = await chat_typed(
            arguments["model"],
            messages,
            options=arguments.get("generation_options") or {},
            tools=tools or None,
            think=bool(arguments.get("reasoning")),
            cancellation_event=context.cancellation_event,
        )
        for key, value in _usage_dict(result.usage).items():
            usage_totals[key] = usage_totals.get(key, 0) + int(value or 0)
        if result.thinking and context.show_thinking:
            await context.emit("model_thinking", {"text": result.thinking, "round": round_index + 1})

        if not result.tool_calls:
            content = result.content[:maximum_output_chars]
            return {
                "content": content,
                "tool_summary": tool_summary,
                "usage": usage_totals,
                "rounds": round_index + 1,
            }

        assistant_message: Dict[str, Any] = {
            "role": "assistant",
            "content": result.content or "",
            "tool_calls": [call.raw for call in result.tool_calls],
        }
        messages.append(assistant_message)
        for call in result.tool_calls:
            if call.name not in allowed_names:
                tool_result = {"error": f"Tool is not allowed: {call.name}"}
            else:
                definition = registry.get_tool(call.name)
                approved = True
                if definition.requires_confirmation and context.request_confirmation:
                    approved = await context.request_confirmation(definition, call.arguments)
                if approved:
                    await context.emit("tool_started", {"tool": call.name, "arguments": call.arguments, "agent_round": round_index + 1})
                    try:
                        tool_result = await registry.call_tool(call.name, call.arguments, context)
                    except Exception as exc:
                        tool_result = {"error": f"{type(exc).__name__}: {exc}"}
                    await context.emit("tool_result", {"tool": call.name, "result": tool_result, "agent_round": round_index + 1})
                else:
                    tool_result = {"status": "rejected", "confirmed": False}
            tool_summary.append({"tool": call.name, "arguments": call.arguments, "result": tool_result})
            messages.append({
                "role": "tool",
                "tool_name": call.name,
                "content": json.dumps(tool_result, ensure_ascii=False, default=str),
            })

    return {
        "content": "I could not complete the task within the configured agent round limit.",
        "tool_summary": tool_summary,
        "usage": usage_totals,
        "rounds": maximum_rounds,
    }
