"""Production Redis Streams contract; skipped locally unless a disposable Redis URL is supplied."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from research_forge.adapters.outbound.queue import RedisTaskQueue
from research_forge.application.ports.queue import AttemptRoute


def test_redis_stream_transport_uses_real_consumer_group_receipts() -> None:
    redis_url = os.getenv("RF_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("Set RF_TEST_REDIS_URL to run the real Redis Streams transport contract.")
    redis = pytest.importorskip("redis")
    client = redis.Redis.from_url(redis_url, decode_responses=True)
    prefix = f"research-forge:test:{uuid4().hex}"
    queue = RedisTaskQueue(client=client, stream_prefix=prefix, consumer_group="test-workers")
    try:
        queue.publish("attempt-baseline", route=AttemptRoute.BASELINE)
        queue.publish("attempt-repair", route=AttemptRoute.REPAIR)

        baseline = queue.receive(route=AttemptRoute.BASELINE, consumer_name="baseline-worker")
        repair = queue.receive(route=AttemptRoute.REPAIR, consumer_name="repair-worker")

        assert baseline is not None and baseline.attempt_id == "attempt-baseline"
        assert repair is not None and repair.attempt_id == "attempt-repair"
        queue.acknowledge(baseline)
        queue.acknowledge(repair)
    finally:
        keys = client.keys(f"{prefix}:*")
        if keys:
            client.delete(*keys)
