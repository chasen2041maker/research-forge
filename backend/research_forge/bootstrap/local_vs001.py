"""Explicit local-development composition root for the complete no-LLM VS-001 flow."""

from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from research_forge.adapters.inbound.worker import BaselineWorker, BaselineWorkerUseCases
from research_forge.adapters.outbound.artifacts import LocalContentAddressedStore
from research_forge.adapters.outbound.bundle import DeterministicZipBundleBuilder
from research_forge.adapters.outbound.git import GitWorktreeManager, PinnedLocalPrerequisiteVerifier
from research_forge.adapters.outbound.persistence import InMemoryUnitOfWork
from research_forge.adapters.outbound.sandbox import LocalDevelopmentSandbox
from research_forge.adapters.outbound.system import SystemClock, UuidGenerator
from research_forge.application.dto import JsonSchemaReproductionSpecValidator
from research_forge.application.use_cases import (
    ClaimBaselineAttempt,
    CompleteReproductionMission,
    CreateReproductionMission,
    EnsureBaselineWorkspace,
    FinalizeBaselineExecution,
    GetBaselineOutcome,
    PersistArtifact,
    RunBaselineAttempt,
)


@dataclass(frozen=True, slots=True)
class LocalVs001Runtime:
    """Fully wired local demo runtime; formal secure execution uses DockerSandboxBroker on Linux/WSL2."""

    create_mission: CreateReproductionMission
    worker: BaselineWorker
    unit_of_work: InMemoryUnitOfWork


def build_local_vs001_runtime(
    *,
    schema: Mapping[str, object],
    workspace_root: Path,
    artifact_root: Path,
    paper_artifacts: Mapping[str, str],
    allowed_image_digests: Set[str],
) -> LocalVs001Runtime:
    """Compose fake persistence with real local Git/CAS and the development-only process runner."""
    clock = SystemClock()
    identifiers = UuidGenerator()
    unit_of_work = InMemoryUnitOfWork()
    workspace_manager = GitWorktreeManager(workspace_root)
    artifact_store = LocalContentAddressedStore(artifact_root)
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
    worker = BaselineWorker(
        BaselineWorkerUseCases(
            get_outcome=GetBaselineOutcome(unit_of_work=unit_of_work),
            claim=ClaimBaselineAttempt(
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
                sandbox_executor=LocalDevelopmentSandbox(workspace_root),
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
    return LocalVs001Runtime(
        create_mission=create_mission,
        worker=worker,
        unit_of_work=unit_of_work,
    )
