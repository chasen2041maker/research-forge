---
title: Documentation index
status: active
---

# Research Forge documentation

This directory distinguishes current commitments from history and future work. A statement is
authoritative only when it is in an `active` document and backed by the implementation-status
matrix.

| Area | Current authority |
| --- | --- |
| Product | [Product overview](product/overview.md) and [Studio → Forge workflow](product/studio-forge-workflow.md) |
| Architecture | [System overview](architecture/system-overview.md), [governance](architecture/core-governance.md), and [implementation status](architecture/implementation-status.yaml) |
| Contracts | [Contract index](contracts/reproduction-spec-v1.md) and the versioned schemas in `contracts/` |
| Studio | [Overview](studio/overview.md), [code rules](studio/code-rules.md), and [limitations](studio/limitations.md) |
| Operations | [Deployment](operations/deployment.md), [recovery](operations/recovery.md), and [known limitations](operations/known-limitations.md) |
| Decisions | [ADR index](adr/README.md) |
| Roadmap | [v0.2](roadmap/v0.2.md) |
| History | [Implementation plans](history/implementation-plans/) and [reviews](history/reviews/) |

Historical material explains how a decision was reached. It is never an implementation claim or
an operational instruction. Frozen contracts remain authoritative for their explicit version, but
the runtime capability profile records the subset the current runtime can actually execute.
