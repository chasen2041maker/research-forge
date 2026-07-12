"""Linux-only Docker broker with a hardened argv-only container invocation."""

from __future__ import annotations

import platform
from collections import OrderedDict
from concurrent.futures import Future, TimeoutError as FutureTimeout
from pathlib import Path
from subprocess import PIPE, Popen, TimeoutExpired
from threading import RLock
from typing import Mapping

from research_forge.adapters.outbound.sandbox.completed_result_store import (
    CompletedResultStoreError,
    DurableCompletedResultStore,
)
from research_forge.application.dto.sandbox import NetworkPolicy, SandboxResult, SandboxRunRequest
from research_forge.domain.errors import PathSafetyViolation


class SandboxUnavailable(RuntimeError):
    """Raised when the formally supported Linux/WSL2 sandbox runtime is unavailable."""


class DockerSandboxBroker:
    """The only adapter allowed to invoke Docker for formal VS-001 execution."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        allowed_images: Mapping[str, str],
        docker_binary: str = "docker",
        memory_limit: str = "1g",
        cpu_limit: str = "1.0",
        pid_limit: int = 128,
        max_completed_results: int = 64,
        completed_result_store: DurableCompletedResultStore | None = None,
    ) -> None:
        self._workspace_root = workspace_root.resolve()
        self._allowed_images = dict(allowed_images)
        self._docker_binary = docker_binary
        self._memory_limit = memory_limit
        self._cpu_limit = cpu_limit
        self._pid_limit = pid_limit
        if max_completed_results <= 0:
            raise ValueError("Completed sandbox result capacity must be positive.")
        self._max_completed_results = max_completed_results
        self._completed_result_store = completed_result_store
        self._processes: dict[str, Popen[bytes]] = {}
        self._completed: OrderedDict[str, SandboxResult] = OrderedDict()
        self._inflight: dict[str, Future[SandboxResult]] = {}
        self._process_lock = RLock()

    def execute(self, request: SandboxRunRequest) -> SandboxResult:
        if platform.system() != "Linux":
            raise SandboxUnavailable("Formal sandbox execution requires Linux or WSL2.")
        existing = self.get_completed(request.operation_id)
        if existing is not None:
            return existing
        future, is_leader = self._claim_execution(request.operation_id)
        if not is_leader:
            return self._await_execution(future, request.timeout_seconds)
        recovered = self.get_completed(request.operation_id)
        if recovered is not None:
            future.set_result(recovered)
            with self._process_lock:
                self._inflight.pop(request.operation_id, None)
            return recovered
        try:
            result = self._run_container(request)
        except Exception as exc:
            future.set_exception(exc)
            raise
        else:
            self._remember_completed(result)
            future.set_result(result)
            return result
        finally:
            with self._process_lock:
                self._inflight.pop(request.operation_id, None)

    def _run_container(self, request: SandboxRunRequest) -> SandboxResult:
        command = self.build_command(request)
        process = Popen(command, stdout=PIPE, stderr=PIPE, shell=False)
        with self._process_lock:
            self._processes[request.operation_id] = process
        try:
            stdout, stderr = process.communicate(timeout=request.timeout_seconds)
        except TimeoutExpired as exc:
            self.cancel(request.operation_id)
            raise SandboxUnavailable("Sandbox execution exceeded its fixed timeout.") from exc
        finally:
            with self._process_lock:
                self._processes.pop(request.operation_id, None)
        stdout, stderr, truncated = self._truncate_logs(stdout, stderr, request.max_log_bytes)
        if process.returncode == 125:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise SandboxUnavailable(f"Docker rejected the hardened sandbox invocation: {detail[:1024]}")
        outputs = self._read_outputs(request)
        return SandboxResult(
            operation_id=request.operation_id,
            execution_id=request.operation_id,
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
            output_files=outputs,
            environment_digest=request.image_digest,
            dataset_sha256="0" * 64,
            logs_truncated=truncated,
        )

    def get_completed(self, operation_id: str) -> SandboxResult | None:
        with self._process_lock:
            result = self._completed.get(operation_id)
            if result is not None:
                self._completed.move_to_end(operation_id)
                return result
        if self._completed_result_store is None:
            return None
        try:
            recovered = self._completed_result_store.get(operation_id)
        except CompletedResultStoreError as exc:
            raise SandboxUnavailable("Broker completion recovery record is unsafe or corrupt.") from exc
        if recovered is not None:
            self._remember_in_memory(recovered)
        return recovered

    def cancel(self, operation_id: str) -> None:
        with self._process_lock:
            process = self._processes.get(operation_id)
        if process is not None and process.poll() is None:
            process.terminate()

    def _remember_completed(self, result: SandboxResult) -> None:
        if self._completed_result_store is not None:
            try:
                self._completed_result_store.put(result)
            except CompletedResultStoreError as exc:
                raise SandboxUnavailable("Broker completion record could not be persisted safely.") from exc
        self._remember_in_memory(result)

    def _remember_in_memory(self, result: SandboxResult) -> None:
        with self._process_lock:
            self._completed[result.operation_id] = result
            self._completed.move_to_end(result.operation_id)
            while len(self._completed) > self._max_completed_results:
                self._completed.popitem(last=False)

    def _claim_execution(self, operation_id: str) -> tuple[Future[SandboxResult], bool]:
        with self._process_lock:
            existing = self._inflight.get(operation_id)
            if existing is not None:
                return existing, False
            future: Future[SandboxResult] = Future()
            self._inflight[operation_id] = future
            return future, True

    @staticmethod
    def _await_execution(future: Future[SandboxResult], timeout_seconds: int) -> SandboxResult:
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeout as exc:
            raise SandboxUnavailable("An existing sandbox operation did not finish before the fixed timeout.") from exc

    def build_command(self, request: SandboxRunRequest) -> list[str]:
        if request.network_policy is not NetworkPolicy.OFFLINE:
            raise SandboxUnavailable("RUN containers must use network=none.")
        image_reference = self._allowed_images.get(request.image_digest)
        if image_reference is None:
            raise SandboxUnavailable("Image digest is not in the broker allowlist.")
        worktree = self._safe_worktree_path(request.worktree_path)
        working_directory = self._safe_relative_path(worktree, request.working_directory)
        return [
            self._docker_binary,
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self._pid_limit),
            "--memory",
            self._memory_limit,
            "--cpus",
            self._cpu_limit,
            "--user",
            "65534:65534",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--mount",
            f"type=bind,src={worktree},dst=/workspace",
            "--workdir",
            "/workspace" if working_directory == worktree else f"/workspace/{working_directory.relative_to(worktree)}",
            "--label",
            f"research-forge.operation={request.operation_id}",
            image_reference,
            *request.argv,
        ]

    def _read_outputs(self, request: SandboxRunRequest) -> dict[str, bytes]:
        worktree = self._safe_worktree_path(request.worktree_path)
        return {
            output_path: self._safe_relative_path(worktree, output_path).read_bytes()
            for output_path in request.expected_output_paths
            if self._safe_relative_path(worktree, output_path).is_file()
        }

    def _safe_worktree_path(self, raw_path: str) -> Path:
        worktree = Path(raw_path).resolve()
        try:
            worktree.relative_to(self._workspace_root)
        except ValueError as exc:
            raise PathSafetyViolation("Sandbox worktree escapes the configured workspace root.") from exc
        if worktree.is_symlink() or not worktree.is_dir():
            raise PathSafetyViolation("Sandbox worktree is missing or unsafe.")
        return worktree

    @staticmethod
    def _safe_relative_path(worktree: Path, raw_path: str) -> Path:
        path = (worktree / raw_path).resolve()
        try:
            path.relative_to(worktree)
        except ValueError as exc:
            raise PathSafetyViolation("Sandbox path escapes the worktree.") from exc
        return path

    @staticmethod
    def _truncate_logs(stdout: bytes, stderr: bytes, maximum: int) -> tuple[bytes, bytes, bool]:
        if len(stdout) + len(stderr) <= maximum:
            return stdout, stderr, False
        stdout_limit = min(len(stdout), maximum)
        return stdout[:stdout_limit], b"", True
