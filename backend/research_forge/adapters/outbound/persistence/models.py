"""SQLAlchemy PostgreSQL schema for Research Forge's VS-001 source-of-truth tables."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base metadata used by Alembic migrations and test-only schema setup."""


class MissionRow(Base):
    __tablename__ = "rf_missions"

    mission_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    spec_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_spec_json: Mapped[str] = mapped_column(Text, nullable=False)
    original_spec_json: Mapped[str] = mapped_column(Text, nullable=False)
    proposal_id: Mapped[str | None] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TaskRow(Base):
    __tablename__ = "rf_tasks"
    __table_args__ = (UniqueConstraint("mission_id", "task_type", name="uq_rf_tasks_mission_type"),)

    task_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey("rf_missions.mission_id"), nullable=False, index=True)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AttemptRow(Base):
    __tablename__ = "rf_attempts"
    __table_args__ = (UniqueConstraint("task_id", "attempt_number", name="uq_rf_attempts_task_number"),)

    attempt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("rf_tasks.task_id"), nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(256))
    lease_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(128))
    resume_from_attempt_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OperationRow(Base):
    __tablename__ = "rf_operations"

    operation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("rf_attempts.attempt_id"), nullable=False, index=True)
    operation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_parent_sha: Mapped[str | None] = mapped_column(String(64))
    target_ref_or_path: Mapped[str] = mapped_column(Text, nullable=False)
    external_result_ref: Mapped[str | None] = mapped_column(Text)
    lease_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ArtifactRow(Base):
    __tablename__ = "rf_artifacts"

    operation_id: Mapped[str] = mapped_column(ForeignKey("rf_operations.operation_id"), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("rf_attempts.attempt_id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MetricRow(Base):
    __tablename__ = "rf_metrics"

    metric_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("rf_attempts.attempt_id"), nullable=False, unique=True)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    artifact_media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    json_pointer: Mapped[str] = mapped_column(String(1024), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    comparator: Mapped[str] = mapped_column(String(16), nullable=False)
    expected_value: Mapped[float] = mapped_column(Float, nullable=False)
    tolerance: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(64), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    command: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    environment_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class ClaimRow(Base):
    __tablename__ = "rf_claims"

    claim_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey("rf_missions.mission_id"), nullable=False, index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("rf_attempts.attempt_id"), nullable=False, index=True)
    claim_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EvidenceRow(Base):
    __tablename__ = "rf_evidence_links"

    evidence_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("rf_claims.claim_id"), nullable=False, index=True)
    evidence_type: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    artifact_media_type: Mapped[str] = mapped_column(String(128), nullable=False)


class AuditEventRow(Base):
    __tablename__ = "rf_audit_events"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


class OutboxEventRow(Base):
    __tablename__ = "rf_outbox_events"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    topic: Mapped[str] = mapped_column(String(128), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class BundleRow(Base):
    __tablename__ = "rf_bundles"

    mission_id: Mapped[str] = mapped_column(ForeignKey("rf_missions.mission_id"), primary_key=True)
    operation_id: Mapped[str] = mapped_column(ForeignKey("rf_operations.operation_id"), nullable=False)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("rf_attempts.attempt_id"), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ApprovalRow(Base):
    __tablename__ = "rf_approvals"
    __table_args__ = (UniqueConstraint("attempt_id", "action_type", "action_hash", name="uq_rf_approvals_attempt_action"),)

    approval_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey("rf_missions.mission_id"), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("rf_tasks.task_id"), nullable=False, index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("rf_attempts.attempt_id"), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    action_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    patch_sha256: Mapped[str | None] = mapped_column(String(64))
    patch_size_bytes: Mapped[int | None] = mapped_column(Integer)
    patch_media_type: Mapped[str | None] = mapped_column(String(128))
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(128), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(128))
