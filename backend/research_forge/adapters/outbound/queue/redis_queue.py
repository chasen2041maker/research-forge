"""Redis transport adapter; Redis carries Attempt IDs only and never owns Mission state."""

from __future__ import annotations

from typing import Protocol


class RedisListClient(Protocol):
    """The narrow Redis list surface required by this transport adapter."""

    def rpush(self, key: str, value: str) -> int: ...

    def lindex(self, key: str, index: int) -> bytes | str | None: ...

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int: ...


class RedisTaskQueue:
    """At-least-once Redis delivery with atomic head acknowledgement and durable Attempt idempotency."""

    _ACK_SCRIPT = """
local current = redis.call('LINDEX', KEYS[1], 0)
if current ~= ARGV[1] then
  return 0
end
redis.call('LPOP', KEYS[1])
return 1
"""

    def __init__(self, *, client: RedisListClient, queue_key: str = "research-forge:attempts") -> None:
        if not queue_key.strip():
            raise ValueError("Redis queue key must not be blank.")
        self._client = client
        self._queue_key = queue_key

    def publish(self, attempt_id: str) -> None:
        if not attempt_id:
            raise ValueError("Attempt ID must not be blank.")
        self._client.rpush(self._queue_key, attempt_id)

    def receive(self) -> str | None:
        value = self._client.lindex(self._queue_key, 0)
        if value is None:
            return None
        return value.decode("utf-8") if isinstance(value, bytes) else value

    def acknowledge(self, attempt_id: str) -> None:
        if not attempt_id:
            raise ValueError("Attempt ID must not be blank.")
        removed = self._client.eval(self._ACK_SCRIPT, 1, self._queue_key, attempt_id)
        if removed != 1:
            raise ValueError("Queue acknowledgement must match the current Redis queue head.")
