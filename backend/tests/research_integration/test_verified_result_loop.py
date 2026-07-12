"""Forge-to-Studio verified-result contract tests without sharing either product's internals."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from co_scientist.public_api import write_verified_result
from research_contracts import VerifiedResultV1
from research_forge.adapters.outbound.persistence import InMemoryUnitOfWork
from research_forge.application.use_cases import GetVerifiedResult, VerifiedResultUnavailable
from research_forge.domain.artifact import ArtifactKind, ArtifactRef, ArtifactRegistration
from research_forge.domain.evidence import Claim, ClaimStatus, ClaimType, MetricRecord
from research_forge.domain.mission import Attempt, AttemptId, Mission, MissionId, Task, TaskId, TaskType


def _completed_source(*, proposal_id: str | None) -> tuple[InMemoryUnitOfWork, str]:
    now = datetime(2026, 7, 12, tzinfo=timezone.utc)
    mission = Mission.create(
        mission_id=MissionId("mission-1"),
        spec_sha256="a" * 64,
        normalized_spec_json="{}",
        created_at=now,
        proposal_id=proposal_id,
    )
    mission.mark_ready()
    mission.start()
    mission.begin_verification()
    mission.complete()
    task = Task(TaskId("task-1"), mission.mission_id, TaskType.BASELINE_REPRODUCTION, now)
    task.start()
    task.succeed()
    attempt = Attempt(AttemptId("attempt-1"), task.task_id, 1, 0, now)
    attempt.claim(owner="worker", now=now, lease_expires_at=now + timedelta(seconds=30))
    attempt.succeed(owner="worker", epoch=1, expected_version=1, now=now)
    metric_artifact = ArtifactRef("b" * 64, 23, "application/json")
    bundle_artifact = ArtifactRef("c" * 64, 42, "application/zip")
    metric = MetricRecord(
        metric_id="metric-1",
        attempt_id=attempt.attempt_id,
        artifact=metric_artifact,
        json_pointer="/accuracy",
        value=0.8,
        comparator="equals",
        expected_value=0.8,
        tolerance=0.001,
        unit="ratio",
        commit_sha="d" * 40,
        command=("python", "evaluate.py"),
        environment_digest="sha256:" + "e" * 64,
        dataset_sha256="f" * 64,
    )
    claim = Claim(
        claim_id="claim-1",
        mission_id=mission.mission_id,
        attempt_id=attempt.attempt_id,
        claim_type=ClaimType.EXPERIMENT_RESULT,
        status=ClaimStatus.VERIFIED,
        statement="The metric evidence passed.",
        created_at=now,
    )
    bundle = ArtifactRegistration(bundle_artifact, ArtifactKind.BUNDLE, attempt.attempt_id, "operation-bundle", now)
    uow = InMemoryUnitOfWork()
    with uow:
        uow.add_mission(mission)
        uow.add_task(task)
        uow.add_attempt(attempt)
        uow.add_metric(metric)
        uow.add_claim(claim)
        uow.add_bundle(str(mission.mission_id), bundle)
        uow.commit()
    return uow, str(mission.mission_id)


def test_forge_emits_verified_result_and_studio_projects_only_its_facts() -> None:
    unit_of_work, mission_id = _completed_source(proposal_id="proposal-studio-1")

    result = GetVerifiedResult(unit_of_work=unit_of_work).execute(mission_id)
    studio_report = write_verified_result(VerifiedResultV1.from_mapping(result.to_mapping()))

    assert result.to_mapping()["status"] == "VERIFIED"
    assert result.to_mapping()["proposal_id"] == "proposal-studio-1"
    assert result.to_mapping()["bundle_sha256"] == "c" * 64
    assert studio_report.to_mapping() == {
        "status": "VERIFIED",
        "proposal_id": "proposal-studio-1",
        "mission_id": "mission-1",
        "spec_sha256": "a" * 64,
        "metric": result.to_mapping()["metric"],
        "bundle_sha256": "c" * 64,
        "completed_at": "2026-07-12T00:00:00+00:00",
    }


def test_forge_refuses_to_label_a_non_handoff_mission_as_a_studio_verified_result() -> None:
    unit_of_work, mission_id = _completed_source(proposal_id=None)

    with pytest.raises(VerifiedResultUnavailable, match="Studio Proposal"):
        GetVerifiedResult(unit_of_work=unit_of_work).execute(mission_id)
