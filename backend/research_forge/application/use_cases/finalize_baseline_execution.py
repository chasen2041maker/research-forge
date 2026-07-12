"""Register baseline artifacts, validate the metric, and close deterministic evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from research_forge.application.dto.sandbox import SandboxResult
from research_forge.application.ports.system import Clock, IdGenerator
from research_forge.application.ports.unit_of_work import UnitOfWork
from research_forge.application.use_cases.claim_baseline_attempt import AttemptNotFound
from research_forge.application.use_cases.persist_artifact import PersistArtifact
from research_forge.domain.artifact import ArtifactKind, ArtifactRef
from research_forge.domain.evidence import (
    Claim,
    ClaimStatus,
    ClaimType,
    EvidenceLink,
    EvidenceType,
    MetricComparator,
    MetricExpectation,
    MetricRecord,
    MetricValidation,
    extract_and_validate_metric,
)
from research_forge.domain.mission import (
    Attempt,
    AttemptId,
    AuditEvent,
    MissionId,
    MissionStatus,
    OutboxEvent,
    Task,
    TaskId,
    TaskType,
)


class BaselineValidationFailure(ValueError):
    """Raised when the fixed command or deterministic metric does not pass."""


@dataclass(frozen=True, slots=True)
class FinalizedBaselineView:
    metric_value: float
    metric_artifact_sha256: str
    log_artifact_sha256: str
    claim_id: str


class FinalizeBaselineExecution:
    """Turn one completed sandbox result into registered facts, never prose or an LLM decision."""

    def __init__(
        self,
        *,
        unit_of_work: UnitOfWork,
        artifact_persister: PersistArtifact,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._artifact_persister = artifact_persister
        self._clock = clock
        self._id_generator = id_generator

    def execute(
        self,
        *,
        attempt_id: str,
        owner: str,
        epoch: int,
        expected_version: int,
        sandbox_result: SandboxResult,
        commit_sha: str,
    ) -> FinalizedBaselineView:
        spec = self._load_spec(attempt_id)
        log_payload = sandbox_result.stdout + b"\n" + sandbox_result.stderr
        budget = spec["budget"]
        if len(log_payload) > budget["max_log_bytes"]:
            self._fail(
                attempt_id, owner, epoch, expected_version, "LOG_BUDGET_EXCEEDED", spec["mode"] == "repair"
            )
            raise BaselineValidationFailure("Sandbox log exceeded ReproductionSpec budget.")
        log_view = self._artifact_persister.execute(
            attempt_id=attempt_id,
            owner=owner,
            epoch=epoch,
            expected_version=expected_version,
            idempotency_key=f"{attempt_id}:execution-log",
            kind=ArtifactKind.EXECUTION_LOG,
            payload=log_payload,
            media_type="text/plain; charset=utf-8",
            target_path="execution.log",
        )
        if sandbox_result.exit_code != 0:
            self._fail(
                attempt_id, owner, epoch, expected_version, "SANDBOX_NONZERO_EXIT", spec["mode"] == "repair"
            )
            raise BaselineValidationFailure(f"Sandbox command exited with {sandbox_result.exit_code}.")

        metric_spec = spec["metric"]
        metric_path = metric_spec["artifact_path"]
        metric_payload = sandbox_result.output_files.get(metric_path)
        if metric_payload is None:
            self._fail(
                attempt_id, owner, epoch, expected_version, "METRIC_ARTIFACT_MISSING", spec["mode"] == "repair"
            )
            raise BaselineValidationFailure("Sandbox result did not contain the required metric artifact.")
        if len(log_payload) + len(metric_payload) > budget["max_artifact_bytes"]:
            self._fail(
                attempt_id, owner, epoch, expected_version, "ARTIFACT_BUDGET_EXCEEDED", spec["mode"] == "repair"
            )
            raise BaselineValidationFailure("Baseline artifacts exceeded ReproductionSpec budget.")
        metric_view = self._artifact_persister.execute(
            attempt_id=attempt_id,
            owner=owner,
            epoch=epoch,
            expected_version=expected_version,
            idempotency_key=f"{attempt_id}:metric",
            kind=ArtifactKind.METRIC,
            payload=metric_payload,
            media_type="application/json",
            target_path=metric_path,
        )
        validation = extract_and_validate_metric(metric_payload, self._expectation(metric_spec))
        if not validation.passed:
            self._fail(
                attempt_id, owner, epoch, expected_version, "METRIC_EXPECTATION_FAILED", spec["mode"] == "repair"
            )
            raise BaselineValidationFailure("Baseline metric did not satisfy the frozen expectation.")
        return self._register_verified_result(
            attempt_id=attempt_id,
            owner=owner,
            epoch=epoch,
            expected_version=expected_version,
            validation=validation,
            metric_artifact=ArtifactRef(
                sha256=metric_view.sha256,
                size_bytes=metric_view.size_bytes,
                media_type="application/json",
            ),
            log_artifact=ArtifactRef(
                sha256=log_view.sha256,
                size_bytes=log_view.size_bytes,
                media_type="text/plain; charset=utf-8",
            ),
            commit_sha=commit_sha,
            command=tuple(spec["execution"]["run_argv"]),
            environment_digest=sandbox_result.environment_digest,
            dataset_sha256=sandbox_result.dataset_sha256,
        )

    def _load_spec(self, attempt_id: str) -> dict[str, object]:
        with self._unit_of_work:
            attempt = self._unit_of_work.get_attempt(attempt_id)
            if attempt is None:
                raise AttemptNotFound(attempt_id)
            task = self._unit_of_work.get_task(str(attempt.task_id))
            if task is None:
                raise AttemptNotFound(f"task for attempt {attempt_id}")
            mission = self._unit_of_work.get_mission(str(task.mission_id))
            if mission is None:
                raise AttemptNotFound(f"mission for attempt {attempt_id}")
            spec = json.loads(mission.normalized_spec_json)
            self._unit_of_work.commit()
        return spec

    @staticmethod
    def _expectation(metric: dict[str, object]) -> MetricExpectation:
        return MetricExpectation(
            json_pointer=str(metric["json_pointer"]),
            comparator=MetricComparator(str(metric["comparator"])),
            expected_value=float(metric["expected_value"]),
            tolerance=float(metric["tolerance"]),
            unit=str(metric["unit"]),
        )

    def _register_verified_result(
        self,
        *,
        attempt_id: str,
        owner: str,
        epoch: int,
        expected_version: int,
        validation: MetricValidation,
        metric_artifact: ArtifactRef,
        log_artifact: ArtifactRef,
        commit_sha: str,
        command: tuple[str, ...],
        environment_digest: str,
        dataset_sha256: str,
    ) -> FinalizedBaselineView:
        now = self._clock.now()
        with self._unit_of_work:
            attempt = self._unit_of_work.get_attempt(attempt_id)
            if attempt is None:
                raise AttemptNotFound(attempt_id)
            task = self._unit_of_work.get_task(str(attempt.task_id))
            if task is None:
                raise AttemptNotFound(f"task for attempt {attempt_id}")
            mission = self._unit_of_work.get_mission(str(task.mission_id))
            if mission is None:
                raise AttemptNotFound(f"mission for attempt {attempt_id}")
            existing_metric = self._unit_of_work.get_metric_by_attempt_id(attempt_id)
            if existing_metric is not None:
                claims = self._unit_of_work.get_claims_for_mission(str(mission.mission_id))
                verified_claim = next(claim for claim in claims if claim.status is ClaimStatus.VERIFIED)
                self._unit_of_work.commit()
                return FinalizedBaselineView(
                    metric_value=existing_metric.value,
                    metric_artifact_sha256=existing_metric.artifact.sha256,
                    log_artifact_sha256=log_artifact.sha256,
                    claim_id=verified_claim.claim_id,
                )
            attempt.assert_active_lease(owner=owner, epoch=epoch, expected_version=expected_version, now=now)
            metric = MetricRecord(
                metric_id=self._id_generator.new("metric"),
                attempt_id=attempt.attempt_id,
                artifact=metric_artifact,
                json_pointer=validation.expectation.json_pointer,
                value=validation.value,
                comparator=str(validation.expectation.comparator),
                expected_value=validation.expectation.expected_value,
                tolerance=validation.expectation.tolerance,
                unit=validation.expectation.unit,
                commit_sha=commit_sha,
                command=command,
                environment_digest=environment_digest,
                dataset_sha256=dataset_sha256,
            )
            claim = Claim(
                claim_id=self._id_generator.new("claim"),
                mission_id=mission.mission_id,
                attempt_id=attempt.attempt_id,
                claim_type=ClaimType.EXPERIMENT_RESULT,
                status=ClaimStatus.VERIFIED,
                statement=(
                    f"Metric {validation.expectation.json_pointer} was {validation.value} "
                    f"{validation.expectation.unit}."
                ),
                created_at=now,
            )
            if mission.status is MissionStatus.RUNNING:
                mission.begin_verification()
            self._unit_of_work.add_metric(metric)
            self._unit_of_work.add_claim(claim)
            self._unit_of_work.add_evidence_link(
                EvidenceLink(claim.claim_id, EvidenceType.METRIC_ARTIFACT, metric_artifact)
            )
            self._unit_of_work.add_evidence_link(EvidenceLink(claim.claim_id, EvidenceType.EXECUTION_LOG, log_artifact))
            self._unit_of_work.add_audit_event(
                AuditEvent(
                    event_id=self._id_generator.new("audit"),
                    aggregate_type="mission",
                    aggregate_id=str(mission.mission_id),
                    event_type="baseline.metric_verified",
                    occurred_at=now,
                    data={"metric_id": metric.metric_id, "claim_id": claim.claim_id},
                )
            )
            self._unit_of_work.commit()
        return FinalizedBaselineView(
            metric_value=validation.value,
            metric_artifact_sha256=metric_artifact.sha256,
            log_artifact_sha256=log_artifact.sha256,
            claim_id=claim.claim_id,
        )

    def _fail(
        self,
        attempt_id: str,
        owner: str,
        epoch: int,
        expected_version: int,
        failure_code: str,
        repair_mode: bool,
    ) -> None:
        now = self._clock.now()
        with self._unit_of_work:
            attempt = self._unit_of_work.get_attempt(attempt_id)
            if attempt is None:
                raise AttemptNotFound(attempt_id)
            task = self._unit_of_work.get_task(str(attempt.task_id))
            if task is None:
                raise AttemptNotFound(f"task for attempt {attempt_id}")
            mission = self._unit_of_work.get_mission(str(task.mission_id))
            if mission is None:
                raise AttemptNotFound(f"mission for attempt {attempt_id}")
            attempt.fail(
                owner=owner,
                epoch=epoch,
                expected_version=expected_version,
                now=now,
                retryable=False,
                failure_code=failure_code,
            )
            task.fail()
            if repair_mode and task.task_type is TaskType.BASELINE_REPRODUCTION:
                self._schedule_single_repair_attempt(mission_id=str(mission.mission_id), now=now)
            else:
                mission.fail()
            self._unit_of_work.commit()

    def _schedule_single_repair_attempt(self, *, mission_id: str, now: datetime) -> None:
        existing = self._unit_of_work.get_tasks_for_mission(mission_id)
        if any(task.task_type is TaskType.REPAIR_CANDIDATE for task in existing):
            return
        repair_task = Task(
            task_id=TaskId(self._id_generator.new("task")),
            mission_id=MissionId(mission_id),
            task_type=TaskType.REPAIR_CANDIDATE,
            created_at=now,
        )
        repair_attempt = Attempt(
            attempt_id=AttemptId(self._id_generator.new("attempt")),
            task_id=repair_task.task_id,
            attempt_number=1,
            lease_epoch=0,
            created_at=now,
        )
        event_payload = {
            "mission_id": mission_id,
            "task_id": str(repair_task.task_id),
            "attempt_id": str(repair_attempt.attempt_id),
        }
        self._unit_of_work.add_task(repair_task)
        self._unit_of_work.add_attempt(repair_attempt)
        self._unit_of_work.add_audit_event(
            AuditEvent(
                event_id=self._id_generator.new("audit"),
                aggregate_type="mission",
                aggregate_id=mission_id,
                event_type="repair.attempt_scheduled",
                occurred_at=now,
                data=event_payload,
            )
        )
        self._unit_of_work.add_outbox_event(
            OutboxEvent(
                event_id=self._id_generator.new("outbox"),
                topic="repair_attempt.ready",
                aggregate_id=mission_id,
                occurred_at=now,
                payload=event_payload,
            )
        )
