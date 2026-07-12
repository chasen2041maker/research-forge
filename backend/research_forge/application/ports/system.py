"""Platform-independent system ports."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """Provides the current time without coupling domain logic to system time."""

    def now(self) -> datetime: ...


class IdGenerator(Protocol):
    """Creates opaque identifiers for new aggregates and messages."""

    def new(self, kind: str) -> str: ...
