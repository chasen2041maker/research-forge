# 方向 A:把 Co-Scientist 做深到 2026 前沿

> **一句话**:不换场景,在现有 agent3 项目上补齐 MCP / Orchestrator-Subagent / 观测性 / 在线 Demo 四件套,让它从"教学项目"跃升到"能直接当简历头牌的生产级架构"。

## 目标

让面试官看完后说出这句话:"这不是学生项目,这是**已经接 2026 前沿架构**的作品。"

## 技术增量清单(按优先级排)

| # | 增量 | 投入 | 面试价值 |
|---|---|---|---|
| 1 | **MCP 适配层** | 1 天 | ⭐⭐⭐ 对齐 2024.11 Anthropic 标准 |
| 2 | **Orchestrator-Subagent 显式范式** | 2-3 天 | ⭐⭐⭐ 对应 Anthropic 2025.4 博客 |
| 3 | **观测性(LangSmith / OpenTelemetry)** | 半天 | ⭐⭐ 生产级证明 |
| 4 | **Extended Thinking 显式控制** | 半天 | ⭐⭐ 对齐 Anthropic 2025 推理模型 API |
| 5 | **成本/Token 预算护栏** | 1 天 | ⭐⭐ 长跑 Agent 的安全边界 |
| 6 | **在线 Demo 部署** | 1 天 | ⭐⭐⭐ 面试官能直接玩 |
| 7 | **架构演进博客(中英双版)** | 1-2 天 | ⭐⭐⭐ 可外链,持续被读 |

## 三周详细计划

### Week 1:架构升级(增量 1+2)

#### Day 1-2:MCP 适配层

**目标**:把 arXiv / Semantic Scholar / OpenAlex 三个检索源包装成 MCP Server,主程序通过 MCP Client 调用。

**技术栈**:
- `mcp` 官方 Python SDK(Anthropic 发布,2024.11)
- 协议:stdio transport(最简单,适合本地)或 SSE transport(网络部署)

**目录规划**:
```
backend/co_scientist/modules/m2_retriever/
├── mcp_servers/                    # 新建
│   ├── arxiv_server.py             # 独立可启动的 MCP Server
│   ├── semantic_scholar_server.py
│   └── openalex_server.py
├── mcp_client.py                   # 新建:主程序用的 MCP Client
└── retriever.py                    # 改造:用 mcp_client 替代直接调用
```

**验收标准**:
- `python -m co_scientist.modules.m2_retriever.mcp_servers.arxiv_server` 能独立启动
- 原 m2 流程功能不变,单元测试全通过
- 可以在 Claude Desktop 里直接加这个 MCP Server 当独立工具用

**面试讲法**:
> "我把检索源独立成 MCP Server,对应 2024 年 11 月 Anthropic 发布的 Model Context Protocol。现在这个服务不只是 Co-Scientist 内部能用,**任何支持 MCP 的 Agent 都能调用** —— Claude Desktop、Cursor、Zed、其他团队的 Agent。我自己在 Claude Desktop 里挂了一下,能直接用自然语言让它查文献。"

#### Day 3-5:Orchestrator-Subagent 显式范式

**目标**:m4 批判圆桌从"预定义 5 个 Reviewer"升级到"主 Agent 动态决定召哪几类 Reviewer"。

**具体改造**:
1. 新建 `m4_critique/orchestrator.py`:
   - 输入:研究问题 + PICO
   - 输出:该场景需要的 Reviewer 角色列表(从已知 6 类里选,数量 3-6)
   - 实现:一次 LLM 调用,返回 `{"reviewers": ["novelty", "statistics", "reproducibility"], "reason": "..."}`
2. 原 `run_roundtable_async` 改成按 Orchestrator 返回的列表动态 spawn
3. 每个 Subagent 有**独立上下文**(你已经有这个)+ 返回总结(新增:只返回 rationale + rating,不返回完整 history)

**为什么这个改动有架构价值**:
- 从"静态多 Agent"升到"动态多 Agent"
- 对应 Anthropic 2025.4《How we built our multi-agent research system》
- 和 Claude Code 的 Agent 工具派 subagent 同构

**验收**:
- 跑一个"纯理论"问题 → Orchestrator 应该不召 reproducibility
- 跑一个"工程应用"问题 → Orchestrator 应该召 reproducibility

**面试讲法**:
> "我的 m4 评审圆桌是 **Orchestrator-Subagent** 模式:主 Agent 先看问题性质,动态决定需要哪几类 Reviewer,然后并行派子 Agent,每个子 Agent 有独立上下文,跑完只返回结论。这对应 Anthropic 2025 年 4 月博客里提到的多 Agent 研究系统架构。跟 2023 年 AutoGen 的 GroupChat 最大区别是:**上下文严格隔离,主 Agent 的上下文不会被 N 个子 Agent 污染**。"

### Week 2:工程化(增量 3+4+5)

#### Day 6:LangSmith / OpenTelemetry 观测性

**二选一**:
- **LangSmith**(推荐,和 LangGraph 天然集成):加几行 env var + 装饰器就能把所有 LLM 调用上报
- **OpenTelemetry**(更通用):自己埋点,能对接 Jaeger / Datadog

**改动量**:
```python
# 方案 1:LangSmith,基本零改造
import os
os.environ["LANGSMITH_TRACING"] = "true"
os.environ["LANGSMITH_PROJECT"] = "co-scientist"
# 之后所有 LangGraph 节点 + LLM 调用自动上报
```

**面试讲法**:
> "我接了 LangSmith,每次 run 都有完整 trace,可以按 thread_id 回放、对比不同 prompt 变体、看每个节点的 token 用量。我在 A/B 测试 Prompt 时用它对比过两版 Meta prompt 的决策分布。"

#### Day 7:Extended Thinking 显式控制

**改动**:`llm/claude.py` 里在调用 Meta-Reviewer 时传 `thinking` 参数:

```python
response = self._sdk.messages.create(
    model=self.default_model,
    messages=messages,
    max_tokens=4096,
    thinking={
        "type": "enabled",
        "budget_tokens": 4000,  # 给 Claude 4000 token 做深度推理
    },
)
```

**按任务分级**:
- Meta 终裁 → budget=8000(最贵,最重要)
- 普通 Reviewer → 不开(节省成本)
- m7 论文写作 → budget=4000

**面试讲法**:
> "Meta 终裁是高风险决策,我启用 Claude 的 Extended Thinking,给 4000-8000 token 的思考预算,对应 Anthropic 2025 推出的推理模型 API。日志里能看到模型的 thinking trace,debug 时很有用。"

#### Day 8-9:成本 / Token 预算护栏

**加一个 BudgetGuard 装饰器**:
```python
# co_scientist/utils/budget_guard.py
class BudgetGuard:
    def __init__(self, max_usd: float, max_tokens: int):
        self.limit_usd = max_usd
        self.used_usd = 0.0
        # ...

    def check(self, cost: float):
        if self.used_usd + cost > self.limit_usd:
            raise BudgetExceeded(...)
        self.used_usd += cost
```

挂到 LLM client 的每次调用,超预算直接抛异常终止流程。

**面试讲法**:
> "Agent 长跑最大的风险是成本失控,我加了一层 BudgetGuard,每次 run 默认 $1 上限,Meta 调用前检查剩余预算。Devin / Cognition 也是类似设计 —— 你不能让一个 Agent 因为 bug 把你账户跑空。"

### Week 3:对外展示(增量 6+7)

#### Day 10-11:在线 Demo

**选型**:
- **Hugging Face Spaces**(免费,Gradio 界面)
- **Vercel + Serverless**(前端好看,但后端 cold start 慢)
- **Railway**(便宜,Docker 一键部)

**推荐 Railway**,因为你项目已经有 `docker-compose.yml`。

**验收**:一个公网 URL,面试官打开能输入研究问题、看到全流程跑起来。

**面试讲法**:
> "项目已部署在 xxx.xxx,面试官可以直接跑。默认用我自己的 API Key 做了限流(每 IP 每天 3 次)。"

#### Day 12-14:架构演进博客

**中文版**发个人站 / 知乎 / 掘金,**英文版**发 Medium / personal blog。

**文章大纲建议**:
```
# 从 LangGraph 到 Orchestrator-Subagent:一个 Agent 项目的架构演进

## 起点:LangGraph 4-Agent 考试系统(2025 Q?)
  - 传统多 Agent 流水线
  - 踩过的坑:X / Y / Z

## 转折:踩到 AutoGen GroupChat 的三个痛点
  - 上下文爆炸 / 从众效应 / 成本失控

## 终点:2026 的 Compound AI + Orchestrator-Subagent
  - MCP 标准化工具层
  - 动态多 Agent 生成
  - 观测性 + Budget Guard
  - Agent Evals 三层覆盖

## 踩坑与权衡
  - 为什么没用 AutoGen
  - 为什么评审隔离上下文(反 anchoring)
  - 为什么 Reflexion 用词袋+embedding 双通道
```

**长度**:3000-5000 字中文,1500-2500 英文。

---

## 里程碑 check

- [ ] Week 1 结束:MCP Server 能启动 + Orchestrator 能动态选 Reviewer
- [ ] Week 2 结束:LangSmith 出 trace + Budget 有效拦截 + Extended Thinking 跑过
- [ ] Week 3 结束:公网 URL + 博客发布

---

## 面试讲点速查

| 面试官问 | 你答 |
|---|---|
| 这项目用了什么新架构? | MCP + Orchestrator-Subagent + Extended Thinking + Compound AI |
| MCP 是啥? | 2024.11 Anthropic 发的工具/上下文标准协议,我的检索源都是独立 MCP Server |
| Subagent 和 AutoGen 区别? | 上下文隔离 / 动态生成 / 结果聚合 vs 共享/轮流/群聊 |
| 怎么保证质量? | 三层 Evals(schema / consistency / LLM-as-Judge) + LangSmith 可观测 |
| 长跑成本怎么控? | BudgetGuard 预算上限 + Extended Thinking 按任务分级 |
| 有 Demo 吗? | xxx.xxx,打开就能用 |

---

## 风险与降级

| 风险 | 概率 | 降级方案 |
|---|---|---|
| MCP SDK Python 版不稳定 | 中 | 降级到自写简化版 JSON-RPC server |
| LangSmith 免费额度不够 | 低 | 降级到自建 SQLite trace 存储 |
| Hugging Face Spaces 太慢 | 中 | 降级到录屏 GIF 放 README |
| 博客没人看 | 高 | 无伤大雅,简历链上就行 |

---

## 投入回报估算

- **时间**:21 天(有工作的话拉长到 6 周)
- **钱**:API 费用约 $30-50(所有升级 + 部署体验)
- **获得**:
  - 一个能跑的 MCP Server(可持续复用到下个项目)
  - 一个生产级 Agent 作品
  - 一篇可外链的技术博客
  - 简历档次 +1 档

---

**开工检查**:选定这个方向后,先跑一遍现有 evals(`EVAL_MOCK=1 pytest tests/evals/ --run-evals`)确认基线通过,再按 Day 1 开始。
