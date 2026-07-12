# Research gateway: Studio → Forge

This package is the one-way handoff boundary. It accepts an `UNVERIFIED` `ResearchProposal v1`
and a separate, human-confirmed completion form. It produces the exact frozen `ReproductionSpec
v1` payload already accepted by Forge's normal Mission endpoint.

The gateway never imports `co_scientist.graph`, `co_scientist.state`, or `co_scientist.modules`.
It does not start workers, choose commands, invent hashes, or mark a result verified. Forge still
performs schema validation, prerequisite pin checks, execution, metric extraction, evidence
closure, and Bundle creation.
