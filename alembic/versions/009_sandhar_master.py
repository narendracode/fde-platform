"""Add Sandhar master tables: employees, lines, machines, customers, products, shifts.

Revision ID: 009
Revises: 008
Create Date: 2026-07-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sandhar_employees",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("employee_code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("department", sa.String(50), nullable=True),
        sa.Column("designation", sa.String(50), nullable=True),
        sa.Column("grade", sa.String(20), nullable=True),
        sa.Column("shift_group", sa.String(10), nullable=True),
        sa.Column("status", sa.String(20), server_default="active"),
        sa.Column("joining_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("employee_code", name="uq_sandhar_employees_code"),
    )
    op.create_index("ix_sandhar_employees_employee_code", "sandhar_employees", ["employee_code"])

    op.create_table(
        "sandhar_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("line_code", sa.String(20), nullable=False),
        sa.Column("line_name", sa.String(100), nullable=False),
        sa.Column("area", sa.String(100), nullable=True),
        sa.Column("capacity_per_shift", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("line_code", name="uq_sandhar_lines_code"),
    )
    op.create_index("ix_sandhar_lines_line_code", "sandhar_lines", ["line_code"])

    op.create_table(
        "sandhar_machines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("machine_code", sa.String(20), nullable=False),
        sa.Column("machine_name", sa.String(100), nullable=False),
        sa.Column("line_id", UUID(as_uuid=True), nullable=True),
        sa.Column("machine_type", sa.String(50), nullable=True),
        sa.Column("capacity_per_hour", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("machine_code", name="uq_sandhar_machines_code"),
        sa.ForeignKeyConstraint(["line_id"], ["sandhar_lines.id"], name="fk_sandhar_machines_line_id"),
    )
    op.create_index("ix_sandhar_machines_machine_code", "sandhar_machines", ["machine_code"])

    op.create_table(
        "sandhar_customers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("customer_code", sa.String(20), nullable=False),
        sa.Column("customer_name", sa.String(100), nullable=False),
        sa.Column("priority_level", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("customer_code", name="uq_sandhar_customers_code"),
    )
    op.create_index("ix_sandhar_customers_customer_code", "sandhar_customers", ["customer_code"])

    op.create_table(
        "sandhar_products",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("product_code", sa.String(30), nullable=False),
        sa.Column("product_name", sa.String(100), nullable=False),
        sa.Column("customer_id", UUID(as_uuid=True), nullable=True),
        sa.Column("standard_cycle_time", sa.Float(), nullable=True),
        sa.Column("standard_manpower", sa.Integer(), nullable=True),
        sa.Column("line_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("product_code", name="uq_sandhar_products_code"),
        sa.ForeignKeyConstraint(["customer_id"], ["sandhar_customers.id"], name="fk_sandhar_products_customer_id"),
        sa.ForeignKeyConstraint(["line_id"], ["sandhar_lines.id"], name="fk_sandhar_products_line_id"),
    )
    op.create_index("ix_sandhar_products_product_code", "sandhar_products", ["product_code"])

    op.create_table(
        "sandhar_shifts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("shift_code", sa.String(10), nullable=False),
        sa.Column("shift_name", sa.String(50), nullable=True),
        sa.Column("start_time", sa.String(10), nullable=True),
        sa.Column("end_time", sa.String(10), nullable=True),
        sa.Column("working_hours", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("shift_code", name="uq_sandhar_shifts_code"),
    )


def downgrade() -> None:
    op.drop_table("sandhar_shifts")
    op.drop_index("ix_sandhar_products_product_code", table_name="sandhar_products")
    op.drop_table("sandhar_products")
    op.drop_index("ix_sandhar_customers_customer_code", table_name="sandhar_customers")
    op.drop_table("sandhar_customers")
    op.drop_index("ix_sandhar_machines_machine_code", table_name="sandhar_machines")
    op.drop_table("sandhar_machines")
    op.drop_index("ix_sandhar_lines_line_code", table_name="sandhar_lines")
    op.drop_table("sandhar_lines")
    op.drop_index("ix_sandhar_employees_employee_code", table_name="sandhar_employees")
    op.drop_table("sandhar_employees")
