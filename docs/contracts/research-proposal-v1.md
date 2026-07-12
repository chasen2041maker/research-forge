---
title: ResearchProposal v1
status: active
---

# ResearchProposal v1

Schema: [research-proposal-v1.schema.json](research-proposal-v1.schema.json)

`ResearchProposal v1` is a versioned, JSON-transportable direction exported by Research Studio.
It carries a research question, hypothesis, paper references, optional repository/command hints,
and a list of fields that a human must still confirm. Its `status` is the literal `UNVERIFIED`.

The contract does not create a Forge Mission and cannot prove a claim. A consumer must not treat
suggested hashes, repositories, commands, metrics, or budgets as frozen execution values. Those
values are supplied by an explicit completion form before Forge validates the ordinary
`ReproductionSpec v1`.

Incompatible changes require `ResearchProposal v2`; do not modify v1 in place.
