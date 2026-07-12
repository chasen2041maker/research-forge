"""FastAPI adapter tests for token enforcement and Application-only delegation."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi.testclient import TestClient

from research_forge.adapters.inbound.api import create_app
from research_forge.application.use_cases import ApprovalResolutionView, BundleDownload, MissionStatusView


@dataclass
class _Controller:
    cancelled: list[str]

    def create(self, raw_spec: object, *, proposal_id: str | None = None) -> object:
        del proposal_id
        return {"mission_id": "mission-1", "received": raw_spec}

    def status(self, mission_id: str) -> MissionStatusView:
        return MissionStatusView(mission_id, "READY", "a" * 64, (), (), None)

    @staticmethod
    def verified_result(mission_id: str) -> object:
        return {"mission_id": mission_id, "status": "VERIFIED"}

    def request_cancel(self, mission_id: str) -> None:
        self.cancelled.append(mission_id)

    def bundle(self, mission_id: str) -> BundleDownload:
        return BundleDownload(f"{mission_id}.zip", "application/zip", b"bundle")

    def resolve_approval(self, *, approval_id: str, approved: bool, decided_by: str) -> ApprovalResolutionView:
        assert approved is True and decided_by == "reviewer"
        return ApprovalResolutionView(approval_id, "APPROVED", "attempt-2")


def test_api_requires_local_token_and_exposes_only_controller_results() -> None:
    controller = _Controller(cancelled=[])
    client = TestClient(
        create_app(controller=controller, local_token="test-token", cors_origins=("http://localhost:3000",))
    )

    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/v1/missions/mission-1").status_code == 401
    headers = {"Authorization": "Bearer test-token"}
    create = client.post("/v1/missions", json={"spec": {"mode": "reproduce"}}, headers=headers)
    status = client.get("/v1/missions/mission-1", headers=headers)
    verified = client.get("/v1/missions/mission-1/verified-result", headers=headers)
    cancel = client.post("/v1/missions/mission-1/cancel", headers=headers)
    bundle = client.get("/v1/missions/mission-1/bundle", headers=headers)
    approval = client.post(
        "/v1/approvals/approval-1/decide",
        json={"approved": True, "decided_by": "reviewer"},
        headers=headers,
    )

    assert create.status_code == 200
    assert status.json()["status"] == "READY"
    assert verified.json()["status"] == "VERIFIED"
    assert cancel.status_code == 202 and controller.cancelled == ["mission-1"]
    assert bundle.content == b"bundle"
    assert bundle.headers["content-disposition"] == 'attachment; filename="mission-1.zip"'
    assert approval.json()["resumed_attempt_id"] == "attempt-2"
