"""Production composition root for the durable VS-001 baseline process roles.

This module deliberately keeps process wiring outside Domain and Application.  It
uses PostgreSQL as the source of truth, Redis only as Attempt transport, and the
Linux Docker broker only in the worker process.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from research_forge.adapters.inbound.api import MissionController, create_app
from research_forge.adapters.inbound.worker import BaselineWorker, BaselineWorkerUseCases
from research_forge.adapters.outbound.artifacts import LocalContentAddressedStore
from research_forge.adapters.outbound.bundle import DeterministicZipBundleBuilder
from research_forge.adapters.outbound.git import GitWorktreeManager, PinnedLocalPrerequisiteVerifier
from research_forge.adapters.outbound.persistence import SqlAlchemyUnitOfWork
from research_forge.adapters.outbound.queue import RedisTaskQueue
from research_forge.adapters.outbound.sandbox import UnixSandboxBrokerClient
from research_forge.adapters.outbound.system import SystemClock, UuidGenerator
from research_forge.application.dto import JsonSchemaReproductionSpecValidator
from research_forge.application.ports.queue import AttemptRoute
from research_forge.application.use_cases import (
    CancelBaselineAttempt,
    ClaimBaselineAttempt,
    CompleteReproductionMission,
    CreateReproductionMission,
    DownloadBundle,
    EnsureBaselineWorkspace,
    FinalizeBaselineExecution,
    GetBaselineOutcome,
    GetVerifiedResult,
    GetMissionStatus,
    PersistArtifact,
    PublishPendingOutbox,
    RequestMissionCancellation,
    ReconcileStaleOperations,
    RenewAttemptLease,
    ResolveApproval,
    RunBaselineAttempt,
)
from research_forge.domain.mission import TaskType
from research_forge.domain.errors import CancellationRequested


class ProductionConfigurationError(ValueError):
    """Raised when a production process would start with incomplete policy."""


class UnsupportedProductionAttempt(RuntimeError):
    """Raised fail-closed when a baseline worker receives a repair attempt."""


@dataclass(frozen=True, slots=True)
class ProductionVs001Settings:
    """Explicit process configuration; secret values are supplied only through environment."""

    database_url: str
    redis_url: str
    redis_stream_prefix: str
    redis_consumer_group: str
    redis_visibility_timeout_seconds: int
    redis_max_delivery_attempts: int
    api_token: str
    schema: Mapping[str, Any]
    workspace_root: Path
    artifact_root: Path
    broker_socket_path: Path
    broker_state_root: Path
    paper_root: Path
    paper_artifacts: Mapping[str, str]
    paper_artifact_paths: Mapping[str, Path]
    allowed_images: Mapping[str, str]
    cors_origins: tuple[str, ...]

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "ProductionVs001Settings":
        """Load paths, schema, and immutable execution policy without logging secrets."""
        values = os.environ if environ is None else environ
        database_url = _required(values, "RF_DATABASE_URL")
        redis_url = _required(values, "RF_REDIS_URL")
        redis_stream_prefix = values.get("RF_REDIS_STREAM_PREFIX", "research-forge:attempts").strip()
        redis_consumer_group = values.get("RF_REDIS_CONSUMER_GROUP", "research-forge-workers").strip()
        if not redis_stream_prefix or not redis_consumer_group:
            raise ProductionConfigurationError("Redis Stream prefix and consumer group must not be blank.")
        api_token = _required(values, "RF_API_TOKEN")
        schema = _read_object(Path(_required(values, "RF_SCHEMA_PATH")), "schema")
        policy = _read_object(Path(_required(values, "RF_POLICY_PATH")), "policy")
        paper_artifacts = _string_mapping(policy.get("paper_artifacts"), "policy.paper_artifacts")
        paper_root = _directory(Path(_required(values, "RF_PAPER_ROOT")), "RF_PAPER_ROOT")
        paper_artifact_paths = _paper_paths(
            policy.get("paper_artifact_paths"),
            paper_root=paper_root,
            paper_artifacts=paper_artifacts,
        )
        allowed_images = _string_mapping(policy.get("allowed_images"), "policy.allowed_images")
        if not paper_artifacts:
            raise ProductionConfigurationError("policy.paper_artifacts must contain at least one registered paper.")
        if not allowed_images:
            raise ProductionConfigurationError("policy.allowed_images must contain at least one immutable image.")
        cors_origins = tuple(
            origin.strip()
            for origin in values.get("RF_CORS_ORIGINS", "http://127.0.0.1:3000,http://localhost:3000").split(",")
            if origin.strip()
        )
        if not cors_origins:
            raise ProductionConfigurationError("RF_CORS_ORIGINS must contain at least one origin.")
        return cls(
            database_url=database_url,
            redis_url=redis_url,
            redis_stream_prefix=redis_stream_prefix,
            redis_consumer_group=redis_consumer_group,
            redis_visibility_timeout_seconds=_positive_environment_integer(
                values, "RF_REDIS_VISIBILITY_TIMEOUT_SECONDS", 60
            ),
            redis_max_delivery_attempts=_positive_environment_integer(values, "RF_REDIS_MAX_DELIVERY_ATTEMPTS", 3),
            api_token=api_token,
            schema=schema,
            workspace_root=Path(_required(values, "RF_WORKSPACE_ROOT")).resolve(),
            artifact_root=Path(_required(values, "RF_ARTIFACT_ROOT")).resolve(),
            broker_socket_path=Path(
                values.get("RF_BROKER_SOCKET_PATH", "/var/lib/research-forge/broker/sandbox.sock")
            ).resolve(),
            broker_state_root=Path(
                values.get("RF_BROKER_STATE_ROOT", "/var/lib/research-forge/broker/completed-results")
            ).absolute(),
            paper_root=paper_root,
            paper_artifacts=paper_artifacts,
            paper_artifact_paths=paper_artifact_paths,
            allowed_images=allowed_images,
            cors_origins=cors_origins,
        )


@dataclass(slots=True)
class ProductionVs001Runtime:
    """Fully wired stateful baseline runtime used by API, publisher, and worker process roles."""

    app: FastAPI
    baseline_worker: BaselineWorker
    publish_outbox: PublishPendingOutbox
    reconcile_stale_operations: ReconcileStaleOperations
    queue: RedisTaskQueue
    unit_of_work: SqlAlchemyUnitOfWork
    database_engine: Engine
    redis_client: object
    sandbox_client: UnixSandboxBrokerClient

    def publish_once(self) -> int:
        """Publish committed Outbox events and return the number of deliveries attempted."""
        return len(self.publish_outbox.execute().published_event_ids)

    def reconcile_once(self) -> int:
        """Durably request redelivery for stale cross-store operations."""
        return len(self.reconcile_stale_operations.execute().operation_ids)

    def process_one(self, *, owner: str) -> bool:
        """Process and acknowledge one baseline-lane Stream receipt after durable completion.

        The reference production composition intentionally consumes only the baseline Stream.
        Repair deliveries remain in their separate lane until a separately reviewed DecisionEngine
        worker is configured, preventing this process from silently treating a test adapter as
        production authority.
        """
        delivery = self.queue.receive(route=AttemptRoute.BASELINE, consumer_name=owner)
        if delivery is None:
            return False
        attempt_id = delivery.attempt_id
        with self.unit_of_work:
            attempt = self.unit_of_work.get_attempt(attempt_id)
            if attempt is None:
                raise UnsupportedProductionAttempt(f"Queue delivered unknown Attempt {attempt_id}.")
            task = self.unit_of_work.get_task(str(attempt.task_id))
            if task is None:
                raise UnsupportedProductionAttempt(f"Attempt {attempt_id} has no durable Task.")
            self.unit_of_work.commit()
        if task.task_type is not TaskType.BASELINE_REPRODUCTION:
            raise UnsupportedProductionAttempt(
                "The baseline process role cannot execute a repair Attempt; configure a separately reviewed "
                "DecisionEngine worker before acknowledging it."
            )
        try:
            self.baseline_worker.process(attempt_id=attempt_id, owner=owner)
        except CancellationRequested:
            self.queue.acknowledge(delivery)
            return True
        self.queue.acknowledge(delivery)
        return True

    def check_dependencies(self, *, check_broker: bool = False) -> None:
        """Verify durable dependencies and, when requested, the separately supervised broker socket."""
        with self.database_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        ping = getattr(self.redis_client, "ping", None)
        if not callable(ping) or not bool(ping()):
            raise RuntimeError("Redis ping failed.")
        if check_broker:
            self.sandbox_client.get_completed("broker-healthcheck")


def build_production_vs001_runtime(
    settings: ProductionVs001Settings,
    *,
    redis_client: object | None = None,
) -> ProductionVs001Runtime:
    """Compose durable adapters without importing concrete infrastructure into application layers."""
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    unit_of_work = SqlAlchemyUnitOfWork(session_factory)
    client = redis_client if redis_client is not None else _connect_redis(settings.redis_url)
    queue = RedisTaskQueue(
        client=client,  # type: ignore[arg-type]
        stream_prefix=settings.redis_stream_prefix,
        consumer_group=settings.redis_consumer_group,
        visibility_timeout_seconds=settings.redis_visibility_timeout_seconds,
        max_delivery_attempts=settings.redis_max_delivery_attempts,
    )
    clock = SystemClock()
    identifiers = UuidGenerator()
    workspace_manager = GitWorktreeManager(settings.workspace_root)
    artifact_store = LocalContentAddressedStore(settings.artifact_root)
    artifact_persister = PersistArtifact(
        unit_of_work=unit_of_work,
        artifact_store=artifact_store,
        clock=clock,
        id_generator=identifiers,
    )
    create_mission = CreateReproductionMission(
        spec_validator=JsonSchemaReproductionSpecValidator(settings.schema),
        unit_of_work=unit_of_work,
        clock=clock,
        id_generator=identifiers,
        prerequisite_verifier=PinnedLocalPrerequisiteVerifier(
            paper_artifacts=settings.paper_artifacts,
            paper_artifact_paths=settings.paper_artifact_paths,
            allowed_image_digests=set(settings.allowed_images),
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
    sandbox_client = UnixSandboxBrokerClient(socket_path=settings.broker_socket_path)
    baseline_worker = BaselineWorker(
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
                sandbox_executor=sandbox_client,
                clock=clock,
                id_generator=identifiers,
            ),
            cancel=CancelBaselineAttempt(
                unit_of_work=unit_of_work,
                sandbox_executor=sandbox_client,
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
    return ProductionVs001Runtime(
        app=create_app(controller=controller, local_token=settings.api_token, cors_origins=settings.cors_origins),
        baseline_worker=baseline_worker,
        publish_outbox=PublishPendingOutbox(unit_of_work=unit_of_work, task_queue=queue, clock=clock),
        reconcile_stale_operations=ReconcileStaleOperations(
            unit_of_work=unit_of_work,
            clock=clock,
            id_generator=identifiers,
            stale_after=timedelta(minutes=2),
        ),
        queue=queue,
        unit_of_work=unit_of_work,
        database_engine=engine,
        redis_client=client,
        sandbox_client=sandbox_client,
    )


def _connect_redis(redis_url: str) -> object:
    try:
        import redis
    except ImportError as exc:  # pragma: no cover - depends on deployment extras
        raise ProductionConfigurationError("Install the redis package before starting a production process.") from exc
    return redis.Redis.from_url(redis_url, decode_responses=False)


def _required(values: Mapping[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise ProductionConfigurationError(f"{name} must be configured.")
    return value


def _positive_environment_integer(values: Mapping[str, str], name: str, default: int) -> int:
    raw_value = values.get(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ProductionConfigurationError(f"{name} must be a positive integer.") from exc
    if value <= 0:
        raise ProductionConfigurationError(f"{name} must be a positive integer.")
    return value


def _read_object(path: Path, label: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductionConfigurationError(f"Unable to read {label} JSON at {path}.") from exc
    if not isinstance(payload, dict):
        raise ProductionConfigurationError(f"{label} at {path} must be a JSON object.")
    return payload


def _string_mapping(value: object, label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ProductionConfigurationError(f"{label} must be an object of non-empty strings.")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(item, str) or not item.strip():
            raise ProductionConfigurationError(f"{label} must be an object of non-empty strings.")
        result[key] = item
    return result


def _directory(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise ProductionConfigurationError(f"{label} must be an existing non-symlink directory.")
    return path.resolve()


def _paper_paths(
    value: object,
    *,
    paper_root: Path,
    paper_artifacts: Mapping[str, str],
) -> dict[str, Path]:
    configured = _string_mapping(value, "policy.paper_artifact_paths")
    if set(configured) != set(paper_artifacts):
        raise ProductionConfigurationError("Paper artifact paths must match registered paper artifact IDs exactly.")
    resolved: dict[str, Path] = {}
    for artifact_id, raw_path in configured.items():
        relative_path = Path(raw_path)
        if relative_path.is_absolute():
            raise ProductionConfigurationError("Paper artifact paths must be relative to RF_PAPER_ROOT.")
        candidate = paper_root / relative_path
        path = candidate.resolve()
        try:
            path.relative_to(paper_root)
        except ValueError as exc:
            raise ProductionConfigurationError("Paper artifact path escapes RF_PAPER_ROOT.") from exc
        if candidate.is_symlink() or path.is_symlink() or not path.is_file():
            raise ProductionConfigurationError("Registered Paper Artifact file is missing or unsafe.")
        resolved[artifact_id] = path
    return resolved
