"""Errors raised when a domain invariant is violated."""


class DomainViolation(Exception):
    """Base class for invalid domain operations."""


class InvalidMissionTransition(DomainViolation):
    """Raised when a Mission is asked to make an invalid state transition."""

    def __init__(self, current: object, target: object) -> None:
        super().__init__(f"Mission cannot transition from {current} to {target}.")
        self.current = current
        self.target = target
