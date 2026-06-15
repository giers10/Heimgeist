import copy
import unittest

from backend.agent.registry import NativeToolProvider, ToolDefinition
from backend.agent.validation import validate_workflow_graph


async def handler(arguments, _context):
    return {"content": arguments["text"]}


def registry():
    value = NativeToolProvider()
    value.register(ToolDefinition(
        "test.echo", "echo",
        {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False},
        {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"], "additionalProperties": False},
        handler,
    ))
    return value


def valid_graph():
    return {
        "schema_version": 1,
        "inputs": {"prompt": {"type": "string"}},
        "outputs": {"content": {"type": "string"}},
        "nodes": [
            {"id": "input", "type": "input", "position": {"x": 0, "y": 0}, "config": {}},
            {"id": "echo", "type": "tool", "position": {"x": 200, "y": 0}, "config": {"tool": "test.echo", "arguments": {"text": {"$ref": "input.prompt"}}}},
            {"id": "output", "type": "output", "position": {"x": 400, "y": 0}, "config": {"value": {"$ref": "nodes.echo.output"}}},
        ],
        "edges": [
            {"id": "a", "source": "input", "source_handle": "output", "target": "echo", "target_handle": "input"},
            {"id": "b", "source": "echo", "source_handle": "output", "target": "output", "target_handle": "input"},
        ],
        "limits": {"maximum_nodes": 10, "maximum_tool_calls": 5, "maximum_llm_calls": 2, "maximum_runtime_seconds": 30, "maximum_result_chars": 10000, "maximum_concurrency": 2},
    }


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.registry = registry()

    def codes(self, graph):
        return {item.code for item in validate_workflow_graph(graph, self.registry).errors}

    def test_valid_dag(self):
        self.assertTrue(validate_workflow_graph(valid_graph(), self.registry).valid)

    def test_unknown_tool_missing_reference_unreachable_duplicate_and_cycle(self):
        graph = valid_graph(); graph["nodes"][1]["config"]["tool"] = "missing"
        self.assertIn("unknown_tool", self.codes(graph))
        graph = valid_graph(); graph["nodes"][1]["config"]["arguments"]["text"] = {"$ref": "input.missing"}
        self.assertIn("missing_reference", self.codes(graph))
        graph = valid_graph(); graph["nodes"].append({"id": "orphan", "type": "template", "position": {"x": 0, "y": 0}, "config": {"template": "x"}})
        self.assertIn("unreachable_node", self.codes(graph))
        graph = valid_graph(); graph["nodes"].append(copy.deepcopy(graph["nodes"][1]))
        self.assertIn("duplicate_node_id", self.codes(graph))
        graph = valid_graph(); graph["edges"].append({"id": "cycle", "source": "output", "source_handle": "output", "target": "echo", "target_handle": "input"})
        self.assertIn("cycle", self.codes(graph))

    def test_invalid_branch_limits_and_schema(self):
        graph = valid_graph()
        graph["nodes"].insert(2, {"id": "condition", "type": "condition", "position": {"x": 300, "y": 0}, "config": {"value": True, "operation": "equals", "compare_to": True}})
        graph["edges"][1]["target"] = "condition"
        graph["edges"].append({"id": "condition-output", "source": "condition", "source_handle": "true", "target": "output", "target_handle": "input"})
        self.assertIn("invalid_branch", self.codes(graph))
        graph = valid_graph(); graph["limits"]["maximum_nodes"] = 999
        self.assertIn("excessive_limit", self.codes(graph))
        graph = valid_graph(); graph["inputs"]["prompt"] = {"type": "made-up"}
        self.assertIn("malformed_schema", self.codes(graph))
