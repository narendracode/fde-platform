"""Add Sandhar planning tables: plan header, plan detail, resource allocation, production actual.

Revision ID: 013
Revises: 012
Create Date: 2026-07-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sandhar_plan_header",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("plan_date", sa.Date(), nullable=False),
        sa.Column("shift_code", sa.String(10), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1"),
        sa.Column("status", sa.String(20), server_default="draft"),
        sa.Column("confidence", sa.String(20), nullable=True),
        sa.Column("planner_id", sa.String(100), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "sandhar_plan_detail",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("plan_header_id", UUID(as_uuid=True), nullable=False),
        sa.Column("wo_id", UUID(as_uuid=True), nullable=True),
        sa.Column("product_id", UUID(as_uuid=True), nullable=True),
        sa.Column("line_id", UUID(as_uuid=True), nullable=True),
        sa.Column("planned_qty", sa.Integer(), nullable=True),
        sa.Column("planned_manpower", sa.Integer(), nullable=True),
        sa.Column("available_manpower", sa.Integer(), nullable=True),
        sa.Column("manpower_gap", sa.Integer(), nullable=True),
        sa.Column("supervisor_employee_id", UUID(as_uuid=True), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), server_default="planned"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["plan_header_id"], ["sandhar_plan_header.id"], name="fk_sandhar_plan_detail_header_id"),
        sa.ForeignKeyConstraint(["wo_id"], ["sandhar_work_orders.id"], name="fk_sandhar_plan_detail_wo_id"),
        sa.ForeignKeyConstraint(["product_id"], ["sandhar_products.id"], name="fk_sandhar_plan_detail_product_id"),
        sa.ForeignKeyConstraint(["line_id"], ["sandhar_lines.id"], name="fk_sandhar_plan_detail_line_id"),
        sa.ForeignKeyConstraint(["supervisor_employee_id"], ["sandhar_employees.id"], name="fk_sandhar_plan_detail_supervisor_id"),
    )
    op.create_index("ix_sandhar_plan_detail_plan_header_id", "sandhar_plan_detail", ["plan_header_id"])
    op.create_index("ix_sandhar_plan_detail_line_id", "sandhar_plan_detail", ["line_id"])

    op.create_table(
        "sandhar_resource_allocation",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("plan_date", sa.Date(), nullable=False),
        sa.Column("shift_code", sa.String(10), nullable=False),
        sa.Column("employee_id", UUID(as_uuid=True), nullable=False),
        sa.Column("line_id", UUID(as_uuid=True), nullable=True),
        sa.Column("machine_id", UUID(as_uuid=True), nullable=True),
        sa.Column("wo_id", UUID(as_uuid=True), nullable=True),
        sa.Column("allocation_status", sa.String(20), server_default="allocated"),
        sa.Column("plan_header_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["employee_id"], ["sandhar_employees.id"], name="fk_sandhar_resource_alloc_employee_id"),
        sa.ForeignKeyConstraint(["line_id"], ["sandhar_lines.id"], name="fk_sandhar_resource_alloc_line_id"),
        sa.ForeignKeyConstraint(["machine_id"], ["sandhar_machines.id"], name="fk_sandhar_resource_alloc_machine_id"),
        sa.ForeignKeyConstraint(["wo_id"], ["sandhar_work_orders.id"], name="fk_sandhar_resource_alloc_wo_id"),
        sa.ForeignKeyConstraint(["plan_header_id"], ["sandhar_plan_header.id"], name="fk_sandhar_resource_alloc_header_id"),
    )
    op.create_index("ix_sandhar_resource_allocation_employee_id", "sandhar_resource_allocation", ["employee_id"])
    op.create_index("ix_sandhar_resource_allocation_plan_header_id", "sandhar_resource_allocation", ["plan_header_id"])

    op.create_table(
        "sandhar_production_actual",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("plan_detail_id", UUID(as_uuid=True), nullable=False),
        sa.Column("shift_code", sa.String(10), nullable=True),
        sa.Column("produced_qty", sa.Integer(), server_default="0"),
        sa.Column("rejected_qty", sa.Integer(), server_default="0"),
        sa.Column("rework_qty", sa.Integer(), server_default="0"),
        sa.Column("downtime_minutes", sa.Integer(), server_default="0"),
        sa.Column("achievement_pct", sa.Float(), nullable=True),
        sa.Column("submitted_by", sa.String(100), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["plan_detail_id"], ["sandhar_plan_detail.id"], name="fk_sandhar_production_actual_detail_id"),
    )
    op.create_index("ix_sandhar_production_actual_plan_detail_id", "sandhar_production_actual", ["plan_detail_id"])


def downgrade() -> None:
    op.drop_index("ix_sandhar_production_actual_plan_detail_id", table_name="sandhar_production_actual")
    op.drop_table("sandhar_production_actual")
    op.drop_index("ix_sandhar_resource_allocation_plan_header_id", table_name="sandhar_resource_allocation")
    op.drop_index("ix_sandhar_resource_allocation_employee_id", table_name="sandhar_resource_allocation")
    op.drop_table("sandhar_resource_allocation")
    op.drop_index("ix_sandhar_plan_detail_line_id", table_name="sandhar_plan_detail")
    op.drop_index("ix_sandhar_plan_detail_plan_header_id", table_name="sandhar_plan_detail")
    op.drop_table("sandhar_plan_detail")
    op.drop_table("sandhar_plan_header")
