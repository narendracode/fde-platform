"""Add verification loop columns to propguru_evaluation_reports.

Revision ID: 019
Revises: 018
Create Date: 2026-07-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "propguru_evaluation_reports",
        sa.Column("verification_retries", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "propguru_evaluation_reports",
        sa.Column("grader_flags", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("propguru_evaluation_reports", "grader_flags")
    op.drop_column("propguru_evaluation_reports", "verification_retries")
