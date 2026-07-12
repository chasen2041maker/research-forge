"""Authorize and read one verified Bundle through Application rather than an API-to-CAS shortcut."""

from __future__ import annotations

from dataclasses import dataclass

from research_forge.application.ports.artifacts import ArtifactStore
from research_forge.application.ports.unit_of_work import UnitOfWork
from research_forge.application.use_cases.get_mission_status import MissionNotFound


@dataclass(frozen=True, slots=True)
class BundleDownload:
    filename: str
    media_type: str
    payload: bytes


class DownloadBundle:
    """Read a CAS Bundle only after verifying durable Mission ownership and registration."""

    def __init__(self, *, unit_of_work: UnitOfWork, artifact_store: ArtifactStore) -> None:
        self._unit_of_work = unit_of_work
        self._artifact_store = artifact_store

    def execute(self, mission_id: str) -> BundleDownload:
        with self._unit_of_work:
            mission = self._unit_of_work.get_mission(mission_id)
            if mission is None:
                raise MissionNotFound(mission_id)
            bundle = self._unit_of_work.get_bundle(mission_id)
            self._unit_of_work.commit()
        if bundle is None:
            raise ValueError("Mission has no completed bundle.")
        return BundleDownload(
            filename=f"research-forge-{mission_id}.zip",
            media_type=bundle.artifact.media_type,
            payload=self._artifact_store.read_verified(bundle.artifact),
        )
