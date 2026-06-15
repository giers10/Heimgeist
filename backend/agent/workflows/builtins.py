from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from ..models import WorkflowDefinition, WorkflowRevision


LIMITS = {
    "maximum_nodes": 40,
    "maximum_tool_calls": 20,
    "maximum_llm_calls": 8,
    "maximum_runtime_seconds": 300,
    "maximum_result_chars": 120000,
    "maximum_concurrency": 4,
}
INPUTS = {"prompt": {"type": "string"}, "session_id": {"type": ["string", "null"]}}


def node(node_id: str, node_type: str, x: float, y: float, config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {"id": node_id, "type": node_type, "position": {"x": x, "y": y}, "config": config or {}}


def edge(source: str, target: str, source_handle: str = "output", suffix: str = "") -> Dict[str, Any]:
    return {
        "id": f"{source}-{target}{suffix}",
        "source": source,
        "source_handle": source_handle,
        "target": target,
        "target_handle": "input",
    }


EVIDENCE_FIRST_SYSTEM_PROMPT = (
    "Answer the current user request using the supplied retrieved context as the primary evidence. "
    "If retrieved context conflicts with older chat history, prefer the retrieved context and explain the correction briefly. "
    "Do not recommend that the user consult external sources when relevant retrieved sources are already present."
)


def chat_arguments(context_blocks: List[Dict[str, str]] | None = None, system_prompt: str = "") -> Dict[str, Any]:
    return {
        "model": {"$ref": "run.chat_model"},
        "messages": {"$ref": "run.messages"},
        "system_prompt": system_prompt,
        "context_blocks": context_blocks or [],
        "attachments": {"$ref": "run.attachments"},
        "vision_model": {"$ref": "run.vision_model"},
        "transcription_model": {"$ref": "run.transcription_model"},
        "generation_options": {"$ref": "run.generation_options"},
        "stream": True,
        "reasoning": False,
    }


def graph(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]], *, limits: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "inputs": INPUTS,
        "outputs": {"content": {"type": "string"}},
        "nodes": nodes,
        "edges": edges,
        "limits": {**LIMITS, **(limits or {})},
    }


DIRECT = graph(
    [
        node("input", "input", 0, 80),
        node("chat", "tool", 320, 80, {"tool": "heimgeist.chat", "arguments": chat_arguments()}),
        node("output", "output", 680, 80, {"value": {"$ref": "nodes.chat.output"}}),
    ],
    [edge("input", "chat"), edge("chat", "output")],
)

KNOWLEDGE = graph(
    [
        node("input", "input", 0, 80),
        node("search", "tool", 280, 20, {"tool": "heimgeist.knowledge_search", "arguments": {
            "prompt": {"$ref": "input.prompt"}, "library_slug": {"$ref": "run.library_slug"},
            "top_k": 6, "context_character_budget": 14000, "embedding_model": None,
        }}),
        node("chat", "tool", 620, 80, {"tool": "heimgeist.chat", "arguments": chat_arguments([{"$ref": "nodes.search.output"}])}),
        node("output", "output", 960, 80, {"value": {"$ref": "nodes.chat.output"}}),
    ],
    [edge("input", "search"), edge("search", "chat"), edge("chat", "output")],
)


def web_graph() -> Dict[str, Any]:
    return graph(
        [
            node("input", "input", 0, 100),
            node("queries", "tool", 240, 20, {"tool": "heimgeist.web_generate_queries", "arguments": {
                "prompt": {"$ref": "input.prompt"}, "model": {"$ref": "run.chat_model"}, "messages": {"$ref": "run.messages"},
            }}),
            node("search", "tool", 500, 20, {"tool": "heimgeist.web_search", "arguments": {
                "query": None, "queries": {"$ref": "nodes.queries.output.queries"}, "engines": {"$ref": "run.searx_engines"},
                "maximum_results": 16, "searx_url": {"$ref": "run.searx_url"},
            }}),
            node("fetch", "tool", 760, 20, {"tool": "heimgeist.web_fetch", "arguments": {
                "url": None, "urls": {"$ref": "nodes.search.output.results"}, "maximum_pages": 6,
            }}),
            node("rerank", "tool", 1010, 20, {"tool": "heimgeist.web_rerank", "arguments": {
                "prompt": {"$ref": "input.prompt"}, "pages": {"$ref": "nodes.fetch.output.pages"}, "model": {"$ref": "run.chat_model"},
                "rerank_model": None, "context_excerpt": "", "maximum_results": 6, "minimum_score": 55,
            }}),
            node("chat", "tool", 1260, 100, {"tool": "heimgeist.chat", "arguments": chat_arguments([{"$ref": "nodes.rerank.output"}], EVIDENCE_FIRST_SYSTEM_PROMPT)}),
            node("output", "output", 1540, 100, {"value": {"$ref": "nodes.chat.output"}}),
        ],
        [edge("input", "queries"), edge("queries", "search"), edge("search", "fetch"), edge("fetch", "rerank"), edge("rerank", "chat"), edge("chat", "output")],
    )


VISION = graph(
    [
        node("input", "input", 0, 80),
        node("vision", "tool", 280, 20, {"tool": "heimgeist.vision_analyze", "arguments": {
            "attachments": {"$ref": "run.attachments"}, "vision_model": {"$ref": "run.vision_model"},
            "transcription_model": {"$ref": "run.transcription_model"},
        }}),
        node("chat", "tool", 620, 80, {"tool": "heimgeist.chat", "arguments": chat_arguments([{"$ref": "nodes.vision.output"}])}),
        node("output", "output", 960, 80, {"value": {"$ref": "nodes.chat.output"}}),
    ],
    [edge("input", "vision"), edge("vision", "chat"), edge("chat", "output")],
    limits={"maximum_runtime_seconds": 600},
)

KNOWLEDGE_WEB = graph(
    [
        node("input", "input", 0, 150),
        node("knowledge", "tool", 260, 20, {"tool": "heimgeist.knowledge_search", "arguments": {
            "prompt": {"$ref": "input.prompt"}, "library_slug": {"$ref": "run.library_slug"}, "top_k": 5,
            "context_character_budget": 10000, "embedding_model": None,
        }}),
        node("queries", "tool", 260, 240, {"tool": "heimgeist.web_generate_queries", "arguments": {
            "prompt": {"$ref": "input.prompt"}, "model": {"$ref": "run.chat_model"}, "messages": {"$ref": "run.messages"},
        }}),
        node("search", "tool", 520, 240, {"tool": "heimgeist.web_search", "arguments": {
            "query": None, "queries": {"$ref": "nodes.queries.output.queries"}, "engines": {"$ref": "run.searx_engines"},
            "maximum_results": 12, "searx_url": {"$ref": "run.searx_url"},
        }}),
        node("fetch", "tool", 780, 240, {"tool": "heimgeist.web_fetch", "arguments": {"url": None, "urls": {"$ref": "nodes.search.output.results"}, "maximum_pages": 5}}),
        node("rerank", "tool", 1040, 240, {"tool": "heimgeist.web_rerank", "arguments": {
            "prompt": {"$ref": "input.prompt"}, "pages": {"$ref": "nodes.fetch.output.pages"}, "model": {"$ref": "run.chat_model"},
            "rerank_model": None, "context_excerpt": "", "maximum_results": 5, "minimum_score": 50,
        }}),
        node("merge", "merge", 1300, 130, {"values": [{"$ref": "nodes.knowledge.output"}, {"$ref": "nodes.rerank.output"}], "deduplicate_by": "url"}),
        node("limit", "limit", 1520, 130, {"value": {"$ref": "nodes.merge.output"}, "maximum_chars": 22000}),
        node("chat", "tool", 1760, 130, {"tool": "heimgeist.chat", "arguments": chat_arguments([{"$ref": "nodes.limit.output"}], EVIDENCE_FIRST_SYSTEM_PROMPT)}),
        node("output", "output", 2040, 130, {"value": {"$ref": "nodes.chat.output"}}),
    ],
    [
        edge("input", "knowledge"), edge("input", "queries"), edge("queries", "search"), edge("search", "fetch"), edge("fetch", "rerank"),
        edge("knowledge", "merge"), edge("rerank", "merge"), edge("merge", "limit"), edge("limit", "chat"), edge("chat", "output"),
    ],
)

REMEMBER = graph(
    [
        node("input", "input", 0, 80),
        node("save", "tool", 300, 80, {"tool": "heimgeist.save_message_to_knowledge", "arguments": {
            "message_id": {"$ref": "run.target_message_id"}, "library_slug": {"$ref": "run.library_slug"}, "title": None, "edited_content": {"$ref": "run.target_message_content"},
        }}),
        node("confirmed", "condition", 570, 80, {"value": {"$ref": "nodes.save.output.confirmed"}, "operation": "equals", "compare_to": True}),
        node("saved", "template", 830, 20, {"template": "Saved the selected message to knowledge."}),
        node("rejected", "template", 830, 150, {"template": "The knowledge save was cancelled."}),
        node("response", "merge", 1080, 80, {"values": [{"$ref": "nodes.saved.output"}, {"$ref": "nodes.rejected.output"}], "allow_missing": True}),
        node("output", "output", 1320, 80, {"value": {"content": {"$ref": "nodes.response.output.context_block"}, "sources": [], "usage": {}}}),
    ],
    [
        edge("input", "save"), edge("save", "confirmed"),
        edge("confirmed", "saved", "true"), edge("confirmed", "rejected", "false"),
        edge("saved", "response"), edge("rejected", "response"), edge("response", "output"),
    ],
)

SAVE_SOURCE = graph(
    [
        node("input", "input", 0, 80),
        node("save", "tool", 300, 80, {"tool": "heimgeist.save_website_to_knowledge", "arguments": {
            "url": {"$ref": "run.target_url"}, "library_slug": {"$ref": "run.library_slug"}, "title": None,
        }}),
        node("confirmed", "condition", 570, 80, {"value": {"$ref": "nodes.save.output.confirmed"}, "operation": "equals", "compare_to": True}),
        node("saved", "template", 830, 20, {"template": "Saved the website snapshot to knowledge."}),
        node("rejected", "template", 830, 150, {"template": "The website save was cancelled."}),
        node("response", "merge", 1080, 80, {"values": [{"$ref": "nodes.saved.output"}, {"$ref": "nodes.rejected.output"}], "allow_missing": True}),
        node("output", "output", 1320, 80, {"value": {"content": {"$ref": "nodes.response.output.context_block"}, "sources": [], "usage": {}}}),
    ],
    [
        edge("input", "save"), edge("save", "confirmed"),
        edge("confirmed", "saved", "true"), edge("confirmed", "rejected", "false"),
        edge("saved", "response"), edge("rejected", "response"), edge("response", "output"),
    ],
)

RESEARCH = graph(
    [
        node("input", "input", 0, 180),
        node("analyze", "prompt", 220, 180, {
            "model_source": "chat_model",
            "system_template": (
                "Rewrite the user's current request as one concrete web research question. "
                "Resolve follow-ups, pronouns, and phrases like 'that agreement' using the recent conversation. "
                "If the request follows a current-news answer, preserve the current-news entities and claims from that answer. "
                "Do not introduce historical topics or named agreements unless they appear in the current request or recent conversation. "
                "Return one concise search-ready question only."
            ),
            "user_template": "Current request:\n{{input.prompt}}\n\nRecent conversation:\n{{run.messages}}",
            "output_mode": "text", "temperature": 0.1,
        }),
        node("queries1", "tool", 470, 80, {"tool": "heimgeist.web_generate_queries", "arguments": {
            "prompt": {"$ref": "nodes.analyze.output.content"}, "model": {"$ref": "run.chat_model"}, "messages": {"$ref": "run.messages"},
        }}),
        node("search1", "tool", 710, 80, {"tool": "heimgeist.web_search", "arguments": {
            "query": None, "queries": {"$ref": "nodes.queries1.output.queries"}, "engines": {"$ref": "run.searx_engines"},
            "maximum_results": 18, "searx_url": {"$ref": "run.searx_url"},
        }}),
        node("fetch1", "tool", 950, 80, {"tool": "heimgeist.web_fetch", "arguments": {"url": None, "urls": {"$ref": "nodes.search1.output.results"}, "maximum_pages": 7}}),
        node("rank1", "tool", 1190, 80, {"tool": "heimgeist.web_rerank", "arguments": {
            "prompt": {"$ref": "input.prompt"}, "pages": {"$ref": "nodes.fetch1.output.pages"}, "model": {"$ref": "run.chat_model"},
            "rerank_model": None, "context_excerpt": "", "maximum_results": 6, "minimum_score": 50,
        }}),
        node("evaluate", "prompt", 1430, 80, {
            "model_source": "chat_model", "system_template": "Judge whether the evidence can answer the request. Return JSON.",
            "user_template": "Request: {{input.prompt}}\nEvidence: {{nodes.rank1.output.context_block}}",
            "output_mode": "json", "json_schema": {"type": "object", "properties": {"sufficient": {"type": "boolean"}, "follow_up_query": {"type": "string"}}, "required": ["sufficient", "follow_up_query"], "additionalProperties": False},
            "temperature": 0.1, "json_repair": True,
        }),
        node("sufficient", "condition", 1690, 80, {"value": {"$ref": "nodes.evaluate.output.sufficient"}, "operation": "equals", "compare_to": True}),
        node("queries2", "select", 1910, 220, {"value": {"$ref": "nodes.evaluate.output.follow_up_query"}}),
        node("search2", "tool", 2130, 220, {"tool": "heimgeist.web_search", "arguments": {
            "query": {"$ref": "nodes.queries2.output"}, "queries": [], "engines": {"$ref": "run.searx_engines"},
            "maximum_results": 10, "searx_url": {"$ref": "run.searx_url"},
        }}),
        node("fetch2", "tool", 2350, 220, {"tool": "heimgeist.web_fetch", "arguments": {"url": None, "urls": {"$ref": "nodes.search2.output.results"}, "maximum_pages": 4}}),
        node("rank2", "tool", 2570, 220, {"tool": "heimgeist.web_rerank", "arguments": {
            "prompt": {"$ref": "input.prompt"}, "pages": {"$ref": "nodes.fetch2.output.pages"}, "model": {"$ref": "run.chat_model"},
            "rerank_model": None, "context_excerpt": "", "maximum_results": 4, "minimum_score": 45,
        }}),
        node("empty2", "template", 1910, -80, {"template": "", "values": {}}),
        node("merge", "merge", 2820, 80, {"values": [{"$ref": "nodes.rank1.output"}, {"$ref": "nodes.rank2.output"}], "deduplicate_by": "url", "allow_missing": True}),
        node("limit", "limit", 3050, 80, {"value": {"$ref": "nodes.merge.output"}, "maximum_chars": 28000}),
        node("chat", "tool", 3280, 80, {"tool": "heimgeist.chat", "arguments": chat_arguments([{"$ref": "nodes.limit.output"}], EVIDENCE_FIRST_SYSTEM_PROMPT)}),
        node("output", "output", 3520, 80, {"value": {"$ref": "nodes.chat.output"}}),
    ],
    [
        edge("input", "analyze"), edge("analyze", "queries1"), edge("queries1", "search1"), edge("search1", "fetch1"), edge("fetch1", "rank1"),
        edge("rank1", "evaluate"), edge("evaluate", "sufficient"), edge("sufficient", "empty2", "true", "-true"), edge("sufficient", "queries2", "false", "-false"),
        edge("queries2", "search2"), edge("search2", "fetch2"), edge("fetch2", "rank2"), edge("rank1", "merge"), edge("rank2", "merge"), edge("empty2", "merge"),
        edge("merge", "limit"), edge("limit", "chat"), edge("chat", "output"),
    ],
    limits={"maximum_tool_calls": 24, "maximum_llm_calls": 10, "maximum_runtime_seconds": 420},
)

BUILTIN_WORKFLOWS = [
    {"slug": "input-output", "name": "Input -> Output", "description": "Normal Heimgeist chat with optional compatibility context.", "routing_description": "Use for ordinary conversation and requests that need no special retrieval.", "routing_examples": ["Explain this concept", "Draft a reply"], "estimated_cost_class": "low", "required_capabilities": ["chat"], "graph": DIRECT},
    {"slug": "knowledge-answer", "name": "Knowledge Answer", "description": "Answer from a selected local knowledge database.", "routing_description": "Use when the user asks about information in the selected local database.", "routing_examples": ["What do my notes say about this?"], "estimated_cost_class": "medium", "required_capabilities": ["rag"], "graph": KNOWLEDGE},
    {"slug": "web-answer", "name": "Web Answer", "description": "Search, fetch, rank, and answer from current web sources.", "routing_description": "Use for current information or explicit requests to search the web.", "routing_examples": ["What happened today?", "Look this up online"], "estimated_cost_class": "medium", "required_capabilities": ["web"], "graph": web_graph()},
    {"slug": "vision-answer", "name": "Vision Answer", "description": "Analyze attachments and answer with a vision model.", "routing_description": "Use when image or document attachments must be analyzed.", "routing_examples": ["What is shown in this image?"], "estimated_cost_class": "medium", "required_capabilities": ["vision"], "graph": VISION},
    {"slug": "knowledge-web-answer", "name": "Knowledge + Web Answer", "description": "Combine local knowledge and current web evidence.", "routing_description": "Use when both the selected database and current web information are relevant.", "routing_examples": ["Compare my notes with the latest information"], "estimated_cost_class": "high", "required_capabilities": ["rag", "web"], "graph": KNOWLEDGE_WEB},
    {"slug": "remember-this", "name": "Remember This", "description": "Save a selected chat message to knowledge.", "routing_description": "Use only when the user explicitly asks to remember or save a chat message.", "routing_examples": ["Remember this answer"], "estimated_cost_class": "low", "required_capabilities": ["knowledge_write"], "graph": REMEMBER},
    {"slug": "save-source", "name": "Save Source", "description": "Save a website snapshot to knowledge.", "routing_description": "Use when the user explicitly asks to save a URL as a knowledge source.", "routing_examples": ["Save this website to my database"], "estimated_cost_class": "low", "required_capabilities": ["web", "knowledge_write"], "graph": SAVE_SOURCE},
    {"slug": "research", "name": "Research", "description": "Bounded two-round evidence research with source validation.", "routing_description": "Use for multi-source research questions requiring deeper evidence gathering.", "routing_examples": ["Research the competing explanations and cite sources"], "estimated_cost_class": "high", "required_capabilities": ["web", "chat"], "graph": RESEARCH},
]


def _checksum(graph_value: Dict[str, Any]) -> str:
    canonical = json.dumps(graph_value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def seed_builtin_workflows(session: Session) -> None:
    for item in BUILTIN_WORKFLOWS:
        workflow = session.query(WorkflowDefinition).filter(WorkflowDefinition.slug == item["slug"]).first()
        if workflow is None:
            workflow = WorkflowDefinition(
                slug=item["slug"], name=item["name"], description=item["description"], built_in=True, enabled=True,
                routing_description=item["routing_description"], routing_examples_json=json.dumps(item["routing_examples"]),
                estimated_cost_class=item["estimated_cost_class"], required_capabilities_json=json.dumps(item["required_capabilities"]),
            )
            session.add(workflow)
            session.flush()
        else:
            workflow.name = item["name"]
            workflow.description = item["description"]
            workflow.built_in = True
            workflow.routing_description = item["routing_description"]
            workflow.routing_examples_json = json.dumps(item["routing_examples"])
            workflow.estimated_cost_class = item["estimated_cost_class"]
            workflow.required_capabilities_json = json.dumps(item["required_capabilities"])
            workflow.updated_at = datetime.utcnow()
        checksum = _checksum(item["graph"])
        revision = session.query(WorkflowRevision).filter(
            WorkflowRevision.workflow_id == workflow.id,
            WorkflowRevision.checksum == checksum,
        ).first()
        if revision is None:
            latest = session.query(WorkflowRevision).filter(WorkflowRevision.workflow_id == workflow.id).order_by(WorkflowRevision.version.desc()).first()
            revision = WorkflowRevision(
                workflow_id=workflow.id,
                version=(latest.version + 1) if latest else 1,
                graph_json=json.dumps(item["graph"], ensure_ascii=False),
                created_by="system",
                trusted=True,
                checksum=checksum,
            )
            session.add(revision)
            session.flush()
        workflow.current_revision_id = revision.id
    session.commit()
