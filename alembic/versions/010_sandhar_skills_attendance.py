"""Add Sandhar employee skill matrix and attendance tables.

Revision ID: 010
Revises: 009
Create Date: 2026-07-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sandhar_employee_skill_matrix",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("employee_id", UUID(as_uuid=True), nullable=False),
        sa.Column("line_id", UUID(as_uuid=True), nullable=True),
        sa.Column("machine_id", UUID(as_uuid=True), nullable=True),
        sa.Column("skill_level", sa.Integer(), nullable=True),
        sa.Column("certification_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("active_flag", sa.Boolean(), server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["employee_id"], ["sandhar_employees.id"], name="fk_sandhar_skill_employee_id"),
        sa.ForeignKeyConstraint(["line_id"], ["sandhar_lines.id"], name="fk_sandhar_skill_line_id"),
        sa.ForeignKeyConstraint(["machine_id"], ["sandhar_machines.id"], name="fk_sandhar_skill_machine_id"),
    )
    op.create_index("ix_sandhar_employee_skill_matrix_employee_id", "sandhar_employee_skill_matrix", ["employee_id"])
    op.create_index("ix_sandhar_employee_skill_matrix_line_id", "sandhar_employee_skill_matrix", ["line_id"])
    op.create_index("ix_sandhar_employee_skill_matrix_machine_id", "sandhar_employee_skill_matrix", ["machine_id"])
    op.create_index("ix_sandhar_employee_skill_matrix_expiry_date", "sandhar_employee_skill_matrix", ["expiry_date"])

    op.create_table(
        "sandhar_attendance",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("employee_id", UUID(as_uuid=True), nullable=False),
        sa.Column("attendance_date", sa.Date(), nullable=False),
        sa.Column("shift_code", sa.String(10), nullable=False),
        sa.Column("check_in_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_out_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=True),
        sa.Column("is_manual_override", sa.Boolean(), server_default="false"),
        sa.Column("override_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["employee_id"], ["sandhar_employees.id"], name="fk_sandhar_attendance_employee_id"),
        sa.UniqueConstraint("employee_id", "attendance_date", "shift_code", name="uq_sandhar_attendance_emp_date_shift"),
    )
    op.create_index("ix_sandhar_attendance_employee_id", "sandhar_attendance", ["employee_id"])


def downgrade() -> None:
    op.drop_index("ix_sandhar_attendance_employee_id", table_name="sandhar_attendance")
    op.drop_table("sandhar_attendance")
    op.drop_index("ix_sandhar_employee_skill_matrix_expiry_date", table_name="sandhar_employee_skill_matrix")
    op.drop_index("ix_sandhar_employee_skill_matrix_machine_id", table_name="sandhar_employee_skill_matrix")
    op.drop_index("ix_sandhar_employee_skill_matrix_line_id", table_name="sandhar_employee_skill_matrix")
    op.drop_index("ix_sandhar_employee_skill_matrix_employee_id", table_name="sandhar_employee_skill_matrix")
    op.drop_table("sandhar_employee_skill_matrix")
