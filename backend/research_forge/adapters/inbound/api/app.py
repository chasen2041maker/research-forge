"""Authenticated local FastAPI surface for Mission, timeline, cancellation, and Bundle download."""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from research_forge.adapters.inbound.api.controller import MissionController
from research_forge.application.use_cases import MissionNotFound


class ReproductionSpecBody(BaseModel):
    spec: dict[str, Any]


def create_app(
    *,
    controller: MissionController,
    local_token: str,
    cors_origins: tuple[str, ...],
) -> FastAPI:
    """Create a loopback-oriented API; concrete dependencies are supplied only by Bootstrap."""
    if not local_token:
        raise ValueError("Local API token must be configured.")
    app = FastAPI(title="Research Forge", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    def authenticate(authorization: str | None = Header(default=None)) -> None:
        expected = f"Bearer {local_token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="Invalid local API token.")

    @app.post("/v1/missions", dependencies=[Depends(authenticate)])
    def create_mission(body: ReproductionSpecBody) -> object:
        try:
            return controller.create(body.spec)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v1/missions/{mission_id}", dependencies=[Depends(authenticate)])
    def mission_status(mission_id: str) -> object:
        try:
            return controller.status(mission_id)
        except MissionNotFound as exc:
            raise HTTPException(status_code=404, detail="Mission not found.") from exc

    @app.post("/v1/missions/{mission_id}/cancel", dependencies=[Depends(authenticate)], status_code=202)
    def cancel_mission(mission_id: str) -> object:
        try:
            controller.request_cancel(mission_id)
        except MissionNotFound as exc:
            raise HTTPException(status_code=404, detail="Mission not found.") from exc
        return {"mission_id": mission_id, "status": "CANCELLING"}

    @app.get("/v1/missions/{mission_id}/bundle", dependencies=[Depends(authenticate)])
    def download_bundle(mission_id: str) -> Response:
        try:
            bundle = controller.bundle(mission_id)
        except MissionNotFound as exc:
            raise HTTPException(status_code=404, detail="Mission not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return Response(
            content=bundle.payload,
            media_type=bundle.media_type,
            headers={"Content-Disposition": f'attachment; filename="{bundle.filename}"'},
        )

    return app
