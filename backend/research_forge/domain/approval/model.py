"""Approval state machine; workers never block waiting for a user decision."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from research_forge.domain.artifact import ArtifactRef
from research_forge.domain.errors import DomainViolation
from research_forge.domain.mission import AttemptId, MissionId, TaskId


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass(slots=True)
class Approval:
    approval_id: str
    mission_id: MissionId
    task_id: TaskId
    attempt_id: AttemptId
    action_type: str
    action_hash: str
    risk_level: str
    scope: str
    requested_at: datetime
    expires_at: datetime
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_at: datetime | None = None
    decided_by: str | None = None
    version: int = 0
    patch_artifact: ArtifactRef | None = None

    def approve(self, *, decided_by: str, now: datetime) -> None:
        self._ensure_pending(now)
        self.status = ApprovalStatus.APPROVED
        self.decided_at = now
        self.decided_by = decided_by
        self.version += 1

    def reject(self, *, decided_by: str, now: datetime) -> None:
        self._ensure_pending(now)
        self.status = ApprovalStatus.REJECTED
        self.decided_at = now
        self.decided_by = decided_by
        self.version += 1

    def expire(self, now: datetime) -> None:
        if self.status is ApprovalStatus.PENDING and self.expires_at <= now:
            self.status = ApprovalStatus.EXPIRED
            self.decided_at = now
            self.version += 1

    def _ensure_pending(self, now: datetime) -> None:
        self.expire(now)
        if self.status is not ApprovalStatus.PENDING:
            raise DomainViolation(f"Approval {self.approval_id} cannot be decided from {self.status}.")
