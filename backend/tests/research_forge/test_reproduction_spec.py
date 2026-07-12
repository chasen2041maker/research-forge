"""Contract and creation tests for the VS-001 mission input boundary."""

from __future__ import annotations

import hashlib
import json
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from typing import Self

import pytest

from research_forge.application.dto import (
    JsonSchemaReproductionSpecValidator,
    ReproductionSpecValidationError,
)
from research_forge.application.use_cases import CreateReproductionMission


def _valid_spec() -> dict[str, object]:
    return {
        "schema_version": 1,
        "mode": "reproduce",
        "paper": {
            "artifact_id": "paper-toy-001",
            "sha256": "a" * 64,
            "extraction_profile": "plain-text-v1",
        },
        "repository": {
            "url_or_path": "tests/fixtures/toy_reproduction_repo",
            "commit_sha": "b" * 40,
        },
        "execution": {
            "image_digest": "sha256:" + "c" * 64,
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


@pytest.fixture
def validator() -> JsonSchemaReproductionSpecValidator:
    schema_path = (
        Path(__file__).resolve().parents[3]
        / "docs"
        / "规范"
        / "科研复现任务规范_v1.schema.json"
    )
    return JsonSchemaReproductionSpecValidator(json.loads(schema_path.read_text(encoding="utf-8")))


def test_validator_canonicalizes_and_hashes_the_frozen_contract(
    validator: JsonSchemaReproductionSpecValidator,
) -> None:
    spec = _valid_spec()

    accepted = validator.validate(spec)

    assert accepted.normalized_json == json.dumps(
        spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    assert accepted.sha256 == hashlib.sha256(accepted.normalized_json.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda spec: spec["change_budget"].update({"max_files": 1}), "max_files=0"),  # type: ignore[union-attr]
        (lambda spec: spec["execution"].update({"allowed_domains": ["pypi.org"]}), "allowed_domains"),  # type: ignore[union-attr]
        (lambda spec: spec["budget"].update({"max_wall_time_seconds": 10}), "max_wall_time_seconds"),  # type: ignore[union-attr]
        (lambda spec: spec["execution"].update({"setup_mode": "lockfile"}), "setup_mode"),  # type: ignore[union-attr]
    ],
)
def test_vs001_rejects_semantic_contract_violations(
    validator: JsonSchemaReproductionSpecValidator,
    mutate: object,
    message: str,
) -> None:
    spec = _valid_spec()
    assert callable(mutate)
    mutate(spec)

    with pytest.raises(ReproductionSpecValidationError, match=message):
        validator.validate(spec)


class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 12, tzinfo=timezone.utc)


class _SequenceIds:
    def __init__(self) -> None:
        self._next = 0

    def new(self, kind: str) -> str:
        self._next += 1
        return f"{kind}-{self._next}"


class _InMemoryUnitOfWork(AbstractContextManager["_InMemoryUnitOfWork"]):
    def __init__(self) -> None:
        self.missions: list[object] = []
        self.tasks: list[object] = []
        self.attempts: list[object] = []
        self.audits: list[object] = []
        self.outbox: list[object] = []
        self.committed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> bool | None:
        if exc_type is not None:
            self.rollback()
        return None

    def add_mission(self, mission: object) -> None:
        self.missions.append(mission)

    def add_task(self, task: object) -> None:
        self.tasks.append(task)

    def add_attempt(self, attempt: object) -> None:
        self.attempts.append(attempt)

    def add_audit_event(self, event: object) -> None:
        self.audits.append(event)

    def add_outbox_event(self, event: object) -> None:
        self.outbox.append(event)

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.committed = False


def test_create_mission_writes_state_audit_and_outbox_in_one_unit_of_work(
    validator: JsonSchemaReproductionSpecValidator,
) -> None:
    uow = _InMemoryUnitOfWork()
    use_case = CreateReproductionMission(
        spec_validator=validator,
        unit_of_work=uow,
        clock=_FixedClock(),
        id_generator=_SequenceIds(),
    )

    view = use_case.execute(_valid_spec())

    assert view.mission_id == "mission-1"
    assert view.task_id == "task-2"
    assert view.attempt_id == "attempt-3"
    assert view.status == "READY"
    assert len(uow.missions) == len(uow.tasks) == len(uow.attempts) == 1
    assert len(uow.audits) == len(uow.outbox) == 1
    assert uow.committed is True
