---
title: Research Studio code rules
status: active
---

# Research Studio code rules

`co_scientist` is the legacy Research Studio. New integrations must consume only
`co_scientist.public_api` and transport versioned JSON contracts.

Do not import `research_forge` from Studio. Do not make the Forge gateway import
`co_scientist.graph`, `co_scientist.state`, or `co_scientist.modules.*`. Do not add handoff fields
to `ResearchState`; translate an existing completed snapshot at the public boundary instead.

Studio output must retain `UNVERIFIED` status until Forge independently closes evidence.
