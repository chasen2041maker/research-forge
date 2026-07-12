"""Decision adapters that return typed proposals and never execute side effects."""

from research_forge.adapters.decision.fixed_patch import FixedPatchDecisionEngine

__all__ = ["FixedPatchDecisionEngine"]
