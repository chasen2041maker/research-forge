"""Process entry points for the production VS-001 composition root.

Run from ``backend`` on the Linux host that owns the frozen repository fixtures
and Docker bind-mount paths, for example
``python -m research_forge.bootstrap.runtime worker``.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import time
from collections.abc import Sequence

import uvicorn

from research_forge.bootstrap.observability import configure_json_logging
from research_forge.bootstrap.production import (
    ProductionConfigurationError,
    ProductionVs001Runtime,
    ProductionVs001Settings,
    UnsupportedProductionAttempt,
    build_production_vs001_runtime,
)


LOGGER = logging.getLogger("research_forge.runtime")


def main(argv: Sequence[str] | None = None) -> int:
    """Run one explicitly selected process role and return a shell-compatible status code."""
    parser = argparse.ArgumentParser(description="Research Forge VS-001 production process roles")
    parser.add_argument("role", choices=("api", "publisher", "worker", "reconciler", "healthcheck"))
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    arguments = parser.parse_args(argv)
    if arguments.poll_seconds <= 0:
        parser.error("--poll-seconds must be positive")
    configure_json_logging(os.getenv("RF_LOG_LEVEL", "INFO"))
    try:
        runtime = build_production_vs001_runtime(ProductionVs001Settings.from_environment())
    except ProductionConfigurationError as exc:
        LOGGER.error("Production configuration rejected: %s", exc)
        return 2

    try:
        if arguments.role == "api":
            return _run_api(runtime)
        if arguments.role == "publisher":
            return _run_publisher(runtime, arguments.poll_seconds)
        if arguments.role == "worker":
            return _run_worker(runtime, arguments.poll_seconds)
        if arguments.role == "reconciler":
            return _run_reconciler(runtime, arguments.poll_seconds)
        runtime.check_dependencies(check_broker=True)
        LOGGER.info("PostgreSQL, Redis, and the sandbox broker are reachable.")
        return 0
    except KeyboardInterrupt:
        LOGGER.info("Process stopped by operator.")
        return 0


def _run_api(runtime: ProductionVs001Runtime) -> int:
    host = os.getenv("RF_API_HOST", "127.0.0.1")
    port = int(os.getenv("RF_API_PORT", "8080"))
    uvicorn.run(runtime.app, host=host, port=port, proxy_headers=False)
    return 0


def _run_publisher(runtime: ProductionVs001Runtime, poll_seconds: float) -> int:
    while True:
        published = runtime.publish_once()
        if published:
            LOGGER.info("Published %s durable outbox event(s).", published)
            continue
        time.sleep(poll_seconds)


def _run_worker(runtime: ProductionVs001Runtime, poll_seconds: float) -> int:
    owner = os.getenv("RF_WORKER_OWNER", f"{socket.gethostname()}:{os.getpid()}")
    while True:
        try:
            processed = runtime.process_one(owner=owner)
        except UnsupportedProductionAttempt as exc:
            LOGGER.error("Worker refused queued Attempt: %s", exc)
            time.sleep(poll_seconds)
        except Exception:
            LOGGER.exception("Attempt processing failed; message remains unacknowledged for recovery.")
            time.sleep(poll_seconds)
        else:
            if not processed:
                time.sleep(poll_seconds)


def _run_reconciler(runtime: ProductionVs001Runtime, poll_seconds: float) -> int:
    while True:
        requested = runtime.reconcile_once()
        if requested:
            LOGGER.info("Requested recovery for %s stale operation(s).", requested)
            continue
        time.sleep(poll_seconds)


if __name__ == "__main__":  # pragma: no cover - exercised through the deployment entry point
    raise SystemExit(main())
