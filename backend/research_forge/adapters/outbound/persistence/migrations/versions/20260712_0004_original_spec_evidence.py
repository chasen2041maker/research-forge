"""Persist original mission input separately from the normalized execution contract.

Revision ID: 20260712_0004
Revises: 20260712_0003
Create Date: 2026-07-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260712_0004"
down_revision = "20260712_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Backfill existing immutable missions with their previously stored normalized input."""
    op.add_column("rf_missions", sa.Column("original_spec_json", sa.Text(), nullable=True))
    op.execute("UPDATE rf_missions SET original_spec_json = normalized_spec_json WHERE original_spec_json IS NULL")
    with op.batch_alter_table("rf_missions") as batch:
        batch.alter_column("original_spec_json", existing_type=sa.Text(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("rf_missions") as batch:
        batch.drop_column("original_spec_json")
