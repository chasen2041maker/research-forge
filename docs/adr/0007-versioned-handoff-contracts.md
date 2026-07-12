---
title: Versioned handoff contracts
status: accepted
---

# ADR-0007: Use versioned JSON contracts for the Studio → Forge handoff

## Decision

The handoff uses `ResearchProposal v1`, `ReproductionSpec v1`, and `VerifiedResult v1`. A Proposal
is always `UNVERIFIED`. A human must explicitly supply every frozen execution prerequisite before
the gateway can compile a normal Forge `ReproductionSpec v1`.

## Consequences

The gateway never chooses a commit, hash, image, command, metric, or budget on a user's behalf.
Forge retains all existing schema and prerequisite validation. Report-back to Studio consumes the
verified-result contract rather than Forge's internal read models.
