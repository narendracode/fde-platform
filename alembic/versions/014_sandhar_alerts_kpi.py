"""Add Sandhar alert and daily KPI tables.

Revision ID: 014
Revises: 013
Create Date: 2026-07-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sandhar_alert",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("alert_type", sa.String(50), nullable=True),
        sa.Column("alert_message", sa.String(1000), nullable=True),
        sa.Column("severity", sa.String(20), nullable=True),
        sa.Column("status", sa.String(20), server_default="active"),
        sa.Column("plan_date", sa.Date(), nullable=True),
        sa.Column("shift_code", sa.String(10), nullable=True),
        sa.Column("related_line_id", UUID(as_uuid=True), nullable=True),
        sa.Column("related_wo_id", UUID(as_uuid=True), nullable=True),
        sa.Column("related_employee_id", UUID(as_uuid=True), nullable=True),
        sa.Column("related_machine_id", UUID(as_uuid=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(100), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(100), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["related_line_id"], ["sandhar_lines.id"], name="fk_sandhar_alert_line_id"),
        sa.ForeignKeyConstraint(["related_wo_id"], ["sandhar_work_orders.id"], name="fk_sandhar_alert_wo_id"),
        sa.ForeignKeyConstraint(["related_employee_id"], ["sandhar_employees.id"], name="fk_sandhar_alert_employee_id"),
        sa.ForeignKeyConstraint(["related_machine_id"], ["sandhar_machines.id"], name="fk_sandhar_alert_machine_id"),
    )

    op.create_table(
        "sandhar_daily_kpi",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("kpi_date", sa.Date(), nullable=False),
        sa.Column("shift_code", sa.String(10), nullable=False),
        sa.Column("total_planned_qty", sa.Integer(), nullable=True),
        sa.Column("total_produced_qty", sa.Integer(), nullable=True),
        sa.Column("plan_achievement_pct", sa.Float(), nullable=True),
        sa.Column("manpower_utilization_pct", sa.Float(), nullable=True),
        sa.Column("line_utilization_pct", sa.Float(), nullable=True),
        sa.Column("rejection_rate_pct", sa.Float(), nullable=True),
        sa.Column("total_downtime_minutes", sa.Integer(), nullable=True),
        sa.Column("oee", sa.Float(), nullable=True),
        sa.Column("skill_gap_count", sa.Integer(), nullable=True),
        sa.Column("active_alert_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("kpi_date", "shift_code", name="uq_sandhar_daily_kpi_date_shift"),
    )


def downgrade() -> None:
    op.drop_table("sandhar_daily_kpi")
    op.drop_table("sandhar_alert")
