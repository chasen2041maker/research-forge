"""Thin API controller that calls Application use cases only."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from research_forge.application.use_cases import (
    CreateReproductionMission,
    DownloadBundle,
    GetMissionStatus,
    RequestMissionCancellation,
)


class MissionController:
    """Adapt API inputs to stable Application calls without importing outbound implementations."""

    def __init__(
        self,
        *,
        create_mission: CreateReproductionMission,
        get_status: GetMissionStatus,
        request_cancellation: RequestMissionCancellation,
        download_bundle: DownloadBundle,
    ) -> None:
        self._create_mission = create_mission
        self._get_status = get_status
        self._request_cancellation = request_cancellation
        self._download_bundle = download_bundle

    def create(self, raw_spec: Mapping[str, Any]) -> object:
        return self._create_mission.execute(raw_spec)

    def status(self, mission_id: str) -> object:
        return self._get_status.execute(mission_id)

    def request_cancel(self, mission_id: str) -> None:
        self._request_cancellation.execute(mission_id=mission_id)

    def bundle(self, mission_id: str) -> object:
        return self._download_bundle.execute(mission_id)
