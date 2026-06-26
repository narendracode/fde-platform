"""SQLAlchemy ORM models for the agent platform."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Agent(Base):
    """Registered agent configuration."""

    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[str] = mapped_column(String(20), default="1.0.0")
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    runs: Mapped[list[AgentRun]] = relationship("AgentRun", back_populates="agent", lazy="dynamic")


class AgentRun(Base):
    """Record of a single agent invocation."""

    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False, index=True
    )
    # LangGraph thread ID for conversation continuity
    thread_id: Mapped[str | None] = mapped_column(String(100), index=True)
    # pending | running | completed | failed | cancelled
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    task_id: Mapped[str | None] = mapped_column(String(100), index=True)  # Celery task ID
    input: Mapped[dict | None] = mapped_column(JSONB)
    output: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    # Token accounting
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    # LangSmith links — set when tracing is enabled
    langsmith_run_id: Mapped[str | None] = mapped_column(String(100), index=True)
    langsmith_trace_url: Mapped[str | None] = mapped_column(Text)
    # OpenTelemetry — trace ID (32-char hex) + full Jaeger deep-link URL
    otel_trace_id: Mapped[str | None] = mapped_column(String(32), index=True)
    otel_trace_url: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    agent: Mapped[Agent] = relationship("Agent", back_populates="runs")


class Order(Base):
    """Pharma distributor order pending shipment mode assignment."""

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_ref: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    retailer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    medicine_name: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_usd: Mapped[float] = mapped_column(Float, nullable=False)
    order_amount_usd: Mapped[float] = mapped_column(Float, nullable=False)
    margin_percent: Mapped[float] = mapped_column(Float, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Workflow state
    # pending → pending_review → ready_to_dispatch → dispatched
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    shipment_mode: Mapped[str | None] = mapped_column(String(20))   # air | train | road
    decided_by: Mapped[str | None] = mapped_column(String(20))      # human | ai

    # AI recommendation fields (populated in human-in-the-loop mode)
    ai_recommended_mode: Mapped[str | None] = mapped_column(String(20))
    ai_confidence: Mapped[str | None] = mapped_column(String(20))   # high | medium | low
    ai_reasoning: Mapped[str | None] = mapped_column(Text)

    # Links back to the agent run that made this decision
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentAction(Base):
    """A proposed action from an agent awaiting human review.

    Self-describing: display_data drives the UI, approval_action drives execution.
    The platform calls approval_action on human approval — no domain-specific code needed.
    """

    __tablename__ = "agent_actions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Provenance
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=True, index=True
    )

    # What to show the human
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[str | None] = mapped_column(String(20), nullable=True)  # high|medium|low
    display_data: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # What to execute on approval: {method, url, url_params?, body?, body_schema?}
    approval_action: Mapped[dict] = mapped_column(JSONB, nullable=False)
    rejection_action: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Lifecycle: pending_review | approved | rejected | approval_failed | expired
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending_review", index=True)
    decided_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    override_body: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    approval_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PlatformSettings(Base):
    """Key-value store for platform-level feature flags and operational settings."""

    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
