"""Explicitly non-production local runner for exercising VS-001 on Windows development hosts."""

from __future__ import annotations

import os
from pathlib import Path
from subprocess import TimeoutExpired, run

from research_forge.application.dto.sandbox import NetworkPolicy, SandboxResult, SandboxRunRequest
from research_forge.domain.errors import PathSafetyViolation


class LocalDevelopmentSandbox:
    """Run argv directly for development-only E2E tests; it does not provide network isolation."""

    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = workspace_root.resolve()
        self._completed: dict[str, SandboxResult] = {}

    def execute(self, request: SandboxRunRequest) -> SandboxResult:
        if request.network_policy is not NetworkPolicy.OFFLINE:
            raise ValueError("VS-001 development runner accepts only offline-declared requests.")
        existing = self._completed.get(request.operation_id)
        if existing is not None:
            return existing
        worktree = self._safe_worktree(request.worktree_path)
        working_directory = self._safe_relative_path(worktree, request.working_directory)
        try:
            completed = run(
                list(request.argv),
                cwd=working_directory,
                check=False,
                capture_output=True,
                timeout=request.timeout_seconds,
                shell=False,
                env=self._minimal_environment(),
            )
        except TimeoutExpired as exc:
            raise RuntimeError("Development baseline execution exceeded its timeout.") from exc
        stdout, stderr, truncated = self._truncate_logs(
            completed.stdout, completed.stderr, request.max_log_bytes
        )
        result = SandboxResult(
            operation_id=request.operation_id,
            execution_id=request.operation_id,
            exit_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            output_files={
                path: self._safe_relative_path(worktree, path).read_bytes()
                for path in request.expected_output_paths
                if self._safe_relative_path(worktree, path).is_file()
            },
            environment_digest=request.image_digest,
            dataset_sha256="0" * 64,
            logs_truncated=truncated,
        )
        self._completed[request.operation_id] = result
        return result

    def get_completed(self, operation_id: str) -> SandboxResult | None:
        return self._completed.get(operation_id)

    def cancel(self, operation_id: str) -> None:
        del operation_id
        raise RuntimeError("The synchronous development runner cannot cancel an already-running process.")

    def _safe_worktree(self, raw_path: str) -> Path:
        worktree = Path(raw_path).resolve()
        try:
            worktree.relative_to(self._workspace_root)
        except ValueError as exc:
            raise PathSafetyViolation("Development worktree escapes its configured root.") from exc
        if worktree.is_symlink() or not worktree.is_dir():
            raise PathSafetyViolation("Development worktree is missing or unsafe.")
        return worktree

    @staticmethod
    def _safe_relative_path(worktree: Path, raw_path: str) -> Path:
        path = (worktree / raw_path).resolve()
        try:
            path.relative_to(worktree)
        except ValueError as exc:
            raise PathSafetyViolation("Development sandbox path escapes its worktree.") from exc
        return path

    @staticmethod
    def _minimal_environment() -> dict[str, str]:
        keep = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "HOME", "USERPROFILE", "TMP", "TEMP")
        return {name: value for name in keep if (value := os.environ.get(name)) is not None}

    @staticmethod
    def _truncate_logs(stdout: bytes, stderr: bytes, maximum: int) -> tuple[bytes, bytes, bool]:
        if len(stdout) + len(stderr) <= maximum:
            return stdout, stderr, False
        return stdout[:maximum], b"", True
