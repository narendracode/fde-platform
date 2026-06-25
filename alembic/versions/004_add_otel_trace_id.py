"""Add otel_trace_id to agent_runs for OpenTelemetry deep-links.

Revision ID: 004
Revises: 003
Create Date: 2026-06-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("otel_trace_id", sa.String(32), nullable=True),
    )
    op.create_index("ix_agent_runs_otel_trace_id", "agent_runs", ["otel_trace_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_otel_trace_id", table_name="agent_runs")
    op.drop_column("agent_runs", "otel_trace_id")
