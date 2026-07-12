"""Deterministic baseline execution, metric, and evidence-gate integration tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from subprocess import run
from threading import Event, Thread
from zipfile import ZipFile

import pytest

from research_forge.adapters.outbound.artifacts import LocalContentAddressedStore
from research_forge.adapters.outbound.git import GitWorktreeManager
from research_forge.adapters.inbound.worker import BaselineWorker, BaselineWorkerUseCases
from research_forge.adapters.outbound.persistence import InMemoryUnitOfWork
from research_forge.adapters.outbound.sandbox import LocalDevelopmentSandbox
from research_forge.adapters.outbound.bundle import DeterministicZipBundleBuilder
from research_forge.application.dto import JsonSchemaReproductionSpecValidator
from research_forge.application.dto.sandbox import SandboxResult, SandboxRunRequest
from research_forge.application.use_cases import (
    CancelBaselineAttempt,
    ClaimBaselineAttempt,
    CompleteReproductionMission,
    CreateReproductionMission,
    EnsureBaselineWorkspace,
    FinalizeBaselineExecution,
    GetBaselineOutcome,
    HeartbeatView,
    PersistArtifact,
    RenewAttemptLease,
    RunBaselineAttempt,
)
from research_forge.domain.evidence import MetricComparator, MetricExpectation, extract_and_validate_metric
from research_forge.domain.evidence.metric import MetricExtractionError
from research_forge.domain.errors import CancellationRequested
from research_forge.domain.mission import AttemptStatus, MissionStatus
from research_forge.bootstrap import build_local_vs001_runtime
from research_forge.application.ports.queue import AttemptRoute


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 7, 12, tzinfo=timezone.utc)


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


class _BlockingSandbox:
    def __init__(self) -> None:
        self.started = Event()
        self.cancelled = Event()

    def execute(self, request: SandboxRunRequest) -> SandboxResult:
        self.started.set()
        if not self.cancelled.wait(timeout=5):
            raise RuntimeError("Cancellation signal did not reach the sandbox.")
        return SandboxResult(
            operation_id=request.operation_id,
            execution_id=request.operation_id,
            exit_code=143,
            stdout=b"",
            stderr=b"cancelled",
            output_files={},
            environment_digest=request.image_digest,
            dataset_sha256="0" * 64,
        )

    @staticmethod
    def get_completed(operation_id: str) -> SandboxResult | None:
        del operation_id
        return None

    def cancel(self, operation_id: str) -> None:
        assert operation_id
        self.cancelled.set()


class _AdvancingClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 12, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


class _HeartbeatProbe(RenewAttemptLease):
    def __init__(self, renewal: RenewAttemptLease) -> None:
        self._renewal = renewal
        self.renewed = Event()

    def execute(self, *, attempt_id: str, owner: str, epoch: int, expected_version: int) -> HeartbeatView:
        result = self._renewal.execute(
            attempt_id=attempt_id,
            owner=owner,
            epoch=epoch,
            expected_version=expected_version,
        )
        self.renewed.set()
        return result


class _LeaseRenewingSandbox:
    def __init__(self, clock: _AdvancingClock, heartbeat: _HeartbeatProbe) -> None:
        self._clock = clock
        self._heartbeat = heartbeat

    def execute(self, request: SandboxRunRequest) -> SandboxResult:
        self._clock.advance(20)
        if not self._heartbeat.renewed.wait(timeout=5):
            raise RuntimeError("Worker did not renew the active lease.")
        self._clock.advance(15)
        return SandboxResult(
            operation_id=request.operation_id,
            execution_id=request.operation_id,
            exit_code=0,
            stdout=b"",
            stderr=b"",
            output_files={"metrics.json": b'{"accuracy": 0.8}'},
            environment_digest=request.image_digest,
            dataset_sha256="0" * 64,
        )

    @staticmethod
    def get_completed(operation_id: str) -> SandboxResult | None:
        del operation_id
        return None

    @staticmethod
    def cancel(operation_id: str) -> None:
        raise AssertionError(f"Unexpected cancellation of {operation_id}.")


def _git(*arguments: str, cwd: Path) -> str:
    return run(["git", *arguments], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _fixture_repository(root: Path) -> tuple[Path, str]:
    repository = root / "fixture-repo"
    repository.mkdir()
    (repository / "evaluate.py").write_text(
        "import json\nfrom pathlib import Path\nPath('metrics.json').write_text(json.dumps({'accuracy': 0.8}))\n",
        encoding="utf-8",
    )
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
    schema_path = Path(__file__).resolve().parents[3] / "docs" / "contracts" / "reproduction-spec-v1.schema.json"
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
        spec_validator=_validator(),
        unit_of_work=uow,
        clock=clock,
        id_generator=ids,
        prerequisite_verifier=_AcceptingPrerequisites(),
    ).execute(_spec(repository, commit_sha))
    lease = ClaimBaselineAttempt(unit_of_work=uow, clock=clock, lease_duration=timedelta(seconds=30)).execute(
        attempt_id=mission.attempt_id, owner="worker-a"
    )
    workspace_manager = GitWorktreeManager(tmp_path / "workspaces")
    workspace = EnsureBaselineWorkspace(
        unit_of_work=uow,
        workspace_manager=workspace_manager,
        clock=clock,
        id_generator=ids,
    ).execute(
        attempt_id=mission.attempt_id,
        owner=lease.owner,
        epoch=lease.epoch,
        expected_version=lease.version,
        idempotency_key=f"{mission.attempt_id}:baseline-worktree",
    )

    result = RunBaselineAttempt(
        unit_of_work=uow,
        sandbox_executor=LocalDevelopmentSandbox(tmp_path / "workspaces"),
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
    cas = LocalContentAddressedStore(tmp_path / "cas")
    persister = PersistArtifact(
        unit_of_work=uow,
        artifact_store=cas,
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
    bundle = CompleteReproductionMission(
        unit_of_work=uow,
        artifact_store=cas,
        artifact_persister=persister,
        workspace_manager=workspace_manager,
        bundle_builder=DeterministicZipBundleBuilder(),
        clock=clock,
        id_generator=ids,
    ).execute(
        attempt_id=mission.attempt_id,
        owner=lease.owner,
        epoch=lease.epoch,
        expected_version=lease.version,
        worktree_path=workspace.worktree_path,
    )

    persisted_mission = uow.get_mission(mission.mission_id)
    persisted_attempt = uow.get_attempt(mission.attempt_id)
    claims = uow.get_claims_for_mission(mission.mission_id)
    assert finalized.metric_value == 0.8
    assert persisted_mission is not None and persisted_mission.status is MissionStatus.COMPLETED
    assert persisted_attempt is not None and persisted_attempt.status is AttemptStatus.SUCCEEDED
    assert len(uow.metrics) == 1
    assert len(claims) == 1
    assert len(uow.get_evidence_for_claim(claims[0].claim_id)) == 2
    persisted_bundle = uow.get_bundle(mission.mission_id)
    assert persisted_bundle is not None and persisted_bundle.artifact.sha256 == bundle.sha256
    with ZipFile(BytesIO(cas.read_verified(persisted_bundle.artifact))) as archive:
        assert {
            "bundle-manifest.json",
            "claims.jsonl",
            "evidence.jsonl",
            "evaluation-report.json",
            "original-mission-spec.json",
            "reproduce.sh",
            "source.tar",
            "artifacts/metrics.json",
        }.issubset(archive.namelist())
        source_archive = archive.read("source.tar")
        safe_extract_script = archive.read("safe_extract.py")
        original_spec_payload = archive.read("original-mission-spec.json")
        normalized_spec_payload = archive.read("mission-spec.json")
        original_spec = json.loads(original_spec_payload)
        normalized_spec = json.loads(normalized_spec_payload)
        evaluation_report = json.loads(archive.read("evaluation-report.json"))
    assert original_spec == normalized_spec
    assert original_spec_payload != normalized_spec_payload
    assert evaluation_report["reproduction_spec_schema_version"] == 1
    assert evaluation_report["spec_sha256"] == mission.spec_sha256
    bundle_directory = tmp_path / "bundle-content"
    bundle_directory.mkdir()
    (bundle_directory / "source.tar").write_bytes(source_archive)
    (bundle_directory / "safe_extract.py").write_bytes(safe_extract_script)
    replay_directory = bundle_directory / "repository"
    run(["python", "safe_extract.py", "source.tar", "repository"], cwd=bundle_directory, check=True)
    run(["python", "evaluate.py", "--output", "metrics.json"], cwd=replay_directory, check=True)
    assert json.loads((replay_directory / "metrics.json").read_text(encoding="utf-8"))["accuracy"] == 0.8


def test_queue_redelivery_after_db_completion_reuses_the_existing_bundle(tmp_path: Path) -> None:
    repository, commit_sha = _fixture_repository(tmp_path)
    schema_path = Path(__file__).resolve().parents[3] / "docs" / "contracts" / "reproduction-spec-v1.schema.json"
    runtime = build_local_vs001_runtime(
        schema=json.loads(schema_path.read_text(encoding="utf-8")),
        workspace_root=tmp_path / "workspaces",
        artifact_root=tmp_path / "cas",
        paper_artifacts={"paper-toy-001": "a" * 64},
        allowed_image_digests={"sha256:" + "b" * 64},
    )
    mission = runtime.create_mission.execute(_spec(repository, commit_sha))
    published = runtime.publish_outbox.execute()

    first = runtime.worker.process(attempt_id=mission.attempt_id, owner="worker-a")
    assert published.published_event_ids
    delivery = runtime.queue.receive(route=AttemptRoute.BASELINE, consumer_name="test-worker")
    assert delivery is not None and delivery.attempt_id == mission.attempt_id
    second = runtime.worker.process(attempt_id=mission.attempt_id, owner="worker-b")
    runtime.queue.acknowledge(delivery)

    assert first == second
    assert runtime.queue.acknowledged == [mission.attempt_id]
    assert len(runtime.unit_of_work.bundles) == 1


def test_worker_renews_lease_during_sandbox_execution_before_finalizing(tmp_path: Path) -> None:
    clock = _AdvancingClock()
    ids = _Ids()
    repository, commit_sha = _fixture_repository(tmp_path)
    uow = InMemoryUnitOfWork()
    mission = CreateReproductionMission(
        spec_validator=_validator(),
        unit_of_work=uow,
        clock=clock,
        id_generator=ids,
        prerequisite_verifier=_AcceptingPrerequisites(),
    ).execute(_spec(repository, commit_sha))
    workspace_manager = GitWorktreeManager(tmp_path / "workspaces")
    artifact_store = LocalContentAddressedStore(tmp_path / "cas")
    artifact_persister = PersistArtifact(
        unit_of_work=uow,
        artifact_store=artifact_store,
        clock=clock,
        id_generator=ids,
    )
    renewal = _HeartbeatProbe(
        RenewAttemptLease(unit_of_work=uow, clock=clock, lease_duration=timedelta(seconds=30))
    )
    sandbox = _LeaseRenewingSandbox(clock, renewal)
    worker = BaselineWorker(
        BaselineWorkerUseCases(
            get_outcome=GetBaselineOutcome(unit_of_work=uow),
            claim=ClaimBaselineAttempt(unit_of_work=uow, clock=clock, lease_duration=timedelta(seconds=30)),
            heartbeat=renewal,
            ensure_workspace=EnsureBaselineWorkspace(
                unit_of_work=uow,
                workspace_manager=workspace_manager,
                clock=clock,
                id_generator=ids,
            ),
            run=RunBaselineAttempt(
                unit_of_work=uow,
                sandbox_executor=sandbox,
                clock=clock,
                id_generator=ids,
            ),
            cancel=CancelBaselineAttempt(
                unit_of_work=uow,
                sandbox_executor=sandbox,
                clock=clock,
                id_generator=ids,
            ),
            finalize=FinalizeBaselineExecution(
                unit_of_work=uow,
                artifact_persister=artifact_persister,
                clock=clock,
                id_generator=ids,
            ),
            complete=CompleteReproductionMission(
                unit_of_work=uow,
                artifact_store=artifact_store,
                artifact_persister=artifact_persister,
                workspace_manager=workspace_manager,
                bundle_builder=DeterministicZipBundleBuilder(),
                clock=clock,
                id_generator=ids,
            ),
        ),
        heartbeat_interval_seconds=0.001,
    )

    bundle = worker.process(attempt_id=mission.attempt_id, owner="worker-heartbeat")

    attempt = uow.get_attempt(mission.attempt_id)
    assert renewal.renewed.is_set()
    assert bundle.sha256
    assert attempt is not None and attempt.status is AttemptStatus.SUCCEEDED
    assert clock.now() == datetime(2026, 7, 12, 0, 0, 35, tzinfo=timezone.utc)


def test_worker_cancellation_stops_the_sandbox_and_registers_no_artifact(tmp_path: Path) -> None:
    repository, commit_sha = _fixture_repository(tmp_path)
    schema_path = Path(__file__).resolve().parents[3] / "docs" / "contracts" / "reproduction-spec-v1.schema.json"
    sandbox = _BlockingSandbox()
    runtime = build_local_vs001_runtime(
        schema=json.loads(schema_path.read_text(encoding="utf-8")),
        workspace_root=tmp_path / "workspaces",
        artifact_root=tmp_path / "cas",
        paper_artifacts={"paper-toy-001": "a" * 64},
        allowed_image_digests={"sha256:" + "b" * 64},
        sandbox_executor=sandbox,
    )
    mission = runtime.create_mission.execute(_spec(repository, commit_sha))
    failure: list[BaseException] = []

    def process() -> None:
        try:
            runtime.worker.process(attempt_id=mission.attempt_id, owner="worker-cancel")
        except BaseException as exc:  # The worker must surface the durable cancellation to its queue adapter.
            failure.append(exc)

    thread = Thread(target=process, daemon=True)
    thread.start()
    assert sandbox.started.wait(timeout=5)
    runtime.controller.request_cancel(mission.mission_id)
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert len(failure) == 1 and isinstance(failure[0], CancellationRequested)
    persisted_mission = runtime.unit_of_work.get_mission(mission.mission_id)
    persisted_attempt = runtime.unit_of_work.get_attempt(mission.attempt_id)
    assert persisted_mission is not None and persisted_mission.status is MissionStatus.CANCELLED
    assert persisted_attempt is not None and persisted_attempt.status is AttemptStatus.CANCELLED
    assert runtime.unit_of_work.artifacts == ()
