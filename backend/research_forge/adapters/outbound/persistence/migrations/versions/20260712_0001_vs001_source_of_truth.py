"""Create the immutable VS-001 source-of-truth schema snapshot.

Revision ID: 20260712_0001
Revises:
Create Date: 2026-07-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260712_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create a static schema snapshot; later ORM fields must use later revisions."""
    op.create_table(
        "rf_missions",
        sa.Column("mission_id", sa.String(length=128), primary_key=True),
        sa.Column("spec_sha256", sa.String(length=64), nullable=False),
        sa.Column("normalized_spec_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "rf_tasks",
        sa.Column("task_id", sa.String(length=128), primary_key=True),
        sa.Column("mission_id", sa.String(length=128), sa.ForeignKey("rf_missions.mission_id"), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rf_tasks_mission_id", "rf_tasks", ["mission_id"])
    op.create_table(
        "rf_attempts",
        sa.Column("attempt_id", sa.String(length=128), primary_key=True),
        sa.Column("task_id", sa.String(length=128), sa.ForeignKey("rf_tasks.task_id"), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("lease_owner", sa.String(length=256)),
        sa.Column("lease_epoch", sa.Integer(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("failure_code", sa.String(length=128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rf_attempts_task_id", "rf_attempts", ["task_id"])
    op.create_table(
        "rf_operations",
        sa.Column("operation_id", sa.String(length=128), primary_key=True),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False, unique=True),
        sa.Column("attempt_id", sa.String(length=128), sa.ForeignKey("rf_attempts.attempt_id"), nullable=False),
        sa.Column("operation_type", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("expected_parent_sha", sa.String(length=64)),
        sa.Column("target_ref_or_path", sa.Text(), nullable=False),
        sa.Column("external_result_ref", sa.Text()),
        sa.Column("lease_epoch", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rf_operations_attempt_id", "rf_operations", ["attempt_id"])
    op.create_table(
        "rf_artifacts",
        sa.Column("operation_id", sa.String(length=128), sa.ForeignKey("rf_operations.operation_id"), primary_key=True),
        sa.Column("attempt_id", sa.String(length=128), sa.ForeignKey("rf_attempts.attempt_id"), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rf_artifacts_attempt_id", "rf_artifacts", ["attempt_id"])
    op.create_index("ix_rf_artifacts_sha256", "rf_artifacts", ["sha256"])
    op.create_table(
        "rf_metrics",
        sa.Column("metric_id", sa.String(length=128), primary_key=True),
        sa.Column("attempt_id", sa.String(length=128), sa.ForeignKey("rf_attempts.attempt_id"), nullable=False, unique=True),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("artifact_size_bytes", sa.Integer(), nullable=False),
        sa.Column("artifact_media_type", sa.String(length=128), nullable=False),
        sa.Column("json_pointer", sa.String(length=1024), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("comparator", sa.String(length=16), nullable=False),
        sa.Column("expected_value", sa.Float(), nullable=False),
        sa.Column("tolerance", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=64), nullable=False),
        sa.Column("commit_sha", sa.String(length=40), nullable=False),
        sa.Column("command", sa.JSON(), nullable=False),
        sa.Column("environment_digest", sa.String(length=128), nullable=False),
        sa.Column("dataset_sha256", sa.String(length=64), nullable=False),
    )
    op.create_table(
        "rf_claims",
        sa.Column("claim_id", sa.String(length=128), primary_key=True),
        sa.Column("mission_id", sa.String(length=128), sa.ForeignKey("rf_missions.mission_id"), nullable=False),
        sa.Column("attempt_id", sa.String(length=128), sa.ForeignKey("rf_attempts.attempt_id"), nullable=False),
        sa.Column("claim_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rf_claims_mission_id", "rf_claims", ["mission_id"])
    op.create_index("ix_rf_claims_attempt_id", "rf_claims", ["attempt_id"])
    op.create_table(
        "rf_evidence_links",
        sa.Column("evidence_id", sa.String(length=256), primary_key=True),
        sa.Column("claim_id", sa.String(length=128), sa.ForeignKey("rf_claims.claim_id"), nullable=False),
        sa.Column("evidence_type", sa.String(length=64), nullable=False),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("artifact_size_bytes", sa.Integer(), nullable=False),
        sa.Column("artifact_media_type", sa.String(length=128), nullable=False),
    )
    op.create_index("ix_rf_evidence_links_claim_id", "rf_evidence_links", ["claim_id"])
    op.create_table(
        "rf_audit_events",
        sa.Column("event_id", sa.String(length=128), primary_key=True),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    op.create_index("ix_rf_audit_events_aggregate_id", "rf_audit_events", ["aggregate_id"])
    op.create_table(
        "rf_outbox_events",
        sa.Column("event_id", sa.String(length=128), primary_key=True),
        sa.Column("topic", sa.String(length=128), nullable=False),
        sa.Column("aggregate_id", sa.String(length=128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_rf_outbox_events_aggregate_id", "rf_outbox_events", ["aggregate_id"])
    op.create_table(
        "rf_bundles",
        sa.Column("mission_id", sa.String(length=128), sa.ForeignKey("rf_missions.mission_id"), primary_key=True),
        sa.Column("operation_id", sa.String(length=128), sa.ForeignKey("rf_operations.operation_id"), nullable=False),
        sa.Column("attempt_id", sa.String(length=128), sa.ForeignKey("rf_attempts.attempt_id"), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    """Remove only the immutable VS-001 snapshot tables in dependency order."""
    op.drop_table("rf_bundles")
    op.drop_index("ix_rf_outbox_events_aggregate_id", table_name="rf_outbox_events")
    op.drop_table("rf_outbox_events")
    op.drop_index("ix_rf_audit_events_aggregate_id", table_name="rf_audit_events")
    op.drop_table("rf_audit_events")
    op.drop_index("ix_rf_evidence_links_claim_id", table_name="rf_evidence_links")
    op.drop_table("rf_evidence_links")
    op.drop_index("ix_rf_claims_attempt_id", table_name="rf_claims")
    op.drop_index("ix_rf_claims_mission_id", table_name="rf_claims")
    op.drop_table("rf_claims")
    op.drop_table("rf_metrics")
    op.drop_index("ix_rf_artifacts_sha256", table_name="rf_artifacts")
    op.drop_index("ix_rf_artifacts_attempt_id", table_name="rf_artifacts")
    op.drop_table("rf_artifacts")
    op.drop_index("ix_rf_operations_attempt_id", table_name="rf_operations")
    op.drop_table("rf_operations")
    op.drop_index("ix_rf_attempts_task_id", table_name="rf_attempts")
    op.drop_table("rf_attempts")
    op.drop_index("ix_rf_tasks_mission_id", table_name="rf_tasks")
    op.drop_table("rf_tasks")
    op.drop_table("rf_missions")
