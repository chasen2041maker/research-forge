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
- Production does not configure an LLM provider or repair worker process. A separately reviewed
  repair worker may consume only the repair Stream and must execute the exact persisted, approved
  PATCH artifact; it must not regenerate a patch after approval.
- The formal Docker fixture path still has a placeholder dataset hash. Do not claim dataset-pinned
  external reproduction coverage.
