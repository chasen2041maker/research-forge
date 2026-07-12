"""Apply one validated DecisionEngine patch proposal to the isolated candidate worktree."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from research_forge.application.dto.repair import CandidateCommitRequest
from research_forge.application.ports.artifacts import ArtifactStore
from research_forge.application.ports.system import Clock, IdGenerator
from research_forge.application.ports.unit_of_work import UnitOfWork
from research_forge.application.ports.workspace import WorkspaceManager
from research_forge.application.use_cases.claim_baseline_attempt import AttemptNotFound
from research_forge.domain.approval import ApprovalStatus
from research_forge.domain.errors import OperationConflict
from research_forge.domain.execution import Operation, OperationStatus, OperationType
from research_forge.domain.mission import AttemptId, AuditEvent, TaskType


@dataclass(frozen=True, slots=True)
class RepairCandidateView:
    worktree_path: str
    commit_sha: str
    changed_paths: tuple[str, ...]
    changed_lines: int


class PrepareRepairCandidate:
    """Apply only the immutable patch bytes bound to an approved record."""

    def __init__(
        self,
        *,
        unit_of_work: UnitOfWork,
        artifact_store: ArtifactStore,
        workspace_manager: WorkspaceManager,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._artifact_store = artifact_store
        self._workspace_manager = workspace_manager
        self._clock = clock
        self._id_generator = id_generator

    def execute(
        self,
        *,
        attempt_id: str,
        owner: str,
        epoch: int,
        expected_version: int,
        idempotency_key: str,
        approval_id: str,
    ) -> RepairCandidateView:
        context = self._load_context(attempt_id, owner, epoch, expected_version, approval_id)
        input_hash = hashlib.sha256(context.unified_diff.encode("utf-8")).hexdigest()
        operation = self._prepare_operation(
            attempt_id=attempt_id,
            owner=owner,
            epoch=epoch,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            input_hash=input_hash,
            target_ref_or_path="worktrees/candidate",
        )
        candidate = self._workspace_manager.recover_candidate(
            mission_id=context.mission_id,
            operation_id=operation.operation_id,
            expected_parent_commit_sha=context.parent_commit_sha,
        )
        if operation.status is OperationStatus.SUCCEEDED:
            if operation.external_result_ref is None:
                raise OperationConflict("Completed candidate operation is missing its commit SHA.")
            return RepairCandidateView(
                worktree_path=candidate.worktree_path,
                commit_sha=operation.external_result_ref,
                changed_paths=(),
                changed_lines=0,
            )
        commit = self._workspace_manager.commit_candidate(
            CandidateCommitRequest(
                worktree_path=candidate.worktree_path,
                unified_diff=context.unified_diff,
                allowed_paths=context.allowed_paths,
                max_files=context.max_files,
                max_changed_lines=context.max_changed_lines,
                operation_id=operation.operation_id,
                input_hash=input_hash,
                expected_parent_sha=context.parent_commit_sha,
            )
        )
        self._finalize_operation(
            attempt_id=attempt_id,
            owner=owner,
            epoch=epoch,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            commit_sha=commit.commit_sha,
        )
        return RepairCandidateView(
            worktree_path=candidate.worktree_path,
            commit_sha=commit.commit_sha,
            changed_paths=commit.changed_paths,
            changed_lines=commit.changed_lines,
        )

    def _load_context(
        self, attempt_id: str, owner: str, epoch: int, expected_version: int, approval_id: str
    ) -> "_RepairContext":
        now = self._clock.now()
        with self._unit_of_work:
            attempt = self._unit_of_work.get_attempt(attempt_id)
            if attempt is None:
                raise AttemptNotFound(attempt_id)
            task = self._unit_of_work.get_task(str(attempt.task_id))
            if task is None or task.task_type is not TaskType.REPAIR_CANDIDATE:
                raise OperationConflict("Only a repair candidate Attempt can prepare a repair patch.")
            mission = self._unit_of_work.get_mission(str(task.mission_id))
            if mission is None:
                raise AttemptNotFound(f"mission for attempt {attempt_id}")
            attempt.assert_active_lease(owner=owner, epoch=epoch, expected_version=expected_version, now=now)
            approval = self._unit_of_work.get_approval(approval_id)
            if approval is None or approval.status is not ApprovalStatus.APPROVED:
                raise OperationConflict("Candidate commit requires an approved persistent Approval record.")
            if (
                approval.scope != "CANDIDATE_COMMIT"
                or approval.action_type != OperationType.CANDIDATE_COMMIT
                or approval.task_id != task.task_id
                or approval.mission_id != mission.mission_id
            ):
                raise OperationConflict("Approval scope does not authorize this repair candidate task.")
            if attempt.resume_from_attempt_id != approval.attempt_id:
                raise OperationConflict("Repair candidate must resume from the Attempt that requested approval.")
            if approval.patch_artifact is None:
                raise OperationConflict("Approval is missing its persisted patch artifact.")
            if approval.patch_artifact.sha256 != approval.action_hash:
                raise OperationConflict("Approved patch artifact hash does not match the Approval action hash.")
            patch_payload = self._artifact_store.read_verified(approval.patch_artifact)
            if hashlib.sha256(patch_payload).hexdigest() != approval.action_hash:
                raise OperationConflict("Persisted patch artifact bytes do not match the Approval action hash.")
            try:
                unified_diff = patch_payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise OperationConflict("Persisted patch artifact must be UTF-8 unified diff bytes.") from exc
            if not _is_unified_diff(unified_diff):
                raise OperationConflict("Persisted patch artifact is not a unified diff.")
            spec = json.loads(mission.normalized_spec_json)
            if spec["mode"] != "repair":
                raise OperationConflict("Repair candidate requires mode='repair'.")
            self._unit_of_work.commit()
        return _RepairContext(
            mission_id=str(mission.mission_id),
            parent_commit_sha=spec["repository"]["commit_sha"],
            allowed_paths=tuple(spec["change_budget"]["allowed_paths"]),
            max_files=spec["change_budget"]["max_files"],
            max_changed_lines=spec["change_budget"]["max_changed_lines"],
            unified_diff=unified_diff,
        )

    def _prepare_operation(
        self,
        *,
        attempt_id: str,
        owner: str,
        epoch: int,
        expected_version: int,
        idempotency_key: str,
        input_hash: str,
        target_ref_or_path: str,
    ) -> Operation:
        now = self._clock.now()
        with self._unit_of_work:
            attempt = self._unit_of_work.get_attempt(attempt_id)
            if attempt is None:
                raise AttemptNotFound(attempt_id)
            attempt.assert_active_lease(owner=owner, epoch=epoch, expected_version=expected_version, now=now)
            operation = self._unit_of_work.get_operation_by_idempotency_key(idempotency_key)
            if operation is None:
                operation = Operation(
                    operation_id=self._id_generator.new("operation"),
                    idempotency_key=idempotency_key,
                    attempt_id=AttemptId(attempt_id),
                    operation_type=OperationType.CANDIDATE_COMMIT,
                    input_hash=input_hash,
                    lease_epoch=epoch,
                    target_ref_or_path=target_ref_or_path,
                    created_at=now,
                    updated_at=now,
                )
                self._unit_of_work.add_operation(operation)
            elif (
                operation.operation_type is not OperationType.CANDIDATE_COMMIT
                or operation.input_hash != input_hash
                or operation.lease_epoch != epoch
            ):
                raise OperationConflict("Idempotency key conflicts with a different candidate commit operation.")
            self._unit_of_work.commit()
        return operation

    def _finalize_operation(
        self,
        *,
        attempt_id: str,
        owner: str,
        epoch: int,
        expected_version: int,
        idempotency_key: str,
        commit_sha: str,
    ) -> None:
        now = self._clock.now()
        with self._unit_of_work:
            attempt = self._unit_of_work.get_attempt(attempt_id)
            operation = self._unit_of_work.get_operation_by_idempotency_key(idempotency_key)
            if attempt is None or operation is None:
                raise AttemptNotFound("Repair attempt or prepared candidate operation is absent.")
            attempt.assert_active_lease(owner=owner, epoch=epoch, expected_version=expected_version, now=now)
            operation.succeed(external_result_ref=commit_sha, now=now)
            self._unit_of_work.add_audit_event(
                AuditEvent(
                    event_id=self._id_generator.new("audit"),
                    aggregate_type="attempt",
                    aggregate_id=attempt_id,
                    event_type="repair.candidate_committed",
                    occurred_at=now,
                    data={"commit_sha": commit_sha, "operation_id": operation.operation_id},
                )
            )
            self._unit_of_work.commit()


@dataclass(frozen=True, slots=True)
class _RepairContext:
    mission_id: str
    parent_commit_sha: str
    allowed_paths: tuple[str, ...]
    max_files: int
    max_changed_lines: int
    unified_diff: str


def _is_unified_diff(value: str) -> bool:
    return value.startswith("diff --git ") and "\n--- " in value and "\n+++ " in value and "\n@@ " in value
