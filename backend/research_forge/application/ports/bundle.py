"""Deterministic Research Bundle rendering boundary."""

from __future__ import annotations

from typing import Protocol

from research_forge.application.dto.bundle import BundleBuildInput


class BundleBuilder(Protocol):
    """Build a stable archive without model calls or database access."""

    def build(self, material: BundleBuildInput) -> bytes: ...
