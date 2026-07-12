"""Production composition-root contracts without requiring PostgreSQL, Redis, or Docker."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
import pytest

from research_forge.adapters.outbound.sandbox import UnixSandboxBrokerClient
from research_forge.bootstrap import (
    ProductionConfigurationError,
    ProductionVs001Runtime,
    ProductionVs001Settings,
    build_production_vs001_runtime,
)
from research_forge.domain.errors import CancellationRequested
from research_forge.domain.mission import MissionId, Task, TaskId, TaskType


class _Redis:
    def __init__(self) -> None:
        self.values: list[str] = []

    def rpush(self, key: str, value: str) -> int:
        assert key == "research-forge:attempts"
        self.values.append(value)
        return len(self.values)

    def lindex(self, key: str, index: int) -> str | None:
        assert key == "research-forge:attempts" and index == 0
        return self.values[0] if self.values else None

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        assert "LPOP" in script and numkeys == 1
        key, attempt_id = keys_and_args
        assert key == "research-forge:attempts"
        if not self.values or self.values[0] != attempt_id:
            return 0
        self.values.pop(0)
        return 1

    @staticmethod
    def ping() -> bool:
        return True


class _CancellationQueue:
    def __init__(self) -> None:
        self.acknowledged: list[str] = []

    @staticmethod
    def receive() -> str:
        return "attempt-cancelled"

    def acknowledge(self, attempt_id: str) -> None:
        self.acknowledged.append(attempt_id)


class _CancellationUnitOfWork:
    def __enter__(self) -> "_CancellationUnitOfWork":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool | None:
        return None

    @staticmethod
    def get_attempt(attempt_id: str) -> object:
        assert attempt_id == "attempt-cancelled"
        return SimpleNamespace(task_id=TaskId("task-cancelled"))

    @staticmethod
    def get_task(task_id: str) -> Task:
        assert task_id == "task-cancelled"
        return Task(TaskId(task_id), MissionId("mission-cancelled"), TaskType.BASELINE_REPRODUCTION, _now())

    @staticmethod
    def commit() -> None:
        return None


class _CancellationWorker:
    @staticmethod
    def process(*, attempt_id: str, owner: str) -> object:
        del attempt_id, owner
        raise CancellationRequested("durably cancelled")


def _now() -> object:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _schema() -> dict[str, object]:
    schema_path = Path(__file__).resolve().parents[3] / "docs" / "规范" / "科研复现任务规范_v1.schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


def test_production_composition_exposes_health_and_checks_dependencies(tmp_path: Path) -> None:
    settings = ProductionVs001Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'forge.db'}",
        redis_url="redis://unused-for-test",
        api_token="test-token",
        schema=_schema(),
        workspace_root=tmp_path / "workspaces",
        artifact_root=tmp_path / "cas",
        broker_socket_path=tmp_path / "broker" / "sandbox.sock",
        paper_root=tmp_path / "papers",
        paper_artifacts={"paper-toy-001": "a" * 64},
        paper_artifact_paths={},
        allowed_images={"sha256:" + "b" * 64: "example.invalid/repro@sha256:" + "b" * 64},
        cors_origins=("http://localhost:3000",),
    )

    runtime = build_production_vs001_runtime(settings, redis_client=_Redis())

    assert TestClient(runtime.app).get("/healthz").json() == {"status": "ok"}
    runtime.check_dependencies()
    assert runtime.process_one(owner="worker-test") is False
    assert runtime.baseline_worker._use_cases.run._sandbox_executor is runtime.sandbox_client
    assert isinstance(runtime.sandbox_client, UnixSandboxBrokerClient)


def test_production_settings_loads_only_explicit_environment_and_policy_files(tmp_path: Path) -> None:
    schema_path = tmp_path / "schema.json"
    policy_path = tmp_path / "policy.json"
    paper_root = tmp_path / "papers"
    paper_root.mkdir()
    paper_payload = b"registered paper bytes"
    (paper_root / "toy-paper.pdf").write_bytes(paper_payload)
    paper_sha256 = sha256(paper_payload).hexdigest()
    schema_path.write_text(json.dumps(_schema()), encoding="utf-8")
    policy_path.write_text(
        json.dumps(
            {
                "paper_artifacts": {"paper-toy-001": paper_sha256},
                "paper_artifact_paths": {"paper-toy-001": "toy-paper.pdf"},
                "allowed_images": {"sha256:" + "b" * 64: "example.invalid/repro@sha256:" + "b" * 64},
            }
        ),
        encoding="utf-8",
    )

    environment = {
        "RF_DATABASE_URL": "sqlite+pysqlite:///:memory:",
        "RF_REDIS_URL": "redis://localhost:6379/0",
        "RF_API_TOKEN": "test-token",
        "RF_SCHEMA_PATH": str(schema_path),
        "RF_POLICY_PATH": str(policy_path),
        "RF_WORKSPACE_ROOT": str(tmp_path / "workspaces"),
        "RF_ARTIFACT_ROOT": str(tmp_path / "cas"),
        "RF_PAPER_ROOT": str(paper_root),
        "RF_BROKER_SOCKET_PATH": str(tmp_path / "broker" / "sandbox.sock"),
        "RF_CORS_ORIGINS": "https://forge.example.test, https://review.example.test",
    }
    settings = ProductionVs001Settings.from_environment(environment)

    assert settings.paper_artifacts == {"paper-toy-001": paper_sha256}
    assert settings.paper_artifact_paths == {"paper-toy-001": paper_root / "toy-paper.pdf"}
    assert settings.broker_socket_path == (tmp_path / "broker" / "sandbox.sock").resolve()
    assert settings.cors_origins == ("https://forge.example.test", "https://review.example.test")

    policy_path.write_text(
        json.dumps(
            {
                "paper_artifacts": {"paper-toy-001": paper_sha256},
                "paper_artifact_paths": {"paper-toy-001": "../escaped-paper.pdf"},
                "allowed_images": {"sha256:" + "b" * 64: "example.invalid/repro@sha256:" + "b" * 64},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ProductionConfigurationError, match="escapes RF_PAPER_ROOT"):
        ProductionVs001Settings.from_environment(environment)


def test_production_runtime_acknowledges_a_durably_cancelled_attempt(tmp_path: Path) -> None:
    queue = _CancellationQueue()
    runtime = ProductionVs001Runtime(
        app=FastAPI(),
        baseline_worker=_CancellationWorker(),
        publish_outbox=object(),
        queue=queue,
        unit_of_work=_CancellationUnitOfWork(),
        database_engine=create_engine(f"sqlite+pysqlite:///{tmp_path / 'forge.db'}"),
        redis_client=object(),
        sandbox_client=UnixSandboxBrokerClient(socket_path=tmp_path / "broker.sock"),
    )

    assert runtime.process_one(owner="worker-cancel") is True
    assert queue.acknowledged == ["attempt-cancelled"]
