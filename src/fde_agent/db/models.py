"""SQLAlchemy ORM models for the agent platform."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

try:
    from pgvector.sqlalchemy import Vector as _Vector
    _VECTOR_TYPE = _Vector(1536)
except ImportError:
    _Vector = None
    _VECTOR_TYPE = None


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

    # Lifecycle: pending_review | approved | rejected | approval_failed | expired | stale | drifted
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending_review", index=True)
    decided_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    override_body: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    approval_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Staleness: agent captures resource state at propose time; platform auto-marks stale at inbox load
    expected_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    stale_after_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stale_marked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Drift detection: platform re-checks resource state at approval time
    drift_detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    drift_details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    drift_override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentRefineSession(Base):
    """A conversational refinement session attached to an AgentAction.

    Created when a planner opens the 'Refine with AI' canvas on a pending action.
    One active session per action at a time; past sessions kept for LLMOps audit.
    """

    __tablename__ = "agent_refine_session"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_actions.id"), nullable=False, index=True
    )
    refinement_agent: Mapped[str] = mapped_column(String(100), nullable=False)
    # active | approved | closed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    opened_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentRefineMessage(Base):
    """A single message turn (user or assistant) inside a refinement session."""

    __tablename__ = "agent_refine_message"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_refine_session.id"), nullable=False, index=True
    )
    # user | assistant | system
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_calls: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    context_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    langsmith_run_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    langsmith_trace_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlatformSettings(Base):
    """Key-value store for platform-level feature flags and operational settings."""

    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ── Sandhar Production Planning Models ────────────────────────────────────────

class SandharEmployee(Base):
    __tablename__ = "sandhar_employees"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    department: Mapped[str | None] = mapped_column(String(50))
    designation: Mapped[str | None] = mapped_column(String(50))
    grade: Mapped[str | None] = mapped_column(String(20))
    shift_group: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="active")
    joining_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SandharLine(Base):
    __tablename__ = "sandhar_lines"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    line_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    line_name: Mapped[str] = mapped_column(String(100), nullable=False)
    area: Mapped[str | None] = mapped_column(String(100))
    capacity_per_shift: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SandharMachine(Base):
    __tablename__ = "sandhar_machines"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    machine_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    machine_name: Mapped[str] = mapped_column(String(100), nullable=False)
    line_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_lines.id"), nullable=True)
    machine_type: Mapped[str | None] = mapped_column(String(50))
    capacity_per_hour: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SandharCustomer(Base):
    __tablename__ = "sandhar_customers"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    customer_name: Mapped[str] = mapped_column(String(100), nullable=False)
    priority_level: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SandharProduct(Base):
    __tablename__ = "sandhar_products"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    product_name: Mapped[str] = mapped_column(String(100), nullable=False)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_customers.id"), nullable=True)
    standard_cycle_time: Mapped[float | None] = mapped_column(Float)
    standard_manpower: Mapped[int | None] = mapped_column(Integer)
    line_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_lines.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SandharShift(Base):
    __tablename__ = "sandhar_shifts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shift_code: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    shift_name: Mapped[str | None] = mapped_column(String(50))
    start_time: Mapped[str | None] = mapped_column(String(10))
    end_time: Mapped[str | None] = mapped_column(String(10))
    working_hours: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SandharEmployeeSkill(Base):
    __tablename__ = "sandhar_employee_skill_matrix"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_employees.id"), nullable=False, index=True)
    line_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_lines.id"), nullable=True, index=True)
    machine_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_machines.id"), nullable=True, index=True)
    skill_level: Mapped[int | None] = mapped_column(Integer)
    certification_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    active_flag: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SandharAttendance(Base):
    __tablename__ = "sandhar_attendance"
    __table_args__ = (
        # UniqueConstraint handled in migration; ORM doesn't need it for functionality
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_employees.id"), nullable=False, index=True)
    attendance_date: Mapped[date] = mapped_column(Date, nullable=False)
    shift_code: Mapped[str] = mapped_column(String(10), nullable=False)
    check_in_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    check_out_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str | None] = mapped_column(String(20))
    is_manual_override: Mapped[bool] = mapped_column(Boolean, default=False)
    override_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SandharWorkOrder(Base):
    __tablename__ = "sandhar_work_orders"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    wo_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_customers.id"), nullable=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_products.id"), nullable=True)
    order_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    priority: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="open")
    quality_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SandharWorkOrderOperation(Base):
    __tablename__ = "sandhar_work_order_operations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    wo_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_work_orders.id"), nullable=False, index=True)
    line_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_lines.id"), nullable=True)
    machine_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_machines.id"), nullable=True)
    planned_qty: Mapped[int | None] = mapped_column(Integer)
    sequence_no: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SandharMachineStatus(Base):
    __tablename__ = "sandhar_machine_status"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    machine_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_machines.id"), nullable=False, index=True)
    status_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    machine_status: Mapped[str | None] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(String(500))
    estimated_restore_datetime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reported_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SandharMaterialAvailability(Base):
    __tablename__ = "sandhar_material_availability"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_products.id"), nullable=False)
    plan_date: Mapped[date] = mapped_column(Date, nullable=False)
    available_qty: Mapped[float | None] = mapped_column(Float)
    required_qty: Mapped[float | None] = mapped_column(Float)
    shortfall_qty: Mapped[float | None] = mapped_column(Float)
    constraint_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SandharQualityHold(Base):
    __tablename__ = "sandhar_quality_hold"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    wo_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_work_orders.id"), nullable=True, index=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_products.id"), nullable=True, index=True)
    hold_reason: Mapped[str | None] = mapped_column(String(500))
    hold_status: Mapped[str] = mapped_column(String(20), default="active")
    raised_by: Mapped[str | None] = mapped_column(String(100))
    released_by: Mapped[str | None] = mapped_column(String(100))
    raised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SandharPlanHeader(Base):
    __tablename__ = "sandhar_plan_header"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_date: Mapped[date] = mapped_column(Date, nullable=False)
    shift_code: Mapped[str] = mapped_column(String(10), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    confidence: Mapped[str | None] = mapped_column(String(20))
    planner_id: Mapped[str | None] = mapped_column(String(100))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SandharPlanDetail(Base):
    __tablename__ = "sandhar_plan_detail"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_header_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_plan_header.id"), nullable=False, index=True)
    wo_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_work_orders.id"), nullable=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_products.id"), nullable=True)
    line_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_lines.id"), nullable=True, index=True)
    planned_qty: Mapped[int | None] = mapped_column(Integer)
    planned_manpower: Mapped[int | None] = mapped_column(Integer)
    available_manpower: Mapped[int | None] = mapped_column(Integer)
    manpower_gap: Mapped[int | None] = mapped_column(Integer)
    supervisor_employee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_employees.id"), nullable=True)
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="planned")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SandharResourceAllocation(Base):
    __tablename__ = "sandhar_resource_allocation"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_date: Mapped[date] = mapped_column(Date, nullable=False)
    shift_code: Mapped[str] = mapped_column(String(10), nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_employees.id"), nullable=False, index=True)
    line_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_lines.id"), nullable=True)
    machine_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_machines.id"), nullable=True)
    wo_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_work_orders.id"), nullable=True)
    allocation_status: Mapped[str] = mapped_column(String(20), default="allocated")
    plan_header_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_plan_header.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SandharProductionActual(Base):
    __tablename__ = "sandhar_production_actual"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_detail_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_plan_detail.id"), nullable=False, index=True)
    shift_code: Mapped[str | None] = mapped_column(String(10))
    produced_qty: Mapped[int] = mapped_column(Integer, default=0)
    rejected_qty: Mapped[int] = mapped_column(Integer, default=0)
    rework_qty: Mapped[int] = mapped_column(Integer, default=0)
    downtime_minutes: Mapped[int] = mapped_column(Integer, default=0)
    achievement_pct: Mapped[float | None] = mapped_column(Float)
    submitted_by: Mapped[str | None] = mapped_column(String(100))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SandharAlert(Base):
    __tablename__ = "sandhar_alert"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alert_type: Mapped[str | None] = mapped_column(String(50))
    alert_message: Mapped[str | None] = mapped_column(String(1000))
    severity: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="active")
    plan_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    shift_code: Mapped[str | None] = mapped_column(String(10))
    related_line_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_lines.id"), nullable=True)
    related_wo_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_work_orders.id"), nullable=True)
    related_employee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_employees.id"), nullable=True)
    related_machine_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandhar_machines.id"), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(100))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(100))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SandharDailyKpi(Base):
    __tablename__ = "sandhar_daily_kpi"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kpi_date: Mapped[date] = mapped_column(Date, nullable=False)
    shift_code: Mapped[str] = mapped_column(String(10), nullable=False)
    total_planned_qty: Mapped[int | None] = mapped_column(Integer)
    total_produced_qty: Mapped[int | None] = mapped_column(Integer)
    plan_achievement_pct: Mapped[float | None] = mapped_column(Float)
    manpower_utilization_pct: Mapped[float | None] = mapped_column(Float)
    line_utilization_pct: Mapped[float | None] = mapped_column(Float)
    rejection_rate_pct: Mapped[float | None] = mapped_column(Float)
    total_downtime_minutes: Mapped[int | None] = mapped_column(Integer)
    oee: Mapped[float | None] = mapped_column(Float)
    skill_gap_count: Mapped[int | None] = mapped_column(Integer)
    active_alert_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ── Propguru Property Evaluation Models ───────────────────────────────────────

class PropguruChannelPartner(Base):
    __tablename__ = "propguru_channel_partners"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cp_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    cp_type: Mapped[str | None] = mapped_column(String(20))   # sourcing | distribution | both
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(100))
    city: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="active")
    commission_pct: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PropguruEvaluationCriteria(Base):
    __tablename__ = "propguru_evaluation_criteria"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    criterion_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str | None] = mapped_column(String(30))   # amenity | location | property | society
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    scoring_type: Mapped[str] = mapped_column(String(20), nullable=False)  # boolean | scale_1_5 | proximity_km
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PropguruProperty(Base):
    __tablename__ = "propguru_properties"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    property_code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    address_line1: Mapped[str | None] = mapped_column(String(300))
    city: Mapped[str | None] = mapped_column(String(100))
    locality: Mapped[str | None] = mapped_column(String(150))
    pincode: Mapped[str | None] = mapped_column(String(10))
    property_type: Mapped[str | None] = mapped_column(String(30))  # apartment | independent_house
    carpet_area_sqft: Mapped[float | None] = mapped_column(Float)
    built_up_area_sqft: Mapped[float | None] = mapped_column(Float)
    bedrooms: Mapped[int | None] = mapped_column(Integer)
    bathrooms: Mapped[int | None] = mapped_column(Integer)
    floor_number: Mapped[int | None] = mapped_column(Integer)
    total_floors: Mapped[int | None] = mapped_column(Integer)
    building_age_years: Mapped[int | None] = mapped_column(Integer)
    facing: Mapped[str | None] = mapped_column(String(20))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PropguruDeal(Base):
    __tablename__ = "propguru_deals"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deal_code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    property_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("propguru_properties.id"), nullable=True)
    sourcing_cp_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("propguru_channel_partners.id"), nullable=True)
    sourcing_cp_commission_pct: Mapped[float | None] = mapped_column(Float)
    stage: Mapped[str] = mapped_column(String(30), default="lead", index=True)
    lead_source: Mapped[str | None] = mapped_column(String(50))
    notes: Mapped[str | None] = mapped_column(Text)
    target_acquisition_price: Mapped[float | None] = mapped_column(Float)
    final_sale_price: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PropguruEvaluationReport(Base):
    __tablename__ = "propguru_evaluation_reports"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("propguru_deals.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    market_rate_per_sqft: Mapped[float | None] = mapped_column(Float)
    base_price: Mapped[float | None] = mapped_column(Float)
    score_factor: Mapped[float | None] = mapped_column(Float)
    price_premium_pct: Mapped[float | None] = mapped_column(Float)
    recommended_price: Mapped[float | None] = mapped_column(Float)
    final_price: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[str | None] = mapped_column(String(20))
    agent_reasoning: Mapped[str | None] = mapped_column(Text)
    analyst_notes: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(String(100))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Verification loop (Phase 1 — code grader)
    verification_retries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    grader_flags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Verification loop (Phase 2 — model grader)
    model_grader_retries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PropguruEvaluationScore(Base):
    __tablename__ = "propguru_evaluation_scores"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("propguru_evaluation_reports.id"), nullable=False, index=True)
    criterion_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("propguru_evaluation_criteria.id"), nullable=False)
    score: Mapped[float | None] = mapped_column(Float)
    raw_value: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(20), default="agent")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PropguruMarketComp(Base):
    __tablename__ = "propguru_market_comps"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    locality: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    property_type: Mapped[str | None] = mapped_column(String(30))
    avg_price_per_sqft: Mapped[float | None] = mapped_column(Float)
    min_price_per_sqft: Mapped[float | None] = mapped_column(Float)
    max_price_per_sqft: Mapped[float | None] = mapped_column(Float)
    price_trend_6m_pct: Mapped[float | None] = mapped_column(Float)
    transaction_count_6m: Mapped[int | None] = mapped_column(Integer)
    data_source: Mapped[str | None] = mapped_column(String(100))
    as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── Platform Memory / Stores (semantic RAG) ───────────────────────────────────

class MemoryStore(Base):
    """Logical knowledge base (slug-addressed). One store per domain/topic."""

    __tablename__ = "memory_stores"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    company: Mapped[str] = mapped_column(String(50), nullable=False, default="platform")
    memory_type: Mapped[str] = mapped_column(String(30), nullable=False, default="semantic")
    embedding_model: Mapped[str] = mapped_column(String(100), nullable=False, default="text-embedding-3-small")
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False, default=512)
    chunk_overlap: Mapped[int] = mapped_column(Integer, nullable=False, default=64)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MemoryDocument(Base):
    """An uploaded knowledge document pending or approved for indexing."""

    __tablename__ = "memory_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_stores.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False, default="text")
    raw_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # pending | approved | rejected
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    uploaded_by: Mapped[str] = mapped_column(String(100), nullable=False, default="system")
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    doc_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MemoryChunk(Base):
    """A text chunk with its pgvector embedding for cosine similarity search."""

    __tablename__ = "memory_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_documents.id"), nullable=False, index=True
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_stores.id"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # vector(1536) — added via pgvector; declared as Column (not Mapped) for compatibility
    embedding = Column(_VECTOR_TYPE) if _VECTOR_TYPE is not None else Column(Text, nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
