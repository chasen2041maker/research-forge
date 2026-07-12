"""Create or recover the one VS-001 baseline Git worktree through the ledger."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from research_forge.application.ports.system import Clock, IdGenerator
from research_forge.application.ports.unit_of_work import UnitOfWork
from research_forge.application.ports.workspace import BaselineWorkspace, WorkspaceManager
from research_forge.application.use_cases.claim_baseline_attempt import AttemptNotFound
from research_forge.domain.errors import CancellationRequested, OperationConflict
from research_forge.domain.execution import Operation, OperationStatus, OperationType
from research_forge.domain.mission import AttemptId, MissionStatus


@dataclass(frozen=True, slots=True)
class WorkspaceView:
    worktree_path: str
    commit_sha: str


class EnsureBaselineWorkspace:
    """Bind a worker lease to a pinned, clean worktree without creating candidate code."""

    def __init__(
        self,
        *,
        unit_of_work: UnitOfWork,
        workspace_manager: WorkspaceManager,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self._unit_of_work = unit_of_work
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
    ) -> WorkspaceView:
        repository_url_or_path, expected_commit_sha, mission_id = self._prepare(
            attempt_id=attempt_id,
            owner=owner,
            epoch=epoch,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
        )
        workspace = self._workspace_manager.ensure_baseline(
            mission_id=mission_id,
            repository_url_or_path=repository_url_or_path,
            expected_commit_sha=expected_commit_sha,
        )
        self._finalize(
            attempt_id=attempt_id,
            owner=owner,
            epoch=epoch,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            workspace=workspace,
        )
        return WorkspaceView(worktree_path=workspace.worktree_path, commit_sha=workspace.commit_sha)

    def _prepare(
        self,
        *,
        attempt_id: str,
        owner: str,
        epoch: int,
        expected_version: int,
        idempotency_key: str,
    ) -> tuple[str, str, str]:
        now = self._clock.now()
        with self._unit_of_work:
            attempt = self._unit_of_work.get_attempt(attempt_id)
            if attempt is None:
                raise AttemptNotFound(attempt_id)
            task = self._unit_of_work.get_task(str(attempt.task_id))
            if task is None:
                raise AttemptNotFound(f"task for attempt {attempt_id}")
            mission = self._unit_of_work.get_mission(str(task.mission_id))
            if mission is None:
                raise AttemptNotFound(f"mission for attempt {attempt_id}")
            if mission.status is MissionStatus.CANCELLING:
                raise CancellationRequested("Mission cancellation forbids creating a baseline worktree.")
            attempt.assert_active_lease(owner=owner, epoch=epoch, expected_version=expected_version, now=now)
            repository = json.loads(mission.normalized_spec_json)["repository"]
            repository_url_or_path = repository["url_or_path"]
            expected_commit_sha = repository["commit_sha"]
            input_hash = hashlib.sha256(
                f"{repository_url_or_path}\n{expected_commit_sha}".encode("utf-8")
            ).hexdigest()
            operation = self._unit_of_work.get_operation_by_idempotency_key(idempotency_key)
            if operation is None:
                self._unit_of_work.add_operation(
                    Operation(
                        operation_id=self._id_generator.new("operation"),
                        idempotency_key=idempotency_key,
                        attempt_id=AttemptId(attempt_id),
                        operation_type=OperationType.WORKTREE_CREATE,
                        input_hash=input_hash,
                        lease_epoch=epoch,
                        target_ref_or_path="worktrees/baseline",
                        created_at=now,
                        updated_at=now,
                    )
                )
            elif (
                operation.operation_type is not OperationType.WORKTREE_CREATE
                or operation.input_hash != input_hash
                or operation.lease_epoch != epoch
            ):
                raise OperationConflict("Idempotency key conflicts with a different worktree operation.")
            self._unit_of_work.commit()
        return repository_url_or_path, expected_commit_sha, str(mission.mission_id)

    def _finalize(
        self,
        *,
        attempt_id: str,
        owner: str,
        epoch: int,
        expected_version: int,
        idempotency_key: str,
        workspace: BaselineWorkspace,
    ) -> None:
        now = self._clock.now()
        with self._unit_of_work:
            attempt = self._unit_of_work.get_attempt(attempt_id)
            operation = self._unit_of_work.get_operation_by_idempotency_key(idempotency_key)
            if attempt is None or operation is None:
                raise AttemptNotFound("Attempt or prepared worktree operation was not found.")
            attempt.assert_active_lease(owner=owner, epoch=epoch, expected_version=expected_version, now=now)
            if operation.status is not OperationStatus.SUCCEEDED:
                operation.succeed(external_result_ref=workspace.worktree_path, now=now)
            self._unit_of_work.commit()
