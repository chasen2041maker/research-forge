"""Formal Linux Docker end-to-end gate for the no-LLM VS-001 baseline slice."""

from __future__ import annotations

import json
import os
import platform
import shutil
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from subprocess import run
from zipfile import ZipFile

import pytest

from research_forge.adapters.outbound.artifacts import LocalContentAddressedStore
from research_forge.adapters.outbound.bundle import DeterministicZipBundleBuilder
from research_forge.adapters.outbound.git import GitWorktreeManager, PinnedLocalPrerequisiteVerifier
from research_forge.adapters.outbound.persistence import InMemoryUnitOfWork
from research_forge.adapters.outbound.sandbox import DockerSandboxBroker
from research_forge.application.dto import JsonSchemaReproductionSpecValidator
from research_forge.application.use_cases import (
    ClaimBaselineAttempt,
    CompleteReproductionMission,
    CreateReproductionMission,
    EnsureBaselineWorkspace,
    FinalizeBaselineExecution,
    PersistArtifact,
    RunBaselineAttempt,
)
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


def _docker(*arguments: str, cwd: Path | None = None) -> str:
    return run(["docker", *arguments], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _schema() -> dict[str, object]:
    schema_path = Path(__file__).resolve().parents[3] / "docs" / "contracts" / "reproduction-spec-v1.schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _make_repository(root: Path) -> tuple[Path, str]:
    repository = root / "source"
    repository.mkdir()
    (repository / "evaluate.py").write_text(
        "import json\n"
        "import socket\n"
        "from pathlib import Path\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 53), timeout=1)\n"
        "    network_blocked = False\n"
        "except OSError:\n"
        "    network_blocked = True\n"
        "Path('metrics.json').write_text(json.dumps({'accuracy': 0.8, 'network_blocked': network_blocked}))\n",
        encoding="utf-8",
    )
    _git("init", cwd=repository)
    _git("config", "user.email", "tests@example.invalid", cwd=repository)
    _git("config", "user.name", "Research Forge Tests", cwd=repository)
    _git("add", "evaluate.py", cwd=repository)
    _git("commit", "-m", "fixture", cwd=repository)
    return repository, _git("rev-parse", "HEAD", cwd=repository)


def _build_image() -> str:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "docker"
    tag = "research-forge-vs001-e2e:ci"
    _docker("build", "--tag", tag, str(fixture))
    return _docker("image", "inspect", "--format", "{{.Id}}", tag)


@pytest.mark.docker
def test_docker_broker_runs_the_complete_offline_baseline_flow(tmp_path: Path) -> None:
    if platform.system() != "Linux" or shutil.which("docker") is None:
        pytest.skip("Formal sandbox gate requires Linux with Docker installed.")
    _docker("info")
    image_digest = _build_image()
    clock = _Clock()
    ids = _Ids()
    repository, commit_sha = _make_repository(tmp_path)
    spec = {
        "schema_version": 1,
        "mode": "reproduce",
        "paper": {"artifact_id": "paper-toy-001", "sha256": "a" * 64, "extraction_profile": "plain-text-v1"},
        "repository": {"url_or_path": str(repository), "commit_sha": commit_sha},
        "execution": {
            "image_digest": image_digest,
            "setup_mode": "prebuilt",
            "setup_argv": [],
            "run_argv": ["python", "evaluate.py", "--output", "metrics.json"],
            "working_directory": ".",
            "timeout_seconds": 30,
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
            "max_wall_time_seconds": 60,
            "max_cost_usd": 0,
            "max_artifact_bytes": 10_485_760,
            "max_log_bytes": 1_048_576,
        },
    }
    uow = InMemoryUnitOfWork()
    mission = CreateReproductionMission(
        spec_validator=JsonSchemaReproductionSpecValidator(_schema()),
        prerequisite_verifier=PinnedLocalPrerequisiteVerifier(
            paper_artifacts={"paper-toy-001": "a" * 64},
            allowed_image_digests={image_digest},
        ),
        unit_of_work=uow,
        clock=clock,
        id_generator=ids,
    ).execute(spec)
    lease = ClaimBaselineAttempt(unit_of_work=uow, clock=clock, lease_duration=timedelta(seconds=30)).execute(
        attempt_id=mission.attempt_id, owner="docker-worker"
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
    os.chmod(workspace.worktree_path, 0o777)
    sandbox = DockerSandboxBroker(
        workspace_root=tmp_path / "workspaces",
        allowed_images={image_digest: image_digest},
    )
    runner = RunBaselineAttempt(
        unit_of_work=uow,
        sandbox_executor=sandbox,
        clock=clock,
        id_generator=ids,
    )
    with pytest.raises(RuntimeError, match="simulated worker crash"):
        runner.execute(
            attempt_id=mission.attempt_id,
            owner=lease.owner,
            epoch=lease.epoch,
            expected_version=lease.version,
            idempotency_key=f"{mission.attempt_id}:sandbox",
            worktree_path=workspace.worktree_path,
            after_execution=lambda: (_ for _ in ()).throw(RuntimeError("simulated worker crash")),
        )
    execution = runner.execute(
        attempt_id=mission.attempt_id,
        owner=lease.owner,
        epoch=lease.epoch,
        expected_version=lease.version,
        idempotency_key=f"{mission.attempt_id}:sandbox",
        worktree_path=workspace.worktree_path,
    )
    cas = LocalContentAddressedStore(tmp_path / "cas")
    persister = PersistArtifact(unit_of_work=uow, artifact_store=cas, clock=clock, id_generator=ids)
    FinalizeBaselineExecution(
        unit_of_work=uow,
        artifact_persister=persister,
        clock=clock,
        id_generator=ids,
    ).execute(
        attempt_id=mission.attempt_id,
        owner=lease.owner,
        epoch=lease.epoch,
        expected_version=lease.version,
        sandbox_result=execution.sandbox_result,
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

    metric = uow.metrics[0]
    assert json.loads(cas.read_verified(metric.artifact))["network_blocked"] is True
    assert uow.get_mission(mission.mission_id).status is MissionStatus.COMPLETED
    assert uow.get_attempt(mission.attempt_id).status is AttemptStatus.SUCCEEDED
    bundle_registration = uow.get_bundle(mission.mission_id)
    assert bundle_registration is not None and bundle_registration.artifact.sha256 == bundle.sha256
    with ZipFile(BytesIO(cas.read_verified(bundle_registration.artifact))) as archive:
        assert "artifacts/metrics.json" in archive.namelist()
