"""Redis Streams transport contracts using a deterministic in-memory Streams double."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import pytest

from research_forge.adapters.outbound.queue import RedisTaskQueue
from research_forge.adapters.outbound.queue.redis_queue import RedisQueueUnavailable
from research_forge.application.ports.queue import AttemptDelivery, AttemptRoute


@dataclass
class _Pending:
    fields: dict[str, str]
    consumer: str
    delivered_at_ms: int
    delivery_count: int


class _RedisStreams:
    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.group_offsets: dict[tuple[str, str], int] = {}
        self.pending: dict[tuple[str, str], dict[str, _Pending]] = {}
        self.now_ms = 0
        self._sequence = 0
        self.calls: list[tuple[str, int, tuple[str, ...]]] = []

    def xgroup_create(self, name: str, groupname: str, id: str, mkstream: bool) -> int:
        assert id == "0-0" and mkstream is True
        key = (name, groupname)
        if key in self.group_offsets:
            raise RuntimeError("BUSYGROUP Consumer Group name already exists")
        self.streams.setdefault(name, [])
        self.group_offsets[key] = 0
        self.pending[key] = {}
        return 1

    def xadd(self, name: str, fields: Mapping[str, str]) -> str:
        self._sequence += 1
        message_id = f"{self._sequence}-0"
        self.streams.setdefault(name, []).append((message_id, dict(fields)))
        return message_id

    def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: Mapping[str, str],
        count: int,
        block: int,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        assert count == 1 and block == 1 and len(streams) == 1
        stream, marker = next(iter(streams.items()))
        assert marker == ">"
        key = (stream, groupname)
        offset = self.group_offsets[key]
        messages = self.streams[stream]
        if offset >= len(messages):
            return []
        message_id, fields = messages[offset]
        self.group_offsets[key] = offset + 1
        self.pending[key][message_id] = _Pending(dict(fields), consumername, self.now_ms, 1)
        return [(stream, [(message_id, dict(fields))])]

    def xautoclaim(
        self,
        name: str,
        groupname: str,
        consumername: str,
        min_idle_time: int,
        start_id: str,
        count: int,
    ) -> tuple[str, list[tuple[str, dict[str, str]]], list[str]]:
        assert start_id == "0-0"
        claimed: list[tuple[str, dict[str, str]]] = []
        for message_id, pending in self.pending[(name, groupname)].items():
            if self.now_ms - pending.delivered_at_ms < min_idle_time:
                continue
            pending.consumer = consumername
            pending.delivered_at_ms = self.now_ms
            pending.delivery_count += 1
            claimed.append((message_id, dict(pending.fields)))
            if len(claimed) >= count:
                break
        return "0-0", claimed, []

    def xpending_range(
        self,
        name: str,
        groupname: str,
        min: str,
        max: str,
        count: int,
    ) -> list[dict[str, int | str]]:
        assert min == max and count == 1
        pending = self.pending[(name, groupname)].get(min)
        if pending is None:
            return []
        return [{"message_id": min, "times_delivered": pending.delivery_count}]

    def xack(self, name: str, groupname: str, *ids: str) -> int:
        pending = self.pending[(name, groupname)]
        removed = 0
        for message_id in ids:
            if message_id in pending:
                pending.pop(message_id)
                removed += 1
        return removed

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        self.calls.append((script, numkeys, keys_and_args))
        assert numkeys == 2
        source_stream, dead_letter_stream, message_id, version, route, attempt_id, delivery_count, reason = keys_and_args
        self.xadd(
            dead_letter_stream,
            {
                "envelope_version": version,
                "route": route,
                "attempt_id": attempt_id,
                "source_stream": source_stream,
                "source_message_id": message_id,
                "delivery_count": delivery_count,
                "reason": reason,
            },
        )
        groupname = next(group for stream, group in self.group_offsets if stream == source_stream)
        return self.xack(source_stream, groupname, message_id)

    def advance(self, milliseconds: int) -> None:
        self.now_ms += milliseconds


def test_redis_streams_keep_baseline_and_repair_deliveries_in_separate_lanes() -> None:
    client = _RedisStreams()
    queue = RedisTaskQueue(client=client, stream_prefix="rf:attempts")
    queue.publish("attempt-baseline", route=AttemptRoute.BASELINE)
    queue.publish("attempt-repair", route=AttemptRoute.REPAIR)

    baseline = queue.receive(route=AttemptRoute.BASELINE, consumer_name="worker-a")
    repair = queue.receive(route=AttemptRoute.REPAIR, consumer_name="repair-worker-a")

    assert baseline is not None
    assert baseline.attempt_id == "attempt-baseline"
    assert baseline.route is AttemptRoute.BASELINE
    assert repair is not None
    assert repair.attempt_id == "attempt-repair"
    assert repair.route is AttemptRoute.REPAIR
    queue.acknowledge(baseline)
    queue.acknowledge(repair)


def test_expired_stream_delivery_is_reclaimed_then_sent_to_dead_letter_queue() -> None:
    client = _RedisStreams()
    queue = RedisTaskQueue(
        client=client,
        stream_prefix="rf:attempts",
        visibility_timeout_seconds=5,
        max_delivery_attempts=3,
    )
    queue.publish("attempt-1", route=AttemptRoute.BASELINE)

    first = queue.receive(route=AttemptRoute.BASELINE, consumer_name="worker-a")
    client.advance(5_000)
    second = queue.receive(route=AttemptRoute.BASELINE, consumer_name="worker-b")
    client.advance(5_000)
    third = queue.receive(route=AttemptRoute.BASELINE, consumer_name="worker-c")
    client.advance(5_000)
    exhausted = queue.receive(route=AttemptRoute.BASELINE, consumer_name="worker-d")

    assert first is not None and second is not None and third is not None
    assert {first.message_id, second.message_id, third.message_id} == {first.message_id}
    assert exhausted is None
    dead_letters = client.streams["rf:attempts:baseline:dead-letter"]
    assert len(dead_letters) == 1
    assert dead_letters[0][1] == {
        "envelope_version": "1",
        "route": "baseline",
        "attempt_id": "attempt-1",
        "source_stream": "rf:attempts:baseline",
        "source_message_id": first.message_id,
        "delivery_count": "4",
        "reason": "visibility_timeout_exceeded",
    }
    assert client.calls[0][1] == 2


def test_redis_stream_queue_refuses_acknowledgement_without_the_pending_receipt() -> None:
    client = _RedisStreams()
    queue = RedisTaskQueue(client=client, stream_prefix="rf:attempts")
    queue.publish("attempt-1", route=AttemptRoute.BASELINE)
    delivery = queue.receive(route=AttemptRoute.BASELINE, consumer_name="worker-a")

    assert delivery is not None
    with pytest.raises(RedisQueueUnavailable, match="did not match"):
        queue.acknowledge(AttemptDelivery("attempt-1", AttemptRoute.BASELINE, "missing-0"))
