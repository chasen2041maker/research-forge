---
title: Studio to Forge workflow
status: active
---

# Studio → Forge workflow

> Agent proposes; the Forge verifies.

1. A user explores a question in Research Studio.
2. Studio emits `ResearchProposal v1` with `status: UNVERIFIED`.
3. A human explicitly supplies every execution prerequisite missing from the proposal.
4. The gateway compiles that input into the existing frozen `ReproductionSpec v1`.
5. Forge creates a normal Mission, then applies its ordinary pin, sandbox, metric, evidence, and
   Bundle gates.
6. Only a completed evidence chain can become `VerifiedResult v1`; Forge exposes it at
   `GET /v1/missions/{mission_id}/verified-result` only for Missions created from a Studio handoff.
7. Studio's public writer projects those contract facts read-only and never consults Studio state or
   an LLM to reinterpret them.

A proposal, a running Mission, and a worker log are not verified results. The handoff does not
copy a suggested repository, command, hash, metric, or budget into an execution spec without an
explicit confirmation.
