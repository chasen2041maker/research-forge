---
title: Recovery behavior
status: active
---

# Recovery behavior

PostgreSQL remains the business source of truth. Durable Outbox records and stale Operation
redelivery help re-drive an Attempt after publisher, worker, or reconciler restart.

This is not a complete cross-store reconciler: the shipped process does not inventory CAS for
unregistered bytes, discover an already-created Git commit, repair missing references, or reclaim
all orphan resources. Broker-side completed-result recovery is valid only while the same broker
process remains alive. After broker restart, retry is governed by the normal Attempt and Operation
paths rather than a persisted broker result cache.

Never repair database rows, worktrees, or CAS bytes manually as an operational shortcut. Preserve
the evidence, cancel or retry through the supported lifecycle, and investigate with Audit/Outbox
records and service logs.
