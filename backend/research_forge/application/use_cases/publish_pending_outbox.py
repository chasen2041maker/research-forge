"""Publish committed Outbox events without allowing queue state to become business truth."""

from __future__ import annotations

from dataclasses import dataclass

from research_forge.application.ports.queue import AttemptRoute, TaskQueue
from research_forge.application.ports.system import Clock
from research_forge.application.ports.unit_of_work import UnitOfWork
from research_forge.domain.mission import OutboxEvent


class OutboxPublicationError(ValueError):
    """Raised when an Outbox event is not a supported Attempt-delivery message."""


@dataclass(frozen=True, slots=True)
class OutboxPublicationView:
    published_event_ids: tuple[str, ...]


class PublishPendingOutbox:
    """Deliver ready Attempts at least once; retries are safe because Attempt state is durable and idempotent."""

    _ATTEMPT_TOPICS = frozenset({"baseline_attempt.ready", "repair_attempt.ready"})

    def __init__(self, *, unit_of_work: UnitOfWork, task_queue: TaskQueue, clock: Clock) -> None:
        self._unit_of_work = unit_of_work
        self._task_queue = task_queue
        self._clock = clock

    def execute(self, *, limit: int = 100) -> OutboxPublicationView:
        with self._unit_of_work:
            events = self._unit_of_work.get_unpublished_outbox_events(limit)
            self._unit_of_work.commit()
        published: list[str] = []
        for event in events:
            attempt_id, route = self._delivery(event)
            self._task_queue.publish(attempt_id, route=route)
            with self._unit_of_work:
                self._unit_of_work.mark_outbox_event_published(event.event_id, self._clock.now())
                self._unit_of_work.commit()
            published.append(event.event_id)
        return OutboxPublicationView(tuple(published))

    def _delivery(self, event: OutboxEvent) -> tuple[str, AttemptRoute]:
        routes = {
            "baseline_attempt.ready": AttemptRoute.BASELINE,
            "repair_attempt.ready": AttemptRoute.REPAIR,
        }
        route = routes.get(event.topic)
        if route is None or event.topic not in self._ATTEMPT_TOPICS:
            raise OutboxPublicationError(f"Unsupported Outbox topic: {event.topic}")
        attempt_id = event.payload.get("attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id:
            raise OutboxPublicationError(f"Outbox event {event.event_id} has no valid attempt_id payload.")
        return attempt_id, route
