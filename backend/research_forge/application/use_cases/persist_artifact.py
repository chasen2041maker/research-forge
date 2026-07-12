"""Recoverable CAS write and artifact-registration use case."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from research_forge.application.ports.artifacts import ArtifactStore
from research_forge.application.ports.system import Clock, IdGenerator
from research_forge.application.ports.unit_of_work import UnitOfWork
from research_forge.application.use_cases.claim_baseline_attempt import AttemptNotFound
from research_forge.domain.artifact import ArtifactKind, ArtifactRef, ArtifactRegistration
from research_forge.domain.errors import OperationConflict
from research_forge.domain.execution import Operation, OperationStatus, OperationType
from research_forge.domain.mission import AttemptId


@dataclass(frozen=True, slots=True)
class ArtifactView:
    sha256: str
    size_bytes: int
    uri: str


class PersistArtifact:
    """Put immutable bytes before atomically registering their business ownership."""

    def __init__(
        self,
        *,
        unit_of_work: UnitOfWork,
        artifact_store: ArtifactStore,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._artifact_store = artifact_store
        self._clock = clock
        self._id_generator = id_generator

    def execute(
        self,
        *,
        attempt_id: str,
        owner: str,
        epoch: int,
        expected_version: int,
        idempotency_key: str,
        kind: ArtifactKind,
        payload: bytes,
        media_type: str,
        target_path: str,
        after_blob_written: Callable[[], None] | None = None,
    ) -> ArtifactView:
        input_hash = hashlib.sha256(payload).hexdigest()
        existing = self._prepare_or_get_existing(
            attempt_id=attempt_id,
            owner=owner,
            epoch=epoch,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            input_hash=input_hash,
            target_path=target_path,
        )
        if existing is not None:
            return self._view(existing)

        reference = self._artifact_store.put(payload, media_type=media_type)
        if after_blob_written is not None:
            after_blob_written()
        return self._finalize(
            attempt_id=attempt_id,
            owner=owner,
            epoch=epoch,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            kind=kind,
            reference=reference,
        )

    def _prepare_or_get_existing(
        self,
        *,
        attempt_id: str,
        owner: str,
        epoch: int,
        expected_version: int,
        idempotency_key: str,
        input_hash: str,
        target_path: str,
    ) -> ArtifactRegistration | None:
        now = self._clock.now()
        with self._unit_of_work:
            attempt = self._unit_of_work.get_attempt(attempt_id)
            if attempt is None:
                raise AttemptNotFound(attempt_id)
            attempt.assert_active_lease(owner=owner, epoch=epoch, expected_version=expected_version, now=now)
            operation = self._unit_of_work.get_operation_by_idempotency_key(idempotency_key)
            if operation is None:
                operation = Operation(
                    operation_id=self._id_generator.new("operation"),
                    idempotency_key=idempotency_key,
                    attempt_id=AttemptId(attempt_id),
                    operation_type=OperationType.CAS_PUT,
                    input_hash=input_hash,
                    lease_epoch=epoch,
                    target_ref_or_path=target_path,
                    created_at=now,
                    updated_at=now,
                )
                self._unit_of_work.add_operation(operation)
                self._unit_of_work.commit()
                return None
            if (
                operation.operation_type is not OperationType.CAS_PUT
                or operation.input_hash != input_hash
                or str(operation.attempt_id) != attempt_id
                or operation.lease_epoch != epoch
            ):
                raise OperationConflict("Idempotency key conflicts with a different CAS operation.")
            if operation.status is OperationStatus.SUCCEEDED:
                registration = self._unit_of_work.get_artifact_by_operation_id(operation.operation_id)
                if registration is None:
                    raise OperationConflict("Completed CAS operation has no artifact registration.")
                self._unit_of_work.commit()
                return registration
            self._unit_of_work.commit()
        return None

    def _finalize(
        self,
        *,
        attempt_id: str,
        owner: str,
        epoch: int,
        expected_version: int,
        idempotency_key: str,
        kind: ArtifactKind,
        reference: ArtifactRef,
    ) -> ArtifactView:
        now = self._clock.now()
        with self._unit_of_work:
            attempt = self._unit_of_work.get_attempt(attempt_id)
            operation = self._unit_of_work.get_operation_by_idempotency_key(idempotency_key)
            if attempt is None or operation is None:
                raise AttemptNotFound("Attempt or prepared CAS operation was not found.")
            attempt.assert_active_lease(owner=owner, epoch=epoch, expected_version=expected_version, now=now)
            operation.succeed(external_result_ref=reference.uri, now=now)
            registration = ArtifactRegistration(
                artifact=reference,
                kind=kind,
                attempt_id=AttemptId(attempt_id),
                operation_id=operation.operation_id,
                created_at=now,
            )
            self._unit_of_work.add_artifact(registration)
            self._unit_of_work.commit()
        return self._view(registration)

    @staticmethod
    def _view(registration: ArtifactRegistration) -> ArtifactView:
        return ArtifactView(
            sha256=registration.artifact.sha256,
            size_bytes=registration.artifact.size_bytes,
            uri=registration.artifact.uri,
        )
