"""Redis Streams transport with typed envelopes, pending recovery, and atomic dead-lettering."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from threading import RLock
from typing import Protocol

from research_forge.application.ports.queue import AttemptDelivery, AttemptRoute


class RedisStreamClient(Protocol):
    """The narrow Redis Streams surface used by the transport adapter."""

    def xgroup_create(self, name: str, groupname: str, id: str, mkstream: bool) -> object: ...

    def xadd(self, name: str, fields: Mapping[str, str]) -> bytes | str: ...

    def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: Mapping[str, str],
        count: int,
        block: int,
    ) -> object: ...

    def xautoclaim(
        self,
        name: str,
        groupname: str,
        consumername: str,
        min_idle_time: int,
        start_id: str,
        count: int,
    ) -> object: ...

    def xpending_range(self, name: str, groupname: str, min: str, max: str, count: int) -> object: ...

    def xack(self, name: str, groupname: str, *ids: str) -> int: ...

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int: ...


class RedisQueueUnavailable(RuntimeError):
    """Raised when Redis cannot provide a delivery with the required safety guarantees."""


class RedisEnvelopeError(ValueError):
    """Raised for a corrupt or mismatched versioned queue envelope."""


class RedisTaskQueue:
    """At-least-once Stream delivery, with separate baseline/repair lanes and DLQ recovery."""

    _ENVELOPE_VERSION = "1"
    _MOVE_TO_DLQ_SCRIPT = """
local dead_letter_id = redis.call('XADD', KEYS[2], '*',
  'envelope_version', ARGV[2],
  'route', ARGV[3],
  'attempt_id', ARGV[4],
  'source_stream', KEYS[1],
  'source_message_id', ARGV[1],
  'delivery_count', ARGV[5],
  'reason', ARGV[6])
local acknowledged = redis.call('XACK', KEYS[1], ARGV[1])
if acknowledged ~= 1 then
  redis.call('XDEL', KEYS[2], dead_letter_id)
  return 0
end
return 1
"""

    def __init__(
        self,
        *,
        client: RedisStreamClient,
        stream_prefix: str = "research-forge:attempts",
        consumer_group: str = "research-forge-workers",
        visibility_timeout_seconds: int = 60,
        max_delivery_attempts: int = 3,
    ) -> None:
        if not stream_prefix.strip():
            raise ValueError("Redis Stream prefix must not be blank.")
        if not consumer_group.strip():
            raise ValueError("Redis consumer group must not be blank.")
        if visibility_timeout_seconds <= 0:
            raise ValueError("Redis visibility timeout must be positive.")
        if max_delivery_attempts <= 0:
            raise ValueError("Redis max delivery attempts must be positive.")
        self._client = client
        self._stream_prefix = stream_prefix
        self._consumer_group = consumer_group
        self._visibility_timeout_ms = visibility_timeout_seconds * 1000
        self._max_delivery_attempts = max_delivery_attempts
        self._initialized_routes: set[AttemptRoute] = set()
        self._initialization_lock = RLock()

    def publish(self, attempt_id: str, *, route: AttemptRoute) -> None:
        """Add one typed delivery envelope to its reviewed execution lane."""
        _required_string(attempt_id, "Attempt ID")
        self._ensure_group(route)
        self._client.xadd(
            self._stream_key(route),
            {
                "envelope_version": self._ENVELOPE_VERSION,
                "route": route.value,
                "attempt_id": attempt_id,
            },
        )

    def receive(self, *, route: AttemptRoute, consumer_name: str) -> AttemptDelivery | None:
        """Recover expired pending work first, then consume one new message from the route stream."""
        _required_string(consumer_name, "Queue consumer name")
        self._ensure_group(route)
        stream_key = self._stream_key(route)
        for message_id, fields in self._claimed_entries(stream_key, consumer_name):
            delivery_count = self._delivery_count(stream_key, message_id)
            if delivery_count > self._max_delivery_attempts:
                self._move_to_dead_letter(
                    route=route,
                    source_stream=stream_key,
                    message_id=message_id,
                    fields=fields,
                    delivery_count=delivery_count,
                    reason="visibility_timeout_exceeded",
                )
                continue
            delivery = self._valid_delivery(route=route, message_id=message_id, fields=fields)
            if delivery is not None:
                return delivery
        response = self._client.xreadgroup(
            self._consumer_group,
            consumer_name,
            {stream_key: ">"},
            count=1,
            block=1,
        )
        for message_id, fields in _stream_entries(response):
            delivery = self._valid_delivery(route=route, message_id=message_id, fields=fields)
            if delivery is not None:
                return delivery
        return None

    def acknowledge(self, delivery: AttemptDelivery) -> None:
        """Acknowledge exactly the Stream entry supplied to the worker, never just an Attempt ID."""
        _required_string(delivery.attempt_id, "Attempt ID")
        _required_string(delivery.message_id, "Stream message ID")
        acknowledged = self._client.xack(self._stream_key(delivery.route), self._consumer_group, delivery.message_id)
        if acknowledged != 1:
            raise RedisQueueUnavailable("Redis acknowledgement did not match one pending Stream delivery.")

    def _claimed_entries(self, stream_key: str, consumer_name: str) -> tuple[tuple[str, Mapping[str, object]], ...]:
        response = self._client.xautoclaim(
            stream_key,
            self._consumer_group,
            consumer_name,
            self._visibility_timeout_ms,
            "0-0",
            count=self._max_delivery_attempts + 1,
        )
        if not isinstance(response, (tuple, list)) or len(response) != 3:
            raise RedisQueueUnavailable("Redis XAUTOCLAIM returned an invalid response.")
        return _entries(response[1])

    def _delivery_count(self, stream_key: str, message_id: str) -> int:
        response = self._client.xpending_range(
            stream_key,
            self._consumer_group,
            message_id,
            message_id,
            1,
        )
        if not isinstance(response, Sequence) or isinstance(response, (str, bytes)) or len(response) != 1:
            raise RedisQueueUnavailable("Redis pending metadata is missing for an auto-claimed delivery.")
        item = response[0]
        if not isinstance(item, Mapping):
            raise RedisQueueUnavailable("Redis pending metadata has an invalid shape.")
        value = item.get("times_delivered", item.get(b"times_delivered"))
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise RedisQueueUnavailable("Redis pending metadata has an invalid delivery count.")
        return value

    def _valid_delivery(
        self,
        *,
        route: AttemptRoute,
        message_id: str,
        fields: Mapping[str, object],
    ) -> AttemptDelivery | None:
        try:
            envelope = _string_fields(fields)
            if envelope.get("envelope_version") != self._ENVELOPE_VERSION:
                raise RedisEnvelopeError("Redis envelope version is unsupported.")
            if envelope.get("route") != route.value:
                raise RedisEnvelopeError("Redis envelope route does not match its Stream.")
            attempt_id = _required_string(envelope.get("attempt_id", ""), "Redis envelope attempt ID")
        except RedisEnvelopeError:
            self._move_to_dead_letter(
                route=route,
                source_stream=self._stream_key(route),
                message_id=message_id,
                fields=fields,
                delivery_count=1,
                reason="invalid_envelope",
            )
            return None
        return AttemptDelivery(attempt_id=attempt_id, route=route, message_id=message_id)

    def _move_to_dead_letter(
        self,
        *,
        route: AttemptRoute,
        source_stream: str,
        message_id: str,
        fields: Mapping[str, object],
        delivery_count: int,
        reason: str,
    ) -> None:
        try:
            envelope = _string_fields(fields)
            attempt_id = envelope.get("attempt_id", "invalid-attempt-id")
            if not attempt_id:
                attempt_id = "invalid-attempt-id"
        except RedisEnvelopeError:
            attempt_id = "invalid-attempt-id"
        moved = self._client.eval(
            self._MOVE_TO_DLQ_SCRIPT,
            2,
            source_stream,
            self._dead_letter_key(route),
            message_id,
            self._ENVELOPE_VERSION,
            route.value,
            attempt_id,
            str(delivery_count),
            reason,
        )
        if moved != 1:
            raise RedisQueueUnavailable("Redis could not atomically dead-letter the pending Stream delivery.")

    def _ensure_group(self, route: AttemptRoute) -> None:
        with self._initialization_lock:
            if route in self._initialized_routes:
                return
            try:
                self._client.xgroup_create(
                    self._stream_key(route),
                    self._consumer_group,
                    id="0-0",
                    mkstream=True,
                )
            except Exception as exc:
                if "BUSYGROUP" not in str(exc):
                    raise RedisQueueUnavailable("Redis consumer group could not be created.") from exc
            self._initialized_routes.add(route)

    def _stream_key(self, route: AttemptRoute) -> str:
        return f"{self._stream_prefix}:{route.value}"

    def _dead_letter_key(self, route: AttemptRoute) -> str:
        return f"{self._stream_key(route)}:dead-letter"


def _entries(value: object) -> tuple[tuple[str, Mapping[str, object]], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise RedisQueueUnavailable("Redis Stream entries have an invalid shape.")
    result: list[tuple[str, Mapping[str, object]]] = []
    for item in value:
        if not isinstance(item, Sequence) or isinstance(item, (str, bytes)) or len(item) != 2:
            raise RedisQueueUnavailable("Redis Stream entry has an invalid shape.")
        message_id, fields = item
        result.append((_as_string(message_id, "Redis Stream message ID"), _as_mapping(fields, "Redis Stream fields")))
    return tuple(result)


def _stream_entries(value: object) -> tuple[tuple[str, Mapping[str, object]], ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise RedisQueueUnavailable("Redis XREADGROUP returned an invalid response.")
    result: list[tuple[str, Mapping[str, object]]] = []
    for item in value:
        if not isinstance(item, Sequence) or isinstance(item, (str, bytes)) or len(item) != 2:
            raise RedisQueueUnavailable("Redis XREADGROUP response has an invalid stream entry.")
        _, messages = item
        result.extend(_entries(messages))
    return tuple(result)


def _string_fields(fields: Mapping[str, object]) -> dict[str, str]:
    return {_as_string(key, "Redis envelope field name"): _as_string(value, "Redis envelope field value") for key, value in fields.items()}


def _as_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RedisQueueUnavailable(f"{name} must be a mapping.")
    return value


def _as_string(value: object, name: str) -> str:
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RedisEnvelopeError(f"{name} must be UTF-8.") from exc
    if not isinstance(value, str) or not value or "\x00" in value:
        raise RedisEnvelopeError(f"{name} must be a non-empty string without NUL.")
    return value


def _required_string(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise RedisEnvelopeError(f"{name} must be a non-empty string without NUL.")
    return value
