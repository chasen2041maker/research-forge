---
title: VerifiedResult v1
status: active
---

# VerifiedResult v1

Schema: [verified-result-v1.schema.json](verified-result-v1.schema.json)

`VerifiedResult v1` is a portable report of a Forge-completed Mission. It names the originating
Proposal, Mission, frozen Spec SHA-256, observed metric, evidence Bundle SHA-256, and completion
time. Its status is the literal `VERIFIED`.

This contract is not emitted by a worker merely because a command returned zero. It is valid only
after Forge's normal metric and evidence closure. Studio consumes it read-only.

For a Mission created through `POST /v1/proposals/handoff`, Forge persists the originating
`proposal_id` separately from the frozen specification and exposes this contract at
`GET /v1/missions/{mission_id}/verified-result`. The endpoint returns `409` until the completed
Mission, Bundle, Metric, and VERIFIED claims are all present. A normal direct Mission has no Studio
proposal identity and cannot be relabeled as a Studio verified result.
