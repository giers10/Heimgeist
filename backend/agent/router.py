from __future__ import annotations

import json
import re
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


def _fallback_result(fallback: WorkflowDefinition, reason: str = "Router fallback to direct chat.") -> Dict[str, Any]:
    return {
        "workflow_id": fallback.id,
        "workflow_slug": fallback.slug,
        "confidence": 0.0,
        "reason": reason,
        "inputs": {},
        "fallback": True,
    }


def _manifest_by_slug(manifests: List[Dict[str, Any]], slug: str) -> Optional[Dict[str, Any]]:
    return next((item for item in manifests if item["slug"] == slug), None)


def _workflow_result(item: Dict[str, Any], *, confidence: float, reason: str, inputs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "workflow_id": item["workflow_id"],
        "workflow_slug": item["slug"],
        "confidence": max(0.0, min(float(confidence), 1.0)),
        "reason": reason,
        "inputs": inputs or {},
        "fallback": False,
    }


def _capability_enabled(capability: str, *, library_slug: Optional[str], web_search_enabled: bool, has_attachments: bool) -> bool:
    if capability in {"chat"}:
        return True
    if capability == "web":
        return web_search_enabled
    if capability in {"rag", "knowledge_write"}:
        return bool(library_slug)
    if capability == "vision":
        return has_attachments
    return False


def _allowed_manifests(
    manifests: List[Dict[str, Any]],
    *,
    library_slug: Optional[str],
    web_search_enabled: bool,
    has_attachments: bool,
) -> List[Dict[str, Any]]:
    allowed = []
    for item in manifests:
        capabilities = item.get("required_capabilities") or []
        if all(_capability_enabled(str(capability), library_slug=library_slug, web_search_enabled=web_search_enabled, has_attachments=has_attachments) for capability in capabilities):
            allowed.append(item)
    return allowed


_REMEMBER_RE = re.compile(
    r"\b(remember\s+(this|that)|save\s+(this|that)|merk\s+dir\s+(das|:)?|speicher(e)?\s+(das|dies|diese|diesen)?)\b",
    re.IGNORECASE,
)
_WEB_RE = re.compile(
    r"\b(web|internet|online|search|look\s+up|google|latest|current|today|news|recent|heute|aktuell|neueste|nachrichten|suche|recherchier)\b",
    re.IGNORECASE,
)
_KNOWLEDGE_RE = re.compile(
    r"\b(my\s+(notes|documents|files|database|knowledge)|meine[nr]?\s+(notizen|dokumente|dateien|datenbank|daten)|rag|knowledge\s+base|datenbank)\b",
    re.IGNORECASE,
)
_GREETING_RE = re.compile(r"^\s*(hi|hello|hey|hallo|moin|servus|danke|thanks|thank\s+you)[!.?\s]*$", re.IGNORECASE)


def _fast_path(
    manifests: List[Dict[str, Any]],
    *,
    message: str,
    library_slug: Optional[str],
    web_search_enabled: bool,
    has_attachments: bool,
) -> Optional[Dict[str, Any]]:
    text = str(message or "").strip()
    if not text and not has_attachments:
        return None

    if has_attachments:
        vision = _manifest_by_slug(manifests, "vision-answer")
        if vision:
            return _workflow_result(vision, confidence=1.0, reason="The request includes attachments.")

    lowered = text.lower()
    mentions_knowledge = bool(_KNOWLEDGE_RE.search(text))
    mentions_web = bool(_WEB_RE.search(text))

    if _REMEMBER_RE.search(text):
        remember = _manifest_by_slug(manifests, "remember-this")
        if remember:
            return _workflow_result(remember, confidence=1.0, reason="The user explicitly asked Heimgeist to remember or save this.")

    if web_search_enabled and library_slug and mentions_web and mentions_knowledge:
        combined = _manifest_by_slug(manifests, "knowledge-web-answer")
        if combined:
            return _workflow_result(combined, confidence=0.95, reason="The request asks to combine selected knowledge with current web information.")

    if web_search_enabled and mentions_web:
        web = _manifest_by_slug(manifests, "web-answer")
        if web:
            return _workflow_result(web, confidence=0.95, reason="The request asks for current or web information and web search is enabled.")

    if library_slug and mentions_knowledge:
        knowledge = _manifest_by_slug(manifests, "knowledge-answer")
        if knowledge:
            return _workflow_result(knowledge, confidence=0.95, reason="The request explicitly refers to the selected knowledge database.")

    if _GREETING_RE.match(text):
        direct = _manifest_by_slug(manifests, "input-output")
        if direct:
            return _workflow_result(direct, confidence=1.0, reason="Simple conversational prompt.")

    return None


async def select_workflow(
    db: Session,
    *,
    message: str,
    recent_messages: List[Dict[str, Any]],
    attachments: List[Dict[str, Any]],
    library_slug: Optional[str],
    router_model: Optional[str],
    chat_model: Optional[str],
    web_search_enabled: bool = False,
    confidence_threshold: float = 0.55,
) -> Dict[str, Any]:
    fallback = db.query(WorkflowDefinition).filter(WorkflowDefinition.slug == "input-output").first()
    if fallback is None:
        raise RuntimeError("The built-in input-output workflow is missing.")
    fallback_result = _fallback_result(fallback)
    has_attachments = bool(attachments)
    manifests = _allowed_manifests(
        workflow_manifests(db),
        library_slug=library_slug,
        web_search_enabled=web_search_enabled,
        has_attachments=has_attachments,
    )
    fast = _fast_path(
        manifests,
        message=message,
        library_slug=library_slug,
        web_search_enabled=web_search_enabled,
        has_attachments=has_attachments,
    )
    if fast:
        return fast

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

    message_excerpt = str(message or "")
    if len(message_excerpt) > 1800:
        message_excerpt = message_excerpt[:1800] + "…"
    history_chars = 300 if len(message_excerpt) > 1000 else 500
    compact_history = [
        {"role": item.get("role"), "content": str(item.get("content") or "")[:history_chars]}
        for item in recent_messages[-4:]
    ]
    prompt = (
        "Select exactly one enabled workflow for the current request. Do not invent workflows. "
        "Prefer the lowest-cost workflow that can complete the task. Return JSON only.\n\n"
        f"Current request: {message_excerpt}\n"
        f"Recent messages: {json.dumps(compact_history, ensure_ascii=False)}\n"
        f"Attachment count: {len(attachments)}\n"
        f"Selected knowledge database: {library_slug or 'none'}\n"
        f"Web search permission: {'enabled' if web_search_enabled else 'disabled'}\n"
        "Only select workflows from the enabled list. Do not select a workflow that needs a disabled capability.\n"
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
