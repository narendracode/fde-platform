"""Add orders and platform_settings tables.

Revision ID: 006
Revises: 005
Create Date: 2026-06-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("order_ref", sa.String(30), nullable=False),
        sa.Column("retailer_name", sa.String(200), nullable=False),
        sa.Column("medicine_name", sa.String(200), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_usd", sa.Float(), nullable=False),
        sa.Column("order_amount_usd", sa.Float(), nullable=False),
        sa.Column("margin_percent", sa.Float(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(30), server_default="pending"),
        sa.Column("shipment_mode", sa.String(20), nullable=True),
        sa.Column("decided_by", sa.String(20), nullable=True),
        sa.Column("ai_recommended_mode", sa.String(20), nullable=True),
        sa.Column("ai_confidence", sa.String(20), nullable=True),
        sa.Column("ai_reasoning", sa.Text(), nullable=True),
        sa.Column("agent_run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("order_ref", name="uq_orders_ref"),
    )
    op.create_index("ix_orders_order_ref", "orders", ["order_ref"])
    op.create_index("ix_orders_status", "orders", ["status"])

    op.create_table(
        "platform_settings",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Seed the default feature flag
    op.execute(
        "INSERT INTO platform_settings (key, value) "
        "VALUES ('ai_automation_enabled', 'false'::jsonb) "
        "ON CONFLICT (key) DO NOTHING"
    )


def downgrade() -> None:
    op.drop_table("orders")
    op.drop_table("platform_settings")
