"""FastAPI inbound adapter for the minimal Research Forge v0.1 interface."""

from research_forge.adapters.inbound.api.app import create_app
from research_forge.adapters.inbound.api.controller import MissionController

__all__ = ["MissionController", "create_app"]
