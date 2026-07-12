"""Broker-owned durable storage for completed sandbox operations.

The store deliberately lives behind the Docker broker boundary.  Workers can ask
the broker to recover a result, but cannot write or alter its completion record.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from research_forge.application.dto.sandbox import SandboxResult


class CompletedResultStoreError(RuntimeError):
    """Raised when a broker completion record is missing integrity or safety guarantees."""


class DurableCompletedResultStore:
    """Persist one immutable result per operation with atomic publication and verification."""

    _SCHEMA_VERSION = 1

    def __init__(self, *, root_path: Path) -> None:
        raw_root = root_path.expanduser()
        if raw_root.exists() and (raw_root.is_symlink() or not raw_root.is_dir()):
            raise CompletedResultStoreError("Broker result root must be a non-symlink directory.")
        raw_root.mkdir(parents=True, exist_ok=True)
        if raw_root.is_symlink() or not raw_root.is_dir():
            raise CompletedResultStoreError("Broker result root must be a non-symlink directory.")
        self._root = raw_root.resolve()

    def get(self, operation_id: str) -> SandboxResult | None:
        """Load and verify an immutable completion, returning ``None`` only when it is absent."""
        destination = self._path_for(operation_id)
        if destination.is_symlink():
            raise CompletedResultStoreError("Broker result record is missing or unsafe.")
        if not destination.exists():
            return None
        if not destination.is_file():
            raise CompletedResultStoreError("Broker result record is missing or unsafe.")
        try:
            document = _mapping(json.loads(destination.read_text(encoding="utf-8")), "completion record")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CompletedResultStoreError("Broker result record cannot be read safely.") from exc
        result = self._result_from_document(document)
        if result.operation_id != operation_id:
            raise CompletedResultStoreError("Broker result record does not match its requested operation.")
        return result

    def put(self, result: SandboxResult) -> None:
        """Atomically publish a result once; a second, different result is an integrity failure."""
        destination = self._path_for(result.operation_id)
        document = self._document_for(result)
        encoded = json.dumps(document, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"
        descriptor, staging_name = tempfile.mkstemp(prefix=".completed-", suffix=".tmp", dir=self._root)
        staging_path = Path(staging_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                os.chmod(staging_path, 0o600)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(staging_path, destination)
            except FileExistsError:
                existing = self.get(result.operation_id)
                if existing != result:
                    raise CompletedResultStoreError("Broker operation already has a conflicting completion record.")
            else:
                self._sync_directory()
        except OSError as exc:
            raise CompletedResultStoreError("Broker result record could not be published durably.") from exc
        finally:
            if staging_path.exists():
                staging_path.unlink()

    def _path_for(self, operation_id: str) -> Path:
        if not isinstance(operation_id, str) or not operation_id.strip() or "\x00" in operation_id:
            raise CompletedResultStoreError("Sandbox operation ID must be a non-empty string without NUL.")
        digest = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
        destination = self._root / f"{digest}.json"
        if destination.parent != self._root:
            raise CompletedResultStoreError("Broker result path escapes its configured root.")
        return destination

    def _document_for(self, result: SandboxResult) -> dict[str, object]:
        payload = _result_payload(result)
        # Validate what will be persisted before it can become the recovery source of truth.
        checked = _result_from_payload(payload)
        if checked != result:
            raise CompletedResultStoreError("Sandbox result cannot be serialized without changing evidence.")
        canonical_payload = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return {
            "schema_version": self._SCHEMA_VERSION,
            "payload": payload,
            "payload_sha256": hashlib.sha256(canonical_payload).hexdigest(),
        }

    def _result_from_document(self, document: Mapping[str, object]) -> SandboxResult:
        if document.get("schema_version") != self._SCHEMA_VERSION:
            raise CompletedResultStoreError("Broker result record has an unsupported schema version.")
        payload = _mapping(document.get("payload"), "completion payload")
        recorded_sha256 = _sha256(document.get("payload_sha256"), "completion payload hash")
        canonical_payload = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if hashlib.sha256(canonical_payload).hexdigest() != recorded_sha256:
            raise CompletedResultStoreError("Broker result record failed its integrity check.")
        try:
            return _result_from_payload(payload)
        except (TypeError, ValueError) as exc:
            raise CompletedResultStoreError("Broker result record has an invalid typed payload.") from exc

    def _sync_directory(self) -> None:
        """Durably commit the rename/link metadata where the host supports directory fsync."""
        try:
            descriptor = os.open(self._root, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            # Windows test filesystems do not support fsync on directory handles.  Formal broker
            # deployments are Linux/WSL2, where this is available and exercised.
            return
        finally:
            os.close(descriptor)


def _result_payload(result: SandboxResult) -> dict[str, object]:
    return {
        "operation_id": result.operation_id,
        "execution_id": result.execution_id,
        "exit_code": result.exit_code,
        "stdout": _encode_bytes(result.stdout),
        "stderr": _encode_bytes(result.stderr),
        "output_files": {path: _encode_bytes(payload) for path, payload in result.output_files.items()},
        "environment_digest": result.environment_digest,
        "dataset_sha256": result.dataset_sha256,
        "logs_truncated": result.logs_truncated,
    }


def _result_from_payload(payload: Mapping[str, object]) -> SandboxResult:
    files = _mapping(payload.get("output_files"), "output_files")
    return SandboxResult(
        operation_id=_string(payload.get("operation_id"), "operation_id"),
        execution_id=_string(payload.get("execution_id"), "execution_id"),
        exit_code=_integer(payload.get("exit_code"), "exit_code"),
        stdout=_decode_bytes(_string(payload.get("stdout"), "stdout")),
        stderr=_decode_bytes(_string(payload.get("stderr"), "stderr")),
        output_files={
            _string(path, "output file path"): _decode_bytes(_string(value, "output file payload"))
            for path, value in files.items()
        },
        environment_digest=_string(payload.get("environment_digest"), "environment_digest"),
        dataset_sha256=_sha256(payload.get("dataset_sha256"), "dataset_sha256"),
        logs_truncated=_boolean(payload.get("logs_truncated"), "logs_truncated"),
    )


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object.")
    return cast(Mapping[str, object], value)


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"{name} must be a non-empty string without NUL.")
    return value


def _integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer.")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean.")
    return value


def _sha256(value: object, name: str) -> str:
    candidate = _string(value, name)
    if len(candidate) != 64 or any(character not in "0123456789abcdef" for character in candidate):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest.")
    return candidate


def _encode_bytes(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")


def _decode_bytes(payload: str) -> bytes:
    try:
        return base64.b64decode(payload, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Sandbox result bytes must use valid Base64.") from exc
