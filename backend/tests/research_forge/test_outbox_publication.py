"""Outbox delivery contracts: queue transport is at-least-once, while business truth remains durable."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from research_forge.adapters.outbound.persistence import InMemoryUnitOfWork
from research_forge.adapters.outbound.queue import ImmediateQueue
from research_forge.application.ports.queue import AttemptDelivery, AttemptRoute
from research_forge.application.use_cases import OutboxPublicationError, PublishPendingOutbox
from research_forge.domain.mission import OutboxEvent


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 7, 12, tzinfo=timezone.utc)


class _FailingQueue:
    def publish(self, attempt_id: str, *, route: AttemptRoute) -> None:
        del attempt_id, route
        raise RuntimeError("transport unavailable")

    def receive(self, *, route: AttemptRoute, consumer_name: str) -> AttemptDelivery | None:
        del route, consumer_name
        return None

    def acknowledge(self, delivery: AttemptDelivery) -> None:
        del delivery


def _event(*, event_id: str = "outbox-1", topic: str = "baseline_attempt.ready") -> OutboxEvent:
    return OutboxEvent(
        event_id=event_id,
        topic=topic,
        aggregate_id="mission-1",
        occurred_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
        payload={"attempt_id": "attempt-1"},
    )


def _add_event(unit_of_work: InMemoryUnitOfWork, event: OutboxEvent) -> None:
    with unit_of_work:
        unit_of_work.add_outbox_event(event)
        unit_of_work.commit()


def test_outbox_publisher_marks_only_successfully_delivered_events() -> None:
    unit_of_work = InMemoryUnitOfWork()
    _add_event(unit_of_work, _event())
    queue = ImmediateQueue()
    publisher = PublishPendingOutbox(unit_of_work=unit_of_work, task_queue=queue, clock=_Clock())

    first = publisher.execute()
    second = publisher.execute()

    assert first.published_event_ids == ("outbox-1",)
    assert second.published_event_ids == ()
    delivery = queue.receive(route=AttemptRoute.BASELINE, consumer_name="test-worker")
    assert delivery is not None and delivery.attempt_id == "attempt-1"
    queue.acknowledge(delivery)


def test_outbox_publisher_leaves_event_pending_when_transport_fails() -> None:
    unit_of_work = InMemoryUnitOfWork()
    _add_event(unit_of_work, _event())
    publisher = PublishPendingOutbox(unit_of_work=unit_of_work, task_queue=_FailingQueue(), clock=_Clock())

    with pytest.raises(RuntimeError, match="transport unavailable"):
        publisher.execute()

    with unit_of_work:
        pending = unit_of_work.get_unpublished_outbox_events(10)
        unit_of_work.commit()
    assert pending == (_event(),)


def test_outbox_publisher_rejects_unrecognized_messages_without_acknowledging_them() -> None:
    unit_of_work = InMemoryUnitOfWork()
    _add_event(unit_of_work, _event(topic="mission.created"))
    publisher = PublishPendingOutbox(unit_of_work=unit_of_work, task_queue=ImmediateQueue(), clock=_Clock())

    with pytest.raises(OutboxPublicationError, match="Unsupported"):
        publisher.execute()

    with unit_of_work:
        assert unit_of_work.get_unpublished_outbox_events(10) == (_event(topic="mission.created"),)
        unit_of_work.commit()
