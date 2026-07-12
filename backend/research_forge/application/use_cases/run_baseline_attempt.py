"""Run the fixed VS-001 command through the idempotent sandbox operation ledger."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

from research_forge.application.dto.sandbox import NetworkPolicy, SandboxResult, SandboxRunRequest
from research_forge.application.ports.sandbox import SandboxExecutor
from research_forge.application.ports.system import Clock, IdGenerator
from research_forge.application.ports.unit_of_work import UnitOfWork
from research_forge.application.use_cases.claim_baseline_attempt import AttemptNotFound
from research_forge.domain.errors import CancellationRequested, OperationConflict
from research_forge.domain.execution import Operation, OperationStatus, OperationType
from research_forge.domain.mission import AttemptId, MissionStatus


@dataclass(frozen=True, slots=True)
class BaselineExecutionView:
    sandbox_result: SandboxResult


class RunBaselineAttempt:
    """Prepare, execute, and finalize one sandbox operation without agent decisions."""

    def __init__(
        self,
        *,
        unit_of_work: UnitOfWork,
        sandbox_executor: SandboxExecutor,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._sandbox_executor = sandbox_executor
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
        worktree_path: str,
        after_execution: Callable[[], None] | None = None,
        on_execution_started: Callable[[SandboxRunRequest], None] | None = None,
    ) -> BaselineExecutionView:
        request, already_succeeded = self._prepare(
            attempt_id=attempt_id,
            owner=owner,
            epoch=epoch,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            worktree_path=worktree_path,
        )
        if already_succeeded:
            result = self._sandbox_executor.get_completed(request.operation_id)
            if result is None:
                raise OperationConflict("Completed sandbox operation has no broker-recoverable result.")
            return BaselineExecutionView(sandbox_result=result)
        if on_execution_started is not None:
            on_execution_started(request)
        result = self._sandbox_executor.execute(request)
        if after_execution is not None:
            after_execution()
        self._finalize(
            attempt_id=attempt_id,
            owner=owner,
            epoch=epoch,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            result=result,
        )
        return BaselineExecutionView(sandbox_result=result)

    def _prepare(
        self,
        *,
        attempt_id: str,
        owner: str,
        epoch: int,
        expected_version: int,
        idempotency_key: str,
        worktree_path: str,
    ) -> tuple[SandboxRunRequest, bool]:
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
                raise CancellationRequested("Mission cancellation forbids starting a sandbox operation.")
            attempt.assert_active_lease(owner=owner, epoch=epoch, expected_version=expected_version, now=now)
            spec = json.loads(mission.normalized_spec_json)
            execution = spec["execution"]
            metric = spec["metric"]
            if execution["network_policy"] != NetworkPolicy.OFFLINE:
                raise OperationConflict("VS-001 permits only a fully offline RUN sandbox.")
            request = SandboxRunRequest(
                operation_id="",
                image_digest=execution["image_digest"],
                argv=tuple(execution["run_argv"]),
                worktree_path=worktree_path,
                working_directory=execution["working_directory"],
                timeout_seconds=execution["timeout_seconds"],
                max_log_bytes=spec["budget"]["max_log_bytes"],
                network_policy=NetworkPolicy.OFFLINE,
                expected_output_paths=(metric["artifact_path"],),
            )
            command_fingerprint = "\x00".join(request.argv)
            input_hash = hashlib.sha256(
                f"{mission.spec_sha256}\n{worktree_path}\n{command_fingerprint}".encode("utf-8")
            ).hexdigest()
            operation = self._unit_of_work.get_operation_by_idempotency_key(idempotency_key)
            if operation is None:
                operation = Operation(
                    operation_id=self._id_generator.new("operation"),
                    idempotency_key=idempotency_key,
                    attempt_id=AttemptId(attempt_id),
                    operation_type=OperationType.SANDBOX_RUN,
                    input_hash=input_hash,
                    lease_epoch=epoch,
                    target_ref_or_path=worktree_path,
                    created_at=now,
                    updated_at=now,
                )
                self._unit_of_work.add_operation(operation)
            elif (
                operation.operation_type is not OperationType.SANDBOX_RUN
                or operation.input_hash != input_hash
                or operation.lease_epoch != epoch
            ):
                raise OperationConflict("Idempotency key conflicts with a different sandbox operation.")
            request = SandboxRunRequest(
                operation_id=operation.operation_id,
                image_digest=request.image_digest,
                argv=request.argv,
                worktree_path=request.worktree_path,
                working_directory=request.working_directory,
                timeout_seconds=request.timeout_seconds,
                max_log_bytes=request.max_log_bytes,
                network_policy=request.network_policy,
                expected_output_paths=request.expected_output_paths,
            )
            already_succeeded = operation.status is OperationStatus.SUCCEEDED
            if not already_succeeded:
                operation.begin(now)
            self._unit_of_work.commit()
        return request, already_succeeded

    def _finalize(
        self,
        *,
        attempt_id: str,
        owner: str,
        epoch: int,
        expected_version: int,
        idempotency_key: str,
        result: SandboxResult,
    ) -> None:
        now = self._clock.now()
        with self._unit_of_work:
            attempt = self._unit_of_work.get_attempt(attempt_id)
            operation = self._unit_of_work.get_operation_by_idempotency_key(idempotency_key)
            if attempt is None or operation is None:
                raise AttemptNotFound("Attempt or prepared sandbox operation was not found.")
            attempt.assert_active_lease(owner=owner, epoch=epoch, expected_version=expected_version, now=now)
            if result.operation_id != operation.operation_id:
                raise OperationConflict("Sandbox result does not belong to the prepared operation.")
            operation.succeed(external_result_ref=result.execution_id, now=now)
            self._unit_of_work.commit()
