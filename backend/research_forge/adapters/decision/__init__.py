"""Decision adapters that return typed proposals and never execute side effects."""

from research_forge.adapters.decision.fixed_patch import FixedPatchDecisionEngine
from research_forge.adapters.decision.limited_patch import LimitedPatchDecisionEngine, PatchTextGenerator

__all__ = ["FixedPatchDecisionEngine", "LimitedPatchDecisionEngine", "PatchTextGenerator"]
