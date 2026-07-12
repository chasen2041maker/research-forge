---
title: Studio and Forge boundary
status: accepted
---

# ADR-0006: Keep Research Studio and Forge Runtime separate

## Context

The repository contains a legacy multi-agent exploration system and a newer deterministic runtime.
Treating the former as a Forge internal would make language-model suggestions look like durable
evidence and would couple two incompatible state models.

## Decision

- `co_scientist` is Research Studio: it explores and produces only `UNVERIFIED` output.
- `research_forge` is Forge Runtime: it owns frozen Missions and can produce a verified result only
  after its ordinary evidence gate closes.
- Neither product imports the other's graph, state, workers, adapters, or persistence models.
- Neither product shares the other's business database.

## Consequences

The product can expose a unified UI without merging its truth boundaries. Integration is limited to
versioned transport contracts and tests that enforce the import boundary.
