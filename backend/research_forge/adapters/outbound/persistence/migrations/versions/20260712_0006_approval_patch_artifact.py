"""Bind each repair approval to its immutable persisted patch artifact.

Revision ID: 20260712_0006
Revises: 20260712_0005
Create Date: 2026-07-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260712_0006"
down_revision = "20260712_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("rf_approvals") as batch:
        batch.add_column(sa.Column("patch_sha256", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("patch_size_bytes", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("patch_media_type", sa.String(length=128), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("rf_approvals") as batch:
        batch.drop_column("patch_media_type")
        batch.drop_column("patch_size_bytes")
        batch.drop_column("patch_sha256")
