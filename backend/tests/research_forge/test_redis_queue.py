"""Redis queue adapter contract tests using a deterministic in-memory Redis-list double."""

from __future__ import annotations

import pytest

from research_forge.adapters.outbound.queue import RedisTaskQueue


class _RedisList:
    def __init__(self) -> None:
        self.values: list[str] = []
        self.calls: list[tuple[str, int, tuple[str, ...]]] = []

    def rpush(self, key: str, value: str) -> int:
        assert key == "rf:attempts"
        self.values.append(value)
        return len(self.values)

    def lindex(self, key: str, index: int) -> bytes | None:
        assert key == "rf:attempts" and index == 0
        return self.values[0].encode("utf-8") if self.values else None

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        self.calls.append((script, numkeys, keys_and_args))
        key, attempt_id = keys_and_args
        assert key == "rf:attempts"
        if not self.values or self.values[0] != attempt_id:
            return 0
        self.values.pop(0)
        return 1


def test_redis_queue_preserves_attempt_order_and_acknowledges_head_atomically() -> None:
    client = _RedisList()
    queue = RedisTaskQueue(client=client, queue_key="rf:attempts")

    queue.publish("attempt-1")
    queue.publish("attempt-2")

    assert queue.receive() == "attempt-1"
    queue.acknowledge("attempt-1")
    assert queue.receive() == "attempt-2"
    assert client.calls[0][1:] == (1, ("rf:attempts", "attempt-1"))


def test_redis_queue_rejects_acknowledgement_for_non_head_message() -> None:
    client = _RedisList()
    queue = RedisTaskQueue(client=client, queue_key="rf:attempts")
    queue.publish("attempt-1")

    with pytest.raises(ValueError, match="head"):
        queue.acknowledge("attempt-other")

    assert queue.receive() == "attempt-1"
