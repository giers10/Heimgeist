from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime
import json
import time
import uuid
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.orm import Session

from .. import models as chat_models
from ..chat_memory import safe_sync_chat_memory_for_session
from ..ollama_client import chat_typed
from .bindings import BindingError, resolve_bindings
from .events import EventWriter
from .models import WorkflowConfirmation, WorkflowNodeRun, WorkflowRevision, WorkflowRun
from .registry import NativeToolProvider, ToolDefinition, ToolExecutionContext
from .schemas import WorkflowGraph, WorkflowNode
from .validation import validate_workflow_graph


TERMINAL_NODE_STATUSES = {"completed", "failed", "skipped", "cancelled"}


def json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str))


def collect_sources(value: Any) -> List[Any]:
    output: List[Any] = []
    seen = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            if isinstance(item.get("sources"), list):
                for source in item["sources"]:
                    key = json.dumps(source, sort_keys=True, ensure_ascii=False, default=str)
                    if key not in seen:
                        seen.add(key)
                        output.append(source)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return output


def _merge_values(values: List[Any], deduplicate_by: Optional[str]) -> Dict[str, Any]:
    context_parts: List[str] = []
    sources: List[Any] = []
    items: List[Any] = []
    seen = set()
    for value in values:
        if value is None:
            continue
        if isinstance(value, dict):
            text = str(value.get("context_block") or value.get("content") or "").strip()
            if text:
                context_parts.append(text)
            candidates = value.get("sources") if isinstance(value.get("sources"), list) else []
            items.append(value)
        elif isinstance(value, list):
            candidates = value
            items.extend(value)
        else:
            candidates = []
            context_parts.append(str(value))
        for source in candidates:
            if deduplicate_by and isinstance(source, dict):
                key = str(source.get(deduplicate_by) or json.dumps(source, sort_keys=True, default=str))
            else:
                key = json.dumps(source, sort_keys=True, ensure_ascii=False, default=str)
            if key not in seen:
                seen.add(key)
                sources.append(source)
    return {"context_block": "\n\n".join(context_parts), "sources": sources, "items": items}


class WorkflowRuntime:
    def __init__(self, registry: NativeToolProvider, db_factory) -> None:
        self.registry = registry
        self.db_factory = db_factory
        self.active_runs: Dict[str, asyncio.Task] = {}
        self.cancellation_events: Dict[str, asyncio.Event] = {}
        self.confirmation_futures: Dict[str, asyncio.Future] = {}

    def start(self, run_id: str) -> None:
        if run_id in self.active_runs and not self.active_runs[run_id].done():
            return
        task = asyncio.create_task(self.execute(run_id))
        self.active_runs[run_id] = task
        task.add_done_callback(lambda _task: self.active_runs.pop(run_id, None))

    def cancel(self, run_id: str) -> bool:
        event = self.cancellation_events.get(run_id)
        if event:
            event.set()
        db = self.db_factory()
        try:
            run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
            if not run:
                return False
            run.cancellation_requested = True
            db.commit()
            return True
        finally:
            db.close()

    def resolve_confirmation(self, confirmation_id: str, approved: bool) -> bool:
        future = self.confirmation_futures.get(confirmation_id)
        if future and not future.done():
            future.set_result(bool(approved))
            return True
        return False

    async def execute(self, run_id: str) -> None:
        db = self.db_factory()
        try:
            run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
            if run is None:
                return
            revision = db.query(WorkflowRevision).filter(WorkflowRevision.id == run.workflow_revision_id).first()
            if revision is None:
                run.status = "failed"
                run.error_json = json.dumps({"message": "Workflow revision not found."})
                run.finished_at = datetime.utcnow()
                db.commit()
                return
            graph_raw = json.loads(revision.graph_json)
            inputs = json.loads(run.inputs_json or "{}")
            workflow_id = run.workflow_id
            selection_mode = run.selection_mode
            session_id = run.session_id
        finally:
            db.close()

        writer = EventWriter(run_id, workflow_id, self.db_factory)
        cancellation_event = asyncio.Event()
        self.cancellation_events[run_id] = cancellation_event
        graph = WorkflowGraph.model_validate(graph_raw)
        validation = validate_workflow_graph(graph_raw, self.registry)
        if not validation.valid:
            await self._fail_run(run_id, writer, "Workflow validation failed.", {"errors": [item.model_dump() for item in validation.errors]})
            return

        db = self.db_factory()
        try:
            run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
            run.status = "running"
            run.started_at = datetime.utcnow()
            db.commit()
        finally:
            db.close()
        await writer.emit("run_started", {"inputs": inputs})

        started = time.monotonic()
        counters = {"tool_calls": 0, "llm_calls": 0, "nodes": 0}
        counter_lock = asyncio.Lock()
        node_outputs: Dict[str, Any] = {}
        run_values = dict(inputs.get("run") or {})
        run_values["maximum_tool_calls_override"] = graph.limits.maximum_tool_calls
        workflow_inputs = dict(inputs.get("input") or {})
        values = {"input": workflow_inputs, "run": run_values, "nodes": {}}

        def consume_llm_call() -> None:
            counters["llm_calls"] += 1
            if counters["llm_calls"] > graph.limits.maximum_llm_calls:
                raise RuntimeError("Workflow exceeded maximum_llm_calls.")

        try:
            if (
                selection_mode == "direct"
                or run_values.get("compatibility_rag_enabled")
                or run_values.get("compatibility_web_enabled")
            ):
                existing_context_blocks = list(run_values.get("context_blocks") or [])
                compatibility_context_blocks = await self._prepare_compatibility_context(
                    run_id, workflow_id, run_values, workflow_inputs, writer, cancellation_event, counters, consume_llm_call
                )
                run_values["context_blocks"] = [*compatibility_context_blocks, *existing_context_blocks]
            await asyncio.wait_for(
                self._execute_graph(
                    run_id, workflow_id, session_id, selection_mode, graph, values, node_outputs,
                    writer, cancellation_event, counters, counter_lock, consume_llm_call,
                    explicit_user_action=bool(inputs.get("explicit_user_action")),
                ),
                timeout=graph.limits.maximum_runtime_seconds,
            )
            if cancellation_event.is_set():
                raise asyncio.CancelledError()
            output_nodes = [node for node in graph.nodes if node.type == "output" and node.id in node_outputs]
            if not output_nodes:
                raise RuntimeError("Workflow completed without an output node result.")
            final_output = node_outputs[output_nodes[-1].id]
            if json_size(final_output) > graph.limits.maximum_result_chars:
                raise RuntimeError("Workflow final output exceeded maximum_result_chars.")
            await self._persist_assistant_message(run_id, final_output, counters, time.monotonic() - started)
            db = self.db_factory()
            try:
                run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
                run.status = "completed"
                run.outputs_json = json.dumps(final_output, ensure_ascii=False, default=str)
                run.metrics_json = json.dumps({**counters, "duration_seconds": time.monotonic() - started})
                run.finished_at = datetime.utcnow()
                db.commit()
            finally:
                db.close()
            await writer.emit("run_completed", {"output": final_output, "metrics": {**counters, "duration_seconds": time.monotonic() - started}})
        except asyncio.CancelledError:
            db = self.db_factory()
            try:
                run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
                run.status = "cancelled"
                run.finished_at = datetime.utcnow()
                run.metrics_json = json.dumps({**counters, "duration_seconds": time.monotonic() - started})
                db.commit()
            finally:
                db.close()
            await writer.emit("run_cancelled", {"metrics": counters})
        except Exception as exc:
            await self._fail_run(run_id, writer, f"{type(exc).__name__}: {exc}", {"metrics": counters})
        finally:
            self.cancellation_events.pop(run_id, None)

    async def _prepare_compatibility_context(
        self, run_id: str, workflow_id: str, run_values: Dict[str, Any], workflow_inputs: Dict[str, Any],
        writer: EventWriter, cancellation_event: asyncio.Event, counters: Dict[str, int], consume_llm_call,
    ) -> List[Any]:
        blocks: List[Any] = []
        context = ToolExecutionContext(
            run_id=run_id, workflow_id=workflow_id, node_id="compatibility", session_id=run_values.get("session_id"),
            selection_mode="direct", explicit_user_action=True,
            emit=lambda event_type, payload: writer.emit(event_type, payload, "compatibility"),
            cancellation_event=cancellation_event, db_factory=self.db_factory, registry=self.registry,
            run_values=run_values, consume_llm_call=consume_llm_call,
        )
        prompt = str(workflow_inputs.get("prompt") or "")
        rag_enabled = bool(run_values.get("compatibility_rag_enabled", run_values.get("manual_library_enabled")))
        web_enabled = bool(run_values.get("compatibility_web_enabled", run_values.get("web_search_enabled")))
        if run_values.get("library_slug") and rag_enabled and prompt:
            try:
                counters["tool_calls"] += 1
                await writer.emit("tool_started", {"tool": "heimgeist.knowledge_search"}, "compatibility")
                result = await self.registry.call_tool("heimgeist.knowledge_search", {
                    "prompt": prompt, "library_slug": run_values["library_slug"], "top_k": 5,
                    "context_character_budget": 12000, "embedding_model": None,
                }, context)
                blocks.append(result)
                await writer.emit("tool_result", {"tool": "heimgeist.knowledge_search", "result": result}, "compatibility")
            except Exception as exc:
                await writer.emit("tool_result", {"tool": "heimgeist.knowledge_search", "error": str(exc)}, "compatibility")
        if web_enabled and prompt:
            try:
                query_args = {"prompt": prompt, "model": run_values["chat_model"], "messages": run_values.get("messages") or []}
                counters["tool_calls"] += 1
                queries = await self.registry.call_tool("heimgeist.web_generate_queries", query_args, context)
                counters["tool_calls"] += 1
                search = await self.registry.call_tool("heimgeist.web_search", {
                    "query": None, "queries": queries["queries"], "engines": run_values.get("searx_engines") or [],
                    "maximum_results": 14, "searx_url": run_values.get("searx_url"),
                }, context)
                counters["tool_calls"] += 1
                fetched = await self.registry.call_tool("heimgeist.web_fetch", {"url": None, "urls": search["results"], "maximum_pages": 6}, context)
                counters["tool_calls"] += 1
                ranked = await self.registry.call_tool("heimgeist.web_rerank", {
                    "prompt": prompt, "pages": fetched["pages"], "model": run_values["chat_model"], "rerank_model": None,
                    "context_excerpt": "", "maximum_results": 6, "minimum_score": 55,
                }, context)
                blocks.append(ranked)
                await writer.emit("tool_result", {"tool": "compatibility_web", "result": ranked}, "compatibility")
            except Exception as exc:
                await writer.emit("tool_result", {"tool": "compatibility_web", "error": str(exc)}, "compatibility")
        return blocks

    async def _execute_graph(
        self, run_id: str, workflow_id: str, session_id: Optional[str], selection_mode: str,
        graph: WorkflowGraph, values: Dict[str, Any], node_outputs: Dict[str, Any], writer: EventWriter,
        cancellation_event: asyncio.Event, counters: Dict[str, int], counter_lock: asyncio.Lock, consume_llm_call,
        explicit_user_action: bool,
    ) -> None:
        nodes = {node.id: node for node in graph.nodes}
        incoming = defaultdict(list)
        outgoing = defaultdict(list)
        for edge in graph.edges:
            incoming[edge.target].append(edge)
            outgoing[edge.source].append(edge)
        statuses = {node_id: "pending" for node_id in nodes}
        semaphore = asyncio.Semaphore(graph.limits.maximum_concurrency)

        def edge_active(edge) -> bool:
            source_node = nodes[edge.source]
            if statuses[edge.source] != "completed":
                return False
            if source_node.type != "condition":
                return True
            result = bool((node_outputs.get(edge.source) or {}).get("result"))
            return edge.source_handle in {"output", "true" if result else "false"}

        async def execute_one(node: WorkflowNode) -> None:
            async with semaphore:
                if cancellation_event.is_set():
                    raise asyncio.CancelledError()
                statuses[node.id] = "running"
                counters["nodes"] += 1
                await self._create_node_run(run_id, node.id, "running")
                await writer.emit("node_started", {"type": node.type}, node.id)
                started = time.monotonic()
                try:
                    output, resolved_inputs = await self._execute_node(
                        run_id, workflow_id, session_id, selection_mode, node, values, writer,
                        cancellation_event, counters, counter_lock, consume_llm_call, explicit_user_action,
                    )
                    if json_size(output) > graph.limits.maximum_result_chars:
                        raise RuntimeError(f"Node {node.id} output exceeded maximum_result_chars.")
                    statuses[node.id] = "completed"
                    node_outputs[node.id] = output
                    values["nodes"][node.id] = {"output": output}
                    await self._finish_node_run(run_id, node.id, "completed", resolved_inputs, output, None, time.monotonic() - started)
                    for source in collect_sources(output):
                        await writer.emit("source_found", {"source": source}, node.id)
                    await writer.emit("node_completed", {"output": output, "duration_seconds": time.monotonic() - started}, node.id)
                except Exception as exc:
                    statuses[node.id] = "failed"
                    error = {"type": type(exc).__name__, "message": str(exc), "node_id": node.id}
                    await self._finish_node_run(run_id, node.id, "failed", None, None, error, time.monotonic() - started)
                    await writer.emit("node_failed", error, node.id)
                    raise

        while True:
            if cancellation_event.is_set():
                raise asyncio.CancelledError()
            pending = [node_id for node_id, status in statuses.items() if status == "pending"]
            if not pending:
                break
            progressed = False
            ready: List[WorkflowNode] = []
            for node_id in pending:
                dependencies = incoming[node_id]
                if any(statuses[edge.source] not in TERMINAL_NODE_STATUSES for edge in dependencies):
                    continue
                if dependencies and not any(edge_active(edge) for edge in dependencies):
                    statuses[node_id] = "skipped"
                    await self._create_node_run(run_id, node_id, "skipped")
                    await writer.emit("node_completed", {"skipped": True}, node_id)
                    progressed = True
                    continue
                ready.append(nodes[node_id])
            if ready:
                progressed = True
                for node in ready:
                    statuses[node.id] = "queued"
                    await writer.emit("node_queued", {"type": node.type}, node.id)
                await asyncio.gather(*(execute_one(node) for node in ready))
            if not progressed:
                raise RuntimeError("Workflow scheduler could not resolve remaining nodes.")

    async def _execute_node(
        self, run_id: str, workflow_id: str, session_id: Optional[str], selection_mode: str,
        node: WorkflowNode, values: Dict[str, Any], writer: EventWriter, cancellation_event: asyncio.Event,
        counters: Dict[str, int], counter_lock: asyncio.Lock, consume_llm_call, explicit_user_action: bool,
    ) -> tuple[Any, Any]:
        if node.type == "input":
            return dict(values["input"]), dict(values["input"])
        if node.type == "output":
            resolved = resolve_bindings(node.config.get("value", {}), values)
            return resolved, resolved
        if node.type == "template":
            template = str(node.config.get("template") or "")
            resolved = resolve_bindings(template, values)
            return resolved, {"template": resolved}
        if node.type == "select":
            resolved = resolve_bindings(node.config.get("value"), values)
            return resolved, {"value": resolved}
        if node.type == "merge":
            resolved_values = []
            for item in node.config.get("values") or []:
                try:
                    resolved_values.append(resolve_bindings(item, values))
                except BindingError:
                    if not node.config.get("allow_missing"):
                        raise
            output = _merge_values(resolved_values, node.config.get("deduplicate_by"))
            return output, {"values": resolved_values}
        if node.type == "limit":
            resolved = resolve_bindings(node.config.get("value"), values)
            maximum = max(1, int(node.config.get("maximum_chars") or 12000))
            output = self._limit_value(resolved, maximum)
            return output, {"value": resolved, "maximum_chars": maximum}
        if node.type == "condition":
            left = resolve_bindings(node.config.get("value"), values)
            right = resolve_bindings(node.config.get("compare_to"), values)
            result = self._condition(node.config.get("operation"), left, right)
            return {"result": result, "value": left}, {"value": left, "compare_to": right}
        if node.type == "prompt":
            return await self._execute_prompt(node, values, writer, consume_llm_call, cancellation_event)
        if node.type in {"tool", "agent"}:
            tool_name = "heimgeist.agent" if node.type == "agent" else str(node.config.get("tool") or "")
            arguments_config = node.config.get("arguments") or node.config
            arguments = resolve_bindings(arguments_config, values)
            if tool_name == "heimgeist.chat":
                run_context_blocks = values["run"].get("context_blocks") or []
                node_context_blocks = arguments.get("context_blocks") or []
                if run_context_blocks:
                    arguments["context_blocks"] = [*node_context_blocks, *run_context_blocks]
            async with counter_lock:
                counters["tool_calls"] += 1
                maximum = int(values.get("run", {}).get("maximum_tool_calls_override") or 0)
                if maximum and counters["tool_calls"] > maximum:
                    raise RuntimeError("Workflow exceeded maximum tool calls.")
            definition = self.registry.get_tool(tool_name)

            async def request_confirmation(tool: ToolDefinition, tool_arguments: Dict[str, Any]) -> bool:
                if explicit_user_action or selection_mode != "automatic":
                    return True
                return await self._request_confirmation(run_id, workflow_id, node.id, tool, tool_arguments, writer)

            context = ToolExecutionContext(
                run_id=run_id, workflow_id=workflow_id, node_id=node.id, session_id=session_id,
                selection_mode=selection_mode, explicit_user_action=explicit_user_action,
                emit=lambda event_type, payload: writer.emit(event_type, payload, node.id),
                cancellation_event=cancellation_event, db_factory=self.db_factory, registry=self.registry,
                run_values=values["run"], request_confirmation=request_confirmation,
                consume_llm_call=consume_llm_call, show_thinking=bool(values["run"].get("show_thinking")),
            )
            if definition.requires_confirmation:
                approved = await request_confirmation(definition, arguments)
                if not approved:
                    return {"status": "rejected", "confirmed": False, "result": {}}, arguments
            await writer.emit("tool_started", {"tool": tool_name, "arguments": arguments}, node.id)
            result = await self.registry.call_tool(tool_name, arguments, context)
            await writer.emit("tool_result", {"tool": tool_name, "result": result}, node.id)
            return result, arguments
        raise RuntimeError(f"Unsupported node type: {node.type}")

    async def _execute_prompt(self, node: WorkflowNode, values: Dict[str, Any], writer: EventWriter, consume_llm_call, cancellation_event) -> tuple[Any, Any]:
        source = node.config.get("model_source")
        if source == "fixed":
            model = str(node.config.get("model") or "")
        elif source == "router_model":
            model = str(values["run"].get("router_model") or values["run"].get("chat_model") or "")
        elif source == "workflow_variable":
            model = str(resolve_bindings(node.config.get("model"), values) or "")
        else:
            model = str(values["run"].get("chat_model") or "")
        if not model:
            raise RuntimeError(f"Prompt node {node.id} has no available model.")
        system = resolve_bindings(str(node.config.get("system_template") or ""), values)
        user = resolve_bindings(str(node.config.get("user_template") or ""), values)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        output_mode = node.config.get("output_mode") or "text"
        format_schema = node.config.get("json_schema") if output_mode in {"json", "score", "decision"} else None
        consume_llm_call()
        result = await chat_typed(
            model, messages,
            format=format_schema,
            options={"temperature": float(node.config.get("temperature") or 0), **(node.config.get("generation_options") or {})},
            cancellation_event=cancellation_event,
        )
        if result.thinking and values["run"].get("show_thinking"):
            await writer.emit("model_thinking", {"text": result.thinking}, node.id)
        if result.content:
            await writer.emit("model_token", {"text": result.content}, node.id)
        if output_mode == "text":
            output: Any = {"content": result.content}
        else:
            try:
                output = json.loads(result.content)
            except Exception:
                if not node.config.get("json_repair"):
                    raise RuntimeError(f"Prompt node {node.id} returned invalid JSON.")
                consume_llm_call()
                repair = await chat_typed(
                    model,
                    [{"role": "user", "content": f"Repair this into valid JSON matching the schema. Return JSON only.\n{result.content}"}],
                    format=format_schema,
                    options={"temperature": 0},
                    cancellation_event=cancellation_event,
                )
                output = json.loads(repair.content)
        return output, {"model": model, "messages": messages, "output_mode": output_mode}

    async def _request_confirmation(
        self, run_id: str, workflow_id: str, node_id: str, definition: ToolDefinition,
        arguments: Dict[str, Any], writer: EventWriter,
    ) -> bool:
        confirmation_id = str(uuid.uuid4())
        db = self.db_factory()
        try:
            db.add(WorkflowConfirmation(
                id=confirmation_id, workflow_run_id=run_id, node_id=node_id, tool_name=definition.name,
                request_json=json.dumps(arguments, ensure_ascii=False, default=str),
            ))
            run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
            run.status = "waiting_confirmation"
            db.commit()
        finally:
            db.close()
        await writer.emit("confirmation_required", {
            "confirmation_id": confirmation_id, "tool": definition.name, "risk_level": definition.risk_level,
            "arguments": arguments,
        }, node_id)
        future = asyncio.get_running_loop().create_future()
        self.confirmation_futures[confirmation_id] = future
        try:
            approved = bool(await future)
        finally:
            self.confirmation_futures.pop(confirmation_id, None)
        db = self.db_factory()
        try:
            confirmation = db.query(WorkflowConfirmation).filter(WorkflowConfirmation.id == confirmation_id).first()
            if confirmation:
                confirmation.status = "approved" if approved else "rejected"
                confirmation.response_json = json.dumps({"approved": approved})
                confirmation.resolved_at = datetime.utcnow()
            run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
            if run:
                run.status = "running"
            db.commit()
        finally:
            db.close()
        return approved

    @staticmethod
    def _condition(operation: str, left: Any, right: Any) -> bool:
        if operation == "equals": return left == right
        if operation == "not_equals": return left != right
        if operation == "exists": return left not in (None, "", [], {})
        if operation == "greater_than": return left > right
        if operation == "less_than": return left < right
        if operation == "contains": return right in left
        if operation == "array_not_empty": return isinstance(left, list) and bool(left)
        if operation == "score_at_least": return float(left or 0) >= float(right or 0)
        raise RuntimeError(f"Unsupported condition operation: {operation}")

    @staticmethod
    def _limit_value(value: Any, maximum: int) -> Any:
        if isinstance(value, str):
            return value[:maximum]
        if isinstance(value, dict):
            result = dict(value)
            if "context_block" in result:
                result["context_block"] = str(result["context_block"])[:maximum]
            result.pop("items", None)
            return result
        if isinstance(value, list):
            result = []
            for item in value:
                if json_size(result + [item]) > maximum:
                    break
                result.append(item)
            return result
        return value

    async def _create_node_run(self, run_id: str, node_id: str, status: str) -> None:
        db = self.db_factory()
        try:
            row = db.query(WorkflowNodeRun).filter(
                WorkflowNodeRun.workflow_run_id == run_id,
                WorkflowNodeRun.node_id == node_id,
                WorkflowNodeRun.attempt == 1,
            ).first()
            if row is None:
                row = WorkflowNodeRun(workflow_run_id=run_id, node_id=node_id, attempt=1)
                db.add(row)
            row.status = status
            if status == "running":
                row.started_at = datetime.utcnow()
            if status == "skipped":
                row.finished_at = datetime.utcnow()
            db.commit()
        finally:
            db.close()

    async def _finish_node_run(self, run_id: str, node_id: str, status: str, resolved: Any, output: Any, error: Any, duration: float) -> None:
        db = self.db_factory()
        try:
            row = db.query(WorkflowNodeRun).filter(
                WorkflowNodeRun.workflow_run_id == run_id,
                WorkflowNodeRun.node_id == node_id,
                WorkflowNodeRun.attempt == 1,
            ).first()
            if row:
                row.status = status
                row.resolved_inputs_json = json.dumps(resolved, ensure_ascii=False, default=str) if resolved is not None else None
                row.outputs_json = json.dumps(output, ensure_ascii=False, default=str) if output is not None else None
                row.error_json = json.dumps(error, ensure_ascii=False, default=str) if error is not None else None
                row.finished_at = datetime.utcnow()
                row.metrics_json = json.dumps({"duration_seconds": duration})
                db.commit()
        finally:
            db.close()

    async def _persist_assistant_message(self, run_id: str, output: Any, counters: Dict[str, int], duration: float) -> None:
        content = output.get("content") if isinstance(output, dict) else str(output)
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Workflow output did not contain assistant content.")
        sources = collect_sources(output)
        usage = output.get("usage") if isinstance(output, dict) and isinstance(output.get("usage"), dict) else {}
        db = self.db_factory()
        try:
            run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
            if run is None or not run.session_id:
                return
            session = db.query(chat_models.ChatSession).filter(chat_models.ChatSession.session_id == run.session_id).first()
            if session is None:
                raise RuntimeError("Chat session no longer exists.")
            db.add(chat_models.ChatMessage(
                session_pk=session.id, role="assistant", content=content,
                sources_json=json.dumps(sources, ensure_ascii=False, default=str),
                workflow_id=run.workflow_id, workflow_revision_id=run.workflow_revision_id, workflow_run_id=run.id,
                agent_summary_json=json.dumps({
                    "tool_calls": counters["tool_calls"],
                    "llm_calls": counters["llm_calls"],
                    "duration_seconds": duration,
                    "selection_mode": run.selection_mode,
                }),
                usage_json=json.dumps(usage),
            ))
            db.commit()
            safe_sync_chat_memory_for_session(db, run.session_id)
        finally:
            db.close()

    async def _fail_run(self, run_id: str, writer: EventWriter, message: str, details: Dict[str, Any]) -> None:
        error = {"message": message, **details}
        db = self.db_factory()
        try:
            run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
            if run:
                run.status = "failed"
                run.error_json = json.dumps(error, ensure_ascii=False, default=str)
                run.finished_at = datetime.utcnow()
                db.commit()
        finally:
            db.close()
        await writer.emit("run_failed", error)
