---
title: Research Studio limitations
status: active
---

# Research Studio limitations

Research Studio is an **Agent engineering demonstration workbench**, not an autonomous scientific
authority. Its Agent Trace records what the system selected, called, observed, and degraded during
a run; it does not turn a model judgement into external scientific evidence.

## Claims we make

- Studio suggestions, citations, critiques, generated code, and dry runs are not Forge-verified
  research results.
- Its in-memory run snapshots and Agent Trace are a UI/API convenience, not Forge business truth.
- Studio does not create a Forge Mission without a human-completed frozen specification.
- The read-only verified-result report reads Forge's completed source of truth rather than
  duplicating mutable Mission status inside Studio; it cannot create or alter a Forge Mission.
- The code runner is a bounded **teaching executor**. A successful exit is execution feedback, not
  proof that a scientific claim is correct.

## Claims we deliberately do not make

| Area | Accurate name | Why |
| --- | --- | --- |
| M0 topic cards | LLM-generated candidate research directions | M0 does not retrieve papers before estimating a candidate gap. |
| M5 checklist | Deterministic experiment-plan validator | Its core checks are rules, not another LLM Agent. |
| M5.5 | Gate recommendation / branch proposal | It writes a recommendation; the base LangGraph does not automatically loop backwards. |
| M8 forks | Logical research forks and checkpoint comparison | Branches are currently scheduled serially and their winner may use model ratings. |
| Writer citations | Citation-constrained generation | Allowed references are prompted, but claims are not individually entailment-verified. |
| Memory / A-B / skills | Experimental adaptive memory and skill prototype | Feedback includes model ratings and exit status, not independent quality validation. |
| Studio runtime | Agent engineering demonstration workbench | It uses process-local run snapshots and is not a durable production workflow system. |

For formal experiment execution, sandbox hardening, durable recovery, and verified outputs, use
[Forge Runtime](../product/overview.md).
