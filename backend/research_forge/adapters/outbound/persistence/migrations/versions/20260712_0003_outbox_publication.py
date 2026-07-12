"""Track successful at-least-once Outbox publication.

Revision ID: 20260712_0003
Revises: 20260712_0002
Create Date: 2026-07-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260712_0003"
down_revision = "20260712_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rf_outbox_events", sa.Column("published_at", sa.DateTime(timezone=True)))
    op.create_index("ix_rf_outbox_events_published_at", "rf_outbox_events", ["published_at"])


def downgrade() -> None:
    op.drop_index("ix_rf_outbox_events_published_at", table_name="rf_outbox_events")
    op.drop_column("rf_outbox_events", "published_at")
