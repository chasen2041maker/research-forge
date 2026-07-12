"""Standard-library implementations of application system ports."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4


class SystemClock:
    """Return aware UTC time from the system clock at the outer adapter boundary."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class UuidGenerator:
    """Create opaque UUID identifiers with a human-readable aggregate prefix."""

    def new(self, kind: str) -> str:
        return f"{kind}-{uuid4()}"
