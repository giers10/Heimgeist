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
        return True
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


def _filter_for_interface_settings(
    manifests: List[Dict[str, Any]],
    *,
    web_search_enabled: bool,
) -> List[Dict[str, Any]]:
    if not web_search_enabled:
        return manifests
    web_capable = [item for item in manifests if "web" in (item.get("required_capabilities") or [])]
    return web_capable or manifests


_REMEMBER_RE = re.compile(
    r"\b(remember\s+(this|that)|save\s+(this|that)|merk\s+dir\s+(das|:)?|speicher(e)?\s+(das|dies|diese|diesen)?)\b",
    re.IGNORECASE,
)
_KNOWLEDGE_RE = re.compile(
    r"\b(my\s+(notes|documents|files|database|knowledge)|meine[nr]?\s+(notizen|dokumente|dateien|datenbank|daten)|rag|knowledge\s+base|datenbank)\b",
    re.IGNORECASE,
)
_GREETING_RE = re.compile(r"^\s*(hi|hello|hey|hallo|moin|servus|danke|thanks|thank\s+you)[!.?\s]*$", re.IGNORECASE)
_FRESH_INFO_RE = re.compile(
    r"\b("
    r"weather|forecast|news|politics|political|latest|current|today|tonight|now|recent|breaking|"
    r"wetter|vorhersage|nachrichten|politik|aktuell|heute|neueste"
    r")\b",
    re.IGNORECASE,
)


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

    mentions_knowledge = bool(_KNOWLEDGE_RE.search(text))

    if _REMEMBER_RE.search(text):
        remember = _manifest_by_slug(manifests, "remember-this")
        if remember:
            return _workflow_result(remember, confidence=1.0, reason="The user explicitly asked Heimgeist to remember or save this.")

    if web_search_enabled:
        return None

    if library_slug and mentions_knowledge:
        knowledge = _manifest_by_slug(manifests, "knowledge-answer")
        if knowledge:
            return _workflow_result(knowledge, confidence=0.95, reason="The request explicitly refers to the selected knowledge database.")

    if _GREETING_RE.match(text):
        direct = _manifest_by_slug(manifests, "input-output")
        if direct:
            return _workflow_result(direct, confidence=1.0, reason="Simple conversational prompt.")

    return None


def _clean_router_queries(raw: Any, fallback: str) -> List[str]:
    values = raw if isinstance(raw, list) else [raw]
    cleaned: List[str] = []
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


def _router_inputs_need_fresh_info(inputs: Any, message: str, reason: str = "") -> bool:
    values: List[str] = [str(message or ""), str(reason or "")]
    if isinstance(inputs, dict):
        for key in ("resolved_request", "web_search_query", "search_query"):
            values.append(str(inputs.get(key) or ""))
        for key in ("web_search_queries", "search_queries"):
            raw = inputs.get(key)
            if isinstance(raw, list):
                values.extend(str(item or "") for item in raw)
    return bool(_FRESH_INFO_RE.search(" ".join(values)))


def _normalize_router_inputs(selected: Dict[str, Any], inputs: Any, message: str) -> Dict[str, Any]:
    normalized = dict(inputs) if isinstance(inputs, dict) else {}
    if "web" not in (selected.get("required_capabilities") or []):
        return normalized
    raw_queries = normalized.get("web_search_queries")
    if raw_queries is None:
        raw_queries = normalized.get("search_queries")
    raw_query = normalized.get("web_search_query") or normalized.get("search_query")
    query_fallback = normalized.get("resolved_request") or message
    queries = _clean_router_queries(raw_queries if raw_queries is not None else raw_query, query_fallback)
    if raw_query:
        raw_query_text = " ".join(str(raw_query).split()).strip()
        if raw_query_text and raw_query_text.casefold() not in {item.casefold() for item in queries}:
            queries.insert(0, raw_query_text[:300])
    queries = queries[:5]
    normalized["web_search_query"] = queries[0] if queries else str(message or "")[:300]
    normalized["web_search_queries"] = queries or [normalized["web_search_query"]]
    return normalized


def _forced_web_manifest(manifests: List[Dict[str, Any]], *, library_slug: Optional[str]) -> Optional[Dict[str, Any]]:
    if library_slug:
        combined = _manifest_by_slug(manifests, "knowledge-web-answer")
        if combined:
            return combined
    return _manifest_by_slug(manifests, "web-answer")


def _format_recent_messages(recent_messages: List[Dict[str, Any]], *, char_limit: int) -> str:
    rows: List[str] = []
    for index, item in enumerate(recent_messages[-4:], 1):
        role = str(item.get("role") or "message").strip().title()
        content = " ".join(str(item.get("content") or "").split())
        if len(content) > char_limit:
            content = content[:char_limit].rstrip() + "..."
        rows.append(f"{index}. {role}: {content}")
    return "\n".join(rows) if rows else "(none)"


def _format_workflow_choices(manifests: List[Dict[str, Any]]) -> str:
    rows: List[str] = []
    for item in manifests:
        examples = "; ".join(str(value) for value in (item.get("examples") or [])[:2])
        capabilities = ", ".join(str(value) for value in (item.get("required_capabilities") or []))
        suffix = f" Examples: {examples}." if examples else ""
        rows.append(
            f"- {item['name']} ({item['slug']}, id: {item['workflow_id']}): "
            f"{item.get('routing_description') or 'No description.'} "
            f"Capabilities: {capabilities or 'none'}.{suffix}"
        )
    return "\n".join(rows)


def _router_prompt(
    *,
    message: str,
    recent_messages: List[Dict[str, Any]],
    attachments: List[Dict[str, Any]],
    manifests: List[Dict[str, Any]],
) -> str:
    message_excerpt = str(message or "")
    if len(message_excerpt) > 1800:
        message_excerpt = message_excerpt[:1800] + "..."
    history_chars = 300 if len(message_excerpt) > 1000 else 500
    attachment_line = "No attachments." if not attachments else f"{len(attachments)} attachment(s) are present."
    return (
        "You are Heimgeist's workflow router. Your job is to understand the current user message in the context of the recent conversation, choose exactly one allowed workflow, and provide any inputs that workflow needs.\n\n"
        "Workflow choice rules:\n"
        "- Input -> Output is only for conversation, writing, reasoning, or stable knowledge that does not require fresh external information.\n"
        "- Never choose Input -> Output for weather, news, politics today, recent events, latest information, current prices, schedules, or anything the user expects to be up to date.\n"
        "- Web Answer is for straightforward current lookups: weather today, news today, latest facts, simple online questions, or follow-up questions about current web information.\n"
        "- Research is for explicit deeper investigation, comparing sources, multi-step synthesis, disputed claims, or broad research requests. Do not choose Research for a simple weather/news lookup.\n\n"
        "Important context rules:\n"
        "- Treat short follow-ups as continuing the topic from recent messages unless the user clearly changes topic.\n"
        "- Words like 'and', 'also', 'what about', 'politically', 'there', 'that', or 'it' usually inherit the previous entity, location, source, or subject.\n"
        "- If the previous turn asked about a place or entity and the current turn asks for another aspect, carry that place or entity into the resolved request.\n"
        "- For web workflows, write search queries for the resolved factual request, not the literal conversational fragment.\n"
        "- Example: previous user asked 'what is the weather in Iran today' and current user asks 'and what is the news politically?' -> search for 'Iran political news today'.\n\n"
        f"Recent conversation:\n{_format_recent_messages(recent_messages, char_limit=history_chars)}\n\n"
        f"Current user message:\n{message_excerpt}\n\n"
        f"Attachments:\n{attachment_line}\n\n"
        "Allowed workflows. This list is already filtered by interface settings and available capabilities; choose only from this list:\n"
        f"{_format_workflow_choices(manifests)}\n\n"
        "Return JSON only with this shape:\n"
        "{\n"
        '  "workflow_id": "<id or slug from allowed workflows>",\n'
        '  "confidence": 0.0,\n'
        '  "reason": "<brief reason>",\n'
        '  "inputs": {\n'
        '    "resolved_request": "<standalone interpretation of the current request>",\n'
        '    "web_search_query": "<required for web workflows>",\n'
        '    "web_search_queries": ["<required for web workflows, 1-5 concise queries>"]\n'
        "  }\n"
        "}\n"
        "For non-web workflows, inputs may be empty unless useful. For web workflows, web_search_query and web_search_queries are required."
    )


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
    manifests = _filter_for_interface_settings(manifests, web_search_enabled=web_search_enabled)
    if not manifests:
        return fallback_result

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

    prompt = _router_prompt(message=message, recent_messages=recent_messages, attachments=attachments, manifests=manifests)
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
        raw_inputs = parsed.get("inputs")
        forced_web = _forced_web_manifest(manifests, library_slug=library_slug) if (
            web_search_enabled or _router_inputs_need_fresh_info(raw_inputs, message, str(parsed.get("reason") or ""))
        ) else None
        forced = False
        if selected is None and forced_web:
            selected = forced_web
            confidence = max(confidence, 0.95)
            forced = True
        elif selected is not None and forced_web and "web" not in (selected.get("required_capabilities") or []):
            selected = forced_web
            confidence = max(confidence, 0.95)
            forced = True
        if selected is None or (confidence < confidence_threshold and not forced):
            return fallback_result
        if "rag" in selected["required_capabilities"] and not library_slug:
            return fallback_result
        inputs = _normalize_router_inputs(selected, parsed.get("inputs"), message)
        return {
            "workflow_id": selected["workflow_id"],
            "workflow_slug": selected["slug"],
            "confidence": max(0.0, min(confidence, 1.0)),
            "reason": (str(parsed.get("reason") or "") + (" Forced web search is enabled." if forced else "")).strip(),
            "inputs": inputs,
            "fallback": False,
            "model": model,
        }
    except Exception as exc:
        return {**fallback_result, "reason": f"Router failed: {type(exc).__name__}: {exc}"}
