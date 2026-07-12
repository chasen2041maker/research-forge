"""Worker adapter for the bounded repair slice; it pauses for approval instead of blocking."""

from __future__ import annotations

from dataclasses import dataclass

from research_forge.application.use_cases import (
    ApprovalRequestView,
    BundleView,
    ClaimBaselineAttempt,
    CompleteReproductionMission,
    FinalizeBaselineExecution,
    GetBaselineOutcome,
    PrepareRepairCandidate,
    ProposeRepairPatch,
    RequestRepairApproval,
    RunBaselineAttempt,
)


@dataclass(frozen=True, slots=True)
class RepairWorkerUseCases:
    get_outcome: GetBaselineOutcome
    claim: ClaimBaselineAttempt
    propose: ProposeRepairPatch
    request_approval: RequestRepairApproval
    prepare: PrepareRepairCandidate
    run: RunBaselineAttempt
    finalize: FinalizeBaselineExecution
    complete: CompleteReproductionMission


class RepairWorker:
    """Handle either the proposal phase or an explicitly approved fresh Attempt, never both in one lease."""

    def __init__(self, use_cases: RepairWorkerUseCases) -> None:
        self._use_cases = use_cases

    def process(
        self,
        *,
        attempt_id: str,
        owner: str,
        approval_id: str | None = None,
    ) -> ApprovalRequestView | BundleView:
        completed = self._use_cases.get_outcome.execute(attempt_id)
        if completed is not None:
            return BundleView(completed.sha256, completed.size_bytes, completed.uri)
        lease = self._use_cases.claim.execute(attempt_id=attempt_id, owner=owner)
        if approval_id is None:
            proposal = self._use_cases.propose.execute(
                attempt_id=attempt_id,
                owner=lease.owner,
                epoch=lease.epoch,
                expected_version=lease.version,
            )
            return self._use_cases.request_approval.execute(
                attempt_id=attempt_id,
                owner=lease.owner,
                epoch=lease.epoch,
                expected_version=lease.version,
                proposal=proposal.proposal,
            )
        candidate = self._use_cases.prepare.execute(
            attempt_id=attempt_id,
            owner=lease.owner,
            epoch=lease.epoch,
            expected_version=lease.version,
            idempotency_key=f"{attempt_id}:candidate",
            approval_id=approval_id,
        )
        execution = self._use_cases.run.execute(
            attempt_id=attempt_id,
            owner=lease.owner,
            epoch=lease.epoch,
            expected_version=lease.version,
            idempotency_key=f"{attempt_id}:sandbox",
            worktree_path=candidate.worktree_path,
        )
        self._use_cases.finalize.execute(
            attempt_id=attempt_id,
            owner=lease.owner,
            epoch=lease.epoch,
            expected_version=lease.version,
            sandbox_result=execution.sandbox_result,
            commit_sha=candidate.commit_sha,
        )
        return self._use_cases.complete.execute(
            attempt_id=attempt_id,
            owner=lease.owner,
            epoch=lease.epoch,
            expected_version=lease.version,
            worktree_path=candidate.worktree_path,
        )
