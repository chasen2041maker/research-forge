# Research Forge

> **Project status: VS-001 baseline-reproduction vertical slice implemented and locally verified.**

Research Forge is being redesigned as an evidence-gated research reproduction agent:

> **Paper + pinned repository + deterministic execution spec → auditable Git experiment → verified claims → reproducible bundle.**

中文定位：

> **一个证据门控的科研复现 Agent，把论文与固定版本代码仓库转化为可审计实验、逐条结论溯源和可复评交付包。**

## Important status notice

The current executable code under [`backend/co_scientist`](./backend/co_scientist) is the legacy AI Co-Scientist demo. It contains a fixed LangGraph research pipeline, literature retrieval, critique, experiment/code generation, paper drafting and SQLite-based branch metadata.

The new [`backend/research_forge`](./backend/research_forge) package now implements the no-LLM VS-001 baseline path with a typed `ReproductionSpec` validator, durable Mission/Task/Attempt state model, lease/epoch protection, Operation Ledger, isolated Git baseline worktree, local CAS, deterministic metric/evidence gate, deterministic bundle and Worker retry handling. The legacy package remains isolated.

The production PostgreSQL/Celery deployment adapters, repair slice, API/UI and formal Linux/WSL2 Docker security gate remain separate follow-on work; they are not represented as completed by the local development runtime.

The old README and its historical feature descriptions are preserved at:

[旧版系统说明](./docs/旧版资料/旧版系统说明.md)

## v0.1 scope

v0.1 supports one bounded mission defined by a machine-validatable `ReproductionSpec`:

```text
Pinned paper artifact
  + pinned Git commit
  + immutable execution image
  + fixed argv command
  + deterministic metric pointer/tolerance
  + explicit change budget
        ↓
Baseline reproduction
        ↓
One optional repair commit and run
        ↓
Claim → Evidence → Metric Artifact → Commit
        ↓
Reproducible Research Bundle
```

v0.1 deliberately excludes Browser automation, MCP, first-class Skills, Reviewer teams, Draft PR creation, semantic long-term memory, multi-candidate search and autonomous Skill/Prompt evolution.

## Architecture documents

- [v0.1 科研复现智能体架构蓝图](./docs/架构设计/科研复现智能体架构蓝图.md)
- [代码分层与架构治理规范](./docs/架构设计/代码分层与架构治理规范.md)
- [科研复现任务规范 v1](./docs/规范/科研复现任务规范_v1.md)
- [科研复现任务规范 JSON Schema](./docs/规范/科研复现任务规范_v1.schema.json)
- [第一条无 LLM 基线复现纵向切片](./docs/规范/基线复现纵向切片规范.md)
- [GPT 第二轮架构送审包](./docs/架构审查/GPT第二轮架构送审包.md)
- [GPT 第二轮架构审查结果](./docs/架构审查/GPT第二轮架构审查结果.md)

### Accepted ADRs

- [架构决策记录 001：分层架构](./docs/架构决策记录/架构决策记录-001-分层架构.md)
- [架构决策记录 002：事实来源](./docs/架构决策记录/架构决策记录-002-事实来源.md)
- [架构决策记录 003：跨存储操作](./docs/架构决策记录/架构决策记录-003-跨存储操作.md)
- [架构决策记录 004：工作进程生命周期](./docs/架构决策记录/架构决策记录-004-工作进程生命周期.md)
- [架构决策记录 005：沙箱平台](./docs/架构决策记录/架构决策记录-005-沙箱平台.md)

## First implementation milestone

The first vertical slice contains no LLM, LangGraph, Skill, MCP, Reviewer, Writer or UI. It now proves in the local deterministic test runtime:

1. a validated `ReproductionSpec` creates a Mission;
2. a durable Worker claims an Attempt using Lease/Epoch;
3. a pinned repository is executed in a baseline worktree;
4. the execution runs offline in a fixed sandbox image;
5. logs and metrics enter a local content-addressed store;
6. a forced Worker crash resumes without duplicate side effects;
7. a deterministic metric produces a replayable bundle.

Only after this slice passes its formal Linux/WSL2 Docker security gate will the project add the LLM repair decision adapter.

### VS-001 local verification

```powershell
python -m pytest backend/tests/research_forge -q
python -m ruff check backend/research_forge backend/tests/research_forge
```

The end-to-end test creates a pinned Git fixture, executes the fixed command through the development-only argv runner, verifies `/accuracy`, writes log/metric/bundle artifacts to CAS, extracts the bundled source archive and replays the command. The Docker Broker implementation is intentionally restricted to Linux/WSL2; Windows native development does not constitute the formal container-security acceptance gate.

## Architecture rules

```text
Inbound Adapters → Application → Domain
Decision Adapter → DecisionEngine Port
Outbound Adapters → Application Ports
Bootstrap → Composition Root
```

Key constraints:

- Application is the only side-effect orchestration center;
- decision code can only return `ActionProposal`;
- PostgreSQL owns business state;
- Git owns code state;
- the content-addressed store owns Artifact bytes;
- LangGraph checkpoints only own temporary Attempt context;
- API routes cannot access ORM/Git/Docker/LLM directly;
- decision adapters cannot access Git/Sandbox/Artifact/Queue/Repository;
- architecture import rules must be enforced in CI.

## Planned delivery

The reviewed estimate is 10 weeks of core development plus a 2-week release buffer:

1. scope, ADRs and fixture;
2. layered skeleton and architecture CI;
3. transactional state and Operation Ledger;
4. durable Worker lifecycle;
5. Git worktree and local CAS recovery;
6. hardened sandbox and no-LLM baseline slice;
7. Evidence Gate;
8. security and environment preparation;
9. one bounded LLM repair slice;
10. minimal API/UI;
11. evaluation and hardening;
12. documentation, demo and release.

## Verification

The combined suite was last run locally on 2026-07-12:

```text
176 passed, 9 skipped
```

This includes the new VS-001 domain, recovery, architecture and local end-to-end tests. Formal Docker/WSL2 security validation remains an environment-specific release gate.

## Development policy

- New core features go into the new `research_forge` package once implementation begins.
- The legacy `co_scientist` package receives only compatibility fixes during migration.
- New code must not import legacy internals; only an explicit legacy adapter may do so.
- The legacy implementation is removed no later than two release cycles after the new baseline slice becomes stable.
- README claims must link to code, tests, traces or immutable artifacts.

## License

A repository license has not yet been selected. Until a license is added, the repository should not be treated as granting permission to reuse or redistribute its code.
