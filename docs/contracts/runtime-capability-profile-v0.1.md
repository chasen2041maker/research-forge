---
title: Runtime capability profile v0.1
status: active
---

# Runtime capability profile v0.1

`ReproductionSpec v1` is frozen. This profile, not a schema mutation, records the currently
executable VS-001 subset.

| Contract area | Executable now | Reserved / not implemented |
| --- | --- | --- |
| Repository | Existing local repository directory at a full pinned commit. | Fetching a remote Git URL. |
| Setup mode | `prebuilt`. | `lockfile` setup execution. |
| Network | `offline`, with no allowed domains. | `allowlisted`. |
| Modes | `reproduce` and bounded `repair`. | `ablation`. |
| Setup argv | Stored in the Spec. | A separate executed setup phase. |
| Budget | Per-run timeout, artifact and log checks. | Mission-wide stopwatch and enforced `max_cost_usd` cost meter. |
| Dataset pin | Fixture flow only. | Production dataset-hash verification; formal Docker fixture hash is placeholder data. |

Applications must validate the frozen schema and then honor this profile. A schema-valid value that
is outside the profile is correctly rejected by the runtime.
