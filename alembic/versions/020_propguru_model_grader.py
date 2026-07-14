"""Add model_grader_retries column to propguru_evaluation_reports.

Revision ID: 020
Revises: 019
Create Date: 2026-07-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "propguru_evaluation_reports",
        sa.Column("model_grader_retries", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("propguru_evaluation_reports", "model_grader_retries")
