"""Durable completed-result recovery contracts for the isolated Docker broker."""

from __future__ import annotations

import platform
from pathlib import Path

import pytest

from research_forge.adapters.outbound.sandbox import DockerSandboxBroker, DurableCompletedResultStore
from research_forge.adapters.outbound.sandbox.docker_broker import SandboxUnavailable
from research_forge.application.dto import NetworkPolicy, SandboxResult, SandboxRunRequest


def _request() -> SandboxRunRequest:
    return SandboxRunRequest(
        operation_id="operation-1",
        image_digest="sha256:" + "a" * 64,
        argv=("python", "evaluate.py"),
        worktree_path="/workspace/mission-1",
        working_directory=".",
        timeout_seconds=5,
        max_log_bytes=1024,
        network_policy=NetworkPolicy.OFFLINE,
        expected_output_paths=("metrics.json",),
    )


def _result(request: SandboxRunRequest) -> SandboxResult:
    return SandboxResult(
        operation_id=request.operation_id,
        execution_id="execution-1",
        exit_code=0,
        stdout=b"stdout",
        stderr=b"stderr",
        output_files={"metrics.json": b'{"accuracy":0.8}'},
        environment_digest=request.image_digest,
        dataset_sha256="b" * 64,
    )


def _broker(root: Path) -> DockerSandboxBroker:
    return DockerSandboxBroker(
        workspace_root=root / "workspaces",
        allowed_images={"sha256:" + "a" * 64: "python@sha256:" + "a" * 64},
        completed_result_store=DurableCompletedResultStore(root_path=root / "completed-results"),
    )


def test_completed_result_recovers_after_broker_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    first = _broker(tmp_path)
    monkeypatch.setattr(first, "_run_container", _result)

    completed = first.execute(_request())

    restarted = _broker(tmp_path)

    def unexpected_execution(request: SandboxRunRequest) -> SandboxResult:
        raise AssertionError(f"broker should recover {request.operation_id} instead of executing it again")

    monkeypatch.setattr(restarted, "_run_container", unexpected_execution)
    assert restarted.execute(_request()) == completed


def test_corrupt_completed_result_fails_closed(tmp_path: Path) -> None:
    store = DurableCompletedResultStore(root_path=tmp_path / "completed-results")
    store.put(_result(_request()))
    record = next((tmp_path / "completed-results").glob("*.json"))
    record.write_text("{not JSON", encoding="utf-8")
    broker = _broker(tmp_path)

    with pytest.raises(SandboxUnavailable, match="unsafe or corrupt"):
        broker.get_completed("operation-1")
