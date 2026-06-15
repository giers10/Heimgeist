from datetime import datetime
import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from ..database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class WorkflowDefinition(Base):
    __tablename__ = "workflow_definitions"

    id = Column(String(36), primary_key=True, default=_uuid)
    slug = Column(String(100), unique=True, nullable=False, index=True)
    name = Column(String(160), nullable=False)
    description = Column(Text, nullable=False, default="")
    current_revision_id = Column(String(36), nullable=True)
    built_in = Column(Boolean, nullable=False, default=False)
    enabled = Column(Boolean, nullable=False, default=True)
    routing_description = Column(Text, nullable=False, default="")
    routing_examples_json = Column(Text, nullable=False, default="[]")
    estimated_cost_class = Column(String(32), nullable=False, default="low")
    required_capabilities_json = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    revisions = relationship(
        "WorkflowRevision",
        back_populates="workflow",
        foreign_keys="WorkflowRevision.workflow_id",
        cascade="all, delete-orphan",
    )


class WorkflowRevision(Base):
    __tablename__ = "workflow_revisions"
    __table_args__ = (UniqueConstraint("workflow_id", "version", name="uq_workflow_revision_version"),)

    id = Column(String(36), primary_key=True, default=_uuid)
    workflow_id = Column(String(36), ForeignKey("workflow_definitions.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    graph_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_by = Column(String(64), nullable=False, default="user")
    trusted = Column(Boolean, nullable=False, default=False)
    checksum = Column(String(64), nullable=False, index=True)

    workflow = relationship("WorkflowDefinition", back_populates="revisions", foreign_keys=[workflow_id])


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id = Column(String(36), primary_key=True, default=_uuid)
    workflow_id = Column(String(36), ForeignKey("workflow_definitions.id"), nullable=False, index=True)
    workflow_revision_id = Column(String(36), ForeignKey("workflow_revisions.id"), nullable=False)
    session_id = Column(String(64), nullable=True, index=True)
    user_message_id = Column(String(36), nullable=True)
    status = Column(String(32), nullable=False, default="queued", index=True)
    selection_mode = Column(String(24), nullable=False, default="direct")
    inputs_json = Column(Text, nullable=False, default="{}")
    outputs_json = Column(Text, nullable=True)
    error_json = Column(Text, nullable=True)
    metrics_json = Column(Text, nullable=False, default="{}")
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    cancellation_requested = Column(Boolean, nullable=False, default=False)


class WorkflowNodeRun(Base):
    __tablename__ = "workflow_node_runs"
    __table_args__ = (UniqueConstraint("workflow_run_id", "node_id", "attempt", name="uq_node_run_attempt"),)

    id = Column(String(36), primary_key=True, default=_uuid)
    workflow_run_id = Column(String(36), ForeignKey("workflow_runs.id"), nullable=False, index=True)
    node_id = Column(String(120), nullable=False)
    attempt = Column(Integer, nullable=False, default=1)
    status = Column(String(32), nullable=False, default="queued")
    resolved_inputs_json = Column(Text, nullable=True)
    outputs_json = Column(Text, nullable=True)
    error_json = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    metrics_json = Column(Text, nullable=False, default="{}")


class WorkflowEvent(Base):
    __tablename__ = "workflow_events"
    __table_args__ = (UniqueConstraint("workflow_run_id", "sequence", name="uq_workflow_event_sequence"),)

    id = Column(String(36), primary_key=True, default=_uuid)
    workflow_run_id = Column(String(36), ForeignKey("workflow_runs.id"), nullable=False, index=True)
    workflow_id = Column(String(36), nullable=False)
    node_id = Column(String(120), nullable=True)
    sequence = Column(Integer, nullable=False)
    event_type = Column(String(64), nullable=False)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    payload_json = Column(Text, nullable=False, default="{}")


class WorkflowConfirmation(Base):
    __tablename__ = "workflow_confirmations"

    id = Column(String(36), primary_key=True, default=_uuid)
    workflow_run_id = Column(String(36), ForeignKey("workflow_runs.id"), nullable=False, index=True)
    node_id = Column(String(120), nullable=False)
    tool_name = Column(String(160), nullable=False)
    status = Column(String(24), nullable=False, default="pending")
    request_json = Column(Text, nullable=False, default="{}")
    response_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
