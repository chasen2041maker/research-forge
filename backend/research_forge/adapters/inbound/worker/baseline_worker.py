"""Worker adapter that executes the VS-001 Application sequence and nothing else."""

from __future__ import annotations

from dataclasses import dataclass

from research_forge.application.use_cases import (
    BundleView,
    ClaimBaselineAttempt,
    CompleteReproductionMission,
    EnsureBaselineWorkspace,
    FinalizeBaselineExecution,
    GetBaselineOutcome,
    RunBaselineAttempt,
)


@dataclass(frozen=True, slots=True)
class BaselineWorkerUseCases:
    get_outcome: GetBaselineOutcome
    claim: ClaimBaselineAttempt
    ensure_workspace: EnsureBaselineWorkspace
    run: RunBaselineAttempt
    finalize: FinalizeBaselineExecution
    complete: CompleteReproductionMission


class BaselineWorker:
    """Translate an Attempt ID delivery into Application calls; it owns no business transitions."""

    def __init__(self, use_cases: BaselineWorkerUseCases) -> None:
        self._use_cases = use_cases

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
        execution = self._use_cases.run.execute(
            attempt_id=attempt_id,
            owner=owner,
            epoch=lease.epoch,
            expected_version=lease.version,
            idempotency_key=f"{attempt_id}:sandbox",
            worktree_path=workspace.worktree_path,
        )
        self._use_cases.finalize.execute(
            attempt_id=attempt_id,
            owner=owner,
            epoch=lease.epoch,
            expected_version=lease.version,
            sandbox_result=execution.sandbox_result,
            commit_sha=workspace.commit_sha,
        )
        return self._use_cases.complete.execute(
            attempt_id=attempt_id,
            owner=owner,
            epoch=lease.epoch,
            expected_version=lease.version,
            worktree_path=workspace.worktree_path,
        )
