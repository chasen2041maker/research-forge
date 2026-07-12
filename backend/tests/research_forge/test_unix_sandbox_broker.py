"""Unix broker service contracts: workers use an RPC client and never call Docker directly."""

from __future__ import annotations

import socket
from pathlib import Path
from threading import Thread

import pytest

from research_forge.adapters.outbound.sandbox import (
    DeterministicFakeSandbox,
    UnixSandboxBrokerClient,
    UnixSandboxBrokerServer,
)
from research_forge.application.dto.sandbox import NetworkPolicy, SandboxResult, SandboxRunRequest


pytestmark = pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="Unix-domain sockets are unavailable.")


def test_unix_broker_transports_only_typed_sandbox_messages(tmp_path: Path) -> None:
    request = SandboxRunRequest(
        operation_id="operation-1",
        image_digest="sha256:" + "a" * 64,
        argv=("python", "evaluate.py"),
        worktree_path="/workspace/mission-1",
        working_directory=".",
        timeout_seconds=5,
        max_log_bytes=1024,
        network_policy=NetworkPolicy.OFFLINE,
        expected_output_paths=("metrics.json",),
    )
    sandbox = DeterministicFakeSandbox(
        lambda item: SandboxResult(
            operation_id=item.operation_id,
            execution_id="execution-1",
            exit_code=0,
            stdout=b"",
            stderr=b"",
            output_files={"metrics.json": b'{"accuracy":0.8}'},
            environment_digest=item.image_digest,
            dataset_sha256="b" * 64,
        )
    )
    server = UnixSandboxBrokerServer(socket_path=tmp_path / "broker.sock", executor=sandbox)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = UnixSandboxBrokerClient(socket_path=tmp_path / "broker.sock")
        result = client.execute(request)
        client.cancel(request.operation_id)
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert result.output_files == {"metrics.json": b'{"accuracy":0.8}'}
    assert result.stdout == b""
    assert result.stderr == b""
    assert sandbox.cancelled_operations == {"operation-1"}
