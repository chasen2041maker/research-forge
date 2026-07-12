# Research Forge v0.1 — GPT 第二轮架构送审包

> **Review status：Completed on 2026-07-12.** 第二轮结论仍为 `CONDITIONAL GO`，风险显著下降。三个剩余 Blocker 已在架构文档、Spec 和 ADR 中处理：ReproductionSpec v1、DecisionEngine Adapter 边界、DB/Git/CAS Operation Ledger。下一步不再进行第三轮大文档循环，而是实现无 LLM Baseline Vertical Slice。
>
> 用途：对首轮审查后的收缩方案和代码分层规范进行第二轮严格审查。
>
> 主设计：`docs/架构设计/科研复现智能体架构蓝图.md`
>
> 分层规范：`docs/架构设计/代码分层与架构治理规范.md`
>
> 仓库：https://github.com/chasen2041maker/research-forge

---

## 1. 首轮审查结论

首轮 GPT-5.6 Thinking 审查给出：

> **CONDITIONAL GO。完整 Agent OS 蓝图 NO-GO；收缩后的证据门控科研复现 Agent GO。**

首轮指出的核心问题：

1. 一个项目同时承担 Agent OS、科研、Coding、Browser、Skill/MCP/Memory 生态，范围失控；
2. 与 DeerFlow、Agent Zero、DeepScientist、Letta、OpenHands、RD-Agent 同质化；
3. LangGraph Checkpoint、Fork DB、内存 `_runs`、文件 Artifact、Memory/Skill DB 构成多个事实来源；
4. `BackgroundTasks`、Catch-All `safe_node`、静默降级不具备持久长任务语义；
5. Docker、API、路径、Secret、MCP/Skill 供应链安全不足；
6. 当前 Skill 是函数库，不是版本化方法包；
7. MCP Gateway 对 MVP 过度设计；
8. Git-like Branch 不是真实 Git/worktree；
9. Evidence 主要靠 Prompt，没有 Writer 代码级门禁；
10. 测试数量不能证明恢复、安全、复现和证据真实性。

本次修订已接受这些核心意见。

---

## 2. 本轮修订内容

### 2.1 产品收缩

从：

> 通用 Agent OS + Research/Coding/Browser 三场景

收缩为：

> 论文 + 仓库 + 有限目标 → 基线复现 → 一次修复/消融 → Verified Claims → Reproducible Research Bundle。

### 2.2 架构收缩

- 模块化单体 + 独立 Worker + Sandbox Broker；
- PostgreSQL 是业务状态真相；
- Git 是代码真相；
- Content-addressed Artifact Store 是日志/指标/交付物真相；
- LangGraph Checkpoint 只保存单次 Attempt 上下文；
- Audit Event 只做审计，不采用完整 Event Sourcing；
- Redis/Celery 只运输任务，不保存业务状态。

### 2.3 能力收缩

- 一个 Supervisor；
- 一个可选 Reviewer；
- 一个确定性 Evaluator；
- 3–5 个静态、人工审核 Skill；
- 库内只读 Capability Adapter，不建设 MCP Gateway 服务；
- 一个 Baseline Worktree 和一个 Candidate Worktree；
- Browser、Plugin Hub、通用 Coding 场景全部 Later。

### 2.4 新增代码防耦合规范

新文档定义六层：

```text
Domain
Application
Runtime
Infrastructure
Interfaces
Bootstrap
```

关键约束：

- 依赖只能向内；
- Domain/Application 不依赖框架实现；
- LangGraph Node 不直接访问数据库、Git、Docker、FastAPI、Redis；
- FastAPI Route 不直接访问 ORM；
- Infrastructure 通过 Application Port 接入；
- Bootstrap 是唯一组装具体实现的位置；
- 跨层只传 DTO/Port，不传完整 ResearchState；
- Architecture Tests 在 CI 自动检查依赖边界。

---

## 3. 本轮审查目标

第二轮不要重复证明“原 Agent OS 范围过大”。请重点判断：

1. 修订后的单场景是否足够聚焦；
2. 模块化单体是否仍然过度设计；
3. 六层代码结构是否真正降低耦合，还是制造样板代码；
4. 唯一事实来源和事务模型是否自洽；
5. 长任务、幂等、审批、取消和恢复是否可实现；
6. Writer/Evidence 门禁能否在代码层强制；
7. 安全底线是否适合本地单用户 MVP；
8. 8 周路线图是否现实；
9. Legacy 渐进迁移是否会形成长期双架构；
10. 哪些规则应当自动检查，哪些只应 Code Review。

---

## 4. 强制审查问题

## A. 产品与 MVP

1. `Paper + Repository + Limited Objective` 是否是足够明确的输入契约？
2. “一次修复或一次消融”是否仍有歧义？应如何进一步限制？
3. Research Bundle 的最小文件是否过多或缺失？
4. v0.1 是否还保留了不必要的 Skills/MCP/Reviewer？
5. 最小 Demo 能否在普通 CPU/低成本条件稳定运行？

## B. 六层架构

1. Domain、Application、Runtime、Infrastructure、Interfaces、Bootstrap 的边界是否正确？
2. Runtime 单独成层是否必要，还是应归入 Application/Infrastructure？
3. `Infrastructure implements Application Ports` 是否符合依赖倒置？
4. Interfaces 是否允许依赖 Domain 公开类型，还是只能依赖 Application DTO？
5. Bootstrap 是否会成为新的 Service Locator/上帝模块？
6. 是否存在双向依赖风险？
7. 哪些目录可以合并以减少样板代码？

## C. 防止牵一发而动全身

1. 哪些公共契约最容易发生级联修改？
2. DTO、Port、Domain Entity、ORM Entity 的分离是否足够？
3. 模块公开 API 和内部 API 规则是否能自动检查？
4. 如何避免 `shared/common/utils` 重新成为耦合中心？
5. 单文件/单函数阈值是否合理？
6. 修改数据库 Schema、Prompt、Skill、Workflow 时，影响范围规则是否完整？
7. 如何量化架构耦合是否在下降？

## D. 状态与一致性

1. PostgreSQL 当前状态 + Audit + Outbox 是否足够？
2. LangGraph Checkpoint 与 Attempt 的生命周期应如何绑定和清理？
3. Worker Lease/Heartbeat/乐观锁是否存在竞态？
4. Artifact 写成功但 DB 提交失败时如何回收？
5. Git Commit 成功但 Attempt 更新失败时如何幂等恢复？
6. Draft PR 等外部操作是否需要 Compensation？
7. 哪些状态必须在同一事务？

## E. Workflow 与 Agent

1. Workflow/Agent 职责划分是否足够确定？
2. LangGraph 只管理 Attempt 是否合理？
3. Agent Action Proposal 应使用什么 Schema？
4. 哪些动作必须是确定性 Workflow 决定，不能交给 Agent？
5. 可选 Reviewer 是否应从 v0.1 完全删除？
6. Task Brief 是否足够支持 Handoff？

## F. Skill 与 MCP

1. v0.1 是否真的需要 3–5 个 Skill，还是普通代码/Prompt 即可？
2. Skill 最小 Manifest 应有哪些字段？
3. Skill Script 作为 Tool 执行是否合理？
4. 库内 CapabilityAdapter 是否重复包装现有 MCP Client？
5. Schema Hash、Payload Limit、Policy Hook 哪些属于 Must Have？
6. 只读 MCP 是否仍存在 Prompt Injection 和数据外泄风险？

## G. Git、Artifact 与 Evidence

1. Baseline/Candidate 两个 Worktree 是否是正确的最小模型？
2. Git、Artifact、DB 三者如何处理部分成功？
3. Local CAS 最小实现是什么？
4. Metric Artifact Schema 应包含哪些字段？
5. Exact-span Evidence 如何处理 PDF 版本和文本抽取差异？
6. VerifiedClaimView 是否足以阻止 Writer 绕过？
7. Writer 是否应完全独立进程/模块？

## H. 安全

1. 本地单用户 Token、Loopback 和严格 CORS 是否足够？
2. Sandbox Broker 是否必须是独立进程？
3. Docker Socket 隔离在 Windows/Docker Desktop 环境如何实现？
4. seccomp/AppArmor/gVisor 哪些是 v0.1 必须，哪些仅 Linux 可选？
5. 默认无网络与依赖安装如何协调？
6. Secret Broker 在本地 MVP 是否过度设计？
7. 安全集能否在 CI 中稳定执行？

## I. 测试和架构治理

1. `import-linter/pytestarch/AST` 应选择哪个？
2. 哪些依赖规则必须作为 CI Blocker？
3. Fake Adapter 如何避免与生产语义漂移？
4. Adapter Contract Test 的最小模板是什么？
5. 16 个冻结任务是否适合 8 周 MVP？
6. 如何避免架构规范变成无人维护的文档？
7. 哪些规则应该删除或降低为建议？

## J. 迁移和路线图

1. Strangler Pattern 是否适合当前小型代码库？
2. 新旧包并存多久合理？
3. 第一条 Vertical Slice 应穿过哪些层？
4. Week 1–8 的顺序是否正确？
5. 哪周最容易延期？
6. 一名全职开发者实际需要多久？
7. 开工前还缺哪些 ADR？

---

## 5. 要求输出格式

请严格按照以下格式回答。

### 1. Second-round Verdict

- `GO / CONDITIONAL GO / NO-GO`；
- 200 字以内说明；
- 与首轮相比，风险是上升还是下降。

### 2. Updated Scorecard

| 维度 | 首轮 | 本轮 | 变化原因 |
|---|---:|---:|---|
| 产品聚焦 | 3 | | |
| 技术差异化 | 4 | | |
| 架构合理性 | 5 | | |
| 一人可实现性 | 2 | | |
| 安全性 | 3 | | |
| 可评测性 | 4 | | |
| 开源吸引力 | 4 | | |
| 面试展示价值 | 8 | | |

### 3. Accepted Fixes

列出本次修订真正解决的首轮问题，禁止泛泛表扬。

### 4. Remaining Blockers

按 Blocker/High/Medium 输出：

- 问题；
- 影响；
- 具体修改；
- 验收条件。

### 5. Layering Review

- 逐层审查六层职责；
- 给出应该合并/拆分的层；
- 给出修正后的依赖图；
- 列出至少 10 条可自动执行的 Import/Architecture Rules；
- 指出最可能重新产生耦合的三个位置。

### 6. Source-of-truth and Failure Review

- 给出事实来源表修正；
- 分析 DB/Git/Artifact/Checkpoint 部分成功；
- 给出幂等和恢复算法；
- 给出必须保持的事务边界。

### 7. Security Minimum

分为：

- v0.1 Blocker；
- v0.1 Should Have；
- Linux-only Hardening；
- Later。

### 8. Revised Vertical Slice

给出第一条端到端 Vertical Slice：

- 涉及文件/模块；
- 输入输出；
- 状态迁移；
- Port/Adapter；
- 测试；
- Demo。

### 9. Revised Roadmap

- 提供现实的 8–12 周计划；
- 每阶段有退出条件；
- 明确哪些工作可以推迟；
- 提供止损条件。

### 10. Final Pre-coding Checklist

列出正式重构前必须完成的 5–10 项事项。

---

## 6. 可直接复制给 GPT 的提示词

```text
这是 Research Forge 的第二轮架构送审。

仓库：
https://github.com/chasen2041maker/research-forge

首轮你给出的结论是 CONDITIONAL GO：完整 Agent OS NO-GO，只有收缩为“证据门控的科研复现 Agent”才 GO。

我已经根据首轮意见重写了两份核心文档：
1. docs/架构设计/科研复现智能体架构蓝图.md
2. docs/架构设计/代码分层与架构治理规范.md

同时提供第二轮送审要求：
3. docs/架构审查/GPT第二轮架构送审包.md

请完整阅读三份文档，并在能够访问 GitHub 时交叉检查当前代码。不要重复花大量篇幅批评已经删除的“通用 Agent OS”范围，而要重点验证修订方案是否真的可实现。

重点审查：
- 六层代码结构是否能降低耦合，还是产生过度抽象；
- 依赖方向和 Ports/Adapters 是否正确；
- PostgreSQL、Git、Artifact、LangGraph Checkpoint 的事实边界是否自洽；
- Worker Lease、Heartbeat、幂等、Cancel、Resume 和 Approval 是否有竞态；
- DB/Git/Artifact 部分成功怎样恢复；
- Writer 只读取 VerifiedClaimView 是否能在代码层强制；
- Skill/MCP 是否仍超出 MVP；
- Sandbox Broker、安全门禁和本地开发环境是否现实；
- Legacy 渐进迁移是否会形成长期双架构；
- 8 周计划和 16 个 Eval 是否现实；
- 哪些架构规则应由 CI 自动执行。

请严格按照第二轮送审包的“要求输出格式”回答：
1. Second-round Verdict；
2. Updated Scorecard；
3. Accepted Fixes；
4. Remaining Blockers；
5. Layering Review；
6. Source-of-truth and Failure Review；
7. Security Minimum；
8. Revised Vertical Slice；
9. Revised Roadmap；
10. Final Pre-coding Checklist。

不要只总结文档，不要因为规则详细就默认规则可执行。请指出具体矛盾、过度设计、缺少的状态、接口、事务和测试。
```

---

## 7. 第二轮结果回传

请把 GPT 完整回复保存为：

```text
docs/架构审查/GPT第二轮架构审查结果.md
```

文件头：

```markdown
# GPT Architecture Review Result V2

- Review date:
- Reviewer model:
- Model version/date:
- GitHub access:
- Tests executed:
- Files reviewed:
- Assumptions:
```

回传后执行：

1. 对 Remaining Blocker 逐条裁决；
2. 冻结 v0.1 Scope；
3. 生成事实来源 ADR；
4. 生成第一条 Vertical Slice ADR；
5. 建立架构测试；
6. 再开始代码迁移。
