"""Persist the Studio proposal identity separately from the frozen ReproductionSpec.

Revision ID: 20260712_0007
Revises: 20260712_0006
Create Date: 2026-07-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260712_0007"
down_revision = "20260712_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("rf_missions") as batch:
        batch.add_column(sa.Column("proposal_id", sa.String(length=128), nullable=True))
        batch.create_index("ix_rf_missions_proposal_id", ["proposal_id"])


def downgrade() -> None:
    with op.batch_alter_table("rf_missions") as batch:
        batch.drop_index("ix_rf_missions_proposal_id")
        batch.drop_column("proposal_id")
