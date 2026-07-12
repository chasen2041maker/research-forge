"""Separate Linux process that is the sole holder of Docker execution capability."""

from __future__ import annotations

import logging
import os
import platform

from research_forge.adapters.outbound.sandbox import (
    DockerSandboxBroker,
    DurableCompletedResultStore,
    UnixSandboxBrokerServer,
)
from research_forge.bootstrap.observability import configure_json_logging
from research_forge.bootstrap.production import ProductionConfigurationError, ProductionVs001Settings


LOGGER = logging.getLogger("research_forge.sandbox_broker")


def main() -> int:
    """Start the local broker only after complete immutable policy configuration is available."""
    configure_json_logging(os.getenv("RF_LOG_LEVEL", "INFO"))
    if platform.system() != "Linux":
        LOGGER.error("Formal sandbox broker requires Linux or WSL2.")
        return 2
    try:
        settings = ProductionVs001Settings.from_environment()
        server = UnixSandboxBrokerServer(
            socket_path=settings.broker_socket_path,
            executor=DockerSandboxBroker(
                workspace_root=settings.workspace_root,
                allowed_images=settings.allowed_images,
                completed_result_store=DurableCompletedResultStore(root_path=settings.broker_state_root),
            ),
            socket_group=os.getenv("RF_BROKER_SOCKET_GROUP") or None,
        )
    except (OSError, ProductionConfigurationError, RuntimeError) as exc:
        LOGGER.error("Sandbox broker configuration rejected: %s", exc)
        return 2
    try:
        LOGGER.info("Sandbox broker listening on %s", settings.broker_socket_path)
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Sandbox broker stopped by operator.")
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by the dedicated systemd service.
    raise SystemExit(main())
