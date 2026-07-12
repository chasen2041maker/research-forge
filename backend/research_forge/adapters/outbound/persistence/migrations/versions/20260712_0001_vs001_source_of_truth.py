"""Create VS-001 source-of-truth tables.

Revision ID: 20260712_0001
Revises:
Create Date: 2026-07-12
"""

from __future__ import annotations

from alembic import op

from research_forge.adapters.outbound.persistence.models import Base


revision = "20260712_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the frozen VS-001 schema through Alembic, never application startup."""
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=False)


def downgrade() -> None:
    """Remove the initial schema only when explicitly rolling back this revision."""
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=False)
