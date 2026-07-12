"""Sandbox broker boundary used by Application use cases."""

from __future__ import annotations

from typing import Protocol

from research_forge.application.dto.sandbox import SandboxResult, SandboxRunRequest


class SandboxExecutor(Protocol):
    """Execute and recover an idempotent sandbox operation by its operation ID."""

    def execute(self, request: SandboxRunRequest) -> SandboxResult: ...

    def get_completed(self, operation_id: str) -> SandboxResult | None: ...

    def cancel(self, operation_id: str) -> None: ...
