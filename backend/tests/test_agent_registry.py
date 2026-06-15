import asyncio
import unittest

from backend.agent.registry import (
    NativeToolProvider,
    SchemaValidationError,
    ToolDefinition,
    ToolExecutionContext,
)


INPUT = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}
OUTPUT = {
    "type": "object",
    "properties": {"result": {"type": "string"}},
    "required": ["result"],
    "additionalProperties": False,
}


def context(registry):
    return ToolExecutionContext(
        run_id="run", workflow_id="workflow", node_id="node", session_id=None,
        selection_mode="explicit", explicit_user_action=True,
        emit=lambda *_args: asyncio.sleep(0), cancellation_event=asyncio.Event(),
        db_factory=lambda: None, registry=registry,
    )


class RegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_names_are_rejected(self):
        async def handler(arguments, _context): return {"result": arguments["value"]}
        registry = NativeToolProvider()
        definition = ToolDefinition("test.echo", "echo", INPUT, OUTPUT, handler)
        registry.register(definition)
        with self.assertRaises(ValueError):
            registry.register(definition)

    async def test_arguments_and_outputs_are_validated(self):
        async def handler(arguments, _context): return {"result": arguments["value"]}
        registry = NativeToolProvider()
        registry.register(ToolDefinition("test.echo", "echo", INPUT, OUTPUT, handler))
        self.assertEqual((await registry.call_tool("test.echo", {"value": "ok"}, context(registry)))["result"], "ok")
        with self.assertRaises(SchemaValidationError):
            await registry.call_tool("test.echo", {"value": 1}, context(registry))

        async def invalid_output(_arguments, _context): return {"result": 1}
        invalid = NativeToolProvider()
        invalid.register(ToolDefinition("test.invalid", "invalid", INPUT, OUTPUT, invalid_output))
        with self.assertRaises(SchemaValidationError):
            await invalid.call_tool("test.invalid", {"value": "x"}, context(invalid))

    async def test_timeout_and_result_size_are_enforced(self):
        async def slow(_arguments, _context):
            await asyncio.sleep(0.05)
            return {"result": "ok"}
        registry = NativeToolProvider()
        registry.register(ToolDefinition("test.slow", "slow", INPUT, OUTPUT, slow, timeout_seconds=0.01))
        with self.assertRaises(TimeoutError):
            await registry.call_tool("test.slow", {"value": "x"}, context(registry))

        async def large(_arguments, _context): return {"result": "x" * 100}
        limited = NativeToolProvider()
        limited.register(ToolDefinition("test.large", "large", INPUT, OUTPUT, large, result_size_limit=20))
        with self.assertRaises(ValueError):
            await limited.call_tool("test.large", {"value": "x"}, context(limited))
