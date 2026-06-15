from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional, Protocol


class SchemaValidationError(ValueError):
    pass


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def validate_json_schema(value: Any, schema: Dict[str, Any], path: str = "$", *, partial: bool = False) -> None:
    if not isinstance(schema, dict):
        raise SchemaValidationError(f"{path}: schema must be an object")
    if "const" in schema and value != schema["const"]:
        raise SchemaValidationError(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaValidationError(f"{path}: must be one of {schema['enum']!r}")
    for union_key in ("oneOf", "anyOf"):
        if union_key in schema:
            matches = 0
            errors = []
            for candidate in schema[union_key]:
                try:
                    validate_json_schema(value, candidate, path, partial=partial)
                    matches += 1
                except SchemaValidationError as exc:
                    errors.append(str(exc))
            if (union_key == "oneOf" and matches != 1) or (union_key == "anyOf" and matches == 0):
                raise SchemaValidationError(f"{path}: does not match {union_key}: {'; '.join(errors[:3])}")
            return

    expected = schema.get("type")
    if isinstance(expected, list):
        if not any(_type_matches(value, item) for item in expected):
            raise SchemaValidationError(f"{path}: expected one of {expected}, got {type(value).__name__}")
    elif expected and not _type_matches(value, expected):
        raise SchemaValidationError(f"{path}: expected {expected}, got {type(value).__name__}")

    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        if not partial:
            for required in schema.get("required") or []:
                if required not in value:
                    raise SchemaValidationError(f"{path}.{required}: required value is missing")
        if schema.get("additionalProperties") is False:
            unknown = [key for key in value if key not in properties]
            if unknown:
                raise SchemaValidationError(f"{path}: unknown properties: {', '.join(unknown)}")
        for key, item in value.items():
            if key in properties:
                validate_json_schema(item, properties[key], f"{path}.{key}", partial=partial)
        minimum = schema.get("minProperties")
        if minimum is not None and len(value) < minimum:
            raise SchemaValidationError(f"{path}: expected at least {minimum} properties")
    elif isinstance(value, list):
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            raise SchemaValidationError(f"{path}: expected at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            raise SchemaValidationError(f"{path}: expected at most {schema['maxItems']} items")
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                validate_json_schema(item, item_schema, f"{path}[{index}]", partial=partial)
    elif isinstance(value, str):
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            raise SchemaValidationError(f"{path}: string is too short")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            raise SchemaValidationError(f"{path}: string is too long")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaValidationError(f"{path}: must be at least {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise SchemaValidationError(f"{path}: must be at most {schema['maximum']}")


def validate_schema_definition(schema: Dict[str, Any], path: str = "$schema") -> None:
    if not isinstance(schema, dict):
        raise SchemaValidationError(f"{path}: schema must be an object")
    expected = schema.get("type")
    allowed = {"object", "array", "string", "boolean", "integer", "number", "null"}
    if isinstance(expected, str) and expected not in allowed:
        raise SchemaValidationError(f"{path}.type: unsupported type {expected!r}")
    if isinstance(expected, list) and any(item not in allowed for item in expected):
        raise SchemaValidationError(f"{path}.type: contains an unsupported type")
    if expected == "object":
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise SchemaValidationError(f"{path}.properties: must be an object")
        for key, child in properties.items():
            validate_schema_definition(child, f"{path}.properties.{key}")
        required = schema.get("required", [])
        if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
            raise SchemaValidationError(f"{path}.required: must be a string array")
        missing = [item for item in required if item not in properties]
        if missing:
            raise SchemaValidationError(f"{path}.required: unknown properties: {', '.join(missing)}")
    if expected == "array" and "items" in schema:
        validate_schema_definition(schema["items"], f"{path}.items")
    for key in ("oneOf", "anyOf"):
        if key in schema:
            if not isinstance(schema[key], list) or not schema[key]:
                raise SchemaValidationError(f"{path}.{key}: must be a non-empty array")
            for index, child in enumerate(schema[key]):
                validate_schema_definition(child, f"{path}.{key}[{index}]")


ToolHandler = Callable[[Dict[str, Any], "ToolExecutionContext"], Awaitable[Dict[str, Any]]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    handler: ToolHandler
    risk_level: str = "low"
    timeout_seconds: float = 120.0
    result_size_limit: int = 120_000
    requires_confirmation: bool = False
    llm_call: bool = False

    def manifest(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "outputSchema": self.output_schema,
            "riskLevel": self.risk_level,
            "timeoutSeconds": self.timeout_seconds,
            "resultSizeLimit": self.result_size_limit,
            "requiresConfirmation": self.requires_confirmation,
        }

    def ollama_manifest(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


@dataclass
class ToolExecutionContext:
    run_id: str
    workflow_id: str
    node_id: str
    session_id: Optional[str]
    selection_mode: str
    explicit_user_action: bool
    emit: Callable[[str, Dict[str, Any]], Awaitable[None]]
    cancellation_event: asyncio.Event
    db_factory: Callable[[], Any]
    registry: Optional["NativeToolProvider"] = None
    run_values: Dict[str, Any] = field(default_factory=dict)
    request_confirmation: Optional[Callable[[ToolDefinition, Dict[str, Any]], Awaitable[bool]]] = None
    consume_llm_call: Optional[Callable[[], None]] = None
    show_thinking: bool = False


class ToolProvider(Protocol):
    def list_tools(self) -> Iterable[Dict[str, Any]]: ...
    def get_tool(self, name: str) -> ToolDefinition: ...
    async def call_tool(self, name: str, arguments: Dict[str, Any], context: ToolExecutionContext) -> Dict[str, Any]: ...


class NativeToolProvider:
    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._tools:
            raise ValueError(f"Duplicate tool name: {definition.name}")
        validate_schema_definition(definition.input_schema, f"{definition.name}.inputSchema")
        validate_schema_definition(definition.output_schema, f"{definition.name}.outputSchema")
        self._tools[definition.name] = definition

    def list_tools(self) -> Iterable[Dict[str, Any]]:
        return [tool.manifest() for tool in sorted(self._tools.values(), key=lambda item: item.name)]

    def get_tool(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {name}") from exc

    async def call_tool(self, name: str, arguments: Dict[str, Any], context: ToolExecutionContext) -> Dict[str, Any]:
        definition = self.get_tool(name)
        validate_json_schema(arguments, definition.input_schema)
        if context.cancellation_event.is_set():
            raise asyncio.CancelledError()
        if definition.llm_call and context.consume_llm_call:
            context.consume_llm_call()
        try:
            result = await asyncio.wait_for(
                definition.handler(arguments, context),
                timeout=definition.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"Tool {name} exceeded {definition.timeout_seconds:g}s") from exc
        validate_json_schema(result, definition.output_schema)
        size = len(json.dumps(result, ensure_ascii=False, default=str))
        if size > definition.result_size_limit:
            raise ValueError(f"Tool {name} result exceeded {definition.result_size_limit} characters")
        return result
