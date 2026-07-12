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
after Forge's normal metric and evidence closure. A future Studio report consumes it read-only.
