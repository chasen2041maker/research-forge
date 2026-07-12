"""Sandbox port and hardened Docker invocation contract tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from research_forge.adapters.outbound.persistence import InMemoryUnitOfWork
from research_forge.adapters.outbound.sandbox import DeterministicFakeSandbox, DockerSandboxBroker
from research_forge.application.dto import NetworkPolicy, SandboxResult, SandboxRunRequest
from research_forge.application.use_cases import (
    CancelBaselineAttempt,
    ClaimBaselineAttempt,
    RequestMissionCancellation,
    RunBaselineAttempt,
)
from research_forge.domain.execution import OperationStatus
from research_forge.domain.mission import (
    Attempt,
    AttemptId,
    AttemptStatus,
    Mission,
    MissionId,
    MissionStatus,
    Task,
    TaskId,
    TaskStatus,
    TaskType,
)


def _request(worktree_path: str = "/workspace/mission-1") -> SandboxRunRequest:
    return SandboxRunRequest(
        operation_id="operation-1",
        image_digest="sha256:" + "a" * 64,
        argv=("python", "evaluate.py", "--output", "metrics.json"),
        worktree_path=worktree_path,
        working_directory=".",
        timeout_seconds=120,
        max_log_bytes=1_024,
        network_policy=NetworkPolicy.OFFLINE,
        expected_output_paths=("metrics.json",),
    )


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 7, 12, tzinfo=timezone.utc)


class _Ids:
    def __init__(self) -> None:
        self.value = 0

    def new(self, kind: str) -> str:
        self.value += 1
        return f"{kind}-{self.value}"


def test_deterministic_fake_reuses_a_completed_operation() -> None:
    calls = 0

    def result_factory(request: SandboxRunRequest) -> SandboxResult:
        nonlocal calls
        calls += 1
        return SandboxResult(
            operation_id=request.operation_id,
            execution_id="execution-1",
            exit_code=0,
            stdout=b"ok\n",
            stderr=b"",
            output_files={"metrics.json": b'{"accuracy": 0.8}'},
            environment_digest=request.image_digest,
            dataset_sha256="0" * 64,
        )

    sandbox = DeterministicFakeSandbox(result_factory)

    assert sandbox.execute(_request()) == sandbox.execute(_request())
    assert calls == 1


def test_docker_command_applies_offline_hardened_policy_without_docker_socket(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    worktree = workspace_root / "mission-1" / "worktrees" / "baseline"
    worktree.mkdir(parents=True)
    broker = DockerSandboxBroker(
        workspace_root=workspace_root,
        allowed_images={"sha256:" + "a" * 64: "python@sha256:" + "a" * 64},
    )

    command = broker.build_command(_request(str(worktree)))

    assert ["--network", "none"] == command[command.index("--network") : command.index("--network") + 2]
    assert "--read-only" in command
    assert ["--cap-drop", "ALL"] == command[command.index("--cap-drop") : command.index("--cap-drop") + 2]
    assert ["--security-opt", "no-new-privileges"] == command[
        command.index("--security-opt") : command.index("--security-opt") + 2
    ]
    assert "docker.sock" not in " ".join(command)
    assert command[-4:] == ["python", "evaluate.py", "--output", "metrics.json"]


def test_sandbox_completion_before_db_finalize_recovers_one_operation() -> None:
    clock = _Clock()
    uow = InMemoryUnitOfWork()
    spec = {
        "execution": {
            "image_digest": "sha256:" + "a" * 64,
            "run_argv": ["python", "evaluate.py", "--output", "metrics.json"],
            "working_directory": ".",
            "timeout_seconds": 120,
            "network_policy": "offline",
        },
        "metric": {"artifact_path": "metrics.json"},
        "budget": {"max_log_bytes": 1024},
    }
    mission = Mission.create(
        mission_id=MissionId("mission-1"),
        spec_sha256="a" * 64,
        normalized_spec_json=json.dumps(spec),
        created_at=clock.now(),
    )
    mission.mark_ready()
    task = Task(TaskId("task-1"), mission.mission_id, TaskType.BASELINE_REPRODUCTION, clock.now())
    attempt = Attempt(AttemptId("attempt-1"), task.task_id, 1, 0, clock.now())
    with uow:
        uow.add_mission(mission)
        uow.add_task(task)
        uow.add_attempt(attempt)
        uow.commit()
    lease = ClaimBaselineAttempt(unit_of_work=uow, clock=clock, lease_duration=timedelta(seconds=30)).execute(
        attempt_id="attempt-1", owner="worker-a"
    )
    calls = 0

    def result_factory(request: SandboxRunRequest) -> SandboxResult:
        nonlocal calls
        calls += 1
        return SandboxResult(
            operation_id=request.operation_id,
            execution_id="execution-1",
            exit_code=0,
            stdout=b"ok",
            stderr=b"",
            output_files={"metrics.json": b'{"accuracy": 0.8}'},
            environment_digest=request.image_digest,
            dataset_sha256="0" * 64,
        )

    runner = RunBaselineAttempt(
        unit_of_work=uow,
        sandbox_executor=DeterministicFakeSandbox(result_factory),
        clock=clock,
        id_generator=_Ids(),
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        runner.execute(
            attempt_id="attempt-1",
            owner=lease.owner,
            epoch=lease.epoch,
            expected_version=lease.version,
            idempotency_key="attempt-1:sandbox",
            worktree_path="/workspace/mission-1",
            after_execution=lambda: (_ for _ in ()).throw(RuntimeError("simulated crash")),
        )

    operation = uow.get_operation_by_idempotency_key("attempt-1:sandbox")
    assert operation is not None and operation.status is OperationStatus.EXECUTING
    recovered = runner.execute(
        attempt_id="attempt-1",
        owner=lease.owner,
        epoch=lease.epoch,
        expected_version=lease.version,
        idempotency_key="attempt-1:sandbox",
        worktree_path="/workspace/mission-1",
    )
    assert recovered.sandbox_result.execution_id == "execution-1"
    assert calls == 1
    assert uow.get_operation_by_idempotency_key("attempt-1:sandbox").status is OperationStatus.SUCCEEDED


def test_cancel_stops_the_sandbox_before_marking_the_mission_cancelled() -> None:
    clock = _Clock()
    uow = InMemoryUnitOfWork()
    mission = Mission.create(
        mission_id=MissionId("mission-1"),
        spec_sha256="a" * 64,
        normalized_spec_json="{}",
        created_at=clock.now(),
    )
    mission.mark_ready()
    task = Task(TaskId("task-1"), mission.mission_id, TaskType.BASELINE_REPRODUCTION, clock.now())
    attempt = Attempt(AttemptId("attempt-1"), task.task_id, 1, 0, clock.now())
    with uow:
        uow.add_mission(mission)
        uow.add_task(task)
        uow.add_attempt(attempt)
        uow.commit()
    lease = ClaimBaselineAttempt(unit_of_work=uow, clock=clock, lease_duration=timedelta(seconds=30)).execute(
        attempt_id="attempt-1", owner="worker-a"
    )
    RequestMissionCancellation(unit_of_work=uow, clock=clock, id_generator=_Ids()).execute(mission_id="mission-1")
    sandbox = DeterministicFakeSandbox(
        lambda request: SandboxResult(
            operation_id=request.operation_id,
            execution_id="unused",
            exit_code=0,
            stdout=b"",
            stderr=b"",
            output_files={},
            environment_digest=request.image_digest,
            dataset_sha256="0" * 64,
        )
    )

    CancelBaselineAttempt(
        unit_of_work=uow,
        sandbox_executor=sandbox,
        clock=clock,
        id_generator=_Ids(),
    ).execute(
        attempt_id="attempt-1",
        owner=lease.owner,
        epoch=lease.epoch,
        expected_version=lease.version,
        sandbox_operation_id="operation-1",
    )

    assert sandbox.cancelled_operations == {"operation-1"}
    assert uow.get_mission("mission-1").status is MissionStatus.CANCELLED
    assert uow.get_task("task-1").status is TaskStatus.CANCELLED
    assert uow.get_attempt("attempt-1").status is AttemptStatus.CANCELLED
