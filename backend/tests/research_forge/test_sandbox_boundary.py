"""Sandbox port and hardened Docker invocation contract tests."""

from __future__ import annotations

from pathlib import Path

from research_forge.adapters.outbound.sandbox import DeterministicFakeSandbox, DockerSandboxBroker
from research_forge.application.dto import NetworkPolicy, SandboxResult, SandboxRunRequest


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
