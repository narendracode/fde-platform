"""Add otel_trace_url to agent_runs — stores the full Jaeger deep-link URL.

Revision ID: 005
Revises: 004
Create Date: 2026-06-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("otel_trace_url", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "otel_trace_url")
