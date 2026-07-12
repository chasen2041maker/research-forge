"""Persist an approval request and release the worker lease instead of blocking for a human."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from research_forge.application.dto.repair import ActionProposal
from research_forge.application.ports.system import Clock, IdGenerator
from research_forge.application.ports.unit_of_work import UnitOfWork
from research_forge.application.use_cases.claim_baseline_attempt import AttemptNotFound
from research_forge.application.use_cases.persist_artifact import PersistArtifact
from research_forge.domain.approval import Approval
from research_forge.domain.artifact import ArtifactKind, ArtifactRef
from research_forge.domain.execution import OperationType
from research_forge.domain.mission import AuditEvent, MissionStatus, TaskType


@dataclass(frozen=True, slots=True)
class ApprovalRequestView:
    approval_id: str
    status: str
    expires_at: str


class RequestRepairApproval:
    """Put a repair patch proposal under durable approval before any candidate commit is possible."""

    _PATCH_MEDIA_TYPE = "text/x-diff; charset=utf-8"

    def __init__(
        self,
        *,
        unit_of_work: UnitOfWork,
        artifact_persister: PersistArtifact,
        clock: Clock,
        id_generator: IdGenerator,
        approval_ttl: timedelta,
    ) -> None:
        if approval_ttl <= timedelta(0):
            raise ValueError("approval_ttl must be positive")
        self._unit_of_work = unit_of_work
        self._artifact_persister = artifact_persister
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
        if proposal.action_type != "APPLY_PATCH" or not _is_unified_diff(proposal.unified_diff):
            raise ValueError("Only a non-empty unified APPLY_PATCH diff can request repair approval.")
        patch_payload = proposal.unified_diff.encode("utf-8")
        patch_hash = self._hash(proposal.unified_diff)
        self._validate_proposal_context(attempt_id, owner, epoch, expected_version)
        persisted_patch = self._artifact_persister.execute(
            attempt_id=attempt_id,
            owner=owner,
            epoch=epoch,
            expected_version=expected_version,
            idempotency_key=f"{attempt_id}:repair-patch:{patch_hash}",
            kind=ArtifactKind.PATCH,
            payload=patch_payload,
            media_type=self._PATCH_MEDIA_TYPE,
            target_path=f"repair/proposals/{patch_hash}.patch",
        )
        if persisted_patch.sha256 != patch_hash:
            raise ValueError("Persisted patch artifact hash does not match the approved patch bytes.")
        patch_artifact = ArtifactRef(persisted_patch.sha256, persisted_patch.size_bytes, self._PATCH_MEDIA_TYPE)
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
                action_hash=patch_hash,
                risk_level="HIGH",
                scope="CANDIDATE_COMMIT",
                requested_at=now,
                expires_at=now + self._approval_ttl,
                patch_artifact=patch_artifact,
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
                    data={
                        "approval_id": approval.approval_id,
                        "action_hash": approval.action_hash,
                        "patch_artifact_uri": patch_artifact.uri,
                    },
                )
            )
            self._unit_of_work.commit()
        return ApprovalRequestView(
            approval_id=approval.approval_id,
            status=str(approval.status),
            expires_at=approval.expires_at.isoformat(),
        )

    def _validate_proposal_context(self, attempt_id: str, owner: str, epoch: int, expected_version: int) -> None:
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
            self._unit_of_work.commit()

    @staticmethod
    def _hash(unified_diff: str) -> str:
        import hashlib

        return hashlib.sha256(unified_diff.encode("utf-8")).hexdigest()


def _is_unified_diff(value: str) -> bool:
    return value.startswith("diff --git ") and "\n--- " in value and "\n+++ " in value and "\n@@ " in value
