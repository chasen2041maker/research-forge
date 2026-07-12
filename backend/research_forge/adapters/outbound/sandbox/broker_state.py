"""Crash-safe local execution-result store owned by the sandbox broker only."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Mapping

from research_forge.application.dto.sandbox import SandboxResult, SandboxRunRequest
from research_forge.domain.errors import PathSafetyViolation


STATE_SCHEMA_VERSION = 1


class BrokerStateConflict(RuntimeError):
    """Raised when one operation ID is reused with different immutable input."""


class BrokerStateStore:
    """Stores only broker recovery bytes; PostgreSQL and CAS remain business facts."""

    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self._root = root.resolve()
        if self._root.is_symlink() or not self._root.is_dir():
            raise PathSafetyViolation("Broker state root must be a real directory.")

    def request_hash(self, request: SandboxRunRequest) -> str:
        payload = {
            "schema_version": STATE_SCHEMA_VERSION,
            "operation_id": request.operation_id,
            "request": _request_payload(request),
        }
        return _sha256(_canonical(payload))

    def remember_request(self, request: SandboxRunRequest) -> str:
        digest = self.request_hash(request)
        directory = self._directory(request.operation_id)
        directory.mkdir(mode=0o750, parents=True, exist_ok=True)
        self._assert_safe_directory(directory)
        payload = {
            "schema_version": STATE_SCHEMA_VERSION,
            "operation_id": request.operation_id,
            "request_hash": digest,
            "request": _request_payload(request),
        }
        request_file = directory / "request.json"
        if request_file.exists():
            existing = _read_json(request_file)
            if existing != payload:
                raise BrokerStateConflict("Operation ID is already bound to a different sandbox request.")
        else:
            _atomic_write(request_file, _canonical(payload))
        return digest

    def load_completed(self, operation_id: str, request: SandboxRunRequest | None = None) -> SandboxResult | None:
        directory = self._directory(operation_id)
        result_file = directory / "result.json"
        if not result_file.exists():
            return None
        self._assert_safe_directory(directory)
        request_metadata = _read_json(directory / "request.json")
        result = _read_json(result_file)
        if result.get("schema_version") != STATE_SCHEMA_VERSION or result.get("operation_id") != operation_id:
            raise BrokerStateConflict("Broker result metadata is invalid.")
        request_hash = result.get("request_hash")
        if not isinstance(request_hash, str):
            raise BrokerStateConflict("Broker result has no request hash.")
        if request_metadata.get("schema_version") != STATE_SCHEMA_VERSION or request_metadata.get("operation_id") != operation_id:
            raise BrokerStateConflict("Broker request metadata is invalid.")
        if request_metadata.get("request_hash") != request_hash:
            raise BrokerStateConflict("Broker request and result hashes disagree.")
        if request is not None and request_hash != self.request_hash(request):
            raise BrokerStateConflict("Operation ID is already bound to a different sandbox request.")
        if request is not None and request_metadata.get("request") != _request_payload(request):
            raise BrokerStateConflict("Broker request payload does not match the caller request.")
        return self._read_result(directory, result)

    def persist_completed(self, request: SandboxRunRequest, result: SandboxResult) -> None:
        if result.operation_id != request.operation_id:
            raise BrokerStateConflict("Sandbox result operation ID does not match the persisted request.")
        request_hash = self.remember_request(request)
        directory = self._directory(request.operation_id)
        existing = self.load_completed(request.operation_id, request)
        if existing is not None:
            if existing != result:
                raise BrokerStateConflict("Completed sandbox result conflicts with durable broker state.")
            return
        outputs_directory = directory / "outputs"
        outputs_directory.mkdir(mode=0o750, exist_ok=True)
        files: dict[str, dict[str, object]] = {}
        for name, payload in result.output_files.items():
            target = self._safe_child(outputs_directory, name)
            target.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
            _atomic_write(target, payload)
            files[name] = {"sha256": _sha256(payload), "size_bytes": len(payload)}
        _atomic_write(directory / "stdout.bin", result.stdout)
        _atomic_write(directory / "stderr.bin", result.stderr)
        metadata = {
            "schema_version": STATE_SCHEMA_VERSION,
            "operation_id": result.operation_id,
            "request_hash": request_hash,
            "execution_id": result.execution_id,
            "exit_code": result.exit_code,
            "environment_digest": result.environment_digest,
            "dataset_sha256": result.dataset_sha256,
            "logs_truncated": result.logs_truncated,
            "stdout": {"sha256": _sha256(result.stdout), "size_bytes": len(result.stdout)},
            "stderr": {"sha256": _sha256(result.stderr), "size_bytes": len(result.stderr)},
            "outputs": files,
        }
        _atomic_write(directory / "result.json", _canonical(metadata))

    def _read_result(self, directory: Path, metadata: Mapping[str, object]) -> SandboxResult:
        stdout = _verified_bytes(directory / "stdout.bin", _mapping(metadata.get("stdout"), "stdout"))
        stderr = _verified_bytes(directory / "stderr.bin", _mapping(metadata.get("stderr"), "stderr"))
        outputs = {
            name: _verified_bytes(self._safe_child(directory / "outputs", name), _mapping(item, "output"))
            for name, item in _mapping(metadata.get("outputs"), "outputs").items()
            if isinstance(name, str)
        }
        return SandboxResult(
            operation_id=_string(metadata, "operation_id"),
            execution_id=_string(metadata, "execution_id"),
            exit_code=_integer(metadata, "exit_code"),
            stdout=stdout,
            stderr=stderr,
            output_files=outputs,
            environment_digest=_string(metadata, "environment_digest"),
            dataset_sha256=_string(metadata, "dataset_sha256"),
            logs_truncated=_boolean(metadata, "logs_truncated"),
        )

    def _directory(self, operation_id: str) -> Path:
        if not operation_id or "\x00" in operation_id:
            raise PathSafetyViolation("Broker operation ID is invalid.")
        return self._root / hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:20]

    def _safe_child(self, root: Path, relative: str) -> Path:
        target = (root / relative).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError as exc:
            raise PathSafetyViolation("Broker state path escapes its operation directory.") from exc
        if target.is_symlink():
            raise PathSafetyViolation("Broker state may not traverse symbolic links.")
        return target

    @staticmethod
    def _assert_safe_directory(directory: Path) -> None:
        if directory.is_symlink() or not directory.is_dir():
            raise PathSafetyViolation("Broker operation state directory is unsafe.")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _canonical(payload: Mapping[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _read_json(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BrokerStateConflict("Broker state JSON is unreadable.") from exc
    return _mapping(value, "state")


def _verified_bytes(path: Path, metadata: Mapping[str, object]) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise BrokerStateConflict("Broker result payload is missing or unsafe.")
    payload = path.read_bytes()
    if len(payload) != _integer(metadata, "size_bytes") or _sha256(payload) != _string(metadata, "sha256"):
        raise BrokerStateConflict("Broker result payload hash does not match metadata.")
    return payload


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise BrokerStateConflict(f"Broker state {name} must be an object.")
    return value


def _string(payload: Mapping[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise BrokerStateConflict(f"Broker state {name} must be a string.")
    return value


def _integer(payload: Mapping[str, object], name: str) -> int:
    value = payload.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise BrokerStateConflict(f"Broker state {name} must be an integer.")
    return value


def _boolean(payload: Mapping[str, object], name: str) -> bool:
    value = payload.get(name)
    if not isinstance(value, bool):
        raise BrokerStateConflict(f"Broker state {name} must be a boolean.")
    return value


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _request_payload(request: SandboxRunRequest) -> dict[str, object]:
    return {
        "image_digest": request.image_digest,
        "argv": list(request.argv),
        "worktree_path": request.worktree_path,
        "working_directory": request.working_directory,
        "timeout_seconds": request.timeout_seconds,
        "max_log_bytes": request.max_log_bytes,
        "network_policy": str(request.network_policy),
        "expected_output_paths": list(request.expected_output_paths),
    }
