"""Add Sandhar work orders and work order operations tables.

Revision ID: 011
Revises: 010
Create Date: 2026-07-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sandhar_work_orders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("wo_number", sa.String(30), nullable=False),
        sa.Column("customer_id", UUID(as_uuid=True), nullable=True),
        sa.Column("product_id", UUID(as_uuid=True), nullable=True),
        sa.Column("order_qty", sa.Integer(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("priority", sa.String(20), nullable=True),
        sa.Column("status", sa.String(20), server_default="open"),
        sa.Column("quality_hold", sa.Boolean(), server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("wo_number", name="uq_sandhar_work_orders_wo_number"),
        sa.ForeignKeyConstraint(["customer_id"], ["sandhar_customers.id"], name="fk_sandhar_wo_customer_id"),
        sa.ForeignKeyConstraint(["product_id"], ["sandhar_products.id"], name="fk_sandhar_wo_product_id"),
    )
    op.create_index("ix_sandhar_work_orders_wo_number", "sandhar_work_orders", ["wo_number"])

    op.create_table(
        "sandhar_work_order_operations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("wo_id", UUID(as_uuid=True), nullable=False),
        sa.Column("line_id", UUID(as_uuid=True), nullable=True),
        sa.Column("machine_id", UUID(as_uuid=True), nullable=True),
        sa.Column("planned_qty", sa.Integer(), nullable=True),
        sa.Column("sequence_no", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["wo_id"], ["sandhar_work_orders.id"], name="fk_sandhar_woo_wo_id"),
        sa.ForeignKeyConstraint(["line_id"], ["sandhar_lines.id"], name="fk_sandhar_woo_line_id"),
        sa.ForeignKeyConstraint(["machine_id"], ["sandhar_machines.id"], name="fk_sandhar_woo_machine_id"),
    )
    op.create_index("ix_sandhar_work_order_operations_wo_id", "sandhar_work_order_operations", ["wo_id"])


def downgrade() -> None:
    op.drop_index("ix_sandhar_work_order_operations_wo_id", table_name="sandhar_work_order_operations")
    op.drop_table("sandhar_work_order_operations")
    op.drop_index("ix_sandhar_work_orders_wo_number", table_name="sandhar_work_orders")
    op.drop_table("sandhar_work_orders")
