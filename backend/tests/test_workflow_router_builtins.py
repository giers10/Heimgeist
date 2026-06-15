import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.agent.models import WorkflowDefinition
from backend.agent.registry import NativeToolProvider
from backend.agent.router import _format_message_for_router, _router_prompt, select_workflow
from backend.agent.tools import register_native_tools
from backend.agent.validation import validate_workflow_graph
from backend.agent.workflows.builtins import BUILTIN_WORKFLOWS, seed_builtin_workflows
from backend.database import Base
from backend.ollama_client import OllamaChatResult


class RouterAndBuiltinTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        engine = create_engine(f"sqlite:///{Path(self.temp.name) / 'router.db'}")
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        self.engine = engine
        db = self.Session(); seed_builtin_workflows(db); db.close()

    async def asyncTearDown(self):
        self.engine.dispose(); self.temp.cleanup()

    async def test_valid_selection_malformed_disabled_and_low_confidence_fallback(self):
        db = self.Session()
        web = db.query(WorkflowDefinition).filter_by(slug="web-answer").one()
        with patch("backend.agent.router.chat_typed", new=AsyncMock(return_value=OllamaChatResult(content=json.dumps({"workflow_id": web.id, "confidence": 0.9, "reason": "current", "inputs": {}})))):
            result = await select_workflow(db, message="tell me something useful", recent_messages=[], attachments=[], library_slug=None, router_model=None, chat_model="model")
            self.assertEqual(result["workflow_slug"], "web-answer")
        with patch("backend.agent.router.chat_typed", new=AsyncMock(return_value=OllamaChatResult(content="not json"))):
            self.assertEqual((await select_workflow(db, message="x", recent_messages=[], attachments=[], library_slug=None, router_model=None, chat_model="model"))["workflow_slug"], "input-output")
        web.enabled = False; db.commit()
        with patch("backend.agent.router.chat_typed", new=AsyncMock(return_value=OllamaChatResult(content=json.dumps({"workflow_id": web.id, "confidence": 0.9, "reason": "x", "inputs": {}})))):
            self.assertEqual((await select_workflow(db, message="x", recent_messages=[], attachments=[], library_slug=None, router_model=None, chat_model="model"))["workflow_slug"], "input-output")
        web.enabled = True; db.commit()
        with patch("backend.agent.router.chat_typed", new=AsyncMock(return_value=OllamaChatResult(content=json.dumps({"workflow_id": web.id, "confidence": 0.2, "reason": "x", "inputs": {}})))):
            self.assertEqual((await select_workflow(db, message="x", recent_messages=[], attachments=[], library_slug=None, router_model=None, chat_model="model"))["workflow_slug"], "input-output")
        db.close()

    async def test_fast_paths_auto_select_or_force_web(self):
        db = self.Session()
        web = db.query(WorkflowDefinition).filter_by(slug="web-answer").one()
        combined = db.query(WorkflowDefinition).filter_by(slug="knowledge-web-answer").one()
        with patch(
            "backend.agent.router.chat_typed",
            new=AsyncMock(return_value=OllamaChatResult(content=json.dumps({
                "workflow_id": web.id,
                "confidence": 0.95,
                "reason": "forced web",
                "inputs": {"web_search_query": "Iran news today", "web_search_queries": ["Iran news today", "Iran latest news"]},
            }))),
        ) as mocked:
            result = await select_workflow(
                db, message="hello", recent_messages=[], attachments=[],
                library_slug=None, router_model=None, chat_model="model", web_search_enabled=True,
            )
            self.assertEqual(result["workflow_slug"], "web-answer")
            self.assertEqual(result["inputs"]["web_search_query"], "Iran news today")
            mocked.assert_awaited()

        direct = db.query(WorkflowDefinition).filter_by(slug="input-output").one()
        with patch(
            "backend.agent.router.chat_typed",
            new=AsyncMock(return_value=OllamaChatResult(content=json.dumps({
                "workflow_id": direct.id,
                "confidence": 0.95,
                "reason": "bad router choice",
                "inputs": {},
            }))),
        ):
            result = await select_workflow(
                db, message="what happened in Iran today", recent_messages=[], attachments=[],
                library_slug=None, router_model=None, chat_model="model", web_search_enabled=True,
            )
            self.assertEqual(result["workflow_slug"], "web-answer")
            self.assertEqual(result["inputs"]["web_search_query"], "what happened in Iran today")

        with patch(
            "backend.agent.router.chat_typed",
            new=AsyncMock(return_value=OllamaChatResult(content=json.dumps({
                "workflow_id": web.id,
                "confidence": 0.95,
                "reason": "current",
                "inputs": {"web_search_queries": ["latest Iran news"]},
            }))),
        ):
            result = await select_workflow(
                db, message="latest news today", recent_messages=[], attachments=[],
                library_slug=None, router_model=None, chat_model="model", web_search_enabled=False,
            )
            self.assertEqual(result["workflow_slug"], "web-answer")
            self.assertEqual(result["inputs"]["web_search_query"], "latest Iran news")

        with patch(
            "backend.agent.router.chat_typed",
            new=AsyncMock(return_value=OllamaChatResult(content=json.dumps({
                "workflow_id": combined.id,
                "confidence": 0.95,
                "reason": "forced web with knowledge",
                "inputs": {"web_search_query": "compare notes with latest docs", "web_search_queries": ["compare notes with latest docs"]},
            }))),
        ):
            result = await select_workflow(
                db, message="hello", recent_messages=[], attachments=[],
                library_slug="notes", router_model=None, chat_model="model", web_search_enabled=True,
            )
            self.assertEqual(result["workflow_slug"], "knowledge-web-answer")
            self.assertEqual(result["inputs"]["web_search_query"], "compare notes with latest docs")

        with patch("backend.agent.router.chat_typed", new=AsyncMock()) as mocked:
            result = await select_workflow(
                db, message="hello", recent_messages=[], attachments=[],
                library_slug=None, router_model=None, chat_model="model", web_search_enabled=False,
            )
            self.assertEqual(result["workflow_slug"], "input-output")
            mocked.assert_not_awaited()

            result = await select_workflow(
                db, message="remember this: I prefer short answers", recent_messages=[], attachments=[],
                library_slug="memory", router_model=None, chat_model="model", web_search_enabled=False,
            )
            self.assertEqual(result["workflow_slug"], "remember-this")
        db.close()

    async def test_missing_router_model_falls_back_to_chat_model(self):
        db = self.Session()
        with patch("backend.agent.router.list_model_catalog", new=AsyncMock(return_value={"chat_models": ["chat"]})), patch("backend.agent.router.chat_typed", new=AsyncMock(side_effect=RuntimeError("bad response"))) as mocked:
            result = await select_workflow(db, message="x", recent_messages=[], attachments=[], library_slug=None, router_model="missing", chat_model="chat")
            self.assertEqual(result["workflow_slug"], "input-output")
            self.assertEqual(mocked.await_args.args[0], "chat")
        db.close()

    async def test_router_prompt_formats_followup_context_and_filters_for_forced_web(self):
        db = self.Session()
        web = db.query(WorkflowDefinition).filter_by(slug="web-answer").one()
        with patch(
            "backend.agent.router.chat_typed",
            new=AsyncMock(return_value=OllamaChatResult(content=json.dumps({
                "workflow_id": web.id,
                "confidence": 0.95,
                "reason": "current follow-up",
                "inputs": {"web_search_query": "Iran political news today", "web_search_queries": ["Iran political news today"]},
            }))),
        ) as mocked:
            result = await select_workflow(
                db,
                message="and what's the news politically?",
                recent_messages=[
                    {"role": "user", "content": "what's the weather in Iran today"},
                    {"role": "assistant", "content": "The weather in Tehran, Iran today is clear."},
                ],
                attachments=[],
                library_slug=None,
                router_model=None,
                chat_model="model",
                web_search_enabled=True,
            )

        prompt = mocked.await_args.args[1][0]["content"]
        self.assertEqual(result["inputs"]["web_search_query"], "Iran political news today")
        self.assertIn("Recent conversation:\n1. User: what's the weather in Iran today", prompt)
        self.assertIn("Resolve the current message into a standalone request", prompt)
        self.assertIn("capabilities cover the required work", prompt)
        self.assertIn("Do not choose a workflow because an example sounds similar", prompt)
        self.assertIn("Choose a web-answer workflow when the missing capability is external facts", prompt)
        self.assertIn("Short follow-ups usually inherit the previous subject", prompt)
        self.assertIn("Routing contract:", prompt)
        self.assertIn("default web workflow for direct answer requests", prompt)
        self.assertIn("Choose only when the standalone request needs an investigation", prompt)
        self.assertIn("Allowed workflows. This list is already filtered", prompt)
        self.assertIn("web-answer", prompt)
        self.assertNotIn("input-output", prompt)
        self.assertNotIn("Cost:", prompt)
        self.assertNotIn("Web search mode:", prompt)
        self.assertNotIn("Selected knowledge database:", prompt)
        db.close()

    async def test_long_message_digest_preserves_request_boundaries(self):
        message = (
            "Please summarize the pasted material and keep the answer short.\n"
            + ("A" * 3600)
            + "\nMiddle note: focus on the security implications.\n"
            + ("B" * 3600)
            + "\nActually, compare the risks and benefits instead."
        )

        digest = _format_message_for_router(message)

        self.assertLess(len(digest), len(message))
        self.assertIn("<long_user_message_digest>", digest)
        self.assertIn("Please summarize the pasted material", digest)
        self.assertIn("Middle note: focus on the security implications", digest)
        self.assertIn("Actually, compare the risks and benefits instead.", digest)
        self.assertIn("Original length:", digest)

    async def test_router_prompt_uses_bounded_digest_and_attachment_summary(self):
        message = (
            "Instruction before payload: analyze this local document.\n"
            + "\n".join(f"payload line {index} " + ("x" * 80) for index in range(400))
            + "\nInstruction after payload: do not search the web unless needed."
        )
        manifest = {
            "workflow_id": "direct",
            "slug": "input-output",
            "name": "Input -> Output",
            "routing_description": "Use when no external tool is needed.",
            "examples": [],
            "required_capabilities": ["chat"],
        }

        prompt = _router_prompt(
            message=message,
            recent_messages=[{"role": "assistant", "content": "previous " + ("y" * 2000)}],
            attachments=[{
                "kind": "file",
                "name": "large-report.pdf",
                "mime_type": "application/pdf",
                "size": 12345,
                "text": "attachment text " * 1000,
            }],
            manifests=[manifest],
        )

        self.assertLess(len(prompt), 9000)
        self.assertIn("Current user message or routing digest:", prompt)
        self.assertIn("Instruction before payload", prompt)
        self.assertIn("Instruction after payload", prompt)
        self.assertIn("large-report.pdf", prompt)
        self.assertIn("text_chars=", prompt)
        self.assertNotIn("attachment text attachment text attachment text", prompt)

    async def test_all_builtins_validate(self):
        registry = NativeToolProvider(); register_native_tools(registry)
        for item in BUILTIN_WORKFLOWS:
            with self.subTest(item=item["slug"]):
                self.assertTrue(validate_workflow_graph(item["graph"], registry).valid)

    async def test_research_evaluator_contract_avoids_contradictory_followups(self):
        research = next(item for item in BUILTIN_WORKFLOWS if item["slug"] == "research")
        evaluate = next(node for node in research["graph"]["nodes"] if node["id"] == "evaluate")
        config = evaluate["config"]

        self.assertIn("If sufficient=true, follow_up_query must be an empty string", config["system_template"])
        self.assertIn("If sufficient=false, follow_up_query must be one concise web search query", config["system_template"])
        self.assertIn("Resolved request and router inputs", config["user_template"])
