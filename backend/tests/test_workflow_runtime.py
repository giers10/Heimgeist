import asyncio
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.agent.models import WorkflowDefinition, WorkflowEvent, WorkflowRevision, WorkflowRun
from backend.agent.registry import NativeToolProvider, ToolDefinition
from backend.agent.runtime import WorkflowRuntime
from backend.database import Base
from backend.models import ChatMessage, ChatSession


def graph(nodes, edges, maximum_tool_calls=10):
    return {
        "schema_version": 1,
        "inputs": {"prompt": {"type": "string"}},
        "outputs": {"content": {"type": "string"}},
        "nodes": nodes,
        "edges": edges,
        "limits": {"maximum_nodes": 20, "maximum_tool_calls": maximum_tool_calls, "maximum_llm_calls": 5, "maximum_runtime_seconds": 5, "maximum_result_chars": 20000, "maximum_concurrency": 4},
    }


def node(node_id, node_type, config=None):
    return {"id": node_id, "type": node_type, "position": {"x": 0, "y": 0}, "config": config or {}}


def edge(source, target, handle="output"):
    return {"id": f"{source}-{target}-{handle}", "source": source, "source_handle": handle, "target": target, "target_handle": "input"}


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name) / 'test.db'}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.registry = NativeToolProvider()

        async def echo(arguments, _context):
            await asyncio.sleep(arguments.get("delay", 0))
            if arguments.get("fail"):
                raise RuntimeError("requested failure")
            return {"content": arguments.get("text", ""), "sources": arguments.get("sources", [])}

        schema = {
            "type": "object",
            "properties": {"text": {"type": "string"}, "delay": {"type": "number"}, "fail": {"type": "boolean"}, "sources": {"type": "array"}},
            "required": ["text"], "additionalProperties": False,
        }
        output = {"type": "object", "properties": {"content": {"type": "string"}, "sources": {"type": "array"}}, "required": ["content", "sources"], "additionalProperties": False}
        self.registry.register(ToolDefinition("test.echo", "echo", schema, output, echo))
        self.runtime = WorkflowRuntime(self.registry, self.Session)

    async def asyncTearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def create_run(self, graph_value, session=True):
        db = self.Session()
        workflow = WorkflowDefinition(slug=f"w-{uuid.uuid4().hex}", name="Test", built_in=False, enabled=True)
        db.add(workflow); db.flush()
        revision = WorkflowRevision(workflow_id=workflow.id, version=1, graph_json=json.dumps(graph_value), checksum="x", created_by="test")
        db.add(revision); db.flush(); workflow.current_revision_id = revision.id
        session_id = None
        user_id = None
        if session:
            chat = ChatSession(session_id=f"s-{id(graph_value)}")
            db.add(chat); db.flush()
            user = ChatMessage(session_pk=chat.id, role="user", content="hello")
            db.add(user); db.flush(); session_id = chat.session_id; user_id = user.message_id
        run = WorkflowRun(
            workflow_id=workflow.id, workflow_revision_id=revision.id, session_id=session_id, user_message_id=user_id,
            selection_mode="explicit", inputs_json=json.dumps({"input": {"prompt": "hello"}, "run": {"messages": [{"role": "user", "content": "hello"}]}}),
        )
        db.add(run); db.commit(); run_id = run.id; db.close()
        return run_id

    async def execute(self, run_id):
        await self.runtime.execute(run_id)
        db = self.Session(); run = db.query(WorkflowRun).filter_by(id=run_id).first(); db.expunge(run); db.close(); return run

    async def test_basic_persistence_and_source_propagation(self):
        value = graph(
            [node("input", "input"), node("echo", "tool", {"tool": "test.echo", "arguments": {"text": {"$ref": "input.prompt"}, "sources": [{"url": "https://example.com"}]}}), node("output", "output", {"value": {"$ref": "nodes.echo.output"}})],
            [edge("input", "echo"), edge("echo", "output")],
        )
        run = await self.execute(self.create_run(value))
        self.assertEqual(run.status, "completed")
        db = self.Session()
        assistant = db.query(ChatMessage).filter(ChatMessage.role == "assistant").one()
        self.assertEqual(assistant.content, "hello")
        self.assertEqual(json.loads(assistant.sources_json)[0]["url"], "https://example.com")
        self.assertTrue(db.query(WorkflowEvent).filter(WorkflowEvent.event_type == "source_found").count())
        db.close()

        test_run = await self.execute(self.create_run(value, session=False))
        self.assertEqual(test_run.status, "completed")

    async def test_concurrent_nodes_and_condition_branch(self):
        value = graph(
            [
                node("input", "input"),
                node("a", "tool", {"tool": "test.echo", "arguments": {"text": "a", "delay": 0.04}}),
                node("b", "tool", {"tool": "test.echo", "arguments": {"text": "b", "delay": 0.04}}),
                node("condition", "condition", {"value": True, "operation": "equals", "compare_to": True}),
                node("yes", "template", {"template": "yes"}),
                node("no", "template", {"template": "no"}),
                node("output", "output", {"value": {"content": {"$ref": "nodes.yes.output"}, "sources": []}}),
            ],
            [edge("input", "a"), edge("input", "b"), edge("a", "condition"), edge("b", "condition"), edge("condition", "yes", "true"), edge("condition", "no", "false"), edge("yes", "output")],
        )
        run = await self.execute(self.create_run(value))
        self.assertEqual(run.status, "completed")
        db = self.Session()
        statuses = {row.node_id: row.status for row in db.query(__import__('backend.agent.models', fromlist=['WorkflowNodeRun']).WorkflowNodeRun).all()}
        self.assertEqual(statuses["no"], "skipped")
        db.close()

    async def test_failure_cancellation_and_tool_limit(self):
        failing = graph(
            [node("input", "input"), node("bad", "tool", {"tool": "test.echo", "arguments": {"text": "x", "fail": True}}), node("output", "output", {"value": {"$ref": "nodes.bad.output"}})],
            [edge("input", "bad"), edge("bad", "output")],
        )
        self.assertEqual((await self.execute(self.create_run(failing))).status, "failed")

        limited = graph(
            [node("input", "input"), node("a", "tool", {"tool": "test.echo", "arguments": {"text": "a"}}), node("b", "tool", {"tool": "test.echo", "arguments": {"text": "b"}}), node("output", "output", {"value": {"$ref": "nodes.b.output"}})],
            [edge("input", "a"), edge("a", "b"), edge("b", "output")], maximum_tool_calls=1,
        )
        self.assertEqual((await self.execute(self.create_run(limited))).status, "failed")

        slow = graph(
            [node("input", "input"), node("slow", "tool", {"tool": "test.echo", "arguments": {"text": "x", "delay": 0.3}}), node("output", "output", {"value": {"$ref": "nodes.slow.output"}})],
            [edge("input", "slow"), edge("slow", "output")],
        )
        run_id = self.create_run(slow)
        task = asyncio.create_task(self.runtime.execute(run_id))
        await asyncio.sleep(0.03)
        self.runtime.cancel(run_id)
        await task
        db = self.Session(); status = db.query(WorkflowRun).filter_by(id=run_id).one().status; db.close()
        self.assertEqual(status, "cancelled")
