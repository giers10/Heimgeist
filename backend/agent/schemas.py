from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class WorkflowPosition(BaseModel):
    x: float = 0
    y: float = 0


class WorkflowNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: Literal["input", "output", "tool", "prompt", "agent", "condition", "merge", "template", "select", "limit"]
    position: WorkflowPosition = Field(default_factory=WorkflowPosition)
    config: Dict[str, Any] = Field(default_factory=dict)


class WorkflowEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    source_handle: str = "output"
    target: str
    target_handle: str = "input"


class WorkflowLimits(BaseModel):
    maximum_nodes: int = 40
    maximum_tool_calls: int = 20
    maximum_llm_calls: int = 8
    maximum_runtime_seconds: int = 300
    maximum_result_chars: int = 120_000
    maximum_concurrency: int = 4


class WorkflowGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    inputs: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    outputs: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    nodes: List[WorkflowNode]
    edges: List[WorkflowEdge]
    limits: WorkflowLimits = Field(default_factory=WorkflowLimits)


class ValidationIssue(BaseModel):
    code: str
    message: str
    node_id: Optional[str] = None
    field_path: Optional[str] = None


class WorkflowValidationResult(BaseModel):
    valid: bool
    errors: List[ValidationIssue] = Field(default_factory=list)
    warnings: List[ValidationIssue] = Field(default_factory=list)


class WorkflowCreateRequest(BaseModel):
    name: str
    slug: Optional[str] = None
    description: str = ""
    routing_description: str = ""
    routing_examples: List[str] = Field(default_factory=list)
    enabled: bool = True
    graph: Dict[str, Any]


class WorkflowUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    routing_description: Optional[str] = None
    routing_examples: Optional[List[str]] = None
    enabled: Optional[bool] = None


class WorkflowRevisionRequest(BaseModel):
    graph: Dict[str, Any]
    created_by: str = "user"


class WorkflowValidateRequest(BaseModel):
    graph: Dict[str, Any]


class WorkflowRunRequest(BaseModel):
    session_id: Optional[str] = None
    message: str = ""
    model: str
    selection_mode: Literal["direct", "automatic", "explicit"] = "direct"
    workflow_id: Optional[str] = None
    workflow_revision_id: Optional[str] = None
    library_slug: Optional[str] = None
    web_search_enabled: bool = False
    searx_url: Optional[str] = None
    searx_engines: List[str] = Field(default_factory=list)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    vision_model: Optional[str] = None
    transcription_model: Optional[str] = None
    stream: bool = True
    router_model: Optional[str] = None
    show_thinking: bool = False
    generation_options: Dict[str, Any] = Field(default_factory=dict)
    sample_inputs: Dict[str, Any] = Field(default_factory=dict)
    explicit_user_action: bool = False
    regenerate_index: Optional[int] = Field(default=None, ge=0)


class ConfirmationResponseRequest(BaseModel):
    approved: bool
    note: Optional[str] = None


class RouterSelectRequest(BaseModel):
    message: str
    recent_messages: List[Dict[str, Any]] = Field(default_factory=list)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    library_slug: Optional[str] = None
    web_search_enabled: bool = False
    model: Optional[str] = None
    chat_model: Optional[str] = None
    confidence_threshold: float = 0.55
