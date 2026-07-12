# Research Forge 2.0 — GPT 架构送审包

> 用途：将本文件与主设计文档一起提交给 GPT，进行独立、严格的架构审查。  
> 主设计文档：`docs/architecture/AGENT_CAPABILITY_PLATFORM_BLUEPRINT.md`  
> 审查目标：发现范围失控、伪创新、不可实现、安全漏洞和评测缺失，而不是获得泛泛表扬。

---

## 1. 提交说明

请把以下两个文件一同发送给 GPT：

1. `AGENT_CAPABILITY_PLATFORM_BLUEPRINT.md`
2. `GPT_ARCHITECTURE_REVIEW_PACKAGE.md`

如果 GPT 可以读取 GitHub，再附上：

- 仓库：https://github.com/chasen2041maker/research-forge
- 当前主编排：`backend/co_scientist/graph.py`
- 当前状态模型：`backend/co_scientist/state/research_state.py`
- 当前多分支：`backend/co_scientist/modules/m8_replay/multi_branch.py`
- 当前代码执行：`backend/co_scientist/modules/m6_code/code_gen.py`
- 当前 API：`backend/co_scientist/api/main.py`

---

## 2. 项目背景

Research Forge 当前是一个 AI Co-Scientist 项目，已有能力包括：

- LangGraph 科研流水线；
- 候选课题、问题精炼、多源文献检索；
- 文献访问状态与知识图谱；
- 多 Reviewer 评审；
- 实验设计；
- Docker 代码沙箱；
- 论文草稿生成；
- SQLite/LangGraph 多分支和回放；
- FastAPI、WebSocket、Next.js；
- 成本控制、记忆、Prompt A/B、Skill Library；
- 150 个通过、9 个跳过的测试。

当前主要问题：

- 主工作流是固定 DAG；
- ResearchGate 没有真正形成条件回路；
- 多分支仍为串行执行；
- Fork 不是实际 Git branch/worktree；
- 状态集中在巨大 TypedDict；
- 测试偏结构正确性，缺少真实科研和任务质量评测；
- Skills 和 MCP 尚未形成统一平台；
- 缺少长期任务、审批、事件溯源和系统化安全层。

新设计希望将项目升级为：

> 一个以科研工程为旗舰场景，统一整合 Agent Runtime、Skills、MCP、Memory、Git Workspace、Sandbox、Browser、Human Approval、Observability 和 Evaluation 的大型 Agent 工程展示项目。

---

## 3. 希望 GPT 扮演的角色

你是一位同时具备以下经验的首席架构审查者：

- 大规模 Agent Runtime 与长任务编排；
- LangGraph/工作流系统；
- Agent Skills 与 MCP；
- 分布式任务、事件溯源和可观测性；
- Git-native Coding Agent；
- 浏览器和代码沙箱安全；
- LLM Eval、Agent Benchmark 和科研复现；
- 开源项目产品化与个人作品集评审。

请保持独立和批判性。不要因为文档内容丰富就默认设计正确，也不要只给通用最佳实践。

---

## 4. 强制审查原则

1. 区分“架构图上存在”和“一个人能够实现”。
2. 区分“Agent 能说自己完成”和“系统有证据证明完成”。
3. 区分 Skill、Tool、MCP、Agent、Workflow、Memory 的职责。
4. 对任何“自主学习/自我进化”要求给出评测与回滚条件。
5. 对外部写入、Shell、Browser、MCP、Secret 提出具体威胁模型。
6. 检查是否重复制造已有框架已有的能力。
7. 检查技术栈是否过度设计。
8. 检查数据模型和状态一致性。
9. 检查任务失败、进程崩溃、模型超时和工具不可用时的行为。
10. 检查 README 可宣传内容是否能被测试、Trace 或 Artifact 证明。

---

## 5. 必须回答的审查问题

### A. 产品定位

1. “Agent OS + 科研旗舰场景”的定位是否仍然过宽？
2. Research Forge 与 DeerFlow、Agent Zero、DeepScientist、Letta、OpenHands、RD-Agent 的差异是否足够明确？
3. 哪一个能力最适合作为真正核心壁垒？
4. 哪些能力只能作为基础设施，不能作为卖点？
5. 对个人开发者而言，两个旗舰 Demo 是否仍然太多？

### B. 架构边界

1. Control Plane、Capability Plane、Execution Plane、Knowledge Plane、Quality Plane 是否职责清晰？
2. LangGraph + FastAPI + Redis/Celery + PostgreSQL 是否合理？
3. 哪些职责不应该放进 LangGraph？
4. 是否真的需要 Event Store，还是普通审计表已经足够？
5. 当前架构中是否存在两个事实来源互相冲突？

### C. Skill 系统

1. `SKILL.md + skill.yaml + scripts/references/evals` 是否过度设计？
2. 如何与现有 Agent Skills 规范保持最大兼容？
3. Skill Router 应该使用规则、Embedding、LLM，还是混合方案？
4. 如何评测 Skill 选择正确，而不是只评测最终答案？
5. Skill 的依赖、权限、版本和锁文件是否有必要全部在 MVP 实现？
6. 自动 Skill 改进怎样避免奖励黑客和能力退化？

### D. MCP Gateway

1. 自建 MCP Gateway 是否重复制造现有 MCP Client/Registry 能力？
2. MVP 的 Gateway 最小职责是什么？
3. 如何处理 Tool 名冲突、Schema 变化和 Server 断线？
4. 如何防范恶意 MCP 输出和 Prompt Injection？
5. 授权应按用户、Mission、Agent、Skill 还是 Tool 管理？
6. 哪些审批能够持久化，哪些必须每次询问？

### E. 多 Agent 与长任务

1. Supervisor + 按需子 Agent 是否优于固定 Reviewer 团队？
2. Handoff 应传递哪些最小结构化信息？
3. 子 Agent 失败或返回低质量结果时由谁判断？
4. 如何避免 Supervisor 成为新的巨大上下文瓶颈？
5. Mission/Task 状态机是否覆盖暂停、恢复、重试、取消、审批和补偿？
6. 是否需要 Saga/Compensation 机制处理部分外部写入成功？

### F. Git Workspace 与执行

1. 一个 Mission 一个仓库是否合理？
2. branch/worktree 与数据库 Branch Record 如何保持一致？
3. 并行实验如何避免依赖、端口、GPU 和数据冲突？
4. Sandbox 的最小安全边界是什么？
5. Browser Worker 和 Code Worker 是否应该完全隔离？
6. GitHub 写入和 PR 创建应在哪个 Approval Gate 后发生？

### G. Memory 与 Evidence

1. 五层记忆是否过多？MVP 最少保留哪几层？
2. 记忆写入、合并、过期、删除和来源如何管理？
3. Claim–Evidence Graph 使用关系表是否足够，是否需要 Neo4j？
4. 如何防止错误证据进入长期语义记忆？
5. Writer 只消费验证 Claim 的约束如何在代码层强制？
6. 如何处理证据之间的矛盾和时效性？

### H. Evaluation

1. 当前五层 Eval 是否可执行？
2. 最小回归集应包含哪些任务？
3. 如何在低预算下使用 PaperBench/MLE-Bench 思想而不是完整跑 benchmark？
4. 哪些指标最能证明项目能力？
5. 如何报告失败，避免只展示挑选过的成功案例？
6. 如何比较单 Agent、多 Agent、单分支、多分支的真实收益？

### I. 开发计划

1. 路线图的时间估计是否现实？
2. 哪些 Phase 应合并、删除或调整顺序？
3. MVP 中必须砍掉哪些功能？
4. 第一个月最合理的可演示成果是什么？
5. 如何在重构期间保持现有 150 个测试和功能可用？

---

## 6. 要求的输出格式

请严格按照以下格式输出：

### 1. Executive Verdict

- 用 200 字以内给出是否值得做、最大优势和最大风险。
- 给出 `GO / CONDITIONAL GO / NO-GO`。

### 2. Scorecard

按 10 分制评分并解释：

| 维度 | 分数 | 主要理由 |
|---|---:|---|
| 产品聚焦 | | |
| 技术差异化 | | |
| 架构合理性 | | |
| 一人可实现性 | | |
| 安全性 | | |
| 可评测性 | | |
| 开源吸引力 | | |
| 面试展示价值 | | |

### 3. Top 10 Critical Findings

每条包含：

- 严重级别：Blocker / High / Medium / Low；
- 问题；
- 为什么；
- 具体修改建议；
- 如果不改会发生什么。

### 4. Architecture Corrections

- 给出修正后的最小架构；
- 指明应该保留、替换、删除的组件；
- 给出新的 Mermaid 图；
- 明确状态、代码、Artifact 和 Event 的事实来源。

### 5. MVP Cut List

分成：

- Must Have；
- Should Have；
- Later；
- Delete。

### 6. Revised 8-Week Roadmap

每周给出：

- 目标；
- 具体交付物；
- 验收测试；
- 最大风险。

### 7. Evaluation Plan

- 给出 10–20 个低成本但高区分度的任务；
- 给出确定性评分方法；
- 给出模型/成本/种子报告模板；
- 给出防止 Cherry-picking 的方法。

### 8. Threat Model

至少覆盖：

- Prompt Injection；
- 恶意 Skill；
- 恶意 MCP；
- Secret 泄露；
- 宿主逃逸；
- 外部写入；
- 供应链；
- 记忆污染；
- 成本失控。

### 9. Differentiation Statement

请最终给出一个不超过 30 个英文单词的定位，以及一个中文版本。

### 10. Final Recommendation

- 是否应该按当前方案开发；
- 开工前必须修改的 3–5 件事；
- 最值得首先实现的垂直 Demo。

---

## 7. 可直接复制给 GPT 的送审提示词

```text
我要你对一个大型开源 Agent 工程项目进行严格架构审查。

项目仓库：
https://github.com/chasen2041maker/research-forge

我会提供两份文档：
1. Research Forge 2.0 主设计蓝图
2. GPT 架构送审包

请先完整阅读两份文档。如果能够访问 GitHub，请同时审查仓库当前代码，尤其是：
- backend/co_scientist/graph.py
- backend/co_scientist/state/research_state.py
- backend/co_scientist/modules/m8_replay/multi_branch.py
- backend/co_scientist/modules/m6_code/code_gen.py
- backend/co_scientist/api/main.py

不要泛泛总结，不要因为功能多就表扬，也不要默认架构图中的能力已经实现。

你需要扮演首席 Agent 平台架构师、安全审查者、开源维护者和评测负责人，重点寻找：
- 范围是否失控；
- 是否与 DeerFlow、Agent Zero、DeepScientist、Letta、OpenHands、RD-Agent 同质化；
- Skill、Tool、MCP、Agent、Workflow、Memory 的边界是否错误；
- 是否重复制造已有基础设施；
- 长任务、并行、恢复、审批和补偿是否成立；
- Git、数据库、事件和 Artifact 的事实来源是否冲突；
- MCP、Skill、浏览器和代码执行是否存在安全漏洞；
- 所谓自主学习是否有 Eval、回滚和防奖励黑客机制；
- 一名开发者是否能在合理时间实现；
- 项目能否通过公开、可重复的结果展示 Agent 工程能力。

请严格按照送审包“要求的输出格式”回答，并给出：
1. GO / CONDITIONAL GO / NO-GO；
2. 分项评分；
3. Top 10 严重问题；
4. 修正后的最小架构和 Mermaid 图；
5. Must/Should/Later/Delete；
6. 修订后的 8 周路线图；
7. 低成本 Eval 方案；
8. 威胁模型；
9. 最终差异化定位；
10. 开工前必须修改的事项。

如果信息不足，请明确列出假设，但仍然给出最有用的判断。不要只向我提问。
```

---

## 8. 审查结果回传模板

GPT 审查完成后，请把回复保存为：

```text
docs/review/GPT_ARCHITECTURE_REVIEW_RESULT.md
```

并在文件顶部补充：

```markdown
# GPT Architecture Review Result

- Review date:
- Reviewer model:
- Model version/date:
- Web/GitHub access: yes/no
- Files reviewed:
- Important assumptions:
```

回传后下一步不是立即全部开发，而是：

1. 对每个 Blocker 判断接受或拒绝；
2. 记录 Architecture Decision Record；
3. 修订主设计文档；
4. 冻结 MVP 边界；
5. 再建立 GitHub Issues/Milestones；
6. 从第一个可验证 Vertical Slice 开始开发。

