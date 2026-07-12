"""Mission, task, attempt, audit, and outbox domain entities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Mapping

from research_forge.domain.errors import (
    InvalidAttemptTransition,
    InvalidMissionTransition,
    InvalidTaskTransition,
    LeaseLost,
    OptimisticLockConflict,
)


class MissionStatus(StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"


class TaskType(StrEnum):
    BASELINE_REPRODUCTION = "BASELINE_REPRODUCTION"
    REPAIR_CANDIDATE = "REPAIR_CANDIDATE"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    RETRYABLE = "RETRYABLE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AttemptStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    RETRYABLE = "RETRYABLE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class MissionId:
    value: str

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class TaskId:
    value: str

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class AttemptId:
    value: str

    def __str__(self) -> str:
        return self.value


@dataclass(slots=True)
class Mission:
    mission_id: MissionId
    spec_sha256: str
    normalized_spec_json: str
    created_at: datetime
    original_spec_json: str = ""
    status: MissionStatus = MissionStatus.DRAFT
    version: int = 0

    @classmethod
    def create(
        cls,
        *,
        mission_id: MissionId,
        spec_sha256: str,
        normalized_spec_json: str,
        created_at: datetime,
        original_spec_json: str | None = None,
    ) -> Mission:
        return cls(
            mission_id=mission_id,
            spec_sha256=spec_sha256,
            normalized_spec_json=normalized_spec_json,
            created_at=created_at,
            original_spec_json=original_spec_json or normalized_spec_json,
        )

    def mark_ready(self) -> None:
        self._transition(MissionStatus.READY, {MissionStatus.DRAFT})

    def start(self) -> None:
        self._transition(MissionStatus.RUNNING, {MissionStatus.READY, MissionStatus.WAITING_APPROVAL})

    def begin_verification(self) -> None:
        self._transition(MissionStatus.VERIFYING, {MissionStatus.RUNNING})

    def wait_for_approval(self) -> None:
        self._transition(MissionStatus.WAITING_APPROVAL, {MissionStatus.RUNNING})

    def complete(self) -> None:
        self._transition(MissionStatus.COMPLETED, {MissionStatus.VERIFYING})

    def fail(self) -> None:
        self._transition(
            MissionStatus.FAILED,
            {MissionStatus.READY, MissionStatus.RUNNING, MissionStatus.WAITING_APPROVAL, MissionStatus.VERIFYING, MissionStatus.CANCELLING},
        )

    def request_cancel(self) -> None:
        self._transition(
            MissionStatus.CANCELLING,
            {MissionStatus.READY, MissionStatus.RUNNING, MissionStatus.WAITING_APPROVAL, MissionStatus.VERIFYING},
        )

    def confirm_cancel(self) -> None:
        self._transition(MissionStatus.CANCELLED, {MissionStatus.CANCELLING})

    def _transition(self, target: MissionStatus, allowed: set[MissionStatus]) -> None:
        if self.status not in allowed:
            raise InvalidMissionTransition(self.status, target)
        self.status = target
        self.version += 1


@dataclass(slots=True)
class Task:
    task_id: TaskId
    mission_id: MissionId
    task_type: TaskType
    created_at: datetime
    status: TaskStatus = TaskStatus.PENDING

    def start(self) -> None:
        self._transition(TaskStatus.RUNNING, {TaskStatus.PENDING, TaskStatus.RETRYABLE})

    def succeed(self) -> None:
        self._transition(TaskStatus.SUCCEEDED, {TaskStatus.RUNNING})

    def retry(self) -> None:
        self._transition(TaskStatus.RETRYABLE, {TaskStatus.RUNNING})

    def fail(self) -> None:
        self._transition(TaskStatus.FAILED, {TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.RETRYABLE})

    def cancel(self) -> None:
        self._transition(TaskStatus.CANCELLED, {TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.RETRYABLE})

    def _transition(self, target: TaskStatus, allowed: set[TaskStatus]) -> None:
        if self.status not in allowed:
            raise InvalidTaskTransition(f"Task cannot transition from {self.status} to {target}.")
        self.status = target


@dataclass(slots=True)
class Attempt:
    attempt_id: AttemptId
    task_id: TaskId
    attempt_number: int
    lease_epoch: int
    created_at: datetime
    status: AttemptStatus = AttemptStatus.PENDING
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    version: int = 0
    failure_code: str | None = None
    resume_from_attempt_id: AttemptId | None = None

    def claim(self, *, owner: str, now: datetime, lease_expires_at: datetime) -> None:
        is_expired = self.lease_expires_at is not None and self.lease_expires_at <= now
        if self.status not in {AttemptStatus.PENDING, AttemptStatus.RETRYABLE, AttemptStatus.RUNNING}:
            raise InvalidAttemptTransition(f"Attempt cannot be claimed from {self.status}.")
        if self.status is AttemptStatus.RUNNING and not is_expired:
            raise LeaseLost("Attempt is already owned by a live worker lease.")
        self.status = AttemptStatus.RUNNING
        self.lease_owner = owner
        self.lease_epoch += 1
        self.lease_expires_at = lease_expires_at
        self.heartbeat_at = now
        self.failure_code = None
        self.version += 1

    def heartbeat(
        self,
        *,
        owner: str,
        epoch: int,
        expected_version: int,
        now: datetime,
        lease_expires_at: datetime,
    ) -> None:
        self._assert_active_lease(owner=owner, epoch=epoch, expected_version=expected_version, now=now)
        self.heartbeat_at = now
        self.lease_expires_at = lease_expires_at
        self.version += 1

    def succeed(self, *, owner: str, epoch: int, expected_version: int, now: datetime) -> None:
        self._finish(
            AttemptStatus.SUCCEEDED,
            owner=owner,
            epoch=epoch,
            expected_version=expected_version,
            now=now,
        )

    def fail(
        self,
        *,
        owner: str,
        epoch: int,
        expected_version: int,
        now: datetime,
        retryable: bool,
        failure_code: str,
    ) -> None:
        self._finish(
            AttemptStatus.RETRYABLE if retryable else AttemptStatus.FAILED,
            owner=owner,
            epoch=epoch,
            expected_version=expected_version,
            now=now,
        )
        self.failure_code = failure_code

    def cancel(self, *, owner: str, epoch: int, expected_version: int, now: datetime) -> None:
        self._finish(
            AttemptStatus.CANCELLED,
            owner=owner,
            epoch=epoch,
            expected_version=expected_version,
            now=now,
        )

    def _finish(
        self,
        target: AttemptStatus,
        *,
        owner: str,
        epoch: int,
        expected_version: int,
        now: datetime,
    ) -> None:
        self._assert_active_lease(owner=owner, epoch=epoch, expected_version=expected_version, now=now)
        self.status = target
        self.lease_expires_at = now
        self.version += 1

    def _assert_active_lease(self, *, owner: str, epoch: int, expected_version: int, now: datetime) -> None:
        if self.version != expected_version:
            raise OptimisticLockConflict(f"Attempt expected version {expected_version}, found {self.version}.")
        if self.status is not AttemptStatus.RUNNING:
            raise InvalidAttemptTransition(f"Attempt is not running: {self.status}.")
        if self.lease_owner != owner or self.lease_epoch != epoch:
            raise LeaseLost("Worker does not own the attempt lease epoch.")
        if self.lease_expires_at is None or self.lease_expires_at <= now:
            raise LeaseLost("Worker lease has expired.")

    def assert_active_lease(self, *, owner: str, epoch: int, expected_version: int, now: datetime) -> None:
        """Check a lease before or after an external side effect without mutating state."""
        self._assert_active_lease(owner=owner, epoch=epoch, expected_version=expected_version, now=now)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: str
    aggregate_type: str
    aggregate_id: str
    event_type: str
    occurred_at: datetime
    data: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    event_id: str
    topic: str
    aggregate_id: str
    occurred_at: datetime
    payload: Mapping[str, object]
