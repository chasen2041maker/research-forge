"""Git workspace adapters."""

from research_forge.adapters.outbound.git.worktree import GitWorktreeManager
from research_forge.adapters.outbound.git.prerequisites import PinnedLocalPrerequisiteVerifier

__all__ = ["GitWorktreeManager", "PinnedLocalPrerequisiteVerifier"]
