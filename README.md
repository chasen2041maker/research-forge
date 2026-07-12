<p align="center">
  <strong>RESEARCH FORGE</strong><br />
  Evidence-gated research reproduction for pinned experiments.
</p>

<p align="center">
  <a href="README.zh-CN.md">中文说明</a>
</p>

<p align="center">
  <a href="https://github.com/chasen2041maker/research-forge/actions/workflows/research-forge.yml"><img src="https://github.com/chasen2041maker/research-forge/actions/workflows/research-forge.yml/badge.svg" alt="Research Forge gates" /></a>
  <img src="https://img.shields.io/badge/execution-offline%20by%20default-22c55e" alt="Offline by default" />
  <img src="https://img.shields.io/badge/evidence-deterministic-06b6d4" alt="Deterministic evidence" />
  <img src="https://img.shields.io/badge/runtime-Python%203.11%2B-6366f1" alt="Python 3.11+" />
</p>

> Turn a paper, pinned repository, immutable execution specification, and fixed metric into an auditable experiment and a replayable bundle.

Research Forge is a product for turning research directions into reproducible claims. It does not accept a result merely because a worker says it passed: the result must be tied to a pinned commit, an operation ledger, content-addressed artifacts, a deterministic metric, evidence links, and a reproducible Bundle.

**Status:** the VS-001 baseline slice, bounded repair workflow, durable approval flow, Studio → Forge JSON handoff, minimal local API/UI, SQLAlchemy persistence adapter, Alembic revisions, Linux Docker gate, and host-process production composition/runbook are implemented and covered by CI. The legacy AI Co-Scientist demo remains isolated under [`backend/co_scientist`](backend/co_scientist).

## One product, two honest modes

| Mode | Responsibility | Output status |
| --- | --- | --- |
| [Research Studio](frontend/src/app/studio/page.tsx) | Explore a question, literature, gaps, critiques, experiment plans, and code suggestions. | `UNVERIFIED` `ResearchProposal v1` |
| [Forge Runtime](frontend/src/app/forge/page.tsx) | Freeze a spec, validate pins and budgets, execute, extract metrics, close evidence, and seal a Bundle. | `VERIFIED` `VerifiedResult v1` only after normal closure |
| Full research loop | Let a user explicitly complete Studio's missing hashes, commit, image, command, metric, and budgets before creating a normal Forge Mission. | Proposal stays unverified until Forge proves it |

```text
Research Studio                         Forge Runtime
question -> papers -> plan      Proposal + human completion -> frozen ReproductionSpec
            |                                |
            +--- UNVERIFIED JSON ----------->| pinned Git -> sandbox -> metric -> evidence -> Bundle
```

Studio and Forge do not import each other's graph, state, workers, adapters, or persistence
models. Their shared vocabulary lives in [`backend/research_contracts`](backend/research_contracts),
and the one-way handoff lives in [`backend/research_gateway`](backend/research_gateway). See the
[product architecture](docs/product/overview.md) for the boundary and API details.

## Why Research Forge?

Research automation often breaks in quiet ways:

- a metric is reported without the exact commit that produced it;
- a killed worker retries a side effect and writes conflicting state;
- an artifact changes after a result has been accepted;
- a candidate patch is committed without an attributable human approval;
- a dashboard presents process-local state as if it were durable truth.

Research Forge is deliberately narrow. It gives a reproduction Mission a deterministic proof path before it becomes complete.

```text
Frozen ReproductionSpec
        |
        v
Mission -> Task -> lease-owned Attempt -> Operation Ledger
        |                                  |
        |                                  v
        +-> pinned Git worktree -> offline sandbox -> CAS artifacts
                                                    |
                                                    v
                                  metric -> claim -> evidence -> Bundle
```

## What works today

- `ReproductionSpec v1` JSON-schema validation, cross-field rules, prerequisite checks, and immutable normalized specs.
- Durable Mission / Task / Attempt state, optimistic versions, lease epochs, heartbeats, cancellation, Audit events, and Outbox events. A running sandbox Attempt renews its lease every 10 seconds; its monitor stops before finalization so the final optimistic version is fixed.
- Git baseline and bounded candidate worktrees with idempotent operation records and strict patch budgets.
- Content-addressed artifacts with SHA-256 verification and safe deterministic Bundle replay extraction; each Bundle preserves original and normalized Specs plus a structured evaluation report bound to the Spec hash.
- Offline Docker execution on Linux with no network, read-only root filesystem, dropped capabilities, non-root user, and a broker boundary.
- Deterministic metric extraction, verified claims, and evidence closure.
- One bounded repair flow: proposal -> persisted approval -> fresh child Attempt -> candidate commit -> candidate run -> evidence-gated Bundle.
- FastAPI local-token surface for Mission status, cancellation, Bundle download, approval decisions, and `POST /v1/proposals/handoff`; the Next.js Forge console reads that state without owning it.
- A versioned JSON-only bridge: a completed Studio run can be exported at `GET /api/research/{fork_id}/proposal`, then a user-confirmed completion form is compiled into Forge's unchanged `ReproductionSpec v1` Mission boundary.
- SQLAlchemy source-of-truth adapter, static Alembic revisions, CI migration upgrade/downgrade verification, and a real PostgreSQL service gate.
- A frozen 16-case release manifest with the baseline end-to-end proof repeated 10 times, repeated recovery cases, append-only JSON reports, and a retained GitHub Actions artifact.
- Structured JSON host-process logs with bounded correlation fields and credential redaction; durable Audit/Outbox records remain the business evidence source.

## 10-second proof

From the repository root:

```powershell
python -m pip install -r deploy/research-forge/requirements.txt httpx mypy pytest ruff
python -m pytest backend/tests/research_forge backend/tests/research_integration -q
python -m ruff check backend/research_forge backend/research_contracts backend/research_gateway backend/co_scientist/public_api backend/tests/research_forge backend/tests/research_integration
python backend/scripts/run_frozen_research_forge_eval.py
```

Build the Forge console:

```powershell
cd frontend
npm install
npm run build
```

The GitHub Actions workflow runs mypy, secret scanning, the non-Docker suite, architecture checks, Alembic upgrade/downgrade contract, a separate Linux Docker end-to-end gate, dependency vulnerability and license reports, and the 16-case frozen evaluation manifest on every push to `main`. The custom AST checks enforce the inbound/outbound/decision boundary, platform SDK ownership, public-signature shape, and an acyclic internal import graph. The evaluation job retains a JSON artifact containing all Case outcomes and the Manifest SHA-256.

## Studio → Forge handoff

1. Finish an exploration in Studio, then request `GET /api/research/{fork_id}/proposal`.
2. Treat the returned `ResearchProposal v1` as a suggestion: it is structurally marked `UNVERIFIED`.
3. In the Forge console's **Studio → Forge handoff** panel, paste the Proposal and explicitly fill
   the paper artifact hash, repository commit, image digest, setup/run arguments, metric, change
   budget, and runtime budgets.
4. Forge calls the normal frozen-spec Mission creation path. It still rejects invalid JSON Schema,
   unavailable pins, disallowed images, and unsatisfied prerequisite checks.

The handoff never copies a Studio suggestion into an execution field automatically. After normal
evidence closure, a handoff Mission exposes read-only `VerifiedResult v1` facts; this does not
change the evidence gate or make unverified output look verified.

## Product demos

Run the three deterministic handoff, verified-result, and bounded-repair demonstrations with:

```bash
python backend/scripts/run_research_forge_demos.py --output-dir artifacts/demo-reports
```

See [product-demos.md](docs/operations/product-demos.md) for the exact evidence each demo covers.

## Core concepts

| Concept | What it means | Why it matters |
| --- | --- | --- |
| `Mission` | Immutable normalized reproduction specification and top-level lifecycle. | Gives every result a stable identity. |
| `Task` / `Attempt` | Work unit and a specific lease-owned execution. | Old workers cannot finalize newer work. |
| `Operation` | Idempotency record around a cross-store effect. | Recovery does not duplicate Git, CAS, or sandbox effects. |
| CAS artifact | SHA-256-addressed execution log, metric, source archive, or Bundle. | Artifact tampering is detectable. |
| Claim + Evidence | A metric statement linked to the artifacts that support it. | The UI does not display unsupported results as facts. |
| Approval | Durable decision for one high-risk repair patch hash. | Workers exit instead of blocking; a changed patch cannot reuse approval. |
| Bundle | Deterministic replay deliverable. | A completed Mission can be independently checked. |

## Quick architecture

```text
Inbound API / Worker
        |
        v
Application use cases
        |
        +--> Domain: Mission, Attempt, Approval, Operation, Evidence
        |
        +--> Ports: UoW, Git, Sandbox, CAS, Decision Engine
                    |
                    v
          PostgreSQL / Git / Docker broker / local CAS
```

The architecture is application-centric by design:

- FastAPI routes and workers call use cases only; they do not access ORM, Git, Docker, or the Decision Engine's side-effect capabilities directly.
- PostgreSQL is the business source of truth; Git owns code state; CAS owns artifact bytes.
- A `DecisionEngine` returns an untrusted `ActionProposal` only. Application policy validates its path budget, approval, patch hash, and operation ledger before Git can commit.
- Windows native is a UI/development environment. Formal container-security acceptance is Linux/WSL2 only.

## Repair flow

```text
Baseline validation fails in repair mode
        |
        v
Repair worker reads verified baseline log
        |
        v
DecisionEngine proposes exactly one patch
        |
        v
Approval persists patch SHA-256; worker exits
        |
        v
Reviewer approves -> child Attempt + Outbox event
        |
        v
Repair worker verifies matching patch, commits once, runs once, validates metric
```

The included adapter for tests is deterministic (`FixedPatchDecisionEngine`). No LLM repair runtime is represented as shipped; an LLM-based decision adapter must satisfy the same narrow `DecisionEngine` port and cannot receive Git, Docker, CAS, Queue, or database capabilities.

## Forge console

The Next.js UI at [`frontend/src/app/forge`](frontend/src/app/forge) is a local control plane rather than a second source of truth. It provides:

1. Mission creation from a frozen spec and local API token.
2. Durable Task / Attempt timeline with lease epochs and failure status.
3. Explicit high-risk patch approvals with reviewer identity.
4. Verified Bundle download only after evidence closure.

The local API defaults to loopback and requires a Bearer token. CORS is restricted to configured local origins.

## Security boundaries

- Formal run stages use `--network none`; no run-stage network is allowed.
- The Docker broker runs as a separate Unix-socket service and is the only process that invokes Docker. API, Outbox publisher, and worker roles do not receive the Docker socket; the worker can send only typed offline requests to the broker.
- Candidate commits are limited by allowed paths, file count, changed lines, one commit, and one run.
- Archive extraction rejects traversal, absolute paths, links, and unexpected members.
- The approval record binds scope, task, parent Attempt, decision identity, expiry, and the exact patch hash.
- Cancel, lease loss, stale epoch, and stale optimistic version are durable state transitions, not UI flags; a running cancellation stops the broker operation before the worker acknowledges its queue delivery.

## Project map

```text
backend/research_forge/
  domain/                 Mission, approval, operation, artifact, evidence rules
  application/            use cases, DTOs, and ports
  adapters/inbound/       FastAPI and lease-owned workers
  adapters/outbound/      SQLAlchemy, Git, CAS, sandbox, system adapters
  bootstrap/              explicit composition roots
frontend/src/app/forge/   local evidence-gated console
docs/                     specification, ADRs, architecture, and review material
```

## Roadmap and boundaries

Implemented core work is intentionally focused on reproducibility and recoverability. Planned work should build on these invariants rather than bypass them:

- A narrowly-capable LLM `DecisionEngine` adapter, only after its policy and supply-chain gates are in place.
- Documentation and demo fixtures for a clean-machine local run.

Research Forge does **not** currently claim browser automation, MCP, Skills, multi-candidate search, autonomous PR creation, or general scientific writing as v0.1 features.

## Further reading

- [ReproductionSpec v1](docs/contracts/reproduction-spec-v1.md)
- [Historical VS-001 implementation plan](docs/history/implementation-plans/vs-001.md)
- [System overview](docs/architecture/system-overview.md)
- [Layering and governance rules](docs/architecture/core-governance.md)
- [Architecture decisions](docs/adr/README.md)
- [Production deployment and recovery runbook](docs/operations/deployment.md)
- [Research Studio notes](docs/studio/overview.md)

## License

Licensed under the [Apache License 2.0](LICENSE).
