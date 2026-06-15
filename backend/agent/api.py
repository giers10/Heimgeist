from __future__ import annotations

import asyncio
from datetime import datetime
import hashlib
import json
import re
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import models as chat_models
from ..chat_memory import build_chat_memory_context, safe_sync_chat_memory_for_session
from ..database import Base, SessionLocal, engine
from .events import EventWriter
from .models import (
    WorkflowConfirmation,
    WorkflowDefinition,
    WorkflowEvent,
    WorkflowNodeRun,
    WorkflowRevision,
    WorkflowRun,
)
from .registry import NativeToolProvider
from .router import select_workflow
from .runtime import WorkflowRuntime
from .schemas import (
    ConfirmationResponseRequest,
    RouterSelectRequest,
    WorkflowCreateRequest,
    WorkflowRevisionRequest,
    WorkflowRunRequest,
    WorkflowUpdateRequest,
    WorkflowValidateRequest,
)
from .tools import register_native_tools
from .validation import validate_workflow_graph
from .workflows import seed_builtin_workflows


router = APIRouter(tags=["agent-workflows"])
registry = NativeToolProvider()
register_native_tools(registry)
runtime = WorkflowRuntime(registry, SessionLocal)


def _json(value: Optional[str], fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except Exception:
        return fallback


def _checksum(graph: Dict[str, Any]) -> str:
    canonical = json.dumps(graph, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return slug[:90] or f"workflow-{uuid.uuid4().hex[:8]}"


_REMEMBER_CONTENT_PATTERNS = [
    re.compile(r"^\s*(?:please\s+)?remember\s+this\s*[:\-–—]\s*(.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\s*(?:please\s+)?remember\s+that\s+(.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\s*(?:please\s+)?save\s+this\s*[:\-–—]\s*(.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\s*(?:please\s+)?save\s+that\s+(.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\s*(?:bitte\s+)?merk\s+dir\s+das\s*[:\-–—]\s*(.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\s*(?:bitte\s+)?merk\s+dir\s+(.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\s*(?:bitte\s+)?speicher(?:e)?\s+(?:das|dies|diese|diesen)\s*[:\-–—]\s*(.+)$", re.IGNORECASE | re.DOTALL),
]


def _extract_remember_content(message: str) -> Optional[str]:
    text = str(message or "").strip()
    for pattern in _REMEMBER_CONTENT_PATTERNS:
        match = pattern.match(text)
        if match:
            content = match.group(1).strip()
            return content or None
    return None


def _clean_search_queries(raw: Any, fallback: str) -> list[str]:
    values = raw if isinstance(raw, list) else [raw]
    cleaned: list[str] = []
    seen = set()
    for item in values:
        text = " ".join(str(item or "").split()).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text[:300])
        if len(cleaned) >= 5:
            break
    fallback_text = " ".join(str(fallback or "").split()).strip()
    if fallback_text and not cleaned:
        cleaned.append(fallback_text[:300])
    return cleaned


def _resolve_web_search_inputs(sample_inputs: Dict[str, Any], message: str) -> tuple[str, list[str]]:
    raw_queries = sample_inputs.get("web_search_queries")
    if raw_queries is None:
        raw_queries = sample_inputs.get("search_queries")
    raw_query = sample_inputs.get("web_search_query") or sample_inputs.get("search_query")
    queries = _clean_search_queries(raw_queries if raw_queries is not None else raw_query, message)
    if raw_query:
        raw_query_text = " ".join(str(raw_query).split()).strip()
        if raw_query_text and raw_query_text.casefold() not in {item.casefold() for item in queries}:
            queries.insert(0, raw_query_text[:300])
    queries = queries[:5]
    query = queries[0] if queries else str(message or "")[:300]
    return query, queries or [query]


def initialize_agent_system() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.query(WorkflowRun).filter(WorkflowRun.status.in_(["queued", "running", "waiting_confirmation"])).update(
            {
                WorkflowRun.status: "interrupted",
                WorkflowRun.error_json: json.dumps({"message": "Backend restarted before the workflow completed."}),
                WorkflowRun.finished_at: datetime.utcnow(),
            },
            synchronize_session=False,
        )
        db.commit()
        seed_builtin_workflows(db)
    finally:
        db.close()


def _workflow_payload(workflow: WorkflowDefinition, db: Session, *, include_graph: bool = False) -> Dict[str, Any]:
    revision = None
    if workflow.current_revision_id:
        revision = db.query(WorkflowRevision).filter(WorkflowRevision.id == workflow.current_revision_id).first()
    payload = {
        "id": workflow.id,
        "slug": workflow.slug,
        "name": workflow.name,
        "description": workflow.description,
        "current_revision_id": workflow.current_revision_id,
        "current_version": revision.version if revision else None,
        "built_in": workflow.built_in,
        "enabled": workflow.enabled,
        "routing_description": workflow.routing_description,
        "routing_examples": _json(workflow.routing_examples_json, []),
        "estimated_cost_class": workflow.estimated_cost_class,
        "required_capabilities": _json(workflow.required_capabilities_json, []),
        "created_at": workflow.created_at.isoformat() if workflow.created_at else None,
        "updated_at": workflow.updated_at.isoformat() if workflow.updated_at else None,
    }
    if include_graph:
        payload["graph"] = _json(revision.graph_json if revision else None, {})
        payload["revisions"] = [
            {
                "id": item.id, "version": item.version, "created_at": item.created_at.isoformat(),
                "created_by": item.created_by, "trusted": item.trusted, "checksum": item.checksum,
            }
            for item in db.query(WorkflowRevision).filter(WorkflowRevision.workflow_id == workflow.id).order_by(WorkflowRevision.version.desc()).all()
        ]
    return payload


def _find_workflow(db: Session, workflow_id: str) -> WorkflowDefinition:
    workflow = db.query(WorkflowDefinition).filter(
        (WorkflowDefinition.id == workflow_id) | (WorkflowDefinition.slug == workflow_id)
    ).first()
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    return workflow


def _run_payload(run: WorkflowRun, db: Session) -> Dict[str, Any]:
    node_runs = db.query(WorkflowNodeRun).filter(WorkflowNodeRun.workflow_run_id == run.id).order_by(WorkflowNodeRun.started_at.asc()).all()
    confirmations = db.query(WorkflowConfirmation).filter(WorkflowConfirmation.workflow_run_id == run.id).order_by(WorkflowConfirmation.created_at.asc()).all()
    return {
        "id": run.id, "workflow_id": run.workflow_id, "workflow_revision_id": run.workflow_revision_id,
        "session_id": run.session_id, "user_message_id": run.user_message_id, "status": run.status,
        "selection_mode": run.selection_mode, "inputs": _json(run.inputs_json, {}), "outputs": _json(run.outputs_json, None),
        "error": _json(run.error_json, None), "metrics": _json(run.metrics_json, {}),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "cancellation_requested": run.cancellation_requested,
        "node_runs": [{
            "id": item.id, "node_id": item.node_id, "attempt": item.attempt, "status": item.status,
            "resolved_inputs": _json(item.resolved_inputs_json, None), "outputs": _json(item.outputs_json, None),
            "error": _json(item.error_json, None), "metrics": _json(item.metrics_json, {}),
            "started_at": item.started_at.isoformat() if item.started_at else None,
            "finished_at": item.finished_at.isoformat() if item.finished_at else None,
        } for item in node_runs],
        "confirmations": [{
            "id": item.id, "node_id": item.node_id, "tool_name": item.tool_name, "status": item.status,
            "request": _json(item.request_json, {}), "response": _json(item.response_json, None),
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
        } for item in confirmations],
    }


@router.get("/tools")
def list_tools():
    return {"tools": list(registry.list_tools())}


@router.get("/workflows")
def list_workflows():
    db = SessionLocal()
    try:
        workflows = db.query(WorkflowDefinition).order_by(WorkflowDefinition.built_in.desc(), WorkflowDefinition.name.asc()).all()
        return {"workflows": [_workflow_payload(item, db) for item in workflows]}
    finally:
        db.close()


@router.post("/workflows")
def create_workflow(request: WorkflowCreateRequest):
    validation = validate_workflow_graph(request.graph, registry)
    if not validation.valid:
        raise HTTPException(status_code=422, detail={"message": "Invalid workflow graph.", "errors": [item.model_dump() for item in validation.errors]})
    db = SessionLocal()
    try:
        slug = _slugify(request.slug or request.name)
        if db.query(WorkflowDefinition).filter(WorkflowDefinition.slug == slug).first():
            slug = f"{slug}-{uuid.uuid4().hex[:6]}"
        workflow = WorkflowDefinition(
            slug=slug, name=request.name.strip() or "Untitled Workflow", description=request.description,
            built_in=False, enabled=request.enabled, routing_description=request.routing_description,
            routing_examples_json=json.dumps(request.routing_examples),
        )
        db.add(workflow)
        db.flush()
        revision = WorkflowRevision(
            workflow_id=workflow.id, version=1, graph_json=json.dumps(request.graph, ensure_ascii=False),
            created_by="user", trusted=False, checksum=_checksum(request.graph),
        )
        db.add(revision)
        db.flush()
        workflow.current_revision_id = revision.id
        db.commit()
        db.refresh(workflow)
        return _workflow_payload(workflow, db, include_graph=True)
    finally:
        db.close()


@router.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: str):
    db = SessionLocal()
    try:
        return _workflow_payload(_find_workflow(db, workflow_id), db, include_graph=True)
    finally:
        db.close()


@router.post("/workflows/{workflow_id}/duplicate")
def duplicate_workflow(workflow_id: str):
    db = SessionLocal()
    try:
        source = _find_workflow(db, workflow_id)
        revision = db.query(WorkflowRevision).filter(WorkflowRevision.id == source.current_revision_id).first()
        workflow = WorkflowDefinition(
            slug=f"{_slugify(source.slug)}-copy-{uuid.uuid4().hex[:6]}", name=f"{source.name} Copy",
            description=source.description, built_in=False, enabled=True,
            routing_description=source.routing_description, routing_examples_json=source.routing_examples_json,
            estimated_cost_class=source.estimated_cost_class, required_capabilities_json=source.required_capabilities_json,
        )
        db.add(workflow)
        db.flush()
        copy_revision = WorkflowRevision(
            workflow_id=workflow.id, version=1, graph_json=revision.graph_json, created_by="user",
            trusted=False, checksum=revision.checksum,
        )
        db.add(copy_revision)
        db.flush()
        workflow.current_revision_id = copy_revision.id
        db.commit()
        return _workflow_payload(workflow, db, include_graph=True)
    finally:
        db.close()


@router.put("/workflows/{workflow_id}")
def update_workflow(workflow_id: str, request: WorkflowUpdateRequest):
    db = SessionLocal()
    try:
        workflow = _find_workflow(db, workflow_id)
        updates = request.model_dump(exclude_unset=True)
        if "name" in updates:
            workflow.name = updates["name"].strip() or workflow.name
        if "description" in updates: workflow.description = updates["description"]
        if "routing_description" in updates: workflow.routing_description = updates["routing_description"]
        if "routing_examples" in updates: workflow.routing_examples_json = json.dumps(updates["routing_examples"])
        if "enabled" in updates: workflow.enabled = bool(updates["enabled"])
        workflow.updated_at = datetime.utcnow()
        db.commit()
        return _workflow_payload(workflow, db, include_graph=True)
    finally:
        db.close()


@router.post("/workflows/{workflow_id}/revisions")
def create_revision(workflow_id: str, request: WorkflowRevisionRequest):
    validation = validate_workflow_graph(request.graph, registry)
    if not validation.valid:
        raise HTTPException(status_code=422, detail={"message": "Invalid workflow graph.", "errors": [item.model_dump() for item in validation.errors]})
    db = SessionLocal()
    try:
        workflow = _find_workflow(db, workflow_id)
        if workflow.built_in:
            raise HTTPException(status_code=409, detail="Built-in workflows are read-only. Duplicate the workflow first.")
        latest = db.query(WorkflowRevision).filter(WorkflowRevision.workflow_id == workflow.id).order_by(WorkflowRevision.version.desc()).first()
        revision = WorkflowRevision(
            workflow_id=workflow.id, version=(latest.version + 1) if latest else 1,
            graph_json=json.dumps(request.graph, ensure_ascii=False), created_by=request.created_by,
            trusted=False, checksum=_checksum(request.graph),
        )
        db.add(revision)
        db.flush()
        workflow.current_revision_id = revision.id
        workflow.updated_at = datetime.utcnow()
        db.commit()
        return _workflow_payload(workflow, db, include_graph=True)
    finally:
        db.close()


@router.post("/workflows/validate")
def validate_workflow(request: WorkflowValidateRequest):
    return validate_workflow_graph(request.graph, registry).model_dump()


@router.delete("/workflows/{workflow_id}")
def delete_workflow(workflow_id: str):
    db = SessionLocal()
    try:
        workflow = _find_workflow(db, workflow_id)
        if workflow.built_in:
            raise HTTPException(status_code=409, detail="Built-in workflows cannot be deleted.")
        db.delete(workflow)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


def _memory_context_for_request(db: Session, request: WorkflowRunRequest) -> Dict[str, Any]:
    try:
        return build_chat_memory_context(
            db,
            request.message,
            exclude_session_id=request.session_id,
            top_k=4,
            context_character_budget=3600,
        )
    except Exception as exc:
        print("[chat-memory] workflow retrieval failed:", exc)
        return {"context_block": "", "sources": [], "hits": []}


async def _select_for_run(
    db: Session,
    request: WorkflowRunRequest,
    memory_context: Optional[Dict[str, Any]] = None,
) -> tuple[WorkflowDefinition, Optional[Dict[str, Any]]]:
    router_result = None
    if request.selection_mode == "automatic":
        recent_messages = []
        if request.session_id:
            session = db.query(chat_models.ChatSession).filter(chat_models.ChatSession.session_id == request.session_id).first()
            if session:
                rows = db.query(chat_models.ChatMessage).filter(chat_models.ChatMessage.session_pk == session.id).order_by(chat_models.ChatMessage.id.desc()).limit(4).all()
                recent_messages = [{"role": row.role, "content": row.content} for row in reversed(rows)]
        if memory_context and memory_context.get("context_block"):
            recent_messages.append({"role": "system", "content": memory_context["context_block"]})
        router_result = await select_workflow(
            db, message=request.message, recent_messages=recent_messages, attachments=request.attachments,
            library_slug=request.library_slug, router_model=request.router_model, chat_model=request.model,
            web_search_enabled=request.web_search_enabled,
        )
        workflow = _find_workflow(db, router_result["workflow_id"])
    elif request.selection_mode == "explicit":
        if not request.workflow_id:
            raise HTTPException(status_code=422, detail="Explicit workflow mode requires workflow_id.")
        workflow = _find_workflow(db, request.workflow_id)
        if not workflow.enabled:
            raise HTTPException(status_code=409, detail="The selected workflow is disabled.")
    else:
        workflow = _find_workflow(db, "input-output")
    return workflow, router_result


@router.post("/workflow-runs")
async def create_workflow_run(request: WorkflowRunRequest):
    db = SessionLocal()
    try:
        memory_context = _memory_context_for_request(db, request)
        workflow, router_result = await _select_for_run(db, request, memory_context)
        revision_id = request.workflow_revision_id or workflow.current_revision_id
        if (
            request.workflow_revision_id
            and request.workflow_revision_id != workflow.current_revision_id
            and request.regenerate_index is None
        ):
            raise HTTPException(status_code=409, detail="The requested workflow revision is stale.")
        revision = db.query(WorkflowRevision).filter(WorkflowRevision.id == revision_id).first()
        if revision is None or revision.workflow_id != workflow.id:
            raise HTTPException(status_code=409, detail="The selected workflow has no current revision.")
        graph = _json(revision.graph_json, {})
        validation = validate_workflow_graph(graph, registry)
        if not validation.valid:
            raise HTTPException(status_code=422, detail={"message": "Invalid workflow graph.", "errors": [item.model_dump() for item in validation.errors]})

        session = None
        user_message = None
        run_attachments = request.attachments
        sample_inputs = dict(request.sample_inputs)
        router_inputs = router_result.get("inputs") if isinstance(router_result, dict) else None
        if isinstance(router_inputs, dict):
            merged_inputs = dict(router_inputs)
            merged_inputs.update(sample_inputs)
            sample_inputs = merged_inputs
        target_message_id = sample_inputs.get("target_message_id") or sample_inputs.get("message_id")
        target_message_content = sample_inputs.get("target_message_content") or sample_inputs.get("edited_content")
        target_url = sample_inputs.get("target_url") or sample_inputs.get("url")
        if not target_url:
            url_match = re.search(r"https?://[^\s<>'\"]+", request.message or "")
            target_url = url_match.group(0).rstrip(".,);]") if url_match else None
        if request.session_id:
            session = db.query(chat_models.ChatSession).filter(chat_models.ChatSession.session_id == request.session_id).first()
            if session is None and request.regenerate_index is not None:
                raise HTTPException(status_code=404, detail="Chat session not found.")
            if session is None:
                session = chat_models.ChatSession(session_id=request.session_id)
                db.add(session)
                db.flush()
            previous_assistant_message_id = None
            if not target_message_id:
                previous_assistant = db.query(chat_models.ChatMessage).filter(
                    chat_models.ChatMessage.session_pk == session.id,
                    chat_models.ChatMessage.role == "assistant",
                ).order_by(chat_models.ChatMessage.created_at.desc(), chat_models.ChatMessage.id.desc()).first()
                if previous_assistant:
                    previous_assistant_message_id = previous_assistant.message_id
            from .. import main as main_module
            if request.regenerate_index is not None:
                rows = db.query(chat_models.ChatMessage).filter(
                    chat_models.ChatMessage.session_pk == session.id
                ).order_by(chat_models.ChatMessage.created_at.asc(), chat_models.ChatMessage.id.asc()).all()
                if request.regenerate_index >= len(rows):
                    raise HTTPException(status_code=400, detail="Invalid regeneration message index.")
                user_index = request.regenerate_index
                while user_index >= 0 and rows[user_index].role != "user":
                    user_index -= 1
                if user_index < 0:
                    raise HTTPException(status_code=400, detail="No user message is available for regeneration.")
                user_message = rows[user_index]
                user_message.content = request.message
                for stale_message in rows[user_index + 1:]:
                    db.delete(stale_message)
                stored_attachments = _json(user_message.attachments_json, [])
                run_attachments = request.attachments or stored_attachments
                db.flush()
                if not target_message_id and workflow.slug == "remember-this":
                    target_message_id = user_message.message_id
                    target_message_content = target_message_content or _extract_remember_content(request.message)
            else:
                attachments = [main_module._normalize_chat_attachment_or_raise(item, include_text=True) for item in request.attachments]
                run_attachments = attachments
                user_message = chat_models.ChatMessage(
                    session_pk=session.id, role="user", content=request.message,
                    attachments_json=json.dumps(attachments, ensure_ascii=False),
                )
                db.add(user_message)
                db.flush()
                if not target_message_id:
                    if workflow.slug == "remember-this":
                        target_message_id = user_message.message_id
                        target_message_content = target_message_content or _extract_remember_content(request.message)
                    elif previous_assistant_message_id:
                        target_message_id = previous_assistant_message_id

        messages = []
        if session:
            rows = db.query(chat_models.ChatMessage).filter(chat_models.ChatMessage.session_pk == session.id).order_by(chat_models.ChatMessage.id.asc()).all()[-20:]
            messages = [{"role": row.role, "content": row.content} for row in rows]
        else:
            messages = [{"role": "user", "content": request.message}]
        sample_inputs.setdefault("prompt", request.message)
        sample_inputs.setdefault("session_id", request.session_id)
        web_search_query, web_search_queries = _resolve_web_search_inputs(sample_inputs, request.message)
        memory_blocks = [memory_context] if memory_context.get("context_block") else []
        run_values = {
            "session_id": request.session_id,
            "chat_model": request.model,
            "router_model": request.router_model or request.model,
            "vision_model": request.vision_model,
            "transcription_model": request.transcription_model,
            "library_slug": request.library_slug,
            "searx_url": request.searx_url,
            "searx_engines": request.searx_engines,
            "messages": messages,
            "attachments": run_attachments,
            "generation_options": request.generation_options,
            "context_blocks": memory_blocks,
            "manual_library_enabled": bool(request.library_slug),
            "web_search_enabled": request.web_search_enabled,
            "web_search_query": web_search_query,
            "web_search_queries": web_search_queries,
            "show_thinking": request.show_thinking,
            "target_message_id": target_message_id,
            "target_message_content": target_message_content,
            "target_url": target_url,
        }
        run = WorkflowRun(
            workflow_id=workflow.id, workflow_revision_id=revision.id, session_id=request.session_id,
            user_message_id=user_message.message_id if user_message else None, selection_mode=request.selection_mode,
            inputs_json=json.dumps({"input": sample_inputs, "run": run_values, "explicit_user_action": request.explicit_user_action}, ensure_ascii=False),
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        if request.regenerate_index is not None and request.session_id:
            safe_sync_chat_memory_for_session(db, request.session_id)
        run_id = run.id
        workflow_payload = {"id": workflow.id, "slug": workflow.slug, "name": workflow.name, "revision_id": revision.id}
    finally:
        db.close()

    writer = EventWriter(run_id, workflow_payload["id"], SessionLocal)
    if router_result:
        await writer.emit("router_completed", router_result)
    await writer.emit("workflow_selected", workflow_payload)
    runtime.start(run_id)
    return {"run_id": run_id, "status": "queued", "workflow": workflow_payload, "router": router_result}


@router.get("/workflow-runs/{run_id}")
def get_workflow_run(run_id: str):
    db = SessionLocal()
    try:
        run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
        if not run:
            raise HTTPException(status_code=404, detail="Workflow run not found.")
        return _run_payload(run, db)
    finally:
        db.close()


@router.get("/workflow-runs/{run_id}/events")
def get_workflow_events(run_id: str, after_sequence: int = Query(0, ge=0), follow: bool = True):
    async def stream():
        sequence = after_sequence
        idle_after_terminal = 0
        emitted_interrupted = False
        while True:
            db = SessionLocal()
            try:
                events = db.query(WorkflowEvent).filter(
                    WorkflowEvent.workflow_run_id == run_id,
                    WorkflowEvent.sequence > sequence,
                ).order_by(WorkflowEvent.sequence.asc()).all()
                run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
                if run is None:
                    yield json.dumps({"type": "error", "payload": {"message": "Workflow run not found."}}) + "\n"
                    return
                for item in events:
                    sequence = item.sequence
                    yield json.dumps({
                        "run_id": item.workflow_run_id, "workflow_id": item.workflow_id, "node_id": item.node_id,
                        "sequence": item.sequence, "timestamp": item.timestamp.isoformat(), "type": item.event_type,
                        "payload": _json(item.payload_json, {}),
                    }, ensure_ascii=False) + "\n"
                terminal = run.status in {"completed", "failed", "cancelled", "interrupted"}
                if run.status == "interrupted" and not emitted_interrupted:
                    emitted_interrupted = True
                    yield json.dumps({
                        "run_id": run.id,
                        "workflow_id": run.workflow_id,
                        "node_id": None,
                        "sequence": sequence + 1,
                        "timestamp": (run.finished_at or datetime.utcnow()).isoformat(),
                        "type": "run_interrupted",
                        "payload": _json(run.error_json, {"message": "Workflow run was interrupted."}),
                    }, ensure_ascii=False) + "\n"
            finally:
                db.close()
            if not follow:
                return
            if terminal:
                idle_after_terminal += 1
                if idle_after_terminal >= 2:
                    return
            else:
                idle_after_terminal = 0
            await asyncio.sleep(0.15)
    return StreamingResponse(stream(), media_type="application/x-ndjson")


@router.post("/workflow-runs/{run_id}/cancel")
def cancel_workflow_run(run_id: str):
    if not runtime.cancel(run_id):
        raise HTTPException(status_code=404, detail="Workflow run not found.")
    return {"ok": True}


@router.post("/workflow-runs/{run_id}/confirmations/{confirmation_id}")
def respond_to_confirmation(run_id: str, confirmation_id: str, request: ConfirmationResponseRequest):
    db = SessionLocal()
    try:
        confirmation = db.query(WorkflowConfirmation).filter(
            WorkflowConfirmation.id == confirmation_id,
            WorkflowConfirmation.workflow_run_id == run_id,
        ).first()
        if not confirmation:
            raise HTTPException(status_code=404, detail="Confirmation request not found.")
        if confirmation.status != "pending":
            raise HTTPException(status_code=409, detail="Confirmation has already been resolved.")
    finally:
        db.close()
    if not runtime.resolve_confirmation(confirmation_id, request.approved):
        raise HTTPException(status_code=409, detail="The workflow is no longer waiting for this confirmation.")
    return {"ok": True, "approved": request.approved}


@router.post("/workflow-router/select")
async def route_workflow(request: RouterSelectRequest):
    db = SessionLocal()
    try:
        return await select_workflow(
            db, message=request.message, recent_messages=request.recent_messages[-4:], attachments=request.attachments,
            library_slug=request.library_slug, router_model=request.model, chat_model=request.chat_model,
            web_search_enabled=request.web_search_enabled,
            confidence_threshold=request.confidence_threshold,
        )
    finally:
        db.close()
