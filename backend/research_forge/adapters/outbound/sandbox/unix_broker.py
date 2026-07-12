"""A narrow Unix-socket boundary between ordinary workers and the Docker broker process."""

from __future__ import annotations

import base64
import json
import os
import socket
import socketserver
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from research_forge.application.dto.sandbox import NetworkPolicy, SandboxResult, SandboxRunRequest
from research_forge.application.ports.sandbox import SandboxExecutor


MAX_MESSAGE_BYTES = 16 * 1024 * 1024


class SandboxBrokerUnavailable(RuntimeError):
    """Raised when the separately supervised sandbox broker cannot safely answer a request."""


class _ThreadedUnixServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    address_family = getattr(socket, "AF_UNIX", socket.AF_INET)
    allow_reuse_address = False

    def __init__(self, socket_path: str, executor: SandboxExecutor) -> None:
        self.executor = executor
        super().__init__(socket_path, _BrokerRequestHandler)


class UnixSandboxBrokerServer:
    """Serve a constrained SandboxExecutor over one local Unix-domain socket."""

    def __init__(
        self,
        *,
        socket_path: Path,
        executor: SandboxExecutor,
        socket_group: str | None = None,
    ) -> None:
        if not hasattr(socket, "AF_UNIX"):
            raise SandboxBrokerUnavailable("Unix-domain sockets are required for the production sandbox broker.")
        self._socket_path = socket_path.resolve()
        self._prepare_socket_path()
        self._server = _ThreadedUnixServer(str(self._socket_path), executor)
        os.chmod(self._socket_path, 0o660)
        if socket_group is not None:
            self._set_socket_group(socket_group)

    def serve_forever(self) -> None:
        """Block while accepting one bounded request per broker connection."""
        self._server.serve_forever(poll_interval=0.5)

    def shutdown(self) -> None:
        """Stop accepting requests and remove the socket path owned by this process."""
        self._server.shutdown()
        self._server.server_close()
        if self._socket_path.exists() and self._socket_path.is_socket():
            self._socket_path.unlink()

    def _prepare_socket_path(self) -> None:
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._socket_path.exists():
            return
        if not self._socket_path.is_socket():
            raise SandboxBrokerUnavailable("Sandbox broker socket path exists but is not a socket.")
        self._socket_path.unlink()

    def _set_socket_group(self, socket_group: str) -> None:
        try:
            import grp
        except ImportError as exc:  # pragma: no cover - Windows CI does not provide POSIX groups.
            raise SandboxBrokerUnavailable("Configuring a sandbox socket group requires POSIX group support.") from exc
        try:
            group_id = grp.getgrnam(socket_group).gr_gid
        except KeyError as exc:
            raise SandboxBrokerUnavailable("Configured sandbox socket group does not exist.") from exc
        os.chown(self._socket_path, -1, group_id)


class UnixSandboxBrokerClient:
    """SandboxExecutor client that can reach only the local broker protocol, never Docker itself."""

    def __init__(self, *, socket_path: Path, connection_slack_seconds: int = 30) -> None:
        if connection_slack_seconds <= 0:
            raise ValueError("Broker connection slack must be positive.")
        self._socket_path = socket_path.resolve()
        self._connection_slack_seconds = connection_slack_seconds

    def execute(self, request: SandboxRunRequest) -> SandboxResult:
        """Forward an approved sandbox request and wait for its bounded result."""
        response = self._round_trip(
            {"operation": "execute", "request": _request_payload(request)},
            timeout_seconds=request.timeout_seconds + self._connection_slack_seconds,
        )
        return _result_from_payload(_required_mapping(response, "result"))

    def get_completed(self, operation_id: str) -> SandboxResult | None:
        """Ask the broker whether it can recover a previously completed operation."""
        response = self._round_trip({"operation": "get_completed", "operation_id": operation_id}, timeout_seconds=30)
        payload = response.get("result")
        return None if payload is None else _result_from_payload(_mapping(payload, "result"))

    def cancel(self, operation_id: str) -> None:
        """Request cancellation of one known broker operation."""
        self._round_trip({"operation": "cancel", "operation_id": operation_id}, timeout_seconds=30)

    def _round_trip(self, request: Mapping[str, object], *, timeout_seconds: int) -> Mapping[str, object]:
        if timeout_seconds <= 0:
            raise ValueError("Broker request timeout must be positive.")
        if not hasattr(socket, "AF_UNIX"):
            raise SandboxBrokerUnavailable("Unix-domain sockets are required for the production sandbox client.")
        payload = _encode_message(request)
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(timeout_seconds)
                connection.connect(str(self._socket_path))
                connection.sendall(payload)
                response = _read_message(connection)
        except (OSError, TimeoutError) as exc:
            raise SandboxBrokerUnavailable("Sandbox broker is unavailable or did not answer in time.") from exc
        if response.get("ok") is not True:
            raise SandboxBrokerUnavailable("Sandbox broker rejected the bounded request.")
        return response


class _BrokerRequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            response = _dispatch(_read_message(self.request), self._executor())
        except (ValueError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
            response = {"ok": False, "error": "invalid_request"}
        except Exception:
            response = {"ok": False, "error": "broker_execution_rejected"}
        self.wfile.write(_encode_message(response))

    def _executor(self) -> SandboxExecutor:
        return cast(_ThreadedUnixServer, self.server).executor


def _dispatch(request: Mapping[str, object], executor: SandboxExecutor) -> dict[str, object]:
    operation = _required_string(request, "operation")
    if operation == "execute":
        result = executor.execute(_request_from_payload(_required_mapping(request, "request")))
        return {"ok": True, "result": _result_payload(result)}
    operation_id = _required_string(request, "operation_id")
    if operation == "get_completed":
        result = executor.get_completed(operation_id)
        return {"ok": True, "result": None if result is None else _result_payload(result)}
    if operation == "cancel":
        executor.cancel(operation_id)
        return {"ok": True}
    raise ValueError("Unsupported sandbox broker operation.")


def _request_payload(request: SandboxRunRequest) -> dict[str, object]:
    return {
        "operation_id": request.operation_id,
        "image_digest": request.image_digest,
        "argv": list(request.argv),
        "worktree_path": request.worktree_path,
        "working_directory": request.working_directory,
        "timeout_seconds": request.timeout_seconds,
        "max_log_bytes": request.max_log_bytes,
        "network_policy": str(request.network_policy),
        "expected_output_paths": list(request.expected_output_paths),
    }


def _request_from_payload(payload: Mapping[str, object]) -> SandboxRunRequest:
    return SandboxRunRequest(
        operation_id=_required_string(payload, "operation_id"),
        image_digest=_required_string(payload, "image_digest"),
        argv=_string_tuple(payload, "argv"),
        worktree_path=_required_string(payload, "worktree_path"),
        working_directory=_required_string(payload, "working_directory"),
        timeout_seconds=_positive_int(payload, "timeout_seconds"),
        max_log_bytes=_nonnegative_int(payload, "max_log_bytes"),
        network_policy=NetworkPolicy(_required_string(payload, "network_policy")),
        expected_output_paths=_string_tuple(payload, "expected_output_paths"),
    )


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
    files = _required_mapping(payload, "output_files")
    return SandboxResult(
        operation_id=_required_string(payload, "operation_id"),
        execution_id=_required_string(payload, "execution_id"),
        exit_code=_integer(payload, "exit_code"),
        stdout=_decode_bytes(_required_string(payload, "stdout")),
        stderr=_decode_bytes(_required_string(payload, "stderr")),
        output_files={path: _decode_bytes(_string(value, "output_files value")) for path, value in files.items()},
        environment_digest=_required_string(payload, "environment_digest"),
        dataset_sha256=_required_string(payload, "dataset_sha256"),
        logs_truncated=_boolean(payload, "logs_truncated"),
    )


def _encode_message(payload: Mapping[str, object]) -> bytes:
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise SandboxBrokerUnavailable("Sandbox broker message exceeds the fixed protocol limit.")
    return encoded


def _read_message(connection: socket.socket) -> Mapping[str, object]:
    payload = bytearray()
    while len(payload) <= MAX_MESSAGE_BYTES:
        chunk = connection.recv(min(64 * 1024, MAX_MESSAGE_BYTES + 1 - len(payload)))
        if not chunk:
            break
        payload.extend(chunk)
        if payload.endswith(b"\n"):
            break
    if not payload or len(payload) > MAX_MESSAGE_BYTES or not payload.endswith(b"\n"):
        raise SandboxBrokerUnavailable("Sandbox broker sent an invalid framed message.")
    return _mapping(json.loads(payload[:-1].decode("utf-8")), "message")


def _required_mapping(payload: Mapping[str, object], name: str) -> Mapping[str, object]:
    return _mapping(payload.get(name), name)


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object.")
    return cast(Mapping[str, object], value)


def _required_string(payload: Mapping[str, object], name: str) -> str:
    return _string(payload.get(name), name)


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"{name} must be a non-empty string without NUL.")
    return value


def _string_tuple(payload: Mapping[str, object], name: str) -> tuple[str, ...]:
    value = payload.get(name)
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty array of strings.")
    return tuple(_string(item, name) for item in value)


def _positive_int(payload: Mapping[str, object], name: str) -> int:
    value = _integer(payload, name)
    if value <= 0:
        raise ValueError(f"{name} must be positive.")
    return value


def _nonnegative_int(payload: Mapping[str, object], name: str) -> int:
    value = _integer(payload, name)
    if value < 0:
        raise ValueError(f"{name} must be non-negative.")
    return value


def _integer(payload: Mapping[str, object], name: str) -> int:
    value = payload.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer.")
    return value


def _boolean(payload: Mapping[str, object], name: str) -> bool:
    value = payload.get(name)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean.")
    return value


def _encode_bytes(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")


def _decode_bytes(payload: str) -> bytes:
    try:
        return base64.b64decode(payload, validate=True)
    except ValueError as exc:
        raise ValueError("Sandbox broker bytes must use valid Base64.") from exc
