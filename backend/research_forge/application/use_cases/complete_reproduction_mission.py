"""Build the deterministic Research Bundle and complete an evidence-closed mission."""

from __future__ import annotations

import hashlib
import json
import shlex
from dataclasses import dataclass

from research_forge.application.dto.bundle import BundleBuildInput
from research_forge.application.ports.artifacts import ArtifactStore
from research_forge.application.ports.bundle import BundleBuilder
from research_forge.application.ports.system import Clock, IdGenerator
from research_forge.application.ports.unit_of_work import UnitOfWork
from research_forge.application.ports.workspace import WorkspaceManager
from research_forge.application.use_cases.claim_baseline_attempt import AttemptNotFound
from research_forge.application.use_cases.persist_artifact import PersistArtifact
from research_forge.domain.artifact import ArtifactKind, ArtifactRef, ArtifactRegistration
from research_forge.domain.evidence import ClaimStatus, EvidenceType, VerifiedClaimView
from research_forge.domain.mission import AuditEvent, MissionStatus


@dataclass(frozen=True, slots=True)
class BundleView:
    sha256: str
    size_bytes: int
    uri: str


@dataclass(frozen=True, slots=True)
class _BundleFacts:
    mission_id: str
    normalized_spec_json: str
    spec_sha256: str
    metric_artifact: ArtifactRef
    log_artifact: ArtifactRef
    metric_value: float
    metric_unit: str
    environment_digest: str
    dataset_sha256: str
    command: tuple[str, ...]
    claims_jsonl: str
    evidence_jsonl: str


class CompleteReproductionMission:
    """Make the success transition only after a deterministic bundle is registered."""

    def __init__(
        self,
        *,
        unit_of_work: UnitOfWork,
        artifact_store: ArtifactStore,
        artifact_persister: PersistArtifact,
        workspace_manager: WorkspaceManager,
        bundle_builder: BundleBuilder,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._artifact_store = artifact_store
        self._artifact_persister = artifact_persister
        self._workspace_manager = workspace_manager
        self._bundle_builder = bundle_builder
        self._clock = clock
        self._id_generator = id_generator

    def execute(
        self,
        *,
        attempt_id: str,
        owner: str,
        epoch: int,
        expected_version: int,
        worktree_path: str,
    ) -> BundleView:
        completed = self._already_completed(attempt_id)
        if completed is not None:
            return completed
        facts = self._collect_facts(attempt_id, owner, epoch, expected_version)
        source_archive = self._workspace_manager.archive_baseline(worktree_path)
        bundle_payload = self._bundle_builder.build(
            self._material(facts, source_archive)
        )
        artifact_view = self._artifact_persister.execute(
            attempt_id=attempt_id,
            owner=owner,
            epoch=epoch,
            expected_version=expected_version,
            idempotency_key=f"{attempt_id}:bundle",
            kind=ArtifactKind.BUNDLE,
            payload=bundle_payload,
            media_type="application/zip",
            target_path="bundle/research-bundle.zip",
        )
        return self._complete(
            attempt_id=attempt_id,
            owner=owner,
            epoch=epoch,
            expected_version=expected_version,
            artifact=ArtifactRef(
                sha256=artifact_view.sha256,
                size_bytes=artifact_view.size_bytes,
                media_type="application/zip",
            ),
        )

    def _already_completed(self, attempt_id: str) -> BundleView | None:
        with self._unit_of_work:
            attempt = self._unit_of_work.get_attempt(attempt_id)
            if attempt is None:
                raise AttemptNotFound(attempt_id)
            task = self._unit_of_work.get_task(str(attempt.task_id))
            if task is None:
                raise AttemptNotFound(f"task for attempt {attempt_id}")
            bundle = self._unit_of_work.get_bundle(str(task.mission_id))
            self._unit_of_work.commit()
        if bundle is None:
            return None
        return BundleView(
            sha256=bundle.artifact.sha256,
            size_bytes=bundle.artifact.size_bytes,
            uri=bundle.artifact.uri,
        )

    def _collect_facts(
        self, attempt_id: str, owner: str, epoch: int, expected_version: int
    ) -> _BundleFacts:
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
            attempt.assert_active_lease(owner=owner, epoch=epoch, expected_version=expected_version, now=now)
            if mission.status is not MissionStatus.VERIFYING:
                raise ValueError("Mission can complete only from evidence verification.")
            metric = self._unit_of_work.get_metric_by_attempt_id(attempt_id)
            if metric is None:
                raise ValueError("Mission completion requires a registered metric.")
            claims = self._unit_of_work.get_claims_for_mission(str(mission.mission_id))
            if not claims or any(claim.status is not ClaimStatus.VERIFIED for claim in claims):
                raise ValueError("Mission completion requires only VERIFIED claims.")
            evidence = tuple(
                link for claim in claims for link in self._unit_of_work.get_evidence_for_claim(claim.claim_id)
            )
            for claim in claims:
                VerifiedClaimView.from_claim(
                    claim,
                    metric,
                    self._unit_of_work.get_evidence_for_claim(claim.claim_id),
                )
            log_artifact = next(
                link.artifact for link in evidence if link.evidence_type is EvidenceType.EXECUTION_LOG
            )
            claims_jsonl = "".join(
                json.dumps(
                    {
                        "claim_id": claim.claim_id,
                        "status": claim.status,
                        "statement": claim.statement,
                        "type": claim.claim_type,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
                for claim in sorted(claims, key=lambda item: item.claim_id)
            )
            evidence_jsonl = "".join(
                json.dumps(
                    {
                        "artifact_sha256": link.artifact.sha256,
                        "claim_id": link.claim_id,
                        "type": link.evidence_type,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
                for link in sorted(evidence, key=lambda item: (item.claim_id, item.evidence_type, item.artifact.sha256))
            )
            self._unit_of_work.commit()
        return _BundleFacts(
            mission_id=str(mission.mission_id),
            normalized_spec_json=mission.normalized_spec_json,
            spec_sha256=mission.spec_sha256,
            metric_artifact=metric.artifact,
            log_artifact=log_artifact,
            metric_value=metric.value,
            metric_unit=metric.unit,
            environment_digest=metric.environment_digest,
            dataset_sha256=metric.dataset_sha256,
            command=metric.command,
            claims_jsonl=claims_jsonl,
            evidence_jsonl=evidence_jsonl,
        )

    def _material(self, facts: _BundleFacts, source_archive: bytes) -> BundleBuildInput:
        manifest = {
            "bundle_schema_version": 1,
            "metric": {
                "artifact_sha256": facts.metric_artifact.sha256,
                "unit": facts.metric_unit,
                "value": facts.metric_value,
            },
            "mission_id": facts.mission_id,
            "source_archive_sha256": hashlib.sha256(source_archive).hexdigest(),
            "spec_sha256": facts.spec_sha256,
        }
        environment = {
            "environment_digest": facts.environment_digest,
            "network_policy": "offline",
        }
        dataset = {"dataset_sha256": facts.dataset_sha256}
        report = f"# Verified baseline result\n\nMetric: {facts.metric_value} {facts.metric_unit}\n"
        reproduce_script = self._reproduce_script(facts.command)
        return BundleBuildInput(
            manifest_json=json.dumps(manifest, sort_keys=True, separators=(",", ":")),
            normalized_spec_json=facts.normalized_spec_json,
            environment_lock_json=json.dumps(environment, sort_keys=True, separators=(",", ":")),
            dataset_manifest_json=json.dumps(dataset, sort_keys=True, separators=(",", ":")),
            claims_jsonl=facts.claims_jsonl,
            evidence_jsonl=facts.evidence_jsonl,
            report_markdown=report,
            reproduce_script=reproduce_script,
            metric_payload=self._artifact_store.read_verified(facts.metric_artifact),
            log_payload=self._artifact_store.read_verified(facts.log_artifact),
            source_archive=source_archive,
        )

    @staticmethod
    def _reproduce_script(command: tuple[str, ...]) -> str:
        return (
            "#!/bin/sh\n"
            "set -eu\n"
            "rm -rf repository\n"
            "mkdir repository\n"
            "tar -xf source.tar -C repository\n"
            "cd repository\n"
            f"exec {shlex.join(command)}\n"
        )

    def _complete(
        self,
        *,
        attempt_id: str,
        owner: str,
        epoch: int,
        expected_version: int,
        artifact: ArtifactRef,
    ) -> BundleView:
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
            attempt.assert_active_lease(owner=owner, epoch=epoch, expected_version=expected_version, now=now)
            bundle_operation = self._unit_of_work.get_operation_by_idempotency_key(f"{attempt_id}:bundle")
            if bundle_operation is None:
                raise ValueError("Registered bundle is missing its operation ledger entry.")
            registration = ArtifactRegistration(
                artifact=artifact,
                kind=ArtifactKind.BUNDLE,
                attempt_id=attempt.attempt_id,
                operation_id=bundle_operation.operation_id,
                created_at=now,
            )
            self._unit_of_work.add_bundle(str(mission.mission_id), registration)
            mission.complete()
            task.succeed()
            attempt.succeed(owner=owner, epoch=epoch, expected_version=expected_version, now=now)
            self._unit_of_work.add_audit_event(
                AuditEvent(
                    event_id=self._id_generator.new("audit"),
                    aggregate_type="mission",
                    aggregate_id=str(mission.mission_id),
                    event_type="mission.completed",
                    occurred_at=now,
                    data={"bundle_sha256": artifact.sha256},
                )
            )
            self._unit_of_work.commit()
        return BundleView(sha256=artifact.sha256, size_bytes=artifact.size_bytes, uri=artifact.uri)
