"""Add Propguru master tables: channel_partners, evaluation_criteria.

Revision ID: 016
Revises: 015
Create Date: 2026-07-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "propguru_channel_partners",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("cp_code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("cp_type", sa.String(20), nullable=True),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("email", sa.String(100), nullable=True),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), server_default="active"),
        sa.Column("commission_pct", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("cp_code", name="uq_propguru_channel_partners_code"),
    )
    op.create_index("ix_propguru_channel_partners_cp_code", "propguru_channel_partners", ["cp_code"])

    op.create_table(
        "propguru_evaluation_criteria",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("criterion_code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(30), nullable=True),
        sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("scoring_type", sa.String(20), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("criterion_code", name="uq_propguru_evaluation_criteria_code"),
    )
    op.create_index("ix_propguru_evaluation_criteria_code", "propguru_evaluation_criteria", ["criterion_code"])


def downgrade() -> None:
    op.drop_index("ix_propguru_evaluation_criteria_code", table_name="propguru_evaluation_criteria")
    op.drop_table("propguru_evaluation_criteria")
    op.drop_index("ix_propguru_channel_partners_cp_code", table_name="propguru_channel_partners")
    op.drop_table("propguru_channel_partners")
