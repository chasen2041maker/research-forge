"""Add optimistic versions and duplicate-work guards to mutable Forge aggregates.

Revision ID: 20260712_0005
Revises: 20260712_0004
Create Date: 2026-07-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260712_0005"
down_revision = "20260712_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Make Task, Operation, and Approval compare-and-swap aggregates durable."""
    with op.batch_alter_table("rf_tasks") as batch:
        batch.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default="0"))
        batch.create_unique_constraint("uq_rf_tasks_mission_type", ["mission_id", "task_type"])
    with op.batch_alter_table("rf_operations") as batch:
        batch.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default="0"))
    with op.batch_alter_table("rf_attempts") as batch:
        batch.create_unique_constraint("uq_rf_attempts_task_number", ["task_id", "attempt_number"])
    with op.batch_alter_table("rf_approvals") as batch:
        batch.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default="0"))
        batch.create_unique_constraint(
            "uq_rf_approvals_attempt_action",
            ["attempt_id", "action_type", "action_hash"],
        )


def downgrade() -> None:
    """Remove only the guards introduced by this revision."""
    with op.batch_alter_table("rf_approvals") as batch:
        batch.drop_constraint("uq_rf_approvals_attempt_action", type_="unique")
        batch.drop_column("version")
    with op.batch_alter_table("rf_attempts") as batch:
        batch.drop_constraint("uq_rf_attempts_task_number", type_="unique")
    with op.batch_alter_table("rf_operations") as batch:
        batch.drop_column("version")
    with op.batch_alter_table("rf_tasks") as batch:
        batch.drop_constraint("uq_rf_tasks_mission_type", type_="unique")
        batch.drop_column("version")
