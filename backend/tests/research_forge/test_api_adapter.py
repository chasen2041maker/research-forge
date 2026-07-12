"""FastAPI adapter tests for token enforcement and Application-only delegation."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi.testclient import TestClient

from research_forge.adapters.inbound.api import create_app
from research_forge.application.use_cases import BundleDownload, MissionStatusView


@dataclass
class _Controller:
    cancelled: list[str]

    def create(self, raw_spec: object) -> object:
        return {"mission_id": "mission-1", "received": raw_spec}

    def status(self, mission_id: str) -> MissionStatusView:
        return MissionStatusView(mission_id, "READY", "a" * 64, (), None)

    def request_cancel(self, mission_id: str) -> None:
        self.cancelled.append(mission_id)

    def bundle(self, mission_id: str) -> BundleDownload:
        return BundleDownload(f"{mission_id}.zip", "application/zip", b"bundle")


def test_api_requires_local_token_and_exposes_only_controller_results() -> None:
    controller = _Controller(cancelled=[])
    client = TestClient(
        create_app(controller=controller, local_token="test-token", cors_origins=("http://localhost:3000",))
    )

    assert client.get("/v1/missions/mission-1").status_code == 401
    headers = {"Authorization": "Bearer test-token"}
    create = client.post("/v1/missions", json={"spec": {"mode": "reproduce"}}, headers=headers)
    status = client.get("/v1/missions/mission-1", headers=headers)
    cancel = client.post("/v1/missions/mission-1/cancel", headers=headers)
    bundle = client.get("/v1/missions/mission-1/bundle", headers=headers)

    assert create.status_code == 200
    assert status.json()["status"] == "READY"
    assert cancel.status_code == 202 and controller.cancelled == ["mission-1"]
    assert bundle.content == b"bundle"
    assert bundle.headers["content-disposition"] == 'attachment; filename="mission-1.zip"'
