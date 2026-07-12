---
title: Runtime known limitations
status: active
---

# Runtime known limitations

- The VS-001 runtime accepts existing local repository directories; a generic remote Git URL is
  not currently fetched by the prerequisite verifier.
- Schema values `execution.setup_mode: lockfile` and `network_policy: allowlisted` are reserved by
  `ReproductionSpec v1`, but the current runtime accepts only `prebuilt` and `offline`.
- `setup_argv` is captured in the frozen spec but is not executed as a separate setup phase.
- Mission wall-clock budget is checked against the per-run timeout; it is not yet a global
  multi-attempt stopwatch. `max_cost_usd` is recorded but not an enforced runtime cost meter.
- Redis transport is a queue of Attempt IDs, not Redis Streams; it has no versioned envelope,
  visibility timeout, routing, or dead-letter queue.
- The formal Docker fixture path still has a placeholder dataset hash. Do not claim dataset-pinned
  external reproduction coverage.
