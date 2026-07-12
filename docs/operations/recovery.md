---
title: Recovery behavior
status: active
---

# Recovery behavior

PostgreSQL remains the business source of truth. Durable Outbox records and stale Operation
redelivery help re-drive an Attempt after publisher, worker, or reconciler restart.

This is not a complete cross-store reconciler: the shipped process does not inventory CAS for
unregistered bytes, discover an already-created Git commit, repair missing references, or reclaim
all orphan resources. The Docker broker owns an atomic, checksummed completion record for each
operation under `RF_BROKER_STATE_ROOT`. A restarted broker reloads that record before attempting
execution, and rejects malformed or conflicting records instead of silently rerunning or replacing
evidence. Back up that state root with the workspace and CAS roots.

Redis is transport only, but it is no longer a destructive list head. Baseline and repair messages
use separate versioned Streams. Each consumer-group receipt stays pending until the worker
acknowledges that exact message ID; an expired receipt is reclaimed after the configured visibility
timeout, and an exhausted receipt moves atomically to its route's dead-letter Stream.

Never repair database rows, worktrees, or CAS bytes manually as an operational shortcut. Preserve
the evidence, cancel or retry through the supported lifecycle, and investigate with Audit/Outbox
records and service logs.
