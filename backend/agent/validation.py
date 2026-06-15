from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from .bindings import iter_references
from .registry import NativeToolProvider, SchemaValidationError, validate_json_schema, validate_schema_definition
from .schemas import ValidationIssue, WorkflowGraph, WorkflowValidationResult


HOST_LIMITS = {
    "maximum_nodes": 80,
    "maximum_tool_calls": 40,
    "maximum_llm_calls": 16,
    "maximum_runtime_seconds": 600,
    "maximum_result_chars": 240_000,
    "maximum_concurrency": 8,
}
RUN_REFERENCE_FIELDS = {
    "session_id", "chat_model", "router_model", "vision_model", "transcription_model",
    "library_slug", "searx_url", "searx_engines", "messages", "attachments",
    "generation_options", "context_blocks", "target_message_id", "target_message_content", "target_url",
}


def _issue(code: str, message: str, node_id: Optional[str] = None, field_path: Optional[str] = None) -> ValidationIssue:
    return ValidationIssue(code=code, message=message, node_id=node_id, field_path=field_path)


def validate_workflow_graph(raw_graph: Dict[str, Any], registry: NativeToolProvider) -> WorkflowValidationResult:
    errors: List[ValidationIssue] = []
    warnings: List[ValidationIssue] = []
    try:
        graph = WorkflowGraph.model_validate(raw_graph)
    except ValidationError as exc:
        for item in exc.errors():
            errors.append(_issue("malformed_graph", item["msg"], field_path=".".join(str(p) for p in item["loc"])))
        return WorkflowValidationResult(valid=False, errors=errors)

    if graph.schema_version != 1:
        errors.append(_issue("unsupported_schema_version", "Only workflow schema_version 1 is supported.", field_path="schema_version"))

    for name, schema in {**graph.inputs, **graph.outputs}.items():
        try:
            validate_schema_definition(schema, f"schema.{name}")
        except SchemaValidationError as exc:
            errors.append(_issue("malformed_schema", str(exc), field_path=f"inputs.{name}"))

    node_ids = [node.id for node in graph.nodes]
    edge_ids = [edge.id for edge in graph.edges]
    for identifier, values, path in (("node", node_ids, "nodes"), ("edge", edge_ids, "edges")):
        seen = set()
        for value in values:
            if not value:
                errors.append(_issue(f"empty_{identifier}_id", f"{identifier.title()} IDs cannot be empty.", field_path=path))
            elif value in seen:
                errors.append(_issue(f"duplicate_{identifier}_id", f"Duplicate {identifier} ID: {value}", field_path=path))
            seen.add(value)

    nodes = {node.id: node for node in graph.nodes}
    input_nodes = [node for node in graph.nodes if node.type == "input"]
    output_nodes = [node for node in graph.nodes if node.type == "output"]
    if not input_nodes:
        errors.append(_issue("missing_input_node", "A workflow must contain an input node."))
    if not output_nodes:
        errors.append(_issue("missing_output_node", "A workflow must contain an output node."))

    outgoing: Dict[str, List[Any]] = defaultdict(list)
    incoming: Dict[str, List[Any]] = defaultdict(list)
    for edge in graph.edges:
        if edge.source not in nodes:
            errors.append(_issue("missing_edge_source", f"Edge source does not exist: {edge.source}", field_path=f"edges.{edge.id}.source"))
        if edge.target not in nodes:
            errors.append(_issue("missing_edge_target", f"Edge target does not exist: {edge.target}", field_path=f"edges.{edge.id}.target"))
        if edge.source in nodes and edge.target in nodes:
            outgoing[edge.source].append(edge)
            incoming[edge.target].append(edge)

    indegree = {node_id: len(incoming[node_id]) for node_id in nodes}
    queue = deque(node_id for node_id, degree in indegree.items() if degree == 0)
    visited: List[str] = []
    while queue:
        node_id = queue.popleft()
        visited.append(node_id)
        for edge in outgoing[node_id]:
            indegree[edge.target] -= 1
            if indegree[edge.target] == 0:
                queue.append(edge.target)
    if len(visited) != len(nodes):
        errors.append(_issue("cycle", "Workflow schema version 1 must be a DAG; cycles are not allowed."))

    reachable = set()
    frontier = [node.id for node in input_nodes]
    while frontier:
        node_id = frontier.pop()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        frontier.extend(edge.target for edge in outgoing[node_id])
    for node in graph.nodes:
        if node.type not in {"input"} and node.id not in reachable:
            errors.append(_issue("unreachable_node", f"Node {node.id} is not reachable from an input node.", node.id))

    for node in graph.nodes:
        if node.type == "tool":
            tool_name = str(node.config.get("tool") or "")
            try:
                tool = registry.get_tool(tool_name)
            except KeyError:
                errors.append(_issue("unknown_tool", f"Unknown tool: {tool_name or '(missing)'}", node.id, "config.tool"))
            else:
                arguments = node.config.get("arguments", {})
                if not isinstance(arguments, dict):
                    errors.append(_issue("invalid_tool_arguments", "Tool arguments must be an object.", node.id, "config.arguments"))
                else:
                    literal_arguments = {
                        key: value for key, value in arguments.items()
                        if not (isinstance(value, dict) and set(value) == {"$ref"})
                    }
                    try:
                        validate_json_schema(literal_arguments, tool.input_schema, partial=True)
                    except SchemaValidationError as exc:
                        errors.append(_issue("invalid_tool_arguments", str(exc), node.id, "config.arguments"))
        if node.type == "condition":
            operation = node.config.get("operation")
            allowed = {"equals", "not_equals", "exists", "greater_than", "less_than", "contains", "array_not_empty", "score_at_least"}
            if operation not in allowed:
                errors.append(_issue("invalid_condition", f"Unsupported condition operation: {operation}", node.id, "config.operation"))
            handles = {edge.source_handle for edge in outgoing[node.id]}
            if not {"true", "false"}.issubset(handles):
                errors.append(_issue("invalid_branch", "Condition nodes require both true and false outgoing branches.", node.id))
        if node.type == "prompt" and not node.config.get("model_source"):
            errors.append(_issue("missing_model_source", "Prompt nodes require model_source.", node.id, "config.model_source"))

        for field_path, reference in iter_references(node.config, f"nodes.{node.id}.config"):
            parts = reference.split(".")
            if parts[0] == "input" and (len(parts) < 2 or parts[1] not in graph.inputs):
                errors.append(_issue("missing_reference", f"Unknown workflow input reference: {reference}", node.id, field_path))
            elif parts[0] == "run" and (len(parts) < 2 or parts[1] not in RUN_REFERENCE_FIELDS):
                errors.append(_issue("missing_reference", f"Unknown run reference: {reference}", node.id, field_path))
            elif parts[0] == "nodes" and (len(parts) < 3 or parts[1] not in nodes):
                errors.append(_issue("missing_reference", f"Unknown node reference: {reference}", node.id, field_path))
            elif parts[0] not in {"input", "run", "nodes"}:
                errors.append(_issue("missing_reference", f"Invalid reference: {reference}", node.id, field_path))

    limits = graph.limits.model_dump()
    for key, maximum in HOST_LIMITS.items():
        value = limits[key]
        if value < 1 or value > maximum:
            errors.append(_issue("excessive_limit", f"{key} must be between 1 and {maximum}.", field_path=f"limits.{key}"))
    if len(graph.nodes) > graph.limits.maximum_nodes:
        errors.append(_issue("excessive_nodes", "The graph contains more nodes than its maximum_nodes limit.", field_path="nodes"))

    return WorkflowValidationResult(valid=not errors, errors=errors, warnings=warnings)
