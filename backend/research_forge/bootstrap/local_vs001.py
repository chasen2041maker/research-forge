"""Explicit local-development composition root for the complete no-LLM VS-001 flow."""

from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI
from research_forge.adapters.inbound.api import MissionController, create_app
from research_forge.adapters.inbound.worker import BaselineWorker, BaselineWorkerUseCases
from research_forge.adapters.outbound.artifacts import LocalContentAddressedStore
from research_forge.adapters.outbound.bundle import DeterministicZipBundleBuilder
from research_forge.adapters.outbound.git import GitWorktreeManager, PinnedLocalPrerequisiteVerifier
from research_forge.adapters.outbound.persistence import InMemoryUnitOfWork
from research_forge.adapters.outbound.queue import ImmediateQueue
from research_forge.adapters.outbound.sandbox import LocalDevelopmentSandbox
from research_forge.adapters.outbound.system import SystemClock, UuidGenerator
from research_forge.application.dto import JsonSchemaReproductionSpecValidator
from research_forge.application.ports.sandbox import SandboxExecutor
from research_forge.application.use_cases import (
    CancelBaselineAttempt,
    ClaimBaselineAttempt,
    CompleteReproductionMission,
    CreateReproductionMission,
    EnsureBaselineWorkspace,
    FinalizeBaselineExecution,
    DownloadBundle,
    GetBaselineOutcome,
    GetMissionStatus,
    GetVerifiedResult,
    PersistArtifact,
    RequestMissionCancellation,
    RenewAttemptLease,
    ResolveApproval,
    RunBaselineAttempt,
    PublishPendingOutbox,
)


@dataclass(frozen=True, slots=True)
class LocalVs001Runtime:
    """Fully wired local demo runtime; formal secure execution uses DockerSandboxBroker on Linux/WSL2."""

    create_mission: CreateReproductionMission
    worker: BaselineWorker
    unit_of_work: InMemoryUnitOfWork
    controller: MissionController
    queue: ImmediateQueue
    publish_outbox: PublishPendingOutbox


def build_local_vs001_runtime(
    *,
    schema: Mapping[str, object],
    workspace_root: Path,
    artifact_root: Path,
    paper_artifacts: Mapping[str, str],
    allowed_image_digests: Set[str],
    sandbox_executor: SandboxExecutor | None = None,
) -> LocalVs001Runtime:
    """Compose fake persistence with real local Git/CAS and the development-only process runner."""
    clock = SystemClock()
    identifiers = UuidGenerator()
    unit_of_work = InMemoryUnitOfWork()
    queue = ImmediateQueue()
    workspace_manager = GitWorktreeManager(workspace_root)
    artifact_store = LocalContentAddressedStore(artifact_root)
    executor = sandbox_executor or LocalDevelopmentSandbox(workspace_root)
    artifact_persister = PersistArtifact(
        unit_of_work=unit_of_work,
        artifact_store=artifact_store,
        clock=clock,
        id_generator=identifiers,
    )
    create_mission = CreateReproductionMission(
        spec_validator=JsonSchemaReproductionSpecValidator(schema),
        unit_of_work=unit_of_work,
        clock=clock,
        id_generator=identifiers,
        prerequisite_verifier=PinnedLocalPrerequisiteVerifier(
            paper_artifacts=paper_artifacts,
            allowed_image_digests=allowed_image_digests,
        ),
    )
    controller = MissionController(
        create_mission=create_mission,
        get_status=GetMissionStatus(unit_of_work=unit_of_work),
        get_verified_result=GetVerifiedResult(unit_of_work=unit_of_work),
        request_cancellation=RequestMissionCancellation(
            unit_of_work=unit_of_work,
            clock=clock,
            id_generator=identifiers,
        ),
        download_bundle=DownloadBundle(unit_of_work=unit_of_work, artifact_store=artifact_store),
        resolve_approval=ResolveApproval(
            unit_of_work=unit_of_work,
            clock=clock,
            id_generator=identifiers,
        ),
    )
    worker = BaselineWorker(
        BaselineWorkerUseCases(
            get_outcome=GetBaselineOutcome(unit_of_work=unit_of_work),
            claim=ClaimBaselineAttempt(
                unit_of_work=unit_of_work,
                clock=clock,
                lease_duration=timedelta(seconds=30),
            ),
            heartbeat=RenewAttemptLease(
                unit_of_work=unit_of_work,
                clock=clock,
                lease_duration=timedelta(seconds=30),
            ),
            ensure_workspace=EnsureBaselineWorkspace(
                unit_of_work=unit_of_work,
                workspace_manager=workspace_manager,
                clock=clock,
                id_generator=identifiers,
            ),
            run=RunBaselineAttempt(
                unit_of_work=unit_of_work,
                sandbox_executor=executor,
                clock=clock,
                id_generator=identifiers,
            ),
            cancel=CancelBaselineAttempt(
                unit_of_work=unit_of_work,
                sandbox_executor=executor,
                clock=clock,
                id_generator=identifiers,
            ),
            finalize=FinalizeBaselineExecution(
                unit_of_work=unit_of_work,
                artifact_persister=artifact_persister,
                clock=clock,
                id_generator=identifiers,
            ),
            complete=CompleteReproductionMission(
                unit_of_work=unit_of_work,
                artifact_store=artifact_store,
                artifact_persister=artifact_persister,
                workspace_manager=workspace_manager,
                bundle_builder=DeterministicZipBundleBuilder(),
                clock=clock,
                id_generator=identifiers,
            ),
        )
    )
    publish_outbox = PublishPendingOutbox(unit_of_work=unit_of_work, task_queue=queue, clock=clock)
    return LocalVs001Runtime(
        create_mission=create_mission,
        worker=worker,
        unit_of_work=unit_of_work,
        controller=controller,
        queue=queue,
        publish_outbox=publish_outbox,
    )


def build_local_vs001_api(
    *,
    schema: Mapping[str, object],
    workspace_root: Path,
    artifact_root: Path,
    paper_artifacts: Mapping[str, str],
    allowed_image_digests: Set[str],
    local_token: str,
    cors_origins: tuple[str, ...] = ("http://localhost:3000",),
) -> FastAPI:
    """Compose the development API in Bootstrap while preserving route-to-use-case boundaries."""
    runtime = build_local_vs001_runtime(
        schema=schema,
        workspace_root=workspace_root,
        artifact_root=artifact_root,
        paper_artifacts=paper_artifacts,
        allowed_image_digests=allowed_image_digests,
    )
    return create_app(controller=runtime.controller, local_token=local_token, cors_origins=cors_origins)
