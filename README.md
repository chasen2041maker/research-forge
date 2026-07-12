# Research Forge

> **Project status: architecture frozen, v0.1 implementation not started.**

Research Forge is being redesigned as an evidence-gated research reproduction agent:

> **Paper + pinned repository + deterministic execution spec → auditable Git experiment → verified claims → reproducible bundle.**

中文定位：

> **一个证据门控的科研复现 Agent，把论文与固定版本代码仓库转化为可审计实验、逐条结论溯源和可复评交付包。**

## Important status notice

The current executable code under [`backend/co_scientist`](./backend/co_scientist) is the legacy AI Co-Scientist demo. It contains a fixed LangGraph research pipeline, literature retrieval, critique, experiment/code generation, paper drafting and SQLite-based branch metadata.

It does **not** yet implement the new v0.1 architecture described below:

- no PostgreSQL Mission/Task/Attempt state machine yet;
- no durable Worker Lease/Heartbeat/Crash Resume yet;
- no real baseline/candidate Git worktree workflow yet;
- no content-addressed Artifact Store yet;
- no Operation Ledger/Reconciler yet;
- no code-enforced VerifiedClaim writer boundary yet;
- no hardened standalone Sandbox Broker yet.

The old README and its historical feature descriptions are preserved at:

[旧版系统说明](./docs/legacy/旧版系统说明.md)

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

- [v0.1 科研复现智能体架构蓝图](./docs/architecture/科研复现智能体架构蓝图.md)
- [代码分层与架构治理规范](./docs/architecture/代码分层与架构治理规范.md)
- [科研复现任务规范 v1](./docs/specs/科研复现任务规范_v1.md)
- [科研复现任务规范 JSON Schema](./docs/specs/科研复现任务规范_v1.schema.json)
- [第一条无 LLM 基线复现纵向切片](./docs/specs/基线复现纵向切片规范.md)
- [GPT 第二轮架构送审包](./docs/review/GPT第二轮架构送审包.md)
- [GPT 第二轮架构审查结果](./docs/review/GPT第二轮架构审查结果.md)

### Accepted ADRs

- [架构决策记录 001：分层架构](./docs/adr/架构决策记录-001-分层架构.md)
- [架构决策记录 002：事实来源](./docs/adr/架构决策记录-002-事实来源.md)
- [架构决策记录 003：跨存储操作](./docs/adr/架构决策记录-003-跨存储操作.md)
- [架构决策记录 004：工作进程生命周期](./docs/adr/架构决策记录-004-工作进程生命周期.md)
- [架构决策记录 005：沙箱平台](./docs/adr/架构决策记录-005-沙箱平台.md)

## First implementation milestone

The first vertical slice contains no LLM, LangGraph, Skill, MCP, Reviewer, Writer or UI. It must prove:

1. a validated `ReproductionSpec` creates a Mission;
2. a durable Worker claims an Attempt using Lease/Epoch;
3. a pinned repository is executed in a baseline worktree;
4. the execution runs offline in a fixed sandbox image;
5. logs and metrics enter a local content-addressed store;
6. a forced Worker crash resumes without duplicate side effects;
7. a deterministic metric produces a replayable bundle.

Only after this slice passes Kill/Resume, Git/CAS recovery and security tests will the project add the LLM repair decision adapter.

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

## Legacy verification

The legacy suite was last run locally on 2026-07-12:

```text
150 passed, 9 skipped, 1 pytest cache warning
```

This is an engineering-regression result for the legacy code, not evidence that the new v0.1 architecture is implemented or validated.

## Development policy

- New core features go into the new `research_forge` package once implementation begins.
- The legacy `co_scientist` package receives only compatibility fixes during migration.
- New code must not import legacy internals; only an explicit legacy adapter may do so.
- The legacy implementation is removed no later than two release cycles after the new baseline slice becomes stable.
- README claims must link to code, tests, traces or immutable artifacts.

## License

A repository license has not yet been selected. Until a license is added, the repository should not be treated as granting permission to reuse or redistribute its code.
