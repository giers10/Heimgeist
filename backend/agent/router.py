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


MESSAGE_DIGEST_THRESHOLD = 2600
MESSAGE_DIGEST_HEAD_CHARS = 900
MESSAGE_DIGEST_MIDDLE_CHARS = 500
MESSAGE_DIGEST_TAIL_CHARS = 900
MESSAGE_DIGEST_MAX_SAMPLE_LINES = 6
MESSAGE_DIGEST_SAMPLE_LINE_CHARS = 180
ATTACHMENT_SUMMARY_LIMIT = 8


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
    if capability in {"chat", "chat_memory"}:
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
            return _workflow_result(knowledge, confidence=0.95, reason="The request explicitly refers to local knowledge.")

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


def _normalize_router_inputs(selected: Dict[str, Any], inputs: Any, message: str) -> Dict[str, Any]:
    normalized = dict(inputs) if isinstance(inputs, dict) else {}
    if "web" not in (selected.get("required_capabilities") or []):
        return normalized
    raw_queries = normalized.get("web_search_queries")
    if raw_queries is None:
        raw_queries = normalized.get("search_queries")
    raw_query = normalized.get("web_search_query") or normalized.get("search_query")
    queries = _clean_router_queries(raw_queries if raw_queries is not None else raw_query, message)
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


def _normalize_message_text(message: str) -> str:
    return str(message or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")


def _short_line(line: str, limit: int = MESSAGE_DIGEST_SAMPLE_LINE_CHARS) -> str:
    text = " ".join(str(line or "").split()).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "..."
    return text


def _sample_representative_lines(text: str, *, max_lines: int = MESSAGE_DIGEST_MAX_SAMPLE_LINES) -> List[str]:
    candidates = []
    seen = set()
    for line_number, line in enumerate(text.splitlines(), 1):
        compact = _short_line(line)
        if not compact:
            continue
        if compact in seen:
            continue
        seen.add(compact)
        candidates.append((line_number, compact))
    if len(candidates) <= max_lines:
        return [f"L{line_number}: {compact}" for line_number, compact in candidates]
    if max_lines <= 1:
        line_number, compact = candidates[0]
        return [f"L{line_number}: {compact}"]
    selected = []
    last_index = len(candidates) - 1
    for index in range(max_lines):
        selected_index = round(index * last_index / (max_lines - 1))
        line_number, compact = candidates[selected_index]
        selected.append(f"L{line_number}: {compact}")
    return selected


def _count_substrings(text: str, values: List[str]) -> int:
    lowered = text.casefold()
    return sum(lowered.count(value.casefold()) for value in values)


def _format_message_for_router(message: str) -> str:
    text = _normalize_message_text(message)
    if len(text) <= MESSAGE_DIGEST_THRESHOLD:
        return text

    lines = text.splitlines()
    middle_start = max(0, (len(text) - MESSAGE_DIGEST_MIDDLE_CHARS) // 2)
    middle_end = min(len(text), middle_start + MESSAGE_DIGEST_MIDDLE_CHARS)
    samples = _sample_representative_lines(text)
    sections = [
        "<long_user_message_digest>",
        f"Original length: {len(text)} characters, {len(lines)} lines.",
        f"Detected structure: {_count_substrings(text, ['http://', 'https://'])} URL marker(s), {text.count('```')} code-fence marker(s).",
        "This is a routing digest, not the full message. Use the visible beginning/end and structural summary to route; do not infer omitted content.",
        "",
        "<beginning_excerpt>",
        text[:MESSAGE_DIGEST_HEAD_CHARS].rstrip(),
        "</beginning_excerpt>",
        "",
        "<middle_excerpt>",
        text[middle_start:middle_end].strip(),
        "</middle_excerpt>",
        "",
        "<ending_excerpt>",
        text[-MESSAGE_DIGEST_TAIL_CHARS:].lstrip(),
        "</ending_excerpt>",
    ]
    if samples:
        sections.extend([
            "",
            "<representative_short_lines>",
            *samples,
            "</representative_short_lines>",
        ])
    sections.append("</long_user_message_digest>")
    return "\n".join(sections)


def _format_attachment_summary(attachments: List[Dict[str, Any]]) -> str:
    if not attachments:
        return "No attachments."
    rows = [f"{len(attachments)} attachment(s) are present."]
    for index, attachment in enumerate(attachments[:ATTACHMENT_SUMMARY_LIMIT], 1):
        if not isinstance(attachment, dict):
            rows.append(f"{index}. Attachment")
            continue
        kind = str(attachment.get("kind") or ("image" if attachment.get("data_url") else "file") or "attachment")
        name = str(attachment.get("name") or attachment.get("source_path") or f"attachment-{index}")
        mime_type = str(attachment.get("mime_type") or "").strip()
        size = attachment.get("size")
        text_length = len(str(attachment.get("text") or ""))
        parts = [f"{index}. {kind}: {name}"]
        if mime_type:
            parts.append(f"mime={mime_type}")
        if size is not None:
            parts.append(f"size={size}")
        if text_length:
            parts.append(f"text_chars={text_length}")
        rows.append("; ".join(parts))
    if len(attachments) > ATTACHMENT_SUMMARY_LIMIT:
        rows.append(f"... {len(attachments) - ATTACHMENT_SUMMARY_LIMIT} more attachment(s) omitted from routing summary.")
    return "\n".join(rows)


def _format_workflow_choices(manifests: List[Dict[str, Any]]) -> str:
    rows: List[str] = []
    for item in manifests:
        examples = "; ".join(str(value) for value in (item.get("examples") or [])[:2])
        capabilities = ", ".join(str(value) for value in (item.get("required_capabilities") or []))
        suffix = f" Examples: {examples}." if examples else ""
        rows.append(
            f"- {item['name']} ({item['slug']}, id: {item['workflow_id']}): "
            f"Capabilities: {capabilities or 'none'}. "
            f"Routing contract: {item.get('routing_description') or 'No description.'}{suffix}"
        )
    return "\n".join(rows)


def _router_prompt(
    *,
    message: str,
    recent_messages: List[Dict[str, Any]],
    attachments: List[Dict[str, Any]],
    manifests: List[Dict[str, Any]],
) -> str:
    message_excerpt = _format_message_for_router(message)
    history_chars = 250 if len(message_excerpt) > MESSAGE_DIGEST_THRESHOLD else 500
    attachment_line = _format_attachment_summary(attachments)
    return (
        "You are Heimgeist's workflow router. Choose exactly one allowed workflow for the current user message.\n\n"
        "Decision method:\n"
        "1. Resolve the current message into a standalone request using recent conversation. Do this before choosing a workflow.\n"
        "2. Identify which capabilities are required to answer faithfully: ordinary chat, local knowledge retrieval, web evidence, attachment analysis, or saving knowledge.\n"
        "3. Choose the lowest-complexity allowed workflow whose capabilities cover the required work. If two workflows can answer faithfully, choose the simpler one. Do not choose a workflow because an example sounds similar; use the routing contract and capabilities.\n"
        "4. Choose a direct chat workflow only when the answer can be produced from the conversation, stable model knowledge, writing/reasoning ability, or already-supplied context. If the request needs information from outside those sources, choose a workflow with the needed capability.\n"
        "5. Choose a web-answer workflow when the missing capability is external facts or source grounding and one search-and-answer pass should be enough. Needing web evidence by itself is not a reason to choose Research.\n"
        "6. Choose the research workflow only when the standalone request asks for or truly requires investigation, comparison, dispute resolution, claim validation, broad synthesis, or a second search pass. If unsure between Web Answer and Research, choose Web Answer.\n\n"
        "Context resolution rules:\n"
        "- Short follow-ups usually inherit the previous subject, place, entity, file, source, or task unless the user clearly changes topic.\n"
        "- Resolve pronouns and fragments such as 'that', 'it', 'there', 'also', 'and', 'politically', or 'what about' into the standalone request.\n"
        "- For web workflows, write search queries for the standalone factual request, not the literal fragment the user typed.\n\n"
        "Long-message routing rule:\n"
        "- If the current user message is shown as a digest, route from the user's explicit request and the required capabilities. A long pasted payload alone does not require web search or research.\n\n"
        f"Recent conversation:\n{_format_recent_messages(recent_messages, char_limit=history_chars)}\n\n"
        f"Current user message or routing digest:\n{message_excerpt}\n\n"
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
        forced_web = _forced_web_manifest(manifests, library_slug=library_slug) if web_search_enabled else None
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
