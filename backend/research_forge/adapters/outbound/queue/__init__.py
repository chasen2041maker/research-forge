"""Queue transport adapters."""

from research_forge.adapters.outbound.queue.immediate import ImmediateQueue
from research_forge.adapters.outbound.queue.redis_queue import RedisTaskQueue

__all__ = ["ImmediateQueue", "RedisTaskQueue"]
