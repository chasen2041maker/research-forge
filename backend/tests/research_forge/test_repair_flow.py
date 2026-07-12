"""One-baseline, one-patch, one-candidate-run integration test for the bounded repair slice."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from subprocess import run

import pytest

from research_forge.adapters.decision import FixedPatchDecisionEngine
from research_forge.adapters.outbound.artifacts import LocalContentAddressedStore
from research_forge.adapters.outbound.bundle import DeterministicZipBundleBuilder
from research_forge.adapters.outbound.git import GitWorktreeManager
from research_forge.adapters.outbound.persistence import InMemoryUnitOfWork
from research_forge.adapters.outbound.sandbox import LocalDevelopmentSandbox
from research_forge.adapters.inbound.worker import RepairWorker, RepairWorkerUseCases
from research_forge.application.dto import ActionProposal, JsonSchemaReproductionSpecValidator
from research_forge.application.use_cases import (
    BaselineValidationFailure,
    ClaimBaselineAttempt,
    CompleteReproductionMission,
    CreateReproductionMission,
    EnsureBaselineWorkspace,
    FinalizeBaselineExecution,
    PersistArtifact,
    PrepareRepairCandidate,
    ProposeRepairPatch,
    RequestRepairApproval,
    ResolveApproval,
    RunBaselineAttempt,
    GetBaselineOutcome,
)
from research_forge.domain.errors import OperationConflict
from research_forge.domain.mission import AttemptStatus, MissionStatus, TaskStatus, TaskType


class _Clock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 12, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


class _Ids:
    def __init__(self) -> None:
        self.counter = 0

    def new(self, kind: str) -> str:
        self.counter += 1
        return f"{kind}-{self.counter}"


class _AcceptingPrerequisites:
    def verify(
        self,
        *,
        paper_artifact_id: str,
        paper_sha256: str,
        repository_url_or_path: str,
        commit_sha: str,
        image_digest: str,
    ) -> None:
        del paper_artifact_id, paper_sha256, repository_url_or_path, commit_sha, image_digest


def _git(*arguments: str, cwd: Path) -> str:
    return run(["git", *arguments], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _schema() -> dict[str, object]:
    schema_path = Path(__file__).resolve().parents[3] / "docs" / "规范" / "科研复现任务规范_v1.schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


def test_repair_runs_exactly_one_budgeted_candidate_after_a_failed_baseline(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "evaluate.py").write_text(
        "import json\nfrom pathlib import Path\nVALUE = 0.0\nPath('metrics.json').write_text(json.dumps({'accuracy': VALUE}))\n",
        encoding="utf-8",
    )
    _git("init", cwd=source)
    _git("config", "user.email", "tests@example.invalid", cwd=source)
    _git("config", "user.name", "Research Forge Tests", cwd=source)
    _git("add", "evaluate.py", cwd=source)
    _git("commit", "-m", "broken baseline", cwd=source)
    commit_sha = _git("rev-parse", "HEAD", cwd=source)
    spec = {
        "schema_version": 1,
        "mode": "repair",
        "paper": {"artifact_id": "paper-toy-001", "sha256": "a" * 64, "extraction_profile": "plain-text-v1"},
        "repository": {"url_or_path": str(source), "commit_sha": commit_sha},
        "execution": {
            "image_digest": "sha256:" + "b" * 64,
            "setup_mode": "prebuilt",
            "setup_argv": [],
            "run_argv": ["python", "evaluate.py", "--output", "metrics.json"],
            "working_directory": ".",
            "timeout_seconds": 120,
            "network_policy": "offline",
            "allowed_domains": [],
        },
        "metric": {
            "artifact_path": "metrics.json",
            "format": "json",
            "json_pointer": "/accuracy",
            "comparator": "equals",
            "expected_value": 0.8,
            "tolerance": 0.001,
            "unit": "ratio",
        },
        "change_budget": {
            "allowed_paths": ["evaluate.py"],
            "max_files": 1,
            "max_changed_lines": 2,
            "max_candidate_commits": 1,
            "max_candidate_runs": 1,
        },
        "budget": {
            "max_wall_time_seconds": 300,
            "max_cost_usd": 0,
            "max_artifact_bytes": 10_485_760,
            "max_log_bytes": 1_048_576,
        },
    }
    clock = _Clock()
    ids = _Ids()
    uow = InMemoryUnitOfWork()
    mission = CreateReproductionMission(
        spec_validator=JsonSchemaReproductionSpecValidator(_schema()),
        prerequisite_verifier=_AcceptingPrerequisites(),
        unit_of_work=uow,
        clock=clock,
        id_generator=ids,
    ).execute(spec)
    workspace_manager = GitWorktreeManager(tmp_path / "workspaces")
    sandbox = LocalDevelopmentSandbox(tmp_path / "workspaces")
    cas = LocalContentAddressedStore(tmp_path / "cas")
    persister = PersistArtifact(unit_of_work=uow, artifact_store=cas, clock=clock, id_generator=ids)
    baseline_lease = ClaimBaselineAttempt(
        unit_of_work=uow, clock=clock, lease_duration=timedelta(seconds=30)
    ).execute(attempt_id=mission.attempt_id, owner="worker-a")
    baseline_workspace = EnsureBaselineWorkspace(
        unit_of_work=uow,
        workspace_manager=workspace_manager,
        clock=clock,
        id_generator=ids,
    ).execute(
        attempt_id=mission.attempt_id,
        owner=baseline_lease.owner,
        epoch=baseline_lease.epoch,
        expected_version=baseline_lease.version,
        idempotency_key=f"{mission.attempt_id}:baseline-worktree",
    )
    baseline_execution = RunBaselineAttempt(
        unit_of_work=uow, sandbox_executor=sandbox, clock=clock, id_generator=ids
    ).execute(
        attempt_id=mission.attempt_id,
        owner=baseline_lease.owner,
        epoch=baseline_lease.epoch,
        expected_version=baseline_lease.version,
        idempotency_key=f"{mission.attempt_id}:sandbox",
        worktree_path=baseline_workspace.worktree_path,
    )
    finalizer = FinalizeBaselineExecution(
        unit_of_work=uow, artifact_persister=persister, clock=clock, id_generator=ids
    )
    with pytest.raises(BaselineValidationFailure, match="frozen expectation"):
        finalizer.execute(
            attempt_id=mission.attempt_id,
            owner=baseline_lease.owner,
            epoch=baseline_lease.epoch,
            expected_version=baseline_lease.version,
            sandbox_result=baseline_execution.sandbox_result,
            commit_sha=baseline_workspace.commit_sha,
        )
    tasks = uow.get_tasks_for_mission(mission.mission_id)
    repair_task = next(task for task in tasks if task.task_type is TaskType.REPAIR_CANDIDATE)
    repair_attempt = uow.get_attempts_for_task(str(repair_task.task_id))[0]
    assert uow.get_attempt(mission.attempt_id).status is AttemptStatus.FAILED
    assert uow.get_task(mission.task_id).status is TaskStatus.FAILED
    proposal = ActionProposal(
        action_type="APPLY_PATCH",
        unified_diff=(
            "diff --git a/evaluate.py b/evaluate.py\n"
            "--- a/evaluate.py\n"
            "+++ b/evaluate.py\n"
            "@@ -1,4 +1,4 @@\n"
            " import json\n"
            " from pathlib import Path\n"
            "-VALUE = 0.0\n"
            "+VALUE = 0.8\n"
            " Path('metrics.json').write_text(json.dumps({'accuracy': VALUE}))\n"
        ),
        rationale_summary="Fix the frozen fixture's constant metric.",
        expected_artifacts=("metrics.json",),
    )
    engine = FixedPatchDecisionEngine(proposal)
    completion = CompleteReproductionMission(
        unit_of_work=uow,
        artifact_store=cas,
        artifact_persister=persister,
        workspace_manager=workspace_manager,
        bundle_builder=DeterministicZipBundleBuilder(),
        clock=clock,
        id_generator=ids,
    )
    repair_worker = RepairWorker(
        RepairWorkerUseCases(
            get_outcome=GetBaselineOutcome(unit_of_work=uow),
            claim=ClaimBaselineAttempt(unit_of_work=uow, clock=clock, lease_duration=timedelta(seconds=30)),
            propose=ProposeRepairPatch(
                unit_of_work=uow,
                artifact_store=cas,
                decision_engine=engine,
                clock=clock,
            ),
            request_approval=RequestRepairApproval(
                unit_of_work=uow,
                clock=clock,
                id_generator=ids,
                approval_ttl=timedelta(minutes=5),
            ),
            prepare=PrepareRepairCandidate(
                unit_of_work=uow,
                artifact_store=cas,
                workspace_manager=workspace_manager,
                decision_engine=engine,
                clock=clock,
                id_generator=ids,
            ),
            run=RunBaselineAttempt(
                unit_of_work=uow, sandbox_executor=sandbox, clock=clock, id_generator=ids
            ),
            finalize=finalizer,
            complete=completion,
        )
    )
    approval = repair_worker.process(attempt_id=str(repair_attempt.attempt_id), owner="worker-b")
    assert uow.get_mission(mission.mission_id).status is MissionStatus.WAITING_APPROVAL
    assert uow.get_attempt(str(repair_attempt.attempt_id)).status is AttemptStatus.RETRYABLE
    resolved = ResolveApproval(unit_of_work=uow, clock=clock, id_generator=ids).execute(
        approval_id=approval.approval_id,
        approved=True,
        decided_by="reviewer-a",
    )
    assert resolved.resumed_attempt_id is not None
    resumed_attempt = uow.get_attempt(resolved.resumed_attempt_id)
    assert resumed_attempt is not None
    assert resumed_attempt.resume_from_attempt_id == repair_attempt.attempt_id
    resumed_lease = ClaimBaselineAttempt(
        unit_of_work=uow, clock=clock, lease_duration=timedelta(seconds=30)
    ).execute(attempt_id=resolved.resumed_attempt_id, owner="worker-c")
    mismatched_engine = FixedPatchDecisionEngine(
        ActionProposal(
            action_type="APPLY_PATCH",
            unified_diff=proposal.unified_diff.replace("+VALUE = 0.8", "+VALUE = 1.0"),
            rationale_summary="A proposal whose hash was not approved.",
            expected_artifacts=("metrics.json",),
        )
    )
    with pytest.raises(OperationConflict, match="hash"):
        PrepareRepairCandidate(
            unit_of_work=uow,
            artifact_store=cas,
            workspace_manager=workspace_manager,
            decision_engine=mismatched_engine,
            clock=clock,
            id_generator=ids,
        ).execute(
            attempt_id=str(resumed_attempt.attempt_id),
            owner=resumed_lease.owner,
            epoch=resumed_lease.epoch,
            expected_version=resumed_lease.version,
            idempotency_key=f"{resumed_attempt.attempt_id}:mismatched-candidate",
            approval_id=approval.approval_id,
        )
    clock.advance(31)
    bundle = repair_worker.process(
        attempt_id=str(resumed_attempt.attempt_id),
        owner="worker-d",
        approval_id=approval.approval_id,
    )

    assert bundle.sha256
    assert uow.get_mission(mission.mission_id).status is MissionStatus.COMPLETED
    assert uow.get_attempt(str(resumed_attempt.attempt_id)).status is AttemptStatus.SUCCEEDED
    assert len(engine.requests) == 2
