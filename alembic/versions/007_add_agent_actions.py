"""Add agent_actions table for generic human-in-the-loop inbox.

Revision ID: 007
Revises: 006
Create Date: 2026-06-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_actions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column(
            "agent_run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False, server_default=""),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("confidence", sa.String(20), nullable=True),
        sa.Column("display_data", JSONB(), nullable=False, server_default="[]"),
        sa.Column("tags", JSONB(), nullable=False, server_default="[]"),
        sa.Column("approval_action", JSONB(), nullable=False),
        sa.Column("rejection_action", JSONB(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending_review"),
        sa.Column("decided_by", sa.String(100), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("override_body", JSONB(), nullable=True),
        sa.Column("approval_error", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_agent_actions_agent_name", "agent_actions", ["agent_name"])
    op.create_index("ix_agent_actions_status", "agent_actions", ["status"])
    op.create_index("ix_agent_actions_agent_run_id", "agent_actions", ["agent_run_id"])
    op.create_index("ix_agent_actions_created_at", "agent_actions", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_actions_created_at", "agent_actions")
    op.drop_index("ix_agent_actions_agent_run_id", "agent_actions")
    op.drop_index("ix_agent_actions_status", "agent_actions")
    op.drop_index("ix_agent_actions_agent_name", "agent_actions")
    op.drop_table("agent_actions")
