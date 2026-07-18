---
title: Research Forge product overview
status: active
---

# Research Forge product overview

## Product promise

Research Forge combines two systems without blurring their truth claims:

- **Research Studio** explores. Its multi-agent engineering workbench can suggest papers, gaps,
  hypotheses, experiment plans, and code, but every exported result is `UNVERIFIED`.
- **Forge Runtime** verifies. It accepts a frozen `ReproductionSpec v1`, checks the pinned
  prerequisites, runs the normal execution/evidence workflow, and exposes a result as verified
  only after metric and Bundle closure.

The product is not a single agent that “does research and claims success.” It is a handoff from
creative exploration to bounded verification.

```text
                 Research Studio (agent workbench, /studio)
  user question -> LangGraph exploration -> completed snapshot
                                         |
                                         | ResearchProposal v1 (UNVERIFIED JSON)
                                         v
                   human confirms all missing execution facts
                                         |
                                         v
                 Research Gateway (one-way Spec Builder)
                                         |
                                         | exact ReproductionSpec v1
                                         v
                  Forge Runtime (/forge, normal Mission path)
  pin checks -> Mission -> Attempt -> sandbox -> metric -> evidence -> Bundle
                                         |
                                         v
                         VerifiedResult v1 (read-only Studio report)
```

## Ownership and boundaries

| Package / route | Owns | May not own or import |
| --- | --- | --- |
| `backend/co_scientist`, `/studio` | Exploration, literature, planning, Agent Trace UI/API. | Forge runtime code or verification state. |
| `backend/research_forge`, `/forge` | Frozen Mission lifecycle, persistence, execution, metrics, evidence, Bundle. | Studio graph, state, modules, or LLM-driven research choices. |
| `backend/research_contracts` | Versioned JSON contracts only. | Either product package. |
| `backend/research_gateway` | Handoff compilation and verified-report shaping. | Studio internals or Forge implementation modules. |

The CI AST gate enforces these rules, including a frozen `ResearchState` field set. New handoff
data belongs in the contract and public snapshot translation, never in the Studio graph state.

## Contract lifecycle

### 1. Studio export

After a run reaches `done`, Studio exposes:

```text
GET /api/research/{fork_id}/proposal
```

The route translates its existing final snapshot through `co_scientist.public_api`. It returns
`ResearchProposal v1`, whose schema requires `status: "UNVERIFIED"`. Candidate repository fields,
paper references, objectives, and command suggestions are context for a human; they are not trusted
execution inputs.

### 2. Explicit human completion

The user supplies all data required by the pre-existing frozen `ReproductionSpec v1`:

- paper artifact ID, SHA-256, and extraction profile;
- an existing local repository directory and a full commit SHA;
- approved image digest, setup/run argv, working directory, timeout, and network policy;
- metric path, JSON pointer, comparator, expected value, tolerance, and unit;
- allowed changes and all runtime/cost/artifact/log budgets.

No Studio field silently fills one of these values. The gateway checks that all fields marked
missing by the Proposal have been confirmed.

### 3. Normal Forge Mission creation

The Forge API owns this route:

```text
POST /v1/proposals/handoff
Authorization: Bearer <local-token>
{
  "proposal": { "...": "ResearchProposal v1" },
  "completion": { "...": "all ReproductionSpec v1 fields except schema_version" }
}
```

The gateway compiles the completion into the exact existing `ReproductionSpec v1` payload, then the
route calls the same `MissionController.create` path as `POST /v1/missions`. JSON Schema checks,
semantic rules, pinned paper/repository/image prerequisites, and all later evidence gates remain in
force.

## Verification semantics

`UNVERIFIED` and `VERIFIED` are product semantics, not visual labels.

- A Proposal can be helpful, specific, and backed by references while still being unverified.
- A Mission being created or running is not a verified result.
- `VerifiedResult v1` must point to the Mission, frozen spec SHA-256, observed metric, Bundle SHA-256,
  and completion time. `research_gateway.verified_report` deliberately requires those values.

Forge now exposes `GET /v1/missions/{mission_id}/verified-result` only after it finds the completed
Mission, registered Bundle, metric record, VERIFIED claims, and persisted Studio proposal link in
its source of truth. Studio's public writer consumes only that contract and never duplicates Forge
status in Studio memory.

## Current scope and non-goals

This integration does not merge the two codebases, add an LLM to Forge Runtime, make Studio output
authoritative, or run an agent without a human-approved frozen specification. It makes the existing
systems usable as one product while preserving their different responsibilities.
