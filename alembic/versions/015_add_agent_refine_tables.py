"""Add agent_refine_session and agent_refine_message tables.

Revision ID: 015
Revises: 014
Create Date: 2026-07-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_refine_session",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("action_id", UUID(as_uuid=True), nullable=False),
        sa.Column("refinement_agent", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
        sa.Column("opened_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["action_id"], ["agent_actions.id"],
            name="fk_agent_refine_session_action_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_agent_refine_session_action_id", "agent_refine_session", ["action_id"])
    op.create_index("ix_agent_refine_session_status", "agent_refine_session", ["status"])

    op.create_table(
        "agent_refine_message",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tool_calls", JSONB, nullable=True),
        sa.Column("context_snapshot", JSONB, nullable=True),
        sa.Column("langsmith_run_id", sa.String(100), nullable=True),
        sa.Column("langsmith_trace_url", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["session_id"], ["agent_refine_session.id"],
            name="fk_agent_refine_message_session_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_agent_refine_message_session_id", "agent_refine_message", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_refine_message_session_id", table_name="agent_refine_message")
    op.drop_table("agent_refine_message")
    op.drop_index("ix_agent_refine_session_status", table_name="agent_refine_session")
    op.drop_index("ix_agent_refine_session_action_id", table_name="agent_refine_session")
    op.drop_table("agent_refine_session")
