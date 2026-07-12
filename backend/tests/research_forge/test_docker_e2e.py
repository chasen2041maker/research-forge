"""Formal Linux Docker end-to-end gate for the no-LLM VS-001 baseline slice."""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from subprocess import PIPE, Popen, run
from threading import Thread
from zipfile import ZipFile

import pytest

from research_forge.adapters.outbound.artifacts import LocalContentAddressedStore
from research_forge.adapters.outbound.bundle import DeterministicZipBundleBuilder
from research_forge.adapters.outbound.git import GitWorktreeManager, PinnedLocalPrerequisiteVerifier
from research_forge.adapters.outbound.persistence import InMemoryUnitOfWork
from research_forge.adapters.outbound.sandbox import (
    DockerSandboxBroker,
    SandboxBrokerUnavailable,
    UnixSandboxBrokerClient,
)
from research_forge.adapters.outbound.sandbox.docker_broker import SandboxUnavailable
from research_forge.application.dto.sandbox import NetworkPolicy, SandboxResult, SandboxRunRequest
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
    schema_path = Path(__file__).resolve().parents[3] / "docs" / "规范" / "科研复现任务规范_v1.schema.json"
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
        "metrics = Path('metrics.json')\n"
        "try:\n"
        "    runs = json.loads(metrics.read_text()).get('runs', 0)\n"
        "except (OSError, json.JSONDecodeError):\n"
        "    runs = 0\n"
        "metrics.write_text(json.dumps({'accuracy': 0.8, 'network_blocked': network_blocked, 'runs': runs + 1}))\n",
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


def _start_broker_process(
    tmp_path: Path, image_digest: str, script: str | None = None, socket_name: str = "sandbox.sock"
) -> tuple[Popen[bytes], UnixSandboxBrokerClient]:
    workspace_root = tmp_path / "process-workspaces"
    worktree = workspace_root / "mission-1" / "worktrees" / "baseline"
    worktree.mkdir(parents=True, exist_ok=True)
    default_script = (
        "import json\nfrom pathlib import Path\n"
        "metrics=Path('metrics.json')\n"
        "try: runs=json.loads(metrics.read_text()).get('runs', 0)\n"
        "except (OSError, json.JSONDecodeError): runs=0\n"
        "metrics.write_text(json.dumps({'accuracy': 0.8, 'runs': runs + 1}))\n"
    )
    script_path = worktree / "evaluate.py"
    if not script_path.exists():
        script_path.write_text(script or default_script, encoding="utf-8")
    paper_root = tmp_path / "papers"
    paper_root.mkdir(exist_ok=True)
    (paper_root / "paper.pdf").write_bytes(b"paper")
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({
        "paper_artifacts": {"paper": "a" * 64},
        "paper_artifact_paths": {"paper": "paper.pdf"},
        "allowed_images": {image_digest: image_digest},
    }), encoding="utf-8")
    socket_path = tmp_path / "broker" / socket_name
    environment = os.environ.copy()
    environment.update({
        "RF_DATABASE_URL": "sqlite+pysqlite:///:memory:",
        "RF_REDIS_URL": "redis://unused",
        "RF_API_TOKEN": "test-token",
        "RF_SCHEMA_PATH": str(Path(__file__).resolve().parents[3] / "docs" / "规范" / "科研复现任务规范_v1.schema.json"),
        "RF_POLICY_PATH": str(policy),
        "RF_WORKSPACE_ROOT": str(workspace_root),
        "RF_ARTIFACT_ROOT": str(tmp_path / "cas"),
        "RF_PAPER_ROOT": str(paper_root),
        "RF_BROKER_SOCKET_PATH": str(socket_path),
        "RF_BROKER_STATE_ROOT": str(tmp_path / "broker-state"),
    })
    process = Popen(
        [sys.executable, "-m", "research_forge.bootstrap.sandbox_broker"],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        stdout=PIPE,
        stderr=PIPE,
    )
    deadline = time.monotonic() + 10
    while not socket_path.exists() and time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.communicate(timeout=1)
            raise RuntimeError(f"Broker process exited early: {output!r}")
        time.sleep(0.05)
    if not socket_path.exists():
        process.terminate()
        raise RuntimeError("Broker socket did not become ready.")
    return process, UnixSandboxBrokerClient(socket_path=socket_path)


def _stop_broker_process(process: Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
    process.communicate(timeout=10)


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
    state_root = tmp_path / "broker-state"
    sandbox = DockerSandboxBroker(
        workspace_root=tmp_path / "workspaces",
        allowed_images={image_digest: image_digest},
        broker_state_root=state_root,
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
    restarted_broker = DockerSandboxBroker(
        workspace_root=tmp_path / "workspaces",
        allowed_images={image_digest: image_digest},
        broker_state_root=state_root,
    )
    execution = RunBaselineAttempt(
        unit_of_work=uow,
        sandbox_executor=restarted_broker,
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
    recorded_metrics = json.loads(cas.read_verified(metric.artifact))
    assert recorded_metrics["network_blocked"] is True
    assert recorded_metrics["runs"] == 1
    assert uow.get_mission(mission.mission_id).status is MissionStatus.COMPLETED
    assert uow.get_attempt(mission.attempt_id).status is AttemptStatus.SUCCEEDED
    bundle_registration = uow.get_bundle(mission.mission_id)
    assert bundle_registration is not None and bundle_registration.artifact.sha256 == bundle.sha256
    with ZipFile(BytesIO(cas.read_verified(bundle_registration.artifact))) as archive:
        assert "artifacts/metrics.json" in archive.namelist()


@pytest.mark.docker
def test_two_broker_processes_recover_one_completed_docker_operation(tmp_path: Path) -> None:
    if platform.system() != "Linux" or shutil.which("docker") is None:
        pytest.skip("Formal sandbox gate requires Linux with Docker installed.")
    _docker("info")
    image_digest = _build_image()
    first_process, first_client = _start_broker_process(tmp_path, image_digest)
    workspace = tmp_path / "process-workspaces" / "mission-1" / "worktrees" / "baseline"
    request = SandboxRunRequest(
        operation_id="cross-process-operation",
        image_digest=image_digest,
        argv=("python", "evaluate.py"),
        worktree_path=str(workspace),
        working_directory=".",
        timeout_seconds=30,
        max_log_bytes=1024,
        network_policy=NetworkPolicy.OFFLINE,
        expected_output_paths=("metrics.json",),
    )
    try:
        first = first_client.execute(request)
    finally:
        _stop_broker_process(first_process)
    second_process, second_client = _start_broker_process(tmp_path, image_digest)
    try:
        recovered = second_client.get_completed(request.operation_id)
        repeated = second_client.execute(request)
    finally:
        _stop_broker_process(second_process)

    assert recovered == first
    assert repeated == first
    assert json.loads((workspace / "metrics.json").read_text(encoding="utf-8"))["runs"] == 1
    assert _docker("ps", "-aq", "--filter", f"name={DockerSandboxBroker(workspace_root=tmp_path / 'process-workspaces', allowed_images={image_digest: image_digest}).container_name(request.operation_id)}") == ""


@pytest.mark.docker
def test_two_broker_processes_coalesce_one_concurrent_docker_operation(tmp_path: Path) -> None:
    if platform.system() != "Linux" or shutil.which("docker") is None:
        pytest.skip("Formal sandbox gate requires Linux with Docker installed.")
    _docker("info")
    image_digest = _build_image()
    script = (
        "import json, time\nfrom pathlib import Path\n"
        "time.sleep(1)\nPath('metrics.json').write_text(json.dumps({'accuracy': 0.8, 'runs': 1}))\n"
    )
    first_process, first_client = _start_broker_process(tmp_path, image_digest, script, "first.sock")
    second_process, second_client = _start_broker_process(tmp_path, image_digest, script, "second.sock")
    workspace = tmp_path / "process-workspaces" / "mission-1" / "worktrees" / "baseline"
    request = SandboxRunRequest(
        operation_id="concurrent-cross-process-operation", image_digest=image_digest, argv=("python", "evaluate.py"),
        worktree_path=str(workspace), working_directory=".", timeout_seconds=30, max_log_bytes=1024,
        network_policy=NetworkPolicy.OFFLINE, expected_output_paths=("metrics.json",),
    )
    results: list[SandboxResult] = []
    failures: list[BaseException] = []
    first_thread = Thread(target=lambda: _execute_into(first_client, request, failures, results), daemon=True)
    second_thread = Thread(target=lambda: _execute_into(second_client, request, failures, results), daemon=True)
    try:
        first_thread.start()
        second_thread.start()
        first_thread.join(timeout=40)
        second_thread.join(timeout=40)
    finally:
        _stop_broker_process(first_process)
        _stop_broker_process(second_process)

    assert not first_thread.is_alive() and not second_thread.is_alive()
    assert not failures
    assert len(results) == 2 and results[0] == results[1]
    assert json.loads((workspace / "metrics.json").read_text(encoding="utf-8"))["runs"] == 1


@pytest.mark.docker
def test_two_broker_processes_reject_same_operation_with_different_input(tmp_path: Path) -> None:
    if platform.system() != "Linux" or shutil.which("docker") is None:
        pytest.skip("Formal sandbox gate requires Linux with Docker installed.")
    _docker("info")
    image_digest = _build_image()
    script = (
        "import json, time\nfrom pathlib import Path\n"
        "time.sleep(2)\nPath('metrics.json').write_text(json.dumps({'accuracy': 0.8, 'runs': 1}))\n"
    )
    first_process, first_client = _start_broker_process(tmp_path, image_digest, script, "first.sock")
    second_process, second_client = _start_broker_process(tmp_path, image_digest, script, "second.sock")
    workspace = tmp_path / "process-workspaces" / "mission-1" / "worktrees" / "baseline"
    request = SandboxRunRequest(
        operation_id="conflict-cross-process-operation", image_digest=image_digest, argv=("python", "evaluate.py"),
        worktree_path=str(workspace), working_directory=".", timeout_seconds=30, max_log_bytes=1024,
        network_policy=NetworkPolicy.OFFLINE, expected_output_paths=("metrics.json",),
    )
    failures: list[BaseException] = []
    first_thread = Thread(target=lambda: _execute_into(first_client, request, failures), daemon=True)
    first_thread.start()
    name = DockerSandboxBroker(workspace_root=tmp_path / "process-workspaces", allowed_images={image_digest: image_digest}).container_name(request.operation_id)
    deadline = time.monotonic() + 10
    while not _docker("ps", "-q", "--filter", f"name={name}") and time.monotonic() < deadline:
        time.sleep(0.05)
    assert _docker("ps", "-q", "--filter", f"name={name}")
    try:
        with pytest.raises(SandboxBrokerUnavailable):
            second_client.execute(
                SandboxRunRequest(
                    operation_id=request.operation_id,
                    image_digest=image_digest,
                    argv=("python", "different.py"),
                    worktree_path=str(workspace),
                    working_directory=".",
                    timeout_seconds=30,
                    max_log_bytes=1024,
                    network_policy=NetworkPolicy.OFFLINE,
                    expected_output_paths=("metrics.json",),
                )
            )
        first_thread.join(timeout=40)
    finally:
        _stop_broker_process(first_process)
        _stop_broker_process(second_process)

    assert not failures
    assert json.loads((workspace / "metrics.json").read_text(encoding="utf-8"))["runs"] == 1


@pytest.mark.docker
def test_second_broker_process_adopts_one_running_container_after_first_exits(tmp_path: Path) -> None:
    if platform.system() != "Linux" or shutil.which("docker") is None:
        pytest.skip("Formal sandbox gate requires Linux with Docker installed.")
    _docker("info")
    image_digest = _build_image()
    script = (
        "import json, time\nfrom pathlib import Path\n"
        "time.sleep(3)\nPath('metrics.json').write_text(json.dumps({'accuracy': 0.8, 'runs': 1}))\n"
    )
    first_process, first_client = _start_broker_process(tmp_path, image_digest, script)
    workspace = tmp_path / "process-workspaces" / "mission-1" / "worktrees" / "baseline"
    request = SandboxRunRequest(
        operation_id="running-cross-process-operation", image_digest=image_digest, argv=("python", "evaluate.py"),
        worktree_path=str(workspace), working_directory=".", timeout_seconds=30, max_log_bytes=1024,
        network_policy=NetworkPolicy.OFFLINE, expected_output_paths=("metrics.json",),
    )
    failure: list[BaseException] = []
    thread = Thread(target=lambda: _execute_into(first_client, request, failure), daemon=True)
    thread.start()
    name = DockerSandboxBroker(workspace_root=tmp_path / "process-workspaces", allowed_images={image_digest: image_digest}).container_name(request.operation_id)
    deadline = time.monotonic() + 10
    while not _docker("ps", "-q", "--filter", f"name={name}") and time.monotonic() < deadline:
        time.sleep(0.05)
    assert _docker("ps", "-q", "--filter", f"name={name}")
    _stop_broker_process(first_process)
    thread.join(timeout=10)
    second_process, second_client = _start_broker_process(tmp_path, image_digest, script)
    try:
        result = second_client.execute(request)
    finally:
        _stop_broker_process(second_process)

    assert result.execution_id == name
    assert failure
    assert json.loads((workspace / "metrics.json").read_text(encoding="utf-8"))["runs"] == 1
    assert _docker("ps", "-aq", "--filter", f"name={name}") == ""


def _execute_into(
    client: UnixSandboxBrokerClient,
    request: SandboxRunRequest,
    failure: list[BaseException],
    results: list[SandboxResult] | None = None,
) -> None:
    try:
        result = client.execute(request)
        if results is not None:
            results.append(result)
    except BaseException as exc:
        failure.append(exc)


@pytest.mark.docker
def test_docker_broker_cancel_removes_the_named_container_and_no_result(tmp_path: Path) -> None:
    if platform.system() != "Linux" or shutil.which("docker") is None:
        pytest.skip("Formal sandbox gate requires Linux with Docker installed.")
    _docker("info")
    image_digest = _build_image()
    workspace = tmp_path / "workspaces" / "mission" / "worktrees" / "baseline"
    workspace.mkdir(parents=True)
    (workspace / "evaluate.py").write_text("import time\ntime.sleep(10)\n", encoding="utf-8")
    broker = DockerSandboxBroker(
        workspace_root=tmp_path / "workspaces", allowed_images={image_digest: image_digest}, broker_state_root=tmp_path / "state"
    )
    request = SandboxRunRequest("cancel-operation", image_digest, ("python", "evaluate.py"), str(workspace), ".", 30, 1024, NetworkPolicy.OFFLINE, ("metrics.json",))
    failure: list[BaseException] = []
    thread = Thread(target=lambda: _execute_broker_into(broker, request, failure), daemon=True)
    thread.start()
    name = broker.container_name(request.operation_id)
    deadline = time.monotonic() + 10
    while not _docker("ps", "-q", "--filter", f"name={name}") and time.monotonic() < deadline:
        time.sleep(0.05)
    broker.cancel(request.operation_id)
    broker.cancel(request.operation_id)
    thread.join(timeout=10)

    assert failure and isinstance(failure[0], SandboxUnavailable)
    assert _docker("ps", "-aq", "--filter", f"name={name}") == ""
    assert broker.get_completed(request.operation_id) is None


@pytest.mark.docker
def test_docker_broker_timeout_removes_container_without_success_state(tmp_path: Path) -> None:
    if platform.system() != "Linux" or shutil.which("docker") is None:
        pytest.skip("Formal sandbox gate requires Linux with Docker installed.")
    _docker("info")
    image_digest = _build_image()
    workspace = tmp_path / "workspaces" / "mission" / "worktrees" / "baseline"
    workspace.mkdir(parents=True)
    (workspace / "evaluate.py").write_text("import time\ntime.sleep(10)\n", encoding="utf-8")
    broker = DockerSandboxBroker(
        workspace_root=tmp_path / "workspaces", allowed_images={image_digest: image_digest}, broker_state_root=tmp_path / "state"
    )
    request = SandboxRunRequest("timeout-operation", image_digest, ("python", "evaluate.py"), str(workspace), ".", 1, 1024, NetworkPolicy.OFFLINE, ("metrics.json",))

    with pytest.raises(SandboxUnavailable, match="timeout"):
        broker.execute(request)

    assert _docker("ps", "-aq", "--filter", f"name={broker.container_name(request.operation_id)}") == ""
    assert broker.get_completed(request.operation_id) is None


def _execute_broker_into(broker: DockerSandboxBroker, request: SandboxRunRequest, failure: list[BaseException]) -> None:
    try:
        broker.execute(request)
    except BaseException as exc:
        failure.append(exc)
