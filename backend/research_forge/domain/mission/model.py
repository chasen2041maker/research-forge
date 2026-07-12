"""Mission, task, attempt, audit, and outbox domain entities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Mapping

from research_forge.domain.errors import InvalidMissionTransition


class MissionStatus(StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"


class TaskType(StrEnum):
    BASELINE_REPRODUCTION = "BASELINE_REPRODUCTION"


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
    ) -> Mission:
        return cls(
            mission_id=mission_id,
            spec_sha256=spec_sha256,
            normalized_spec_json=normalized_spec_json,
            created_at=created_at,
        )

    def mark_ready(self) -> None:
        if self.status is not MissionStatus.DRAFT:
            raise InvalidMissionTransition(self.status, MissionStatus.READY)
        self.status = MissionStatus.READY
        self.version += 1


@dataclass(frozen=True, slots=True)
class Task:
    task_id: TaskId
    mission_id: MissionId
    task_type: TaskType
    created_at: datetime
    status: TaskStatus = TaskStatus.PENDING


@dataclass(frozen=True, slots=True)
class Attempt:
    attempt_id: AttemptId
    task_id: TaskId
    attempt_number: int
    lease_epoch: int
    created_at: datetime
    status: AttemptStatus = AttemptStatus.PENDING


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
