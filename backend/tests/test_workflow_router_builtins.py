import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.agent.models import WorkflowDefinition
from backend.agent.registry import NativeToolProvider
from backend.agent.router import select_workflow
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
        with patch("backend.agent.router.chat_typed", new=AsyncMock()) as mocked:
            result = await select_workflow(
                db, message="hello", recent_messages=[], attachments=[],
                library_slug=None, router_model=None, chat_model="model", web_search_enabled=True,
            )
            self.assertEqual(result["workflow_slug"], "web-answer")
            mocked.assert_not_awaited()

            result = await select_workflow(
                db, message="latest news today", recent_messages=[], attachments=[],
                library_slug=None, router_model=None, chat_model="model", web_search_enabled=False,
            )
            self.assertEqual(result["workflow_slug"], "web-answer")

            result = await select_workflow(
                db, message="hello", recent_messages=[], attachments=[],
                library_slug="notes", router_model=None, chat_model="model", web_search_enabled=True,
            )
            self.assertEqual(result["workflow_slug"], "knowledge-web-answer")

            result = await select_workflow(
                db, message="hello", recent_messages=[], attachments=[],
                library_slug=None, router_model=None, chat_model="model", web_search_enabled=False,
            )
            self.assertEqual(result["workflow_slug"], "input-output")

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

    async def test_all_builtins_validate(self):
        registry = NativeToolProvider(); register_native_tools(registry)
        for item in BUILTIN_WORKFLOWS:
            with self.subTest(item=item["slug"]):
                self.assertTrue(validate_workflow_graph(item["graph"], registry).valid)
