"""Deterministic baseline execution, metric, and evidence-gate integration tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from subprocess import run

import pytest

from research_forge.adapters.outbound.artifacts import LocalContentAddressedStore
from research_forge.adapters.outbound.git import GitWorktreeManager
from research_forge.adapters.outbound.persistence import InMemoryUnitOfWork
from research_forge.adapters.outbound.sandbox import DeterministicFakeSandbox
from research_forge.application.dto import JsonSchemaReproductionSpecValidator, SandboxResult, SandboxRunRequest
from research_forge.application.use_cases import (
    ClaimBaselineAttempt,
    CreateReproductionMission,
    EnsureBaselineWorkspace,
    FinalizeBaselineExecution,
    PersistArtifact,
    RunBaselineAttempt,
)
from research_forge.domain.evidence import MetricComparator, MetricExpectation, extract_and_validate_metric
from research_forge.domain.evidence.metric import MetricExtractionError
from research_forge.domain.mission import AttemptStatus, MissionStatus


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 7, 12, tzinfo=timezone.utc)


class _Ids:
    def __init__(self) -> None:
        self.counter = 0

    def new(self, kind: str) -> str:
        self.counter += 1
        return f"{kind}-{self.counter}"


def _git(*arguments: str, cwd: Path) -> str:
    return run(["git", *arguments], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _fixture_repository(root: Path) -> tuple[Path, str]:
    repository = root / "fixture-repo"
    repository.mkdir()
    (repository / "evaluate.py").write_text("# executed by the pinned image\n", encoding="utf-8")
    _git("init", cwd=repository)
    _git("config", "user.email", "tests@example.invalid", cwd=repository)
    _git("config", "user.name", "Research Forge Tests", cwd=repository)
    _git("add", "evaluate.py", cwd=repository)
    _git("commit", "-m", "fixture", cwd=repository)
    return repository, _git("rev-parse", "HEAD", cwd=repository)


def _spec(repository: Path, commit_sha: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "mode": "reproduce",
        "paper": {"artifact_id": "paper-toy-001", "sha256": "a" * 64, "extraction_profile": "plain-text-v1"},
        "repository": {"url_or_path": str(repository), "commit_sha": commit_sha},
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
            "allowed_paths": [],
            "max_files": 0,
            "max_changed_lines": 0,
            "max_candidate_commits": 0,
            "max_candidate_runs": 0,
        },
        "budget": {
            "max_wall_time_seconds": 300,
            "max_cost_usd": 0,
            "max_artifact_bytes": 10_485_760,
            "max_log_bytes": 1_048_576,
        },
    }


def _validator() -> JsonSchemaReproductionSpecValidator:
    schema_path = Path(__file__).resolve().parents[3] / "docs" / "规范" / "科研复现任务规范_v1.schema.json"
    return JsonSchemaReproductionSpecValidator(json.loads(schema_path.read_text(encoding="utf-8")))


def test_metric_extractor_uses_rfc6901_and_requires_a_finite_number() -> None:
    expectation = MetricExpectation("/scores/a~1b", MetricComparator.EQUALS, 0.8, 0.001, "ratio")

    validation = extract_and_validate_metric(b'{"scores":{"a/b":0.8}}', expectation)

    assert validation.passed is True
    with pytest.raises(MetricExtractionError):
        extract_and_validate_metric(b'{"scores":{"a/b":"0.8"}}', expectation)


def test_no_llm_baseline_flow_creates_verified_metric_evidence(tmp_path: Path) -> None:
    clock = _Clock()
    ids = _Ids()
    repository, commit_sha = _fixture_repository(tmp_path)
    uow = InMemoryUnitOfWork()
    mission = CreateReproductionMission(
        spec_validator=_validator(), unit_of_work=uow, clock=clock, id_generator=ids
    ).execute(_spec(repository, commit_sha))
    lease = ClaimBaselineAttempt(unit_of_work=uow, clock=clock, lease_duration=timedelta(seconds=30)).execute(
        attempt_id=mission.attempt_id, owner="worker-a"
    )
    workspace = EnsureBaselineWorkspace(
        unit_of_work=uow,
        workspace_manager=GitWorktreeManager(tmp_path / "workspaces"),
        clock=clock,
        id_generator=ids,
    ).execute(
        attempt_id=mission.attempt_id,
        owner=lease.owner,
        epoch=lease.epoch,
        expected_version=lease.version,
        idempotency_key=f"{mission.attempt_id}:baseline-worktree",
    )

    def result_factory(request: SandboxRunRequest) -> SandboxResult:
        return SandboxResult(
            operation_id=request.operation_id,
            execution_id="sandbox-execution-1",
            exit_code=0,
            stdout=b"baseline complete\n",
            stderr=b"",
            output_files={"metrics.json": b'{"accuracy": 0.8}'},
            environment_digest=request.image_digest,
            dataset_sha256="0" * 64,
        )

    result = RunBaselineAttempt(
        unit_of_work=uow,
        sandbox_executor=DeterministicFakeSandbox(result_factory),
        clock=clock,
        id_generator=ids,
    ).execute(
        attempt_id=mission.attempt_id,
        owner=lease.owner,
        epoch=lease.epoch,
        expected_version=lease.version,
        idempotency_key=f"{mission.attempt_id}:sandbox",
        worktree_path=workspace.worktree_path,
    )
    persister = PersistArtifact(
        unit_of_work=uow,
        artifact_store=LocalContentAddressedStore(tmp_path / "cas"),
        clock=clock,
        id_generator=ids,
    )
    finalized = FinalizeBaselineExecution(
        unit_of_work=uow,
        artifact_persister=persister,
        clock=clock,
        id_generator=ids,
    ).execute(
        attempt_id=mission.attempt_id,
        owner=lease.owner,
        epoch=lease.epoch,
        expected_version=lease.version,
        sandbox_result=result.sandbox_result,
        commit_sha=workspace.commit_sha,
    )

    persisted_mission = uow.get_mission(mission.mission_id)
    persisted_attempt = uow.get_attempt(mission.attempt_id)
    claims = uow.get_claims_for_mission(mission.mission_id)
    assert finalized.metric_value == 0.8
    assert persisted_mission is not None and persisted_mission.status is MissionStatus.VERIFYING
    assert persisted_attempt is not None and persisted_attempt.status is AttemptStatus.RUNNING
    assert len(uow.metrics) == 1
    assert len(claims) == 1
    assert len(uow.get_evidence_for_claim(claims[0].claim_id)) == 2
