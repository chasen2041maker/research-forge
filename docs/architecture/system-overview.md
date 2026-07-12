---
title: System overview
status: active
---

# System overview

Research Forge is one product with two separately owned systems:

```text
Research Studio                    Research Forge Runtime
co_scientist                       research_forge
explore and propose                execute and verify
          |                                    ^
          +-- versioned JSON contracts --------+
```

Studio owns multi-agent exploration and exports only `UNVERIFIED` proposals. Forge owns frozen
Mission creation, pinned prerequisites, execution, durable business state, metric extraction,
evidence, and Bundle generation. The systems do not share internal state, a business database, or
implementation imports.

Within Forge, the dependency direction is `Inbound → Application → Domain`. Outbound adapters own
technology integrations; Bootstrap is the composition root. The active architectural decisions are
listed in [ADR](../adr/README.md). Actual delivery status is in
[implementation-status.yaml](implementation-status.yaml), not inferred from an ADR's intended end
state.
