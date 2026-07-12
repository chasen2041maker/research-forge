"""Persist an approval request and release the worker lease instead of blocking for a human."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from research_forge.application.dto.repair import ActionProposal
from research_forge.application.ports.system import Clock, IdGenerator
from research_forge.application.ports.unit_of_work import UnitOfWork
from research_forge.application.use_cases.claim_baseline_attempt import AttemptNotFound
from research_forge.domain.approval import Approval
from research_forge.domain.execution import OperationType
from research_forge.domain.mission import AuditEvent, MissionStatus, TaskType


@dataclass(frozen=True, slots=True)
class ApprovalRequestView:
    approval_id: str
    status: str
    expires_at: str


class RequestRepairApproval:
    """Put a repair patch proposal under durable approval before any candidate commit is possible."""

    def __init__(
        self,
        *,
        unit_of_work: UnitOfWork,
        clock: Clock,
        id_generator: IdGenerator,
        approval_ttl: timedelta,
    ) -> None:
        if approval_ttl <= timedelta(0):
            raise ValueError("approval_ttl must be positive")
        self._unit_of_work = unit_of_work
        self._clock = clock
        self._id_generator = id_generator
        self._approval_ttl = approval_ttl

    def execute(
        self,
        *,
        attempt_id: str,
        owner: str,
        epoch: int,
        expected_version: int,
        proposal: ActionProposal,
    ) -> ApprovalRequestView:
        if proposal.action_type != "APPLY_PATCH" or not proposal.unified_diff.strip():
            raise ValueError("Only a non-empty APPLY_PATCH proposal can request repair approval.")
        now = self._clock.now()
        with self._unit_of_work:
            attempt = self._unit_of_work.get_attempt(attempt_id)
            if attempt is None:
                raise AttemptNotFound(attempt_id)
            task = self._unit_of_work.get_task(str(attempt.task_id))
            if task is None or task.task_type is not TaskType.REPAIR_CANDIDATE:
                raise ValueError("Only repair candidate Attempts can request patch approval.")
            mission = self._unit_of_work.get_mission(str(task.mission_id))
            if mission is None or mission.status is not MissionStatus.RUNNING:
                raise ValueError("Mission must be running before a repair proposal can be paused for approval.")
            attempt.assert_active_lease(owner=owner, epoch=epoch, expected_version=expected_version, now=now)
            approval = Approval(
                approval_id=self._id_generator.new("approval"),
                mission_id=mission.mission_id,
                task_id=task.task_id,
                attempt_id=attempt.attempt_id,
                action_type=OperationType.CANDIDATE_COMMIT,
                action_hash=self._hash(proposal.unified_diff),
                risk_level="HIGH",
                scope="CANDIDATE_COMMIT",
                requested_at=now,
                expires_at=now + self._approval_ttl,
            )
            mission.wait_for_approval()
            attempt.fail(
                owner=owner,
                epoch=epoch,
                expected_version=expected_version,
                now=now,
                retryable=True,
                failure_code="WAITING_APPROVAL",
            )
            task.retry()
            self._unit_of_work.add_approval(approval)
            self._unit_of_work.add_audit_event(
                AuditEvent(
                    event_id=self._id_generator.new("audit"),
                    aggregate_type="mission",
                    aggregate_id=str(mission.mission_id),
                    event_type="repair.approval_requested",
                    occurred_at=now,
                    data={"approval_id": approval.approval_id, "action_hash": approval.action_hash},
                )
            )
            self._unit_of_work.commit()
        return ApprovalRequestView(
            approval_id=approval.approval_id,
            status=str(approval.status),
            expires_at=approval.expires_at.isoformat(),
        )

    @staticmethod
    def _hash(unified_diff: str) -> str:
        import hashlib

        return hashlib.sha256(unified_diff.encode("utf-8")).hexdigest()
