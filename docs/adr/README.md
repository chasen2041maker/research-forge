---
title: Architecture decision index
status: active
---

# Architecture decisions

| ADR | Status | Decision |
| --- | --- | --- |
| [0001](0001-application-centric-hexagonal.md) | Accepted / implemented | Forge uses application-centric hexagonal boundaries. |
| [0002](0002-source-of-truth.md) | Accepted / partially implemented | PostgreSQL, Git, and CAS have distinct ownership. |
| [0003](0003-operation-ledger.md) | Accepted / partially implemented | Operations make recovery explicit; only stale-operation redelivery is shipped. |
| [0004](0004-worker-lifecycle.md) | Accepted / partially implemented | Lease, heartbeat, retry, cancellation, and resume rules. |
| [0005](0005-sandbox-platform.md) | Accepted / partially implemented | Linux/WSL2 and a dedicated broker are the formal execution boundary. |
| [0006](0006-studio-and-forge-boundary.md) | Accepted | Studio and Forge remain separately owned products. |
| [0007](0007-versioned-handoff-contracts.md) | Accepted | The product boundary uses versioned JSON contracts only. |

An accepted ADR describes the chosen direction. Its `Status` and the implementation-status matrix
state whether every part is currently delivered.
