# AI Co-Scientist (Multi-Branch 版)

自动化科研工作流系统：从「被动单线助手」升级为「主动、多路线、可回放、可进化」的研究操作系统。LangGraph DAG 编排 + Orchestrator-Subagent 多 Agent 评审圆桌 + Git-like 多分支研究管理 + GPT/Claude 中转站统一路由。

基于 [AI-Co-Scientist 8 模块基础架构](./AI-Co-Scientist-技术方案.md) + Phase 1-3 工程化升级（MCP 协议工具层 / LangSmith / Extended Thinking / Budget Guard）+ 整理版 [Phase A-E 多路线升级](./新增架构设想_整理版.md)。

## 主流水线

```
                ┌─────────────────────────────────────────────┐
                │     AI Co-Scientist 多路线科研工作流         │
                └─────────────────────────────────────────────┘

  raw_question
       │
       ▼
 ┌─────────────┐  USE_M0=true   ┌──────────┐    ┌──────────────────┐
 │ appendix    ├───────────────►│ M0 候选  │───►│ user_select_topic│
 │ recall (附A)│                │ 课题发现 │    └──────────────────┘
 └──────┬──────┘                └──────────┘             │
        │   USE_M0=false                                 │
        ▼                                                ▼
 ┌──────────┐ ┌──────────┐ ┌────────────┐ ┌──────────┐ ┌──────────────┐
 │ M1 PICO  ├►│ M2 多源  ├►│ M2.5 访问  ├►│ M3 KG +  ├►│ M4 Roundtable│
 │ refiner  │ │ 检索融合 │ │ 状态分级   │ │ GapCard  │ │ + Decision   │
 └──────────┘ └──────────┘ └────────────┘ └──────────┘ └──────┬───────┘
              (RRF + Embed)  (启发式)    (双输出)    (Orchestr+Devil)
                                                              │
                                                              ▼
              ┌─────────┐  ┌──────────┐  ┌───────────┐ ┌──────────────┐
              │ M7 论文 │◄─│ M6 代码  │◄─│ M5.5 Gate │◄│ M5 实验设计  │
              │ 草稿    │  │ + 沙箱   │  │ 质量门禁  │ │ + GapCard 先验│
              └────┬────┘  └──────────┘  └─────┬─────┘ └──────────────┘
                   ▼                           │
            ┌──────────────┐                   │
            │ appendix     │             gate_decision
            │ reflect (附A)│             ├─ continue_to_m6
            └──────────────┘             ├─ revise_experiment ───► M5
                                         ├─ fetch_more_evidence ─► M2
                                         ├─ refine_question ─────► M1
                                         └─ choose_new_topic ────► M0

 ┌─ M8 Git-like 多分支管理(整理版 Phase D)─────────────────────────────┐
 │  K 张 TopicCard ──► branch_from_topic_cards ──► K 条 fork(thread_id)│
 │              │                                                      │
 │              ▼                                                      │
 │     compare:规则版(final_rating max) | LLM 综合版(critical Claude)  │
 │              │                                                      │
 │              ▼                                                      │
 │     merge_winner ──► mark_mainline(同父唯一,前端琥珀高亮)            │
 └─────────────────────────────────────────────────────────────────────┘
```

## 特性

- **整理版 Phase A-E 全部落地** &mdash; 5 个 Phase 共 45 个新测试,**136 passed 零回归**;Phase A 模型入口 + 数据结构 → B M0 + GapCard → C DecisionCard + ResearchGate + M2.5 → D Git-like 多分支 → E API + 前端
- **M0 候选课题发现器** &mdash; LLM 直接基于粗粒度兴趣生成 K 张 TopicCard(title / candidate_question / suspected_gap / score),`USE_M0_DISCOVERY=true` 启用
- **M2.5 文献访问状态层** &mdash; 启发式分级 fulltext/abstract_only/restricted/failed + GitHub 代码 / HuggingFace 数据集嗅探,有代码自动升一档证据等级
- **M3 GapCard 双输出** &mdash; 老 `research_gaps: list[str]` + 新 `gap_cards: list[GapCard]` 共存,M5 实验设计直接继承 datasets/baselines/metrics 作先验
- **M4 Evidence-grounded Roundtable** &mdash; Orchestrator 动态选 3-5 个 Reviewer + 方差>2 触发 Devil 二次辩论 + Meta 终裁(Claude Opus + Extended Thinking)+ `build_decision_card` 综合输出 DecisionCard(action / target_node / branch_count / blocking_issues)
- **M5.5 ResearchGate 质量门禁** &mdash; 启发式优先(完整性 / 低证据 / 服从 DecisionCard 三类规则)+ `USE_M5_5_LLM=true` 叠加 LLM 综合;6 种动作 continue_to_m6 / revise_experiment / fetch_more_evidence / refine_question / choose_new_topic / stop
- **M6 Verification Layer** &mdash; 三档代码生成 generate_only / dry_run / full_execute;Docker 沙箱 `network_mode=none` + `pids_limit` 安全加固;失败自修复闭环(stderr 回灌 LLM 重试 N 轮)
- **M8 Git-like 多分支管理** &mdash; `branch_from_topic_cards` 批量分叉 + `branch_from_gate_decision` 派生回退分支(fetch→m2 / refine→m1 / new_topic→m0)+ `compare` 双模式(规则 + LLM critical)+ `merge_winner` mark mainline 同父唯一
- **统一模型路由(整理版 §3)** &mdash; 业务模块只调 `get_llm("chat" | "reasoner" | "critical")`;chat/reasoner 固定走 GPT 中转站(gpt-5.5),critical 固定走 Claude 中转站(claude-opus-4-7);不再提供 DeepSeek fallback
- **MCP 协议工具层(Phase 1)** &mdash; arXiv / Semantic Scholar / OpenAlex 检索源独立成 MCP Server,对应 Anthropic 2024.11 Model Context Protocol 标准,可被 Claude Desktop / Cursor 复用
- **三层进化引擎(附录 A)** &mdash; L1 Reflexion 经验记忆库(分层召回 + 遗忘机制)/ L2 Prompt A/B Bandit 自动进化 / L3 Voyager 风格 SkillLibrary 工具自生成
- **对抗数据工厂(附录 B)** &mdash; Red/Blue/Judge 红蓝对抗循环 → DPO 偏好数据;Pairwise 单轮 + 多轮模式
- **生产级基础设施(Phase 3)** &mdash; LangSmith trace 自动上报 + Claude Extended Thinking(meta 节点自动开 4000 token 推理预算)+ Budget Guard ContextVar 做 run 级硬上限
- **React + Next.js 前端** &mdash; 研究视图 5 张证据链 Card(TopicCard / GapCard / AccessStatus / DecisionCard / ResearchGate)+ ForkTreeView 递归分支树(mainline 琥珀高亮 / 多选 merge / LLM critical 综合评分)

## 安装

### 前置要求

- Python 3.11+(推荐 3.13)
- Node.js 18+(可选,跑前端)
- Docker(可选,跑 m6 full_execute 沙箱)
- LLM API key(GPT/Claude 中转站)

### 后端

```bash
cd backend
python -m venv venv
# Windows
venv\Scripts\activate
# Mac/Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 前端

```bash
cd frontend
pnpm install      # 或 npm install
```

### 配置 .env

复制 `.env.example` 为 `.env`,填写 GPT/Claude 中转站密钥:

<details>
<summary>模式 A:GPT/Claude 中转站(整理版推荐)</summary>

```bash
USE_RELAY=true
RELAY_GPT_BASE_URL=https://right.codes/codex/v1
RELAY_GPT_API_KEY=sk-xxx
RELAY_CLAUDE_BASE_URL=https://www.right.codes
RELAY_CLAUDE_API_KEY=sk-xxx
RELAY_MODEL_CHAT=gpt-5.5
RELAY_MODEL_REASONER=gpt-5.5
RELAY_MODEL_CRITICAL=claude-opus-4-7
RELAY_MODEL_EMBEDDING=text-embedding-3-small
```

</details>

<details>
<summary>整理版 Phase B/C 开关</summary>

| 开关 | 默认 | 启用后效果 |
|---|---|---|
| `USE_M0_DISCOVERY` | `true` | 主图前置 M0 节点,LLM 直接基于 raw_question 生成 K 张 TopicCard |
| `M0_DEFAULT_K` | `3` | 候选课题数量(整理版默认 3-5) |
| `M0_AUTO_SELECT_TOP` | `false` | CLI 可人工选择;Web 走两阶段 TopicCard 选择 |
| `USE_M2_5_ACCESS_STATUS` | `true` | M2 与 M3 之间插入 M2.5 文献访问状态分级 |
| `USE_M5_5_GATE` | `true` | M5 与 M6 之间插入 M5.5 质量门禁(纯启发式) |
| `USE_M5_5_LLM` | `false` | M5.5 在启发式之上叠加 LLM 综合判断(更细但有成本) |

</details>

<details>
<summary>Phase 1-3 基础设施开关</summary>

| 开关 | 默认 | 说明 |
|---|---|---|
| `USE_MCP` | `false` | M2 检索走独立 MCP Server 子进程 |
| `M4_USE_ORCHESTRATOR` | `true` | M4 动态选 Reviewer(关闭则全员评审) |
| `LANGSMITH_TRACING` | `false` | 启用 LangSmith trace |
| `LANGSMITH_API_KEY` | - | LangSmith 密钥 |
| `CLAUDE_THINKING_BUDGET_META` | `0` | meta 节点 Extended Thinking token 数(建议 4000+) |
| `RUN_BUDGET_USD` | `1.0` | 单次 run 成本上限,超限抛 BudgetExceeded |
| `MONTHLY_BUDGET_USD` | `15.0` | 月预算,达 80% 告警 |
| `CODE_EXECUTION_MODE` | `generate_only` | M6 档位:`generate_only` / `dry_run` / `full_execute` |

</details>

---

## 启动

### CLI 单次跑(最快验证)

```bash
# 模式 A:用户已有方向(默认)
python -m co_scientist.cli run --question "如何让 RAG 减少 LLM 幻觉"

# 模式 B:让系统先发现候选课题
USE_M0_DISCOVERY=true python -m co_scientist.cli run --question "我想做 RAG 方向"

# 模式 C:全开整理版 Phase B+C
USE_M0_DISCOVERY=true USE_M2_5_ACCESS_STATUS=true USE_M5_5_GATE=true \
  python -m co_scientist.cli run --question "..."

# 看本月成本
python -m co_scientist.cli cost
```

<details>
<summary>cli run 完整参数</summary>

| 参数 | 默认 | 说明 |
|---|---|---|
| `--question` / `-q` | - | 用户原始研究问题(必填) |
| `--execution-mode` | `generate_only` | M6 代码档位:`generate_only` / `dry_run` / `full_execute` |
| `--fork-id` | 自动 hash | 显式指定 fork_id(LangGraph thread_id),用于断点续跑 |
| `--budget-usd` | `RUN_BUDGET_USD` | 本次 run 成本上限 |

</details>

### Web 前后端

```bash
# Terminal 1 - 后端 (8001)
uvicorn co_scientist.api.main:app --reload --port 8001

# Terminal 2 - 前端 (3000)
cd frontend && pnpm dev

# 浏览器打开 http://localhost:3000
```

前端两个视图:
- **研究视图** &mdash; 输入问题启动一次研究,实时看 5 张证据链 Card(TopicCard / AccessStatus / GapCard / DecisionCard / ResearchGate)+ Reviewer 评分 + 论文初稿路径
- **Fork 树视图** &mdash; 看历史所有 fork 的树状结构(mainline 琥珀高亮 / abandoned 灰显)+ 多选 fork 触发 merge(规则版 / LLM critical 双按钮)+ 侧栏 fork 详情 + snapshot

### Python API 多分支(整理版 Phase D)

```python
from co_scientist.modules.m0_topic_discovery import discover_topics
from co_scientist.modules.m8_replay import run_topic_branches, merge_winner

# 1. 系统先发现 3 张候选 TopicCard
cards = discover_topics("我想做 RAG 方向", k=3)

# 2. 各张 TopicCard 各跑一条完整 fork(M1→M7)
winner, all_branches = run_topic_branches("我想做 RAG 方向", cards)

# 3. LLM critical 综合评分选 winner mark mainline
mainline = merge_winner(all_branches, use_llm_compare=True)
print("winner:", mainline.fork_meta.fork_id, mainline.fork_meta.description)
```

### 附录 CLI(进化记忆 / 对抗数据工厂)

```bash
# 进化仪表盘:Reflexion 记忆库 + Prompt A/B + 技能库三库统计
python -m co_scientist.cli evolve-dashboard

# Red/Blue 单轮 / 多轮对抗
python -m co_scientist.cli adversarial-run --proposal "..."
python -m co_scientist.cli adversarial-multi --proposal "..." --max-rounds 3

# 批量产 DPO 训练集
python -m co_scientist.cli adversarial-build --input seeds.txt

# Prompt A/B 管理
python -m co_scientist.cli prompt-ab-register --name m5_experiment --file p.txt
python -m co_scientist.cli prompt-ab-best --name m5_experiment

# L3 技能库管理
python -m co_scientist.cli skill-list
python -m co_scientist.cli skill-show --name <func_name>
```

---

## 整理版 Phase A→E 升级路线

| Phase | 范围 | 关键产出 | 测试 |
|---|---|---|---|
| **A** | 模型入口 + 数据结构 | `USE_RELAY` 中转站路由 + `TopicCard` / `GapCard` / `DecisionCard` / `EvidenceAccessStatus` 四个 TypedDict + ResearchState 7 个新字段 | 91 通过(回归) |
| **B** | 候选课题 + GapCard | `M0` 模块(LLM 直生 K 张 TopicCard)+ M3 双输出 `research_gaps` + `gap_cards` + M5 设计实验注入 GapCard 先验 + 用户选课题图节点 + `USE_M0_DISCOVERY` 路由 | +7 |
| **C** | 文献状态 + 决策门禁 | `M2.5` 启发式分级 + `M4 build_decision_card` 输出结构化 DecisionCard + `M5.5 ResearchGate` 启发式优先 + 可选 LLM 叠加 | +11 |
| **D** | Git-like 多分支 | ForkManager 增强 5 个方法 + `multi_branch.py` runner(串行 + 失败隔离 + 依赖注入)+ `score_branches_with_llm`(critical 角色)+ `merge_winner` mark_mainline | +15 |
| **E** | API + 前端 | 4 个新端点(`/forks/{id}` `/branches/run` `/branches/compare` `/branches/merge`)+ WS snapshot 升级 + 前端 5 张证据链 Card + `ForkTreeView` 递归分支树 | +12 |

**累计 45 新测试,136 passed 零回归。**

---

## API 端点

`uvicorn co_scientist.api.main:app --port 8001` 启动后:

| 路由 | 方法 | 说明 |
|---|---|---|
| `/api/health` | GET | 健康检查 |
| `/api/cost` | GET | 本月累计花费 + 预算使用率 |
| `/api/cost/by-purpose` | GET | 按 purpose 聚合调用分布 |
| `/api/metrics` | GET | Prometheus 文本格式 metrics |
| `/api/research/start` | POST | 启动一次研究(返回 fork_id,后台跑) |
| `/api/research/{fork_id}/status` | GET | 查单次 run 状态 |
| `/api/forks/create` | POST | 显式创建 fork |
| `/api/forks/tree` | GET | 父→子映射 + 全部 fork 列表 |
| `/api/forks/{fork_id}` ⭐ | GET | 单条 fork 元数据 + snapshot(整理版 Phase E) |
| `/api/branches/run` ⭐ | POST | K 张 TopicCard 启动多分支(整理版 Phase D) |
| `/api/branches/compare` ⭐ | GET | 多 fork 摘要对比(逗号分隔 fork_ids) |
| `/api/branches/merge` ⭐ | POST | 选 winner mark mainline(可选 `use_llm_compare`) |
| `/ws/research/{fork_id}` | WS | 流式订阅研究进度 + 整理版全字段 snapshot |

---

## 架构

```
agent3/
├── README.md                                # 本文件
├── AI-Co-Scientist-技术方案.md             # 设计圣经(基础 8 模块 + 整理版第零章)
├── 新增架构设想_整理版.md                   # 整理版架构原文(Phase A-E 路线)
├── 项目阅读顺序.md                          # 文档/代码阅读地图
├── 面试讲稿_终极版.md                       # 面试 speedrun
├── 教学资料/                                # 12 个教学子文档(对应模块)
├── .env.example                             # 全部环境变量模板
│
├── backend/
│   ├── requirements.txt
│   ├── tests/                               # 136 passed
│   │   ├── test_modules.py                     # m1-m8 单元测试
│   │   ├── test_mcp_integration.py             # Phase 1
│   │   ├── test_orchestrator.py                # Phase 2
│   │   ├── test_phase3_infra.py                # Phase 3
│   │   ├── test_phase_b_m0_gapcard.py       ⭐ # 整理版 B 7 测试
│   │   ├── test_phase_c_decision_gate.py    ⭐ # 整理版 C 11 测试
│   │   ├── test_phase_d_multi_branch.py     ⭐ # 整理版 D 15 测试
│   │   ├── test_phase_e_api.py              ⭐ # 整理版 E 12 测试
│   │   └── evals/                              # LLM-as-Judge 评测
│   │
│   └── co_scientist/
│       ├── graph.py                         # LangGraph 主编排(11 节点 DAG)
│       ├── cli.py                           # typer CLI 入口
│       │
│       ├── api/
│       │   └── main.py                      # FastAPI(13 路由 + WS)
│       │
│       ├── config/
│       │   └── settings.py                  # pydantic-settings,所有 feature flag
│       │
│       ├── llm/
│       │   ├── base.py                      # LLMClient 抽象基类 + Message 类型
│       │   ├── openai_compat.py             # OpenAI 兼容(GPT 中转站)
│       │   ├── claude.py                    # Anthropic 协议(Claude 中转站复用本类)
│       │   └── factory.py                   # get_llm("chat" | "reasoner" | "critical")
│       │
│       ├── state/
│       │   ├── research_state.py            # ResearchState 主类型(整理版加 7 字段)
│       │   └── cards.py                  ⭐ # 整理版数据契约:TopicCard/GapCard/
│       │                                       DecisionCard/EvidenceAccessStatus
│       │
│       ├── prompts/
│       │   └── templates.py                 # 所有 SYSTEM_M*/USER_M* 模板
│       │
│       ├── modules/
│       │   ├── m0_topic_discovery/       ⭐ # 整理版 B:M0 候选课题发现器
│       │   ├── m1_refiner/                  # PICO 问题精炼
│       │   ├── m2_retriever/                # 多源检索 + RRF + Embed Rerank
│       │   │   ├── sources/                    # 原生 async 函数
│       │   │   ├── mcp_servers/                # Phase 1:独立 MCP Server
│       │   │   └── mcp_client.py               # Phase 1:MCP Client
│       │   ├── m2_5_access_status/       ⭐ # 整理版 C:文献访问状态启发式
│       │   ├── m3_kg/                       # 知识图谱 + GapCard 双输出
│       │   ├── m4_critique/                 # Orchestrator-Subagent 评审圆桌
│       │   │   ├── reviewers.py                # Persona + REVIEWER_REGISTRY
│       │   │   ├── orchestrator.py             # Phase 2:动态选 Reviewer
│       │   │   └── roundtable.py               # 编排 + Devil + Meta + DecisionCard
│       │   ├── m5_experiment/               # 实验设计(继承 GapCard 先验)
│       │   ├── m5_5_research_gate/       ⭐ # 整理版 C:质量门禁
│       │   ├── m6_code/                     # 三档代码生成 + Docker 沙箱
│       │   ├── m7_writer/                   # IMRaD 论文初稿 + 引用幻觉检测
│       │   └── m8_replay/                   # Git-like 多分支管理
│       │       ├── fork_manager.py             # ForkMeta + 元数据 SQLite
│       │       └── multi_branch.py       ⭐ # 整理版 D:批量 runner + LLM compare + merge
│       │
│       ├── appendix/
│       │   ├── evolve/                      # 附录 A:三层进化引擎
│       │   │   ├── memory.py                   # L1 Reflexion 经验记忆
│       │   │   ├── prompt_ab.py                # L2 Prompt A/B Bandit
│       │   │   └── skill_library.py            # L3 Voyager 技能库
│       │   └── adversarial/
│       │       └── red_blue.py                 # 附录 B:Red/Blue/Judge 对抗
│       │
│       └── utils/
│           ├── logger.py                    # loguru
│           ├── cost_tracker.py              # SQLite 计费 + 月预算告警
│           ├── budget_guard.py              # Phase 3:ContextVar run 级硬上限
│           ├── observability.py             # Phase 3:LangSmith 初始化
│           └── cache.py                     # diskcache 兜底
│
├── frontend/                                # Next.js 15 + React 19
│   ├── package.json
│   ├── next.config.js                       # /api/* 代理到 :8001
│   └── src/
│       ├── app/
│       │   └── page.tsx                     # 主页:研究视图 + Fork 树视图切换
│       └── components/
│           └── ForkTree.tsx              ⭐ # 整理版 E:递归分支树 + merge 按钮
│
├── docker-compose.yml                       # Qdrant + Neo4j + Postgres
└── data/                                    # 运行时数据(.gitignore)
    ├── cache/                                  # LLM 响应缓存
    ├── checkpoints/                            # LangGraph SqliteSaver(thread_id=fork_id)
    ├── outputs/                                # 论文 LaTeX 等产物
    ├── memory.db                               # 附录 A 经验记忆库
    ├── prompts_ab.db                           # 附录 A Prompt A/B
    ├── skills.db                               # 附录 A 技能库
    ├── forks.db                                # M8 fork 元数据
    └── cost_tracker.db                         # LLM 调用计费
```

### LLM 路由

```
                业务模块只调三个语义角色
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
  get_llm("chat")  get_llm("reasoner") get_llm("critical")
        │                 │                 │
        └─────────────────┼─────────────────┘
                          ▼
                  llm/factory.py
                          │
              ┌───────────┴───────────┐
              │                       │
        GPT 中转站               Claude 中转站
              │                       │
       chat / reasoner             critical
              │                       │
          gpt-5.5             claude-opus-4-7

USE_RELAY 是旧配置兼容字段,运行时不再切回 DeepSeek。
```

### 卡片数据契约

```
       ┌─────────┐                    ┌────────────┐
       │   M0    │──── TopicCard ────►│ user_select│
       └─────────┘                    └─────┬──────┘
                                            │ candidate_question → raw_question
                                            ▼
       ┌─────────┐  ┌────────────────────────────────┐
       │   M2    │  │  evidence_access_status        │
       └────┬────┘  │  (per paper:                   │
            │       │   access_status / has_code /   │
            ▼       │   has_dataset / evidence_level)│
       ┌─────────┐  └────────────────────────────────┘
       │  M2.5   │──────────►          │
       └────┬────┘                     │
            ▼                          │
       ┌─────────┐                     │
       │   M3    │──── GapCard ───────►│  (datasets/baselines/metrics)
       └─────────┘     gap_cards[*]    │
                                       ▼
                                ┌──────────────┐
                                │      M4      │
                                │ Roundtable + │
                                │ DecisionCard │
                                └───────┬──────┘
                                        │ recommended_action / target_node
                                        │ branch_count / final_rating
                                        ▼
                                ┌──────────────┐
                                │      M5      │ ◄─ GapCard 先验注入
                                └───────┬──────┘
                                        ▼
                                ┌──────────────┐
                                │     M5.5     │ ◄─ DecisionCard / AccessStatus
                                │ ResearchGate │
                                └──────────────┘
                                        │
                              gate_decision(6 选 1)
                                        │
                                        ▼
                            写入 state.metadata.research_gate
                              (Phase D M8 据此派生新 fork)
```

### M8 Git-like 多分支状态机

```
   ┌──────────┐         create_fork           ┌──────────┐
   │  root    │──────────────────────────────►│ running  │
   └──────────┘                                └─────┬────┘
                                                     │
                          run_pipeline(thread_id=fork_id)
                                                     │
                  ┌──────────────────────┬───────────┤
                  ▼                      ▼           ▼
            ┌──────────┐          ┌──────────┐ ┌──────────┐
            │   done   │          │abandoned │ │ running  │
            │(成功跑完)│          │ (失败)   │ │(进行中)  │
            └─────┬────┘          └──────────┘ └──────────┘
                  │
                  │  merge_winner(mainline 同父唯一)
                  ▼
            ┌──────────┐
            │ mainline │ ◄─ 当前主线指针,前端琥珀高亮
            └──────────┘
```

### 神经决策核心 &mdash; M4 Roundtable

```
              refined_question + evidence + experiment_brief
                                  │
                                  ▼
                    ┌─────────────────────────┐
                    │  M4 Orchestrator Agent  │
                    │  动态选 3-5 Reviewer    │
                    └────────────┬────────────┘
                                 │
        ┌────────┬───────┬───────┼────────┬────────┬─────────┐
        ▼        ▼       ▼       ▼        ▼        ▼         ▼
   ┌────────┐┌─────────┐┌────────┐┌─────────┐┌────────┐┌───────────┐
   │novelty ││methodolog││statisti││reproduce││ devil  ││domain_*   │
   │reviewer││reviewer  ││reviewer││reviewer ││ (强制) ││specific   │
   └────┬───┘└────┬─────┘└───┬────┘└────┬────┘└────┬───┘└─────┬─────┘
        └────────┴───────┬──┴──────────┴──────────┘          │
                         │  并行评审 → CritiqueCard[*]        │
                         ▼                                    │
              ┌──────────────────┐                            │
              │ pvariance > 2.0? │ Yes ──► Devil Round 2 ─────┤
              │   (rating)       │                            │
              └──────────────────┘                            │
                         │ No                                 │
                         ▼                                    │
              ┌──────────────────────────┐                    │
              │  Meta-Reviewer (critical)│                    │
              │  Claude Opus + Extended  │                    │
              │  Thinking 4000 tokens    │                    │
              └────────────┬─────────────┘                    │
                           │                                  │
                           ▼                                  │
                    meta_decision(legacy)                     │
                           │                                  │
                           ▼                                  │
              ┌──────────────────────────┐                    │
              │  build_decision_card()   │ ◄─ GapCard summary │
              │  (整理版 Phase C)         │ ◄─ AccessStatus ───┘
              └────────────┬─────────────┘
                           │
                           ▼
                  DecisionCard(passed/decision/
                  recommended_action/target_node/
                  branch_count/blocking_issues)
```

---

## 加新模块

整理版架构鼓励"模块即插件",加一个新模块跟着这条线:

1. 在 `backend/co_scientist/modules/mX_yourname/` 新建包,实现 `xxx_node(state) -> dict` 函数,返回 patch dict(LangGraph 自动 merge)。失败兜底交给 graph.py 的 `safe_node()` 包装。
2. 在 `prompts/templates.py` 加 `SYSTEM_MX_YOURMOD` / `USER_MX_YOURMOD` 模板。
3. 在 `state/research_state.py` 加新字段(若产出新数据);`state/cards.py` 加跨模块流转的 TypedDict 契约。
4. 在 `graph.py` 的 `build_graph()` 用 `add_node` + `add_edge` 把新节点串入 DAG,通过 `settings.USE_X` 做 feature flag 路由。
5. 在 `config/settings.py` 加对应开关,默认 `False` 保证向后兼容。
6. 在 `tests/test_phase_x_yourmod.py` 用 `FakeLLM` 桩做单测;别忘了正常路径 + 失败兜底 + 节点跳过(已有数据时)。
7. 在 `api/main.py` 的 `_build_snapshot()` 加新字段;前端 `page.tsx` 加对应 Card 组件。

参考 [整理版 Phase A-E 实施细节](./AI-Co-Scientist-技术方案.md#04a-phase-a-实施细节本次落地)。

---

## 加新检索源

```bash
# 1. backend/co_scientist/modules/m2_retriever/sources/yourname.py 实现 async fetch_papers(query)
# 2. retriever.py 的 SOURCE_REGISTRY 注册
# 3. (可选)mcp_servers/yourname_server.py 暴露成 MCP Server,照 arxiv_server.py 模板
# 4. mcp_client.py 加 source 名映射
```

---

## 测试

```bash
cd backend

# 全量(不含联网)
pytest tests/

# Phase A-E 整理版 45 个新测试
pytest tests/test_phase_b_m0_gapcard.py \
       tests/test_phase_c_decision_gate.py \
       tests/test_phase_d_multi_branch.py \
       tests/test_phase_e_api.py -v

# Agent Evals(mock 模式,零成本)
EVAL_MOCK=1 pytest tests/evals/ --run-evals

# 联网端到端
pytest tests/ --run-net
```

当前测试统计:**136 passed, 9 skipped**(基础 91 + Phase B 7 + C 11 + D 15 + E 12),零回归。

---

## 参考文献

- Silver, D. et al. *Mastering the game of Go without human knowledge.* Nature 550, 354-359 (2017).
- Anthropic. *Building effective agents.* (2024)
- Anthropic. *How we built our multi-agent research system.* (2025.4) &mdash; 对应 Phase 2 Orchestrator-Subagent 范式
- Anthropic. *Model Context Protocol specification.* (2024.11) &mdash; 对应 Phase 1 MCP 工具层
- Shinn, N. et al. *Reflexion: Language Agents with Verbal Reinforcement Learning.* NeurIPS (2023) &mdash; 对应附录 A.L1
- Wang, G. et al. *Voyager: An Open-Ended Embodied Agent with Large Language Models.* arXiv (2023) &mdash; 对应附录 A.L3 SkillLibrary
- Khattab, O. et al. *DSPy: Compiling Declarative Language Model Calls into Self-Improving Pipelines.* (2023) &mdash; 对应附录 A.L2 Prompt A/B
- Rafailov, R. et al. *Direct Preference Optimization.* NeurIPS (2023) &mdash; 对应附录 B 偏好数据

完整设计与教学资料见 [AI-Co-Scientist-技术方案.md](./AI-Co-Scientist-技术方案.md) 与 [项目阅读顺序.md](./项目阅读顺序.md)。

## 许可证

[MIT](LICENSE)
