"""Errors raised when a domain invariant is violated."""


class DomainViolation(Exception):
    """Base class for invalid domain operations."""


class InvalidMissionTransition(DomainViolation):
    """Raised when a Mission is asked to make an invalid state transition."""

    def __init__(self, current: object, target: object) -> None:
        super().__init__(f"Mission cannot transition from {current} to {target}.")
        self.current = current
        self.target = target


class InvalidTaskTransition(DomainViolation):
    """Raised when a Task is asked to make an invalid state transition."""


class InvalidAttemptTransition(DomainViolation):
    """Raised when an Attempt is asked to make an invalid state transition."""


class LeaseLost(DomainViolation):
    """Raised when a worker no longer owns the current lease epoch."""


class OptimisticLockConflict(DomainViolation):
    """Raised when a write is based on a stale aggregate version."""


class OperationConflict(DomainViolation):
    """Raised when an operation's immutable identity conflicts with an existing one."""
