"""Linux Docker broker with durable results and discoverable operation containers."""

from __future__ import annotations

import hashlib
import platform
from collections import OrderedDict
from concurrent.futures import Future, TimeoutError as FutureTimeout
from pathlib import Path
from subprocess import CalledProcessError, PIPE, Popen, TimeoutExpired, run
from threading import Lock, RLock, Thread
from typing import BinaryIO, Mapping

from research_forge.adapters.outbound.sandbox.broker_state import BrokerStateConflict, BrokerStateStore
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
        broker_state_root: Path | None = None,
        docker_binary: str = "docker",
        memory_limit: str = "1g",
        cpu_limit: str = "1.0",
        pid_limit: int = 128,
        max_completed_results: int = 64,
    ) -> None:
        self._workspace_root = workspace_root.resolve()
        self._allowed_images = dict(allowed_images)
        self._docker_binary = docker_binary
        self._memory_limit = memory_limit
        self._cpu_limit = cpu_limit
        self._pid_limit = pid_limit
        if max_completed_results <= 0:
            raise ValueError("Completed sandbox result capacity must be positive.")
        self._state = BrokerStateStore(broker_state_root or (self._workspace_root / ".broker-state"))
        self._max_completed_results = max_completed_results
        self._completed: OrderedDict[str, SandboxResult] = OrderedDict()
        self._inflight: dict[str, Future[SandboxResult]] = {}
        self._process_lock = RLock()

    def execute(self, request: SandboxRunRequest) -> SandboxResult:
        if platform.system() != "Linux":
            raise SandboxUnavailable("Formal sandbox execution requires Linux or WSL2.")
        try:
            if self._state.is_cancelled(request.operation_id):
                raise SandboxUnavailable("Sandbox operation was cancelled.")
            existing = self._load_completed(request)
        except BrokerStateConflict as exc:
            raise SandboxUnavailable(str(exc)) from exc
        if existing is not None:
            self._remove_completed_container(request)
            return existing
        future, is_leader = self._claim_execution(request.operation_id)
        if not is_leader:
            return self._await_execution(future, request.timeout_seconds)
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

    def get_completed(self, operation_id: str) -> SandboxResult | None:
        try:
            if self._state.is_cancelled(operation_id):
                return None
        except BrokerStateConflict as exc:
            raise SandboxUnavailable(str(exc)) from exc
        with self._process_lock:
            cached = self._completed.get(operation_id)
            if cached is not None:
                self._completed.move_to_end(operation_id)
                return cached
        try:
            result = self._state.load_completed(operation_id)
        except BrokerStateConflict as exc:
            raise SandboxUnavailable(str(exc)) from exc
        if result is not None:
            self._remember_completed(result)
        return result

    def cancel(self, operation_id: str) -> None:
        """Stop, kill if needed, and remove the deterministic operation container idempotently."""
        try:
            self._state.mark_cancelled(operation_id)
        except BrokerStateConflict as exc:
            raise SandboxUnavailable(str(exc)) from exc
        name = self.container_name(operation_id)
        if not self._container_exists(name):
            return
        self._docker(("stop", "--time", "2", name), allow_failure=True)
        if self._container_exists(name):
            self._docker(("kill", name), allow_failure=True)
        self._docker(("rm", "-f", name), allow_failure=True)

    def container_name(self, operation_id: str) -> str:
        return "rf-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:20]

    def _run_container(self, request: SandboxRunRequest) -> SandboxResult:
        if self._state.is_cancelled(request.operation_id):
            raise SandboxUnavailable("Sandbox operation was cancelled.")
        request_hash = self._state.remember_request(request)
        existing = self._load_completed(request)
        if existing is not None:
            return existing
        name = self.container_name(request.operation_id)
        state = self._container_state(name)
        if state is None:
            self._prepare_output_paths(request)
            try:
                self._docker(tuple(self.build_command(request)[1:]))
            except CalledProcessError as exc:
                if not self._container_exists(name):
                    raise SandboxUnavailable(
                        "Docker could not launch the sandbox operation container: "
                        f"{_bounded_diagnostic(exc.stderr)}"
                    ) from exc
                self._assert_container_matches(name, request_hash)
        else:
            self._assert_container_matches(name, request_hash)
        try:
            self._wait_for_container(name, request.timeout_seconds)
        except TimeoutExpired as exc:
            self.cancel(request.operation_id)
            raise SandboxUnavailable("Sandbox execution exceeded its fixed timeout and was cleaned up.") from exc
        if self._state.is_cancelled(request.operation_id):
            raise SandboxUnavailable("Sandbox operation was cancelled.")
        exit_code = self._exit_code(name)
        stdout, stderr, truncated = self._capture_logs(name, request.max_log_bytes)
        outputs = self._read_outputs(request)
        result = SandboxResult(
            operation_id=request.operation_id,
            execution_id=name,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            output_files=outputs,
            environment_digest=request.image_digest,
            dataset_sha256="0" * 64,
            logs_truncated=truncated,
        )
        try:
            self._state.persist_completed(request, result)
        except BrokerStateConflict as exc:
            if "cancelled" in str(exc):
                raise SandboxUnavailable("Sandbox operation was cancelled.") from exc
            raise
        self._docker(("rm", "-f", name), allow_failure=True)
        return result

    def build_command(self, request: SandboxRunRequest) -> list[str]:
        if request.network_policy is not NetworkPolicy.OFFLINE:
            raise SandboxUnavailable("RUN containers must use network=none.")
        image_reference = self._allowed_images.get(request.image_digest)
        if image_reference is None:
            raise SandboxUnavailable("Image digest is not in the broker allowlist.")
        worktree = self._safe_worktree_path(request.worktree_path)
        working_directory = self._safe_relative_path(worktree, request.working_directory)
        request_hash = self._state.request_hash(request)
        return [
            self._docker_binary, "run", "-d", "--name", self.container_name(request.operation_id),
            "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", str(self._pid_limit), "--memory", self._memory_limit, "--cpus", self._cpu_limit,
            "--user", "65534:65534", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--mount", f"type=bind,src={worktree},dst=/workspace",
            "--workdir", "/workspace" if working_directory == worktree else f"/workspace/{working_directory.relative_to(worktree)}",
            "--label", f"research-forge.operation={request.operation_id}",
            "--label", f"research-forge.input-hash={request_hash}",
            image_reference, *request.argv,
        ]

    def _load_completed(self, request: SandboxRunRequest) -> SandboxResult | None:
        with self._process_lock:
            result = self._completed.get(request.operation_id)
        if result is not None:
            self._state.load_completed(request.operation_id, request)
            return result
        result = self._state.load_completed(request.operation_id, request)
        if result is not None:
            self._remember_completed(result)
        return result

    def _container_state(self, name: str) -> str | None:
        result = self._docker(("inspect", "--format", "{{.State.Status}}", name), allow_failure=True)
        return result.strip() if result else None

    def _container_exists(self, name: str) -> bool:
        return self._container_state(name) is not None

    def _assert_container_matches(self, name: str, request_hash: str) -> None:
        value = self._docker(("inspect", "--format", "{{index .Config.Labels \"research-forge.input-hash\"}}", name))
        if value.strip() != request_hash:
            raise SandboxUnavailable("Existing operation container has a conflicting immutable request hash.")

    def _remove_completed_container(self, request: SandboxRunRequest) -> None:
        """Remove an orphaned named container only after validating its immutable input label."""
        name = self.container_name(request.operation_id)
        if not self._container_exists(name):
            return
        self._assert_container_matches(name, self._state.request_hash(request))
        self._docker(("rm", "-f", name), allow_failure=True)

    def _wait_for_container(self, name: str, timeout_seconds: int) -> None:
        try:
            run([self._docker_binary, "wait", name], check=True, timeout=timeout_seconds, stdout=PIPE, stderr=PIPE)
        except CalledProcessError as exc:
            raise SandboxUnavailable("Docker container disappeared before a durable result was collected.") from exc

    def _exit_code(self, name: str) -> int:
        value = self._docker(("inspect", "--format", "{{.State.ExitCode}}", name))
        try:
            return int(value.strip())
        except ValueError as exc:
            raise SandboxUnavailable("Docker container exit code is invalid.") from exc

    def _capture_logs(self, name: str, maximum: int) -> tuple[bytes, bytes, bool]:
        process = Popen([self._docker_binary, "logs", name], stdout=PIPE, stderr=PIPE)
        collector = _BoundedLogCollector(maximum)
        readers = (
            Thread(target=collector.drain, args=("stdout", process.stdout)),
            Thread(target=collector.drain, args=("stderr", process.stderr)),
        )
        for reader in readers:
            reader.start()
        for reader in readers:
            reader.join()
        process.wait()
        return collector.result()

    def _read_outputs(self, request: SandboxRunRequest) -> dict[str, bytes]:
        worktree = self._safe_worktree_path(request.worktree_path)
        return {path: self._safe_relative_path(worktree, path).read_bytes() for path in request.expected_output_paths if self._safe_relative_path(worktree, path).is_file()}

    def _prepare_output_paths(self, request: SandboxRunRequest) -> None:
        worktree = self._safe_worktree_path(request.worktree_path)
        for raw_path in request.expected_output_paths:
            path = self._safe_relative_path(worktree, raw_path)
            path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            if path.exists() and not path.is_file():
                raise PathSafetyViolation("Sandbox output path is not a regular file.")
            path.touch(exist_ok=True)
            path.chmod(0o666)

    def _docker(self, arguments: tuple[str, ...], *, allow_failure: bool = False) -> str:
        completed = run([self._docker_binary, *arguments], check=not allow_failure, stdout=PIPE, stderr=PIPE)
        if allow_failure and completed.returncode != 0:
            return ""
        return completed.stdout.decode("utf-8", errors="replace")

    def _remember_completed(self, result: SandboxResult) -> None:
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

    def _safe_worktree_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path)
        if candidate.is_symlink():
            raise PathSafetyViolation("Sandbox worktree may not be a symbolic link.")
        worktree = candidate.resolve()
        try:
            worktree.relative_to(self._workspace_root)
        except ValueError as exc:
            raise PathSafetyViolation("Sandbox worktree escapes the configured workspace root.") from exc
        if worktree.is_symlink() or not worktree.is_dir():
            raise PathSafetyViolation("Sandbox worktree is missing or unsafe.")
        return worktree

    @staticmethod
    def _safe_relative_path(worktree: Path, raw_path: str) -> Path:
        relative = Path(raw_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise PathSafetyViolation("Sandbox path escapes the worktree.")
        path = worktree / relative
        cursor = worktree
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise PathSafetyViolation("Sandbox paths may not traverse symbolic links.")
        try:
            path.resolve().relative_to(worktree)
        except ValueError as exc:
            raise PathSafetyViolation("Sandbox path escapes the worktree.") from exc
        return path


class _BoundedLogCollector:
    """Consume both Docker log pipes without retaining more than one fixed total budget."""

    def __init__(self, maximum: int) -> None:
        self._remaining = maximum
        self._stdout = bytearray()
        self._stderr = bytearray()
        self._truncated = False
        self._lock = Lock()

    def drain(self, channel: str, stream: BinaryIO | None) -> None:
        if stream is None:
            return
        while chunk := stream.read(64 * 1024):
            with self._lock:
                size = min(len(chunk), self._remaining)
                target = self._stdout if channel == "stdout" else self._stderr
                target.extend(chunk[:size])
                self._remaining -= size
                self._truncated = self._truncated or size != len(chunk)

    def result(self) -> tuple[bytes, bytes, bool]:
        return bytes(self._stdout), bytes(self._stderr), self._truncated


def _bounded_diagnostic(payload: bytes | str | None, maximum: int = 512) -> str:
    """Expose one bounded Docker launch diagnostic without retaining an unbounded daemon response."""
    if payload is None:
        return "no diagnostic was returned"
    text = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else payload
    normalized = " ".join(text.split())
    return normalized[:maximum] or "no diagnostic was returned"
