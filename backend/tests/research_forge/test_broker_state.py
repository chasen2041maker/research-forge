"""Broker state survives a process replacement without becoming a business fact source."""

from __future__ import annotations

import pytest
import json

from research_forge.adapters.outbound.sandbox.broker_state import BrokerStateConflict, BrokerStateStore
from research_forge.application.dto import NetworkPolicy, SandboxResult, SandboxRunRequest
from research_forge.domain.errors import PathSafetyViolation


def _request(operation_id: str = "operation-1", argv: tuple[str, ...] = ("python", "evaluate.py")) -> SandboxRunRequest:
    return SandboxRunRequest(operation_id, "sha256:" + "a" * 64, argv, "/workspace/mission-1", ".", 30, 1024, NetworkPolicy.OFFLINE, ("metrics.json",))


def _result(request: SandboxRunRequest) -> SandboxResult:
    return SandboxResult(request.operation_id, "rf-execution", 0, b"stdout", b"stderr", {"metrics.json": b'{"accuracy":0.8}'}, request.image_digest, "0" * 64)


def test_state_store_recovers_result_from_a_fresh_instance(tmp_path) -> None:
    request = _request()
    BrokerStateStore(tmp_path / "broker-state").persist_completed(request, _result(request))
    assert BrokerStateStore(tmp_path / "broker-state").load_completed(request.operation_id, request) == _result(request)


def test_state_store_rejects_operation_id_reuse_with_different_request(tmp_path) -> None:
    state = BrokerStateStore(tmp_path / "broker-state")
    state.remember_request(_request())
    with pytest.raises(BrokerStateConflict, match="different sandbox request"):
        state.remember_request(_request(argv=("python", "other.py")))


def test_state_store_detects_tampered_result_bytes(tmp_path) -> None:
    request = _request()
    state = BrokerStateStore(tmp_path / "broker-state")
    state.persist_completed(request, _result(request))
    directory = next((tmp_path / "broker-state").iterdir())
    (directory / "stdout.bin").write_bytes(b"tampered")
    with pytest.raises(BrokerStateConflict, match="hash"):
        state.load_completed(request.operation_id, request)


def test_state_store_detects_request_metadata_tampering(tmp_path) -> None:
    request = _request()
    state = BrokerStateStore(tmp_path / "broker-state")
    state.persist_completed(request, _result(request))
    directory = next((tmp_path / "broker-state").iterdir())
    metadata = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    metadata["request"]["argv"] = ["python", "tampered.py"]
    (directory / "request.json").write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(BrokerStateConflict, match="payload"):
        state.load_completed(request.operation_id, request)


def test_state_store_rejects_a_symlinked_payload(tmp_path) -> None:
    request = _request()
    state = BrokerStateStore(tmp_path / "broker-state")
    state.persist_completed(request, _result(request))
    directory = next((tmp_path / "broker-state").iterdir())
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    (directory / "stdout.bin").unlink()
    try:
        (directory / "stdout.bin").symlink_to(outside)
    except OSError:
        pytest.skip("This filesystem does not permit symbolic-link creation.")

    with pytest.raises(PathSafetyViolation, match="symbolic links"):
        state.load_completed(request.operation_id, request)
