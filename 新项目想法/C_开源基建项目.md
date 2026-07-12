# 方向 C:开源 Agent 基建项目

> **一句话**:做一个**被其他 Agent 项目当依赖使用**的工具/服务,开源到 GitHub。成败看运气,但一旦跑起来,简历直接起飞。

## 谁该选 C

**必选**:
- 目标大厂 AI Infra / Agent 基建团队(字节 Seed、阿里通义、智谱、DeepSeek、Anthropic、OpenAI...)
- 目标研究院 / AI Lab(需要开源作品证明能力)

**慎选**:
- 找工作时间紧(< 3 个月)
- 没有持续维护开源项目的耐心
- 不善于英文技术写作(README / issues 要英文)

**不要选**:
- 只想快速拿 offer
- 想做"有用户"类产品(那是方向 B)

## 为什么 C 收益最高也最有风险

**高收益**:
- 一个有 500+ star 的开源项目 ≈ 任何大厂 offer 的硬通货
- 你会被开源社区看到,可能被邀请 contribute 到更大的项目(LangChain / LlamaIndex)
- 技术深度天花板远高于应用层项目

**高风险**:
- 80% 的开源项目永远没人用
- 需要持续维护(issue / PR / 版本兼容)
- 时间成本可能"打水漂"(但学到的东西不会)

## 三个选题(按可行性 + 热度)

| 选题 | 市场空缺 | 技术难度 | 预计周期 | 爆的概率 |
|---|---|---|---|---|
| **C1 MCP Gateway** | 高(刚出生的生态) | 中 | 2-3 月 | **中等** |
| **C2 Agent Eval 框架** | 中(LangSmith / Braintrust 已占位) | 高 | 3-4 月 | 中低 |
| **C3 Agent Memory OS** | 中(Letta/Mem0 已有) | 高 | 3-4 月 | 低 |

---

# C1:MCP Gateway(推荐首选)

## 背景

2024.11 Anthropic 发布 MCP,2025 年大厂都跟进了,但**生态工具还很稀缺**:
- 有了 100+ MCP Server(官方和社区)
- 但还没有一个好用的"MCP 路由 + 权限 + 监控"中间层
- 这就像有 DNS 有服务器,但没有 Nginx

## 你要做的

一个 **MCP Gateway**,部署在用户和 MCP Server 之间,提供:

1. **多服务聚合**:把 10 个 MCP Server 合成 1 个对外接口
2. **权限控制**:基于用户 / 角色限制哪些工具能调
3. **限流 / 熔断**:保护上游 MCP Server
4. **审计日志**:所有工具调用入库,可回放
5. **缓存**:相同 tool call 命中缓存(省调用)
6. **可观测性**:Prometheus metrics / Grafana dashboard

## 技术栈

| 层 | 选型 |
|---|---|
| 语言 | **Python**(和 MCP SDK 同家族)或 **Go**(性能和部署优势) |
| MCP 库 | `mcp` 官方 SDK |
| 协议 | stdio + SSE + HTTP(MCP 2025 新加的) |
| 后端 | FastAPI(Python)/ Gin(Go) |
| 存储 | SQLite(审计) + Redis(缓存) |
| 可观测性 | OpenTelemetry + Prometheus |

**推荐 Python**:生态熟、和 MCP SDK 原生、你不用学新语言。

## 目录结构

```
mcp-gateway/
├── README.md              ← 中英双语,必须有 GIF Demo
├── mcp_gateway/
│   ├── server.py          ← 核心网关
│   ├── router.py          ← 路由到上游 MCP Server
│   ├── auth.py            ← 权限控制
│   ├── ratelimit.py       ← 限流
│   ├── cache.py           ← 响应缓存
│   ├── audit.py           ← 审计日志
│   └── metrics.py         ← OpenTelemetry
├── examples/
│   ├── with_claude_desktop.md
│   ├── with_cursor.md
│   └── with_langgraph.md
├── tests/
└── docs/
    ├── architecture.md
    ├── protocol-support.md
    └── performance.md
```

## 10 周计划

### Week 1-2:MVP
- 能启动网关,代理一个上游 stdio MCP Server
- 基础 router / config 解析

### Week 3-4:核心功能
- 多上游聚合
- 权限 + 限流

### Week 5-6:生产级特性
- 审计 + 缓存 + metrics
- SSE / HTTP transport 支持

### Week 7-8:文档与示例
- 完整 README
- 3 个集成示例(Claude Desktop / Cursor / 自家 Agent)
- 架构文档 + 性能 benchmark

### Week 9:社区推广
- 发 HN / Reddit r/LocalLLaMA / Twitter / 小红书
- 投稿给 MCP 官方仓库的 "awesome-mcp" 列表
- 找 3-5 个早期用户 beta

### Week 10:迭代
- 根据反馈修最紧急的问题
- v0.1 → v0.2 版本号
- 启动持续维护节奏

## 里程碑 star 目标

| 时间 | 乐观 | 现实 | 悲观 |
|---|---|---|---|
| Week 10 | 200 | 50 | 10 |
| 3 个月 | 500 | 150 | 30 |
| 6 个月 | 1500 | 400 | 80 |

**50 stars 就够写简历了**,面试官看到"我做了一个 MCP 生态工具"会感兴趣,具体数字是次要的。

## 风险与降级

| 风险 | 概率 | 降级 |
|---|---|---|
| MCP 协议大改 | 中 | 跟着改,写 migration 文档能涨 star |
| 大厂官方出了同类 Gateway | 高 | 定位差异化:你专注某个场景(如 dev tools),避开企业级 |
| 没人用 | 高 | **不要紧,简历上是"我造了一个 MCP 基建工具"**,star 数是 bonus |

## 面试讲点

> "我开源了 mcp-gateway,解决 2024.11 MCP 协议出来后'多 Server 聚合 + 权限 + 观测性'的生态空缺。现在有 XX stars,被 XX 个项目引用,issue 里有用户反馈我做过 N 次迭代。我面临的核心挑战是 Y,最终用 Z 方案解决的。"

**讲技术深度**:protocol buffer 协议设计、双向流、断线重连、权限模型、缓存一致性 ——这些都是 MCP Gateway 真会遇到的问题。

---

# C2:Agent Eval 框架

## 背景

- OpenAI Evals 是标杆但太重、需要他们的 infra
- LangSmith 是商业产品,开源版没有
- Braintrust 同上
- Inspect(UK AISI 出的)太偏安全评测
- **社区缺一个"轻量、专为 Agent 而非 LLM 单调用"的开源框架**

## 你要做的

一个 Python 包 `agent-evals`,提供:

1. **多层评测**:schema / consistency / LLM-as-judge / pairwise
2. **Agent 特化**:不是评单次 LLM call,是评 **trajectory**(整条 agent 跑的轨迹)
3. **可组合 rubric**:rubric 用 YAML 写,像 pytest 一样收集运行
4. **报告生成**:每次 eval 出 HTML 报告,历史曲线
5. **成本可控**:默认 skip, `--live` 才真跑,Mock 模式 CI 友好
6. **框架无关**:LangGraph / AutoGen / Crew / 自写 agent 都能用

## 与你现在的 `tests/evals/` 关系

你现在的 evals 是 **v0.0 原型**,C2 就是把它独立成开源库,能力扩展 10 倍。

## 目录结构

```
agent-evals/
├── agent_evals/
│   ├── core/           # Runner, Report, Session
│   ├── judges/         # rubric / pairwise / structural
│   ├── adapters/       # LangGraph / AutoGen / Crew 适配器
│   ├── mock/           # Mock LLM 基础设施
│   └── reports/        # HTML / Markdown 报告生成
├── examples/
│   ├── langgraph_example/
│   ├── autogen_example/
│   └── simple_agent/
└── tests/
```

## 12 周计划

- Week 1-3:核心 Runner + rubric judge + pytest 集成
- Week 4-5:pairwise + consistency evaluator
- Week 6-7:3 个框架适配器
- Week 8-9:HTML 报告 + 历史曲线
- Week 10-11:文档 + 示例
- Week 12:发布 + 推广

## 面试讲点

> "我做了 agent-evals,一个 Agent 专用的评测框架。对应 LangSmith / Braintrust 的开源轻量版,但聚焦 Agent trajectory 评测,不是单次 LLM call。我在自己的 Co-Scientist 项目上用了一年,再抽象出来开源。"

## 风险
- LangSmith 突然开源类似功能 → 定位"可自部署、零外部依赖"差异化
- 评测标准难统一 → 主打"可插拔 rubric",不强推一套标准

---

# C3:Agent Memory OS

## 背景

- MemGPT 2023 论文→ 商业化成 Letta
- Mem0(原 embedchain)2024 起来
- **都有自己的问题**:Letta 偏企业级、Mem0 功能单一

## 你要做的

一个 Python 包 `agent-memory-os`,提供:

1. **分级存储**:hot(内存) / warm(Redis) / cold(向量库)
2. **自动驱逐**:MemGPT 式分页,按 LRU + 重要度
3. **类型化召回**:domain / strategy / failure / user(你已经做过了!)
4. **Reflexion 集成**:一键接入 agent,自动反思入库
5. **多租户**:按 user_id 隔离,支持 SaaS 场景
6. **时间线查询**:"某个用户过去一周发生了什么"

## 与你现在的 EvolvingMemory 关系

**同构**。EvolvingMemory 是 MVP,C3 是把它 10x + 开源。

## 14 周计划

- Week 1-4:核心分级存储 + 类型召回(基于 EvolvingMemory 扩展)
- Week 5-8:Reflexion 集成 + 遗忘机制 + 时间线
- Week 9-11:多租户 + 认证 + REST API
- Week 12-14:文档 + 推广

## 面试讲点

> "我开源了 agent-memory-os,对应 MemGPT/Letta 的轻量版,专为 Python 生态。基于我自己 Co-Scientist 项目里的 Reflexion 记忆模块抽象出来。和 Letta 相比,我的差异化是'完全自部署 + 类型化记忆 + 主动遗忘机制'。"

## 风险
- Letta 已经很成熟 → 差异化必须清晰
- Memory 评估困难 → 需要自己造 benchmark 才说服别人用

---

## 三 C 选题对比

| 维度 | C1 MCP Gateway | C2 Eval 框架 | C3 Memory OS |
|---|---|---|---|
| 技术难度 | 中 | 高 | 高 |
| 市场空缺 | 高(新生态) | 中 | 中(已有竞品) |
| 复用 agent3 率 | 低(新领域) | **高**(你已有 evals 原型) | **高**(你已有 EvolvingMemory) |
| 维护负担 | 中(协议稳定度) | 低 | 中(用户数据迁移) |
| 面试题材 | **协议 / 网络 / 分布式** | **评测理论 / 统计** | **存储 / 检索 / OS 思想** |

---

## 三选一建议

- **想最快出成果 + 借势新协议** → **C1**(MCP 还在早期,先入为主)
- **想复用已有工作最大化** → **C2**(你已经写了 evals 原型,8 成可搬)
- **想做技术最硬的** → **C3**(存储系统是传统硬核领域)

---

## 全 C 方向通用建议

### 1. 开源不是"我随便开源一下"
README 里必须有:
- **Why**:为什么存在(市场空缺)
- **What**:是什么(3 句话 elevator pitch)
- **How**:怎么用(3 行代码能跑起来)
- **Demo**:GIF 或视频(第一眼吸引)
- **Roadmap**:未来计划(告诉用户这不是废弃项目)

### 2. GitHub 之外必须做的
- **推特 / 小红书**:发开发 vlog / 进度
- **HN / Reddit**:节点性宣传(v0.1 / v0.5 / v1.0)
- **博客**:每月一篇"设计决策背后"
- **Discord**:用户群,就算只有 10 人也要建

### 3. 别指望一口气火
平均一个开源项目从建仓到 50 star 要 4-6 个月。**你 10 周只是起步**,后续维护期至少 6 个月。

### 4. 开源和找工作可以并行
不需要等 "项目火了" 再去面试。**启动后 2 周就可以开始写简历了**,简历里写:
> "作者:agent-evals(MIT,GitHub 20 stars,2026 起活跃维护)"

即使 20 star 也远比"做了个 Demo"有说服力。

---

## 开工 check

选定 C 后必做的第一件事:
1. **搜一遍 GitHub / 官方 awesome list**,确认你的选题没有强竞品(3 个月内 star > 1000 的同类项目)
2. **列竞品对比表**,明确你的差异化定位(1 句话能说清)
3. **建仓**,写第一版 README(可以没代码,但必须有 Why/What/How)
4. **定时间盒**:"先干 4 周,看势头决定要不要继续投入"

---

## 面试叙事("开源作者"话术)

> "除了业务项目,我还维护一个开源项目 XX(GitHub stars N,用户 M)。做这个的动机是发现 2025 年 Agent 生态里缺 YY,我自己在 Co-Scientist 里写了个原型,觉得其他人也会需要,就抽象出来开源。
>
> 这个经历让我:
> - 学会了 **用户思维**(我的代码不止自己用,要考虑其他开发者的使用场景)
> - 学会了 **系统设计**(向前兼容 / 版本管理 / 协议演进)
> - 学会了 **开源协作**(review PR / 处理 issue / 维护社区)
>
> 这些是做闭源项目永远学不到的能力。"

**这段话讲完,大部分面试官会把你归到"候选人第一档"。** 尤其是大厂基建岗。
