"""Composition roots; no Domain, Application, or Adapter module may import this package."""

from research_forge.bootstrap.local_vs001 import LocalVs001Runtime, build_local_vs001_runtime

__all__ = ["LocalVs001Runtime", "build_local_vs001_runtime"]
