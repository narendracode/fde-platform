"""Add Propguru evaluation tables: evaluation_reports, evaluation_scores, market_comps.

Revision ID: 018
Revises: 017
Create Date: 2026-07-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "propguru_evaluation_reports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("deal_id", UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1"),
        sa.Column("status", sa.String(20), server_default="draft"),
        sa.Column("market_rate_per_sqft", sa.Float(), nullable=True),
        sa.Column("base_price", sa.Float(), nullable=True),
        sa.Column("score_factor", sa.Float(), nullable=True),
        sa.Column("price_premium_pct", sa.Float(), nullable=True),
        sa.Column("recommended_price", sa.Float(), nullable=True),
        sa.Column("final_price", sa.Float(), nullable=True),
        sa.Column("confidence", sa.String(20), nullable=True),
        sa.Column("agent_reasoning", sa.Text(), nullable=True),
        sa.Column("analyst_notes", sa.Text(), nullable=True),
        sa.Column("approved_by", sa.String(100), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["deal_id"], ["propguru_deals.id"], name="fk_propguru_evaluation_reports_deal_id"
        ),
    )
    op.create_index("ix_propguru_evaluation_reports_deal_id", "propguru_evaluation_reports", ["deal_id"])
    op.create_index("ix_propguru_evaluation_reports_status", "propguru_evaluation_reports", ["status"])

    op.create_table(
        "propguru_evaluation_scores",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("report_id", UUID(as_uuid=True), nullable=False),
        sa.Column("criterion_id", UUID(as_uuid=True), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("raw_value", sa.String(200), nullable=True),
        sa.Column("source", sa.String(20), server_default="agent"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["propguru_evaluation_reports.id"],
            name="fk_propguru_evaluation_scores_report_id",
        ),
        sa.ForeignKeyConstraint(
            ["criterion_id"],
            ["propguru_evaluation_criteria.id"],
            name="fk_propguru_evaluation_scores_criterion_id",
        ),
    )
    op.create_index("ix_propguru_evaluation_scores_report_id", "propguru_evaluation_scores", ["report_id"])

    op.create_table(
        "propguru_market_comps",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("locality", sa.String(150), nullable=False),
        sa.Column("property_type", sa.String(30), nullable=True),
        sa.Column("avg_price_per_sqft", sa.Float(), nullable=True),
        sa.Column("min_price_per_sqft", sa.Float(), nullable=True),
        sa.Column("max_price_per_sqft", sa.Float(), nullable=True),
        sa.Column("price_trend_6m_pct", sa.Float(), nullable=True),
        sa.Column("transaction_count_6m", sa.Integer(), nullable=True),
        sa.Column("data_source", sa.String(100), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_propguru_market_comps_locality", "propguru_market_comps", ["locality"])


def downgrade() -> None:
    op.drop_index("ix_propguru_market_comps_locality", table_name="propguru_market_comps")
    op.drop_table("propguru_market_comps")
    op.drop_index("ix_propguru_evaluation_scores_report_id", table_name="propguru_evaluation_scores")
    op.drop_table("propguru_evaluation_scores")
    op.drop_index("ix_propguru_evaluation_reports_status", table_name="propguru_evaluation_reports")
    op.drop_index("ix_propguru_evaluation_reports_deal_id", table_name="propguru_evaluation_reports")
    op.drop_table("propguru_evaluation_reports")
