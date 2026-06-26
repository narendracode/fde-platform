"""Add staleness and drift fields to agent_actions.

Revision ID: 008
Revises: 007
Create Date: 2026-06-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agent_actions", sa.Column("expected_state", JSONB(), nullable=True))
    op.add_column("agent_actions", sa.Column("stale_after_seconds", sa.Integer(), nullable=True))
    op.add_column("agent_actions", sa.Column("stale_marked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agent_actions", sa.Column("drift_detected_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agent_actions", sa.Column("drift_details", JSONB(), nullable=True))
    op.add_column("agent_actions", sa.Column("drift_override", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("agent_actions", "drift_override")
    op.drop_column("agent_actions", "drift_details")
    op.drop_column("agent_actions", "drift_detected_at")
    op.drop_column("agent_actions", "stale_marked_at")
    op.drop_column("agent_actions", "stale_after_seconds")
    op.drop_column("agent_actions", "expected_state")
