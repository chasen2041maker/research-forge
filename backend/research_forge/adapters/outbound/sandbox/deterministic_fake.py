"""Deterministic sandbox fake for integration tests without a container runtime."""

from __future__ import annotations

from collections.abc import Callable

from research_forge.application.dto.sandbox import NetworkPolicy, SandboxResult, SandboxRunRequest


class DeterministicFakeSandbox:
    """Caches one result per operation, emulating broker-side idempotency and recovery."""

    def __init__(self, result_factory: Callable[[SandboxRunRequest], SandboxResult]) -> None:
        self._result_factory = result_factory
        self._completed: dict[str, SandboxResult] = {}
        self.cancelled_operations: set[str] = set()

    def execute(self, request: SandboxRunRequest) -> SandboxResult:
        if request.network_policy is not NetworkPolicy.OFFLINE:
            raise ValueError("VS-001 fake sandbox accepts only offline requests.")
        if request.operation_id in self.cancelled_operations:
            raise RuntimeError("Cancelled sandbox operation cannot be restarted.")
        existing = self._completed.get(request.operation_id)
        if existing is not None:
            return existing
        result = self._result_factory(request)
        if result.operation_id != request.operation_id:
            raise ValueError("Sandbox result operation ID does not match the request.")
        self._completed[request.operation_id] = result
        return result

    def get_completed(self, operation_id: str) -> SandboxResult | None:
        return self._completed.get(operation_id)

    def cancel(self, operation_id: str) -> None:
        self.cancelled_operations.add(operation_id)
