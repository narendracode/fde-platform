"""Add Sandhar constraint tables: machine status, material availability, quality hold.

Revision ID: 012
Revises: 011
Create Date: 2026-07-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sandhar_machine_status",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("machine_id", UUID(as_uuid=True), nullable=False),
        sa.Column("status_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("machine_status", sa.String(20), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("estimated_restore_datetime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reported_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["machine_id"], ["sandhar_machines.id"], name="fk_sandhar_machine_status_machine_id"),
    )
    op.create_index("ix_sandhar_machine_status_machine_id", "sandhar_machine_status", ["machine_id"])

    op.create_table(
        "sandhar_material_availability",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("product_id", UUID(as_uuid=True), nullable=False),
        sa.Column("plan_date", sa.Date(), nullable=False),
        sa.Column("available_qty", sa.Float(), nullable=True),
        sa.Column("required_qty", sa.Float(), nullable=True),
        sa.Column("shortfall_qty", sa.Float(), nullable=True),
        sa.Column("constraint_flag", sa.Boolean(), server_default="false"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["product_id"], ["sandhar_products.id"], name="fk_sandhar_material_avail_product_id"),
        sa.UniqueConstraint("product_id", "plan_date", name="uq_sandhar_material_availability_product_date"),
    )

    op.create_table(
        "sandhar_quality_hold",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("wo_id", UUID(as_uuid=True), nullable=True),
        sa.Column("product_id", UUID(as_uuid=True), nullable=True),
        sa.Column("hold_reason", sa.String(500), nullable=True),
        sa.Column("hold_status", sa.String(20), server_default="active"),
        sa.Column("raised_by", sa.String(100), nullable=True),
        sa.Column("released_by", sa.String(100), nullable=True),
        sa.Column("raised_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["wo_id"], ["sandhar_work_orders.id"], name="fk_sandhar_quality_hold_wo_id"),
        sa.ForeignKeyConstraint(["product_id"], ["sandhar_products.id"], name="fk_sandhar_quality_hold_product_id"),
    )
    op.create_index("ix_sandhar_quality_hold_wo_id", "sandhar_quality_hold", ["wo_id"])
    op.create_index("ix_sandhar_quality_hold_product_id", "sandhar_quality_hold", ["product_id"])


def downgrade() -> None:
    op.drop_index("ix_sandhar_quality_hold_product_id", table_name="sandhar_quality_hold")
    op.drop_index("ix_sandhar_quality_hold_wo_id", table_name="sandhar_quality_hold")
    op.drop_table("sandhar_quality_hold")
    op.drop_table("sandhar_material_availability")
    op.drop_index("ix_sandhar_machine_status_machine_id", table_name="sandhar_machine_status")
    op.drop_table("sandhar_machine_status")
