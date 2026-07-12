"""Create durable approval records for repair policy pauses.

Revision ID: 20260712_0002
Revises: 20260712_0001
Create Date: 2026-07-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260712_0002"
down_revision = "20260712_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Persist approval state separately from volatile worker process state."""
    bind = op.get_bind()
    existing_attempt_columns = {column["name"] for column in sa.inspect(bind).get_columns("rf_attempts")}
    if "resume_from_attempt_id" not in existing_attempt_columns:
        op.add_column("rf_attempts", sa.Column("resume_from_attempt_id", sa.String(length=128)))
    op.create_table(
        "rf_approvals",
        sa.Column("approval_id", sa.String(length=128), primary_key=True),
        sa.Column("mission_id", sa.String(length=128), sa.ForeignKey("rf_missions.mission_id"), nullable=False),
        sa.Column("task_id", sa.String(length=128), sa.ForeignKey("rf_tasks.task_id"), nullable=False),
        sa.Column("attempt_id", sa.String(length=128), sa.ForeignKey("rf_attempts.attempt_id"), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("action_hash", sa.String(length=64), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("scope", sa.String(length=128), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decided_by", sa.String(length=128)),
    )
    op.create_index("ix_rf_approvals_mission_id", "rf_approvals", ["mission_id"])
    op.create_index("ix_rf_approvals_task_id", "rf_approvals", ["task_id"])
    op.create_index("ix_rf_approvals_attempt_id", "rf_approvals", ["attempt_id"])


def downgrade() -> None:
    op.drop_index("ix_rf_approvals_attempt_id", table_name="rf_approvals")
    op.drop_index("ix_rf_approvals_task_id", table_name="rf_approvals")
    op.drop_index("ix_rf_approvals_mission_id", table_name="rf_approvals")
    op.drop_table("rf_approvals")
    bind = op.get_bind()
    existing_attempt_columns = {column["name"] for column in sa.inspect(bind).get_columns("rf_attempts")}
    if "resume_from_attempt_id" in existing_attempt_columns:
        op.drop_column("rf_attempts", "resume_from_attempt_id")
