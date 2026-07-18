---
title: Research Studio capability guide
status: active
---

# Research Studio: Agent Capability Guide

Research Studio is a multi-agent research orchestration workbench. It is designed to make Agent
behaviour inspectable: who was selected, what tool path ran, when parallel reviewers disagreed,
and why the system continued in a degraded state.

It creates **research proposals**, not verified scientific facts. Forge Runtime owns the separate
execution and evidence gate described in [the product overview](../product/overview.md).

## Start here

| If you want to inspect | Read or run |
| --- | --- |
| Specialist routing and parallel critique | [Agent topology](agent-topology.md) and `examples/studio/dynamic-review/` |
| Multi-source retrieval pipeline | `examples/studio/research-retrieval/` |
| Bounded code-feedback loop | `examples/studio/code-repair/` |
| UI-visible runtime decisions | `/studio` → **Agent Trace** |
| Contract with Forge | [Studio → Forge workflow](../product/studio-forge-workflow.md) |
| Explicit non-claims | [Known limitations](limitations.md) |

## What is proved by code, tests, and demos

| Capability | Evidence | What the trace makes visible |
| --- | --- | --- |
| Dynamic specialist routing | `m4_critique/orchestrator.py`, `tests/studio/test_agent_events.py` | selected reviewers, reason, fallback flag |
| Isolated parallel reviews | `m4_critique/roundtable.py` and dynamic-review demo | reviewer start/completion, rating, variance |
| Disagreement-driven critique | `compute_variance()` and Round 2 Devil reviewer | threshold, variance, whether Round 2 triggered |
| Multi-source retrieval | `m2_retriever/retriever.py` and retrieval demo | rewritten queries, source counts, RRF/rerank stages |
| Human control | `/api/topics/discover`, `/api/m1/clarify`, Studio UI | selected topic and clarification remain user input |
| Code-feedback repair | `m6_code/code_gen.py` and code-repair demo | failed execution, bounded repair proposal, applied repair |

The trace never claims token/cost values that a provider did not return. Unknown values are `null`,
and a provider/model fallback is explicitly marked.

## Run the proof pack

```powershell
python backend/scripts/run_studio_capability_demos.py --output-dir artifacts/studio-capability-demos
python -m pytest backend/tests/studio -q
```

The demos are deterministic and use local fixtures only; they do not need an LLM key, Docker
daemon, or external research API. See each example directory for its expected event sequence.

## Event contract

Every event has a run and step identity plus role, type, model role, safe summaries, duration,
optional token/cost values, fallback marker, and parent step. The API exposes the bounded event
list at:

```text
GET /api/research/{fork_id}/trace
```

The WebSocket and ordinary status response include the same list so the UI does not invent an
execution history. The event taxonomy and topology are in [agent-topology.md](agent-topology.md).

## Status semantics

Studio distinguishes `SUCCEEDED`, `DEGRADED`, `FAILED`, and `SKIPPED` in trace/progress data.
`DEGRADED` means a stage failed but the teaching workflow continued with an explicit error record;
it is not displayed as green success.

## Scope boundary

Studio may propose, summarize, and demonstrate. It must not assign itself a verified result,
silently construct a frozen Forge specification, or execute a high-risk patch as an authoritative
research result. Those steps require an explicit human handoff to Forge Runtime.
