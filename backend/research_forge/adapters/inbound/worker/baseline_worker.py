"""Worker adapter that executes the VS-001 Application sequence and nothing else."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Lock, Thread
from time import monotonic

from research_forge.application.use_cases import (
    BaselineExecutionView,
    BundleView,
    CancelBaselineAttempt,
    ClaimBaselineAttempt,
    CompleteReproductionMission,
    EnsureBaselineWorkspace,
    FinalizeBaselineExecution,
    GetBaselineOutcome,
    RenewAttemptLease,
    RunBaselineAttempt,
)
from research_forge.application.dto.sandbox import SandboxRunRequest
from research_forge.domain.errors import CancellationRequested


@dataclass(frozen=True, slots=True)
class BaselineWorkerUseCases:
    get_outcome: GetBaselineOutcome
    claim: ClaimBaselineAttempt
    heartbeat: RenewAttemptLease
    ensure_workspace: EnsureBaselineWorkspace
    run: RunBaselineAttempt
    cancel: CancelBaselineAttempt
    finalize: FinalizeBaselineExecution
    complete: CompleteReproductionMission


class BaselineWorker:
    """Translate an Attempt ID delivery into Application calls; it owns no business transitions."""

    def __init__(self, use_cases: BaselineWorkerUseCases, *, heartbeat_interval_seconds: float = 10.0) -> None:
        if heartbeat_interval_seconds <= 0:
            raise ValueError("Heartbeat interval must be positive.")
        self._use_cases = use_cases
        self._heartbeat_interval_seconds = heartbeat_interval_seconds

    def process(self, *, attempt_id: str, owner: str) -> BundleView:
        completed = self._use_cases.get_outcome.execute(attempt_id)
        if completed is not None:
            return BundleView(completed.sha256, completed.size_bytes, completed.uri)
        lease = self._use_cases.claim.execute(attempt_id=attempt_id, owner=owner)
        workspace = self._use_cases.ensure_workspace.execute(
            attempt_id=attempt_id,
            owner=owner,
            epoch=lease.epoch,
            expected_version=lease.version,
            idempotency_key=f"{attempt_id}:baseline-worktree",
        )
        execution, expected_version = self._run_with_cancellation_monitor(
            attempt_id=attempt_id,
            owner=owner,
            epoch=lease.epoch,
            expected_version=lease.version,
            worktree_path=workspace.worktree_path,
        )
        self._use_cases.finalize.execute(
            attempt_id=attempt_id,
            owner=owner,
            epoch=lease.epoch,
            expected_version=expected_version,
            sandbox_result=execution.sandbox_result,
            commit_sha=workspace.commit_sha,
        )
        return self._use_cases.complete.execute(
            attempt_id=attempt_id,
            owner=owner,
            epoch=lease.epoch,
            expected_version=expected_version,
            worktree_path=workspace.worktree_path,
        )

    def _run_with_cancellation_monitor(
        self,
        *,
        attempt_id: str,
        owner: str,
        epoch: int,
        expected_version: int,
        worktree_path: str,
    ) -> tuple[BaselineExecutionView, int]:
        stop = Event()
        cancelled = Event()
        monitor: Thread | None = None
        lease_version = _LeaseVersion(expected_version)

        def start_monitor(request: SandboxRunRequest) -> None:
            nonlocal monitor
            monitor = Thread(
                target=self._watch_for_cancellation,
                args=(stop, cancelled, attempt_id, owner, epoch, lease_version, request.operation_id),
                daemon=True,
            )
            monitor.start()

        def stop_monitor() -> int:
            stop.set()
            if monitor is not None:
                monitor.join(timeout=1)
            return lease_version.current()

        try:
            execution = self._use_cases.run.execute(
                attempt_id=attempt_id,
                owner=owner,
                epoch=epoch,
                expected_version=expected_version,
                idempotency_key=f"{attempt_id}:sandbox",
                worktree_path=worktree_path,
                on_execution_started=start_monitor,
                on_execution_finished=stop_monitor,
            )
            return execution, lease_version.current()
        except Exception:
            if cancelled.is_set():
                raise CancellationRequested("Sandbox was cancelled before artifact finalization.") from None
            raise
        finally:
            stop_monitor()

    def _watch_for_cancellation(
        self,
        stop: Event,
        cancelled: Event,
        attempt_id: str,
        owner: str,
        epoch: int,
        lease_version: "_LeaseVersion",
        operation_id: str,
    ) -> None:
        next_heartbeat = monotonic() + self._heartbeat_interval_seconds
        while not stop.wait(min(0.05, max(0.0, next_heartbeat - monotonic()))):
            expected_version = lease_version.current()
            try:
                self._use_cases.cancel.execute(
                    attempt_id=attempt_id,
                    owner=owner,
                    epoch=epoch,
                    expected_version=expected_version,
                    sandbox_operation_id=operation_id,
                )
            except CancellationRequested:
                pass
            else:
                cancelled.set()
                return
            if monotonic() >= next_heartbeat:
                heartbeat = self._use_cases.heartbeat.execute(
                    attempt_id=attempt_id,
                    owner=owner,
                    epoch=epoch,
                    expected_version=expected_version,
                )
                lease_version.update(heartbeat.version)
                next_heartbeat = monotonic() + self._heartbeat_interval_seconds


class _LeaseVersion:
    """Share the current optimistic version between the execution and monitor threads."""

    def __init__(self, version: int) -> None:
        self._version = version
        self._lock = Lock()

    def current(self) -> int:
        with self._lock:
            return self._version

    def update(self, version: int) -> None:
        with self._lock:
            self._version = version
