"""Operation Ledger entities for cross-store side effects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from research_forge.domain.errors import OperationConflict
from research_forge.domain.mission import AttemptId


class OperationType(StrEnum):
    WORKTREE_CREATE = "WORKTREE_CREATE"
    SANDBOX_RUN = "SANDBOX_RUN"
    CAS_PUT = "CAS_PUT"
    BUNDLE_BUILD = "BUNDLE_BUILD"
    CANDIDATE_COMMIT = "CANDIDATE_COMMIT"


class OperationStatus(StrEnum):
    PREPARED = "PREPARED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    MANUAL_RECOVERY = "MANUAL_RECOVERY"


@dataclass(slots=True)
class Operation:
    """Durable identity and state for one idempotent external effect."""

    operation_id: str
    idempotency_key: str
    attempt_id: AttemptId
    operation_type: OperationType
    input_hash: str
    lease_epoch: int
    target_ref_or_path: str
    created_at: datetime
    updated_at: datetime
    expected_parent_sha: str | None = None
    external_result_ref: str | None = None
    error_code: str | None = None
    status: OperationStatus = OperationStatus.PREPARED

    def begin(self, now: datetime) -> None:
        if self.status is OperationStatus.PREPARED:
            self.status = OperationStatus.EXECUTING
            self.updated_at = now
            return
        if self.status is not OperationStatus.EXECUTING:
            raise OperationConflict(f"Operation {self.operation_id} cannot begin from {self.status}.")

    def succeed(self, *, external_result_ref: str, now: datetime) -> None:
        if self.status not in {OperationStatus.PREPARED, OperationStatus.EXECUTING, OperationStatus.SUCCEEDED}:
            raise OperationConflict(f"Operation {self.operation_id} cannot succeed from {self.status}.")
        if self.external_result_ref is not None and self.external_result_ref != external_result_ref:
            raise OperationConflict("Operation result ref conflicts with completed operation.")
        self.status = OperationStatus.SUCCEEDED
        self.external_result_ref = external_result_ref
        self.updated_at = now

    def fail(self, *, error_code: str, now: datetime, manual_recovery: bool = False) -> None:
        if self.status is OperationStatus.SUCCEEDED:
            raise OperationConflict("A successful operation cannot be marked failed.")
        self.status = OperationStatus.MANUAL_RECOVERY if manual_recovery else OperationStatus.FAILED
        self.error_code = error_code
        self.updated_at = now

    def request_recovery(self, now: datetime) -> None:
        """Record a bounded redelivery request without changing the original effect identity."""
        if self.status not in {OperationStatus.PREPARED, OperationStatus.EXECUTING}:
            raise OperationConflict(f"Operation {self.operation_id} cannot be reconciled from {self.status}.")
        self.updated_at = now
