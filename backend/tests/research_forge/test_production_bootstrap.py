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
from research_forge.application.ports.queue import AttemptDelivery, AttemptRoute
from research_forge.domain.errors import CancellationRequested
from research_forge.domain.mission import MissionId, Task, TaskId, TaskType


class _Redis:
    def xgroup_create(self, name: str, groupname: str, id: str, mkstream: bool) -> int:
        del name, groupname, id, mkstream
        return 1

    def xadd(self, name: str, fields: object) -> str:
        del name, fields
        return "1-0"

    def xreadgroup(self, groupname: str, consumername: str, streams: object, count: int, block: int) -> list[object]:
        del groupname, consumername, streams, count, block
        return []

    def xautoclaim(
        self,
        name: str,
        groupname: str,
        consumername: str,
        min_idle_time: int,
        start_id: str,
        count: int,
    ) -> tuple[str, list[object], list[object]]:
        del name, groupname, consumername, min_idle_time, start_id, count
        return "0-0", [], []

    def xpending_range(self, name: str, groupname: str, min: str, max: str, count: int) -> list[object]:
        del name, groupname, min, max, count
        return []

    def xack(self, name: str, groupname: str, *ids: str) -> int:
        del name, groupname, ids
        return 1

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        del script, numkeys, keys_and_args
        return 1

    @staticmethod
    def ping() -> bool:
        return True


class _CancellationQueue:
    def __init__(self) -> None:
        self.acknowledged: list[str] = []

    def receive(self, *, route: AttemptRoute, consumer_name: str) -> AttemptDelivery:
        assert route is AttemptRoute.BASELINE and consumer_name == "worker-cancel"
        return AttemptDelivery("attempt-cancelled", AttemptRoute.BASELINE, "1-0")

    def acknowledge(self, delivery: AttemptDelivery) -> None:
        self.acknowledged.append(delivery.attempt_id)


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
    schema_path = Path(__file__).resolve().parents[3] / "docs" / "contracts" / "reproduction-spec-v1.schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


def test_production_composition_exposes_health_and_checks_dependencies(tmp_path: Path) -> None:
    settings = ProductionVs001Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'forge.db'}",
        redis_url="redis://unused-for-test",
        redis_stream_prefix="research-forge:attempts",
        redis_consumer_group="research-forge-workers",
        redis_visibility_timeout_seconds=60,
        redis_max_delivery_attempts=3,
        api_token="test-token",
        schema=_schema(),
        workspace_root=tmp_path / "workspaces",
        artifact_root=tmp_path / "cas",
        broker_socket_path=tmp_path / "broker" / "sandbox.sock",
        broker_state_root=tmp_path / "broker" / "completed-results",
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
        "RF_BROKER_STATE_ROOT": str(tmp_path / "broker" / "completed-results"),
        "RF_CORS_ORIGINS": "https://forge.example.test, https://review.example.test",
    }
    settings = ProductionVs001Settings.from_environment(environment)

    assert settings.paper_artifacts == {"paper-toy-001": paper_sha256}
    assert settings.redis_stream_prefix == "research-forge:attempts"
    assert settings.redis_consumer_group == "research-forge-workers"
    assert settings.redis_visibility_timeout_seconds == 60
    assert settings.redis_max_delivery_attempts == 3
    assert settings.paper_artifact_paths == {"paper-toy-001": paper_root / "toy-paper.pdf"}
    assert settings.broker_socket_path == (tmp_path / "broker" / "sandbox.sock").resolve()
    assert settings.broker_state_root == (tmp_path / "broker" / "completed-results").resolve()
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
        reconcile_stale_operations=object(),
        queue=queue,
        unit_of_work=_CancellationUnitOfWork(),
        database_engine=create_engine(f"sqlite+pysqlite:///{tmp_path / 'forge.db'}"),
        redis_client=object(),
        sandbox_client=UnixSandboxBrokerClient(socket_path=tmp_path / "broker.sock"),
    )

    assert runtime.process_one(owner="worker-cancel") is True
    assert queue.acknowledged == ["attempt-cancelled"]
