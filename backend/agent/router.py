from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..app_settings import get_workflow_router_model_preference
from ..ollama_client import chat_typed, list_model_catalog
from .models import WorkflowDefinition


ROUTER_FORMAT = {
    "type": "object",
    "properties": {
        "workflow_id": {"type": "string"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
        "inputs": {"type": "object"},
    },
    "required": ["workflow_id", "confidence", "reason", "inputs"],
    "additionalProperties": False,
}


def workflow_manifests(db: Session) -> List[Dict[str, Any]]:
    workflows = db.query(WorkflowDefinition).filter(WorkflowDefinition.enabled.is_(True)).order_by(WorkflowDefinition.name.asc()).all()
    manifests = []
    for item in workflows:
        manifests.append({
            "workflow_id": item.id,
            "slug": item.slug,
            "name": item.name,
            "routing_description": item.routing_description,
            "examples": json.loads(item.routing_examples_json or "[]"),
            "estimated_cost_class": item.estimated_cost_class,
            "required_capabilities": json.loads(item.required_capabilities_json or "[]"),
        })
    return manifests


async def select_workflow(
    db: Session,
    *,
    message: str,
    recent_messages: List[Dict[str, Any]],
    attachments: List[Dict[str, Any]],
    library_slug: Optional[str],
    router_model: Optional[str],
    chat_model: Optional[str],
    confidence_threshold: float = 0.55,
) -> Dict[str, Any]:
    fallback = db.query(WorkflowDefinition).filter(WorkflowDefinition.slug == "input-output").first()
    if fallback is None:
        raise RuntimeError("The built-in input-output workflow is missing.")
    fallback_result = {
        "workflow_id": fallback.id,
        "workflow_slug": fallback.slug,
        "confidence": 0.0,
        "reason": "Router fallback to direct chat.",
        "inputs": {},
        "fallback": True,
    }
    manifests = workflow_manifests(db)
    if attachments:
        vision = next((item for item in manifests if item["slug"] == "vision-answer"), None)
        if vision:
            return {**fallback_result, "workflow_id": vision["workflow_id"], "workflow_slug": vision["slug"], "confidence": 1.0, "reason": "The request includes attachments.", "fallback": False}

    preferred = str(router_model or get_workflow_router_model_preference() or "").strip()
    model = preferred or str(chat_model or "").strip()
    if not model:
        return fallback_result
    if preferred:
        try:
            catalog = await list_model_catalog()
            if preferred not in (catalog.get("chat_models") or []):
                model = str(chat_model or "").strip()
        except Exception:
            model = str(chat_model or "").strip() or preferred
    if not model:
        return fallback_result

    compact_history = [
        {"role": item.get("role"), "content": str(item.get("content") or "")[:700]}
        for item in recent_messages[-4:]
    ]
    prompt = (
        "Select exactly one enabled workflow for the current request. Do not invent workflows. "
        "Prefer the lowest-cost workflow that can complete the task. Return JSON only.\n\n"
        f"Current request: {message}\n"
        f"Recent messages: {json.dumps(compact_history, ensure_ascii=False)}\n"
        f"Attachment count: {len(attachments)}\n"
        f"Selected knowledge database: {library_slug or 'none'}\n"
        f"Enabled workflows: {json.dumps(manifests, ensure_ascii=False)}"
    )
    try:
        result = await chat_typed(
            model,
            [{"role": "user", "content": prompt}],
            format=ROUTER_FORMAT,
            options={"temperature": 0, "num_ctx": 4096},
        )
        parsed = json.loads(result.content)
        workflow_id = str(parsed.get("workflow_id") or "")
        selected = next((item for item in manifests if item["workflow_id"] == workflow_id or item["slug"] == workflow_id), None)
        confidence = float(parsed.get("confidence") or 0)
        if selected is None or confidence < confidence_threshold:
            return fallback_result
        if "rag" in selected["required_capabilities"] and not library_slug:
            return fallback_result
        return {
            "workflow_id": selected["workflow_id"],
            "workflow_slug": selected["slug"],
            "confidence": max(0.0, min(confidence, 1.0)),
            "reason": str(parsed.get("reason") or ""),
            "inputs": parsed.get("inputs") if isinstance(parsed.get("inputs"), dict) else {},
            "fallback": False,
            "model": model,
        }
    except Exception as exc:
        return {**fallback_result, "reason": f"Router failed: {type(exc).__name__}: {exc}"}
