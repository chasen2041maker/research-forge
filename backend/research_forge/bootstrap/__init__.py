"""Composition roots; no Domain, Application, or Adapter module may import this package."""

from research_forge.bootstrap.local_vs001 import (
    LocalVs001Runtime,
    build_local_vs001_api,
    build_local_vs001_runtime,
)
from research_forge.bootstrap.production import (
    ProductionConfigurationError,
    ProductionVs001Runtime,
    ProductionVs001Settings,
    UnsupportedProductionAttempt,
    build_production_vs001_runtime,
)

__all__ = [
    "LocalVs001Runtime",
    "ProductionConfigurationError",
    "ProductionVs001Runtime",
    "ProductionVs001Settings",
    "UnsupportedProductionAttempt",
    "build_local_vs001_api",
    "build_local_vs001_runtime",
    "build_production_vs001_runtime",
]
