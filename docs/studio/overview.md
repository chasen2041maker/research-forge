---
title: Research Studio overview
status: active
---

# Research Studio

Research Studio is the product name for the legacy `backend/co_scientist` application. It is kept
because it demonstrates the research-exploration side of the product:

- multi-agent question refinement, literature retrieval, knowledge/gap construction, critique, and
  experiment planning;
- optional code-generation suggestions and a visual exploration workspace;
- research forks and progress snapshots for an interactive Studio session.

Studio is not the verification runtime. Its proposals, references, generated code, and task status
are `UNVERIFIED` until a separately owned Forge Mission closes the required evidence chain.

## Local development boundary

The Studio API and UI remain independently runnable. Consumers use `co_scientist.public_api` to
export a completed snapshot as `ResearchProposal v1`; they must not import Studio's LangGraph,
`ResearchState`, or modules. The handoff contract is described in
[Studio → Forge workflow](../product/studio-forge-workflow.md).

## License

The repository is licensed under [Apache-2.0](../../LICENSE). No separate MIT license applies to
this legacy component.
