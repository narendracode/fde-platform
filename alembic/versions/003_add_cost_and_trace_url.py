"""Store langsmith_trace_url in DB; cost_usd already Float (no schema change needed).

Revision ID: 003
Revises: 002
Create Date: 2026-06-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("langsmith_trace_url", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "langsmith_trace_url")
