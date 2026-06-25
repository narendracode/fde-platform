"""Add langsmith_run_id to agent_runs.

Revision ID: 002
Revises: 001
Create Date: 2026-06-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("langsmith_run_id", sa.String(100), nullable=True),
    )
    op.create_index("ix_agent_runs_langsmith_run_id", "agent_runs", ["langsmith_run_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_langsmith_run_id", table_name="agent_runs")
    op.drop_column("agent_runs", "langsmith_run_id")
