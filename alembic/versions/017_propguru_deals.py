"""Add Propguru deal tables: properties, deals.

Revision ID: 017
Revises: 016
Create Date: 2026-07-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "propguru_properties",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("property_code", sa.String(30), nullable=False),
        sa.Column("address_line1", sa.String(300), nullable=True),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("locality", sa.String(150), nullable=True),
        sa.Column("pincode", sa.String(10), nullable=True),
        sa.Column("property_type", sa.String(30), nullable=True),
        sa.Column("carpet_area_sqft", sa.Float(), nullable=True),
        sa.Column("built_up_area_sqft", sa.Float(), nullable=True),
        sa.Column("bedrooms", sa.Integer(), nullable=True),
        sa.Column("bathrooms", sa.Integer(), nullable=True),
        sa.Column("floor_number", sa.Integer(), nullable=True),
        sa.Column("total_floors", sa.Integer(), nullable=True),
        sa.Column("building_age_years", sa.Integer(), nullable=True),
        sa.Column("facing", sa.String(20), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("property_code", name="uq_propguru_properties_code"),
    )
    op.create_index("ix_propguru_properties_code", "propguru_properties", ["property_code"])

    op.create_table(
        "propguru_deals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("deal_code", sa.String(30), nullable=False),
        sa.Column("property_id", UUID(as_uuid=True), nullable=True),
        sa.Column("sourcing_cp_id", UUID(as_uuid=True), nullable=True),
        sa.Column("sourcing_cp_commission_pct", sa.Float(), nullable=True),
        sa.Column("stage", sa.String(30), server_default="lead"),
        sa.Column("lead_source", sa.String(50), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("target_acquisition_price", sa.Float(), nullable=True),
        sa.Column("final_sale_price", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("deal_code", name="uq_propguru_deals_code"),
        sa.ForeignKeyConstraint(
            ["property_id"], ["propguru_properties.id"], name="fk_propguru_deals_property_id"
        ),
        sa.ForeignKeyConstraint(
            ["sourcing_cp_id"],
            ["propguru_channel_partners.id"],
            name="fk_propguru_deals_sourcing_cp_id",
        ),
    )
    op.create_index("ix_propguru_deals_code", "propguru_deals", ["deal_code"])
    op.create_index("ix_propguru_deals_stage", "propguru_deals", ["stage"])


def downgrade() -> None:
    op.drop_index("ix_propguru_deals_stage", table_name="propguru_deals")
    op.drop_index("ix_propguru_deals_code", table_name="propguru_deals")
    op.drop_table("propguru_deals")
    op.drop_index("ix_propguru_properties_code", table_name="propguru_properties")
    op.drop_table("propguru_properties")
