# 12. 生产级基础设施:观测性 / 推理预算 / 成本护栏

> **本章学什么**:把一个 Demo 级 Agent 项目升级到"能生产运行"的三件配套基础设施。
>
> 对标:Devin / Cognition、Replit Agent、Claude Code 这类长跑 Agent 都必做的事。

---

## 12.1 为什么需要这一层

Demo 级 Agent 和生产级 Agent 的分水岭,不是"能跑",而是**能持续跑、能看见、能兜住**:

| 问题 | Demo 阶段 | 生产阶段怎么办 |
|---|---|---|
| 昨天那条 run 哪步挂的? | 翻 log 找半天 | **Trace 回放**(LangSmith) |
| Meta 终裁为什么给 3 分? | 不知道模型想了啥 | **Extended Thinking** 可见 |
| 半夜 bug 把 API 账户打爆 | 第二天看账单哭 | **Budget Guard** 硬拦截 |
| 模型供应商坏了 / 涨价 / 换中转站 | 业务代码到处改 | **统一中转站 + 三角色路由**(整理版 §3) |

这四件事在本项目里分别对应:
- **观测性** → `utils/observability.py`(LangSmith 集成)
- **推理预算** → `llm/claude.py`(Extended Thinking)
- **成本护栏** → `utils/budget_guard.py`(BudgetExceeded 硬上限)
- **模型路由** → `llm/factory.py`(USE_RELAY 切换中转站,业务模块只调 chat/reasoner/critical 三角色)

---

## 12.2 LangSmith 观测性

### 12.2.1 是什么

LangSmith 是 LangChain 官方的 tracing / 评估平台(langsmith.com)。
开启后,**LangGraph 的每个节点**、**每次 LLM 调用**、**每个工具调用**自动上报,
可按 `thread_id` 把整条 run 串起来在 Web 上回放。

### 12.2.2 为什么选 LangSmith(而不是自建)

| 选项 | 优点 | 缺点 |
|---|---|---|
| **LangSmith** | 和 LangGraph 零集成成本、免费额度 5000 trace/月 | 需要注册、数据托管在第三方 |
| OpenTelemetry + Jaeger | 完全自主可控 | 要自己搭 collector、UI 粗糙 |
| Arize Phoenix | 开源自建 | 要维护服务、上手曲线 |
| Langfuse | 开源自建 | 同上 |

Demo / 面试场景:**LangSmith 最轻量**。

### 12.2.3 启用方法

1. 在 `.env` 加:
   ```
   LANGSMITH_TRACING=true
   LANGSMITH_API_KEY=ls_xxx
   LANGSMITH_PROJECT=co-scientist
   ```

2. 什么都不做。`build_graph()` 入口会自动调 `setup_langsmith()`,

   环境变量 export 后 LangChain SDK 会在每次 LLM 调用内部自动上报。

3. 到 https://smith.langchain.com 看 trace,按 `thread_id`(我们用 `fork_id`)搜索。

### 12.2.4 核心实现

```python
# utils/observability.py
_LANGSMITH_INITIALIZED = False

def setup_langsmith() -> bool:
    global _LANGSMITH_INITIALIZED
    if _LANGSMITH_INITIALIZED:
        return True
    if not settings.LANGSMITH_TRACING:
        return False

    # LangChain 历史包袱:底层仍看 LANGCHAIN_* 前缀,新名字 LANGSMITH_*
    # 兼容期两套都 export 最稳
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = api_key
    os.environ["LANGCHAIN_PROJECT"] = settings.LANGSMITH_PROJECT
    os.environ["LANGSMITH_TRACING"] = "true"
    # ...
    _LANGSMITH_INITIALIZED = True
    return True
```

**设计要点**:
- **idempotent**:同进程多次调用只真正生效一次
- **无 Key 静默跳过**:demo 场景不强制注册,体验更顺
- **延迟初始化**:放在 `build_graph()` 里,不在 import 时做 side effect

### 12.2.5 面试讲点

> "我接了 LangSmith 做 trace,每次 run 都有完整 trajectory —— 节点级调用、
> LLM I/O、cost、latency 都能看。改 Prompt 时可以直接对比两版运行,
> 不用靠看控制台日志猜模型输出差别。"

---

## 12.3 Extended Thinking(Claude 推理预算)

### 12.3.1 是什么

Claude Sonnet 4+ / Opus 4 支持**显式思考预算** —— 让模型在回答前做 N 个 token 的
**内部推理**(类似 OpenAI o1/o3 的 reasoning tokens,DeepSeek-R1 的思考链)。

API 调用时传:
```python
thinking={"type": "enabled", "budget_tokens": 4000}
```

模型会先花掉这 ~4000 token 做深度思考,再产出最终回答。**回答质量在复杂推理
题上能提升 10-20%**(Anthropic 官方 benchmark)。

### 12.3.2 为什么不全局开

Extended Thinking 不是免费午餐:
- 贵(思考 token 也算钱)
- 慢(多输出几千 token 要等几秒)
- 简单任务上纯浪费(格式转换、概要抽取不需要"深思")

**策略:按任务分级**。

### 12.3.3 本项目的分级

在 `ClaudeClient.chat()` 里按 `purpose` 智能选择:

```python
if budget is None:
    if "meta" in (purpose or "").lower():
        budget = settings.CLAUDE_THINKING_BUDGET_META  # 建议 4000-8000
    else:
        budget = settings.CLAUDE_THINKING_BUDGET_DEFAULT  # 0(关闭)
```

这意味着:
- m4 Meta 终裁 → 自动开大预算(高风险决策值得深思熟虑)
- 普通 Claude 调用(如 Reviewer Meta 用不到的场景)→ 默认关闭

### 12.3.4 硬约束

Anthropic API 规定:
- `budget_tokens` 必须小于 `max_tokens`(代码自动抬高 max_tokens)
- `temperature` 必须为 1.0(我们强制覆盖)
- 只有特定模型支持(Claude Sonnet 4+ / Opus 4+)

### 12.3.5 代码最小示例

```python
from co_scientist.llm import get_llm

llm = get_llm("critical")  # Claude Opus 4.7

# 自动启用(因为 purpose 含 "meta")
resp = llm.chat(
    messages=[{"role": "user", "content": "评审这个方案..."}],
    purpose="m4_meta_decision",
)

# 显式控制(覆盖自动逻辑)
resp = llm.chat(
    messages=[{"role": "user", "content": "..."}],
    purpose="m7_writer",
    thinking_budget=2000,  # 强制开 2000 token 推理
)

# 显式关闭(覆盖 settings.CLAUDE_THINKING_BUDGET_META)
resp = llm.chat(
    messages=[...],
    purpose="m4_meta",
    thinking_budget=0,
)
```

### 12.3.6 面试讲点

> "Meta 终裁是高风险决策,我启用了 Claude 的 Extended Thinking,给它 4000-8000
> token 的推理预算,对应 Anthropic 2025 推出的推理模型 API。按 `purpose` 字段
> 自动分级 —— 普通调用不开,只有 'meta' 这种关键节点才启用。好处:决策质量提升,
> 但大部分调用的成本和延迟不变。"

---

## 12.4 Budget Guard(成本护栏)

### 12.4.1 为什么必做

真实案例:
- Devin 早期有 PR 报告"Agent 死循环,一次跑烧掉 $40"
- Claude Code 早期用户反馈"忘了停 Agent,周末回来收到账单"

Agent 长跑的核心风险不是"慢",是"**在某个 bug 触发时反复调用 LLM**"。
没有 Budget Guard = 你的 API Key 就是定时炸弹。

### 12.4.2 本项目实现

```python
# utils/budget_guard.py
import contextvars

_RUN_SPENT = ContextVar("run_spent", default=0.0)
_RUN_BUDGET = ContextVar("run_budget", default=0.0)

@contextmanager
def budget_guard(limit_usd: float):
    spent_token = _RUN_SPENT.set(0.0)
    budget_token = _RUN_BUDGET.set(max(0.0, limit_usd))
    try:
        yield
    finally:
        _RUN_SPENT.reset(spent_token)
        _RUN_BUDGET.reset(budget_token)

def charge(cost_usd: float) -> None:
    budget = _RUN_BUDGET.get()
    if budget <= 0:
        return  # 未进入 guard,不记账
    new_total = _RUN_SPENT.get() + cost_usd
    _RUN_SPENT.set(new_total)
    if new_total > budget:
        raise BudgetExceeded(spent=new_total, budget=budget)
```

**挂载点**:`cost_tracker.add()` 在每次 LLM 调用落库后调 `charge(cost)`。

**入口包裹**:`run_pipeline()` 用 `with budget_guard(settings.RUN_BUDGET_USD)` 包住
整条 graph.invoke。

### 12.4.3 关键设计

#### (1) 为什么用 ContextVar 而不是全局变量
ContextVar 天然支持 asyncio,**多个 run 并发时互不"偷钱"**。FastAPI 多请求并发跑
m4 圆桌,每个请求自己的预算独立。

#### (2) 为什么超限用 raise 而不是返回 flag
- raise 立刻中断后续 LLM 调用,**避免"已经超了还在调"**
- LangGraph 的 `safe_node` 捕获异常写 error_log,流程优雅结束
- 上层能 catch 到 BudgetExceeded,给用户清晰提示

#### (3) 为什么 limit_usd=0 当"不限"
用户有意关闭时(如内部测试)不应该被"0 就是 0 成本"卡住。显式关闭 > 意外启用。

#### (4) 为什么不挂锁
ContextVar 每个 run 独立实例,**单 run 内部 LLM 调用是串行的**(或在 await 点让出
控制权,但同一 context 下),不会有并发问题。

### 12.4.4 使用示例

```python
from co_scientist.graph import run_pipeline
from co_scientist.utils.budget_guard import BudgetExceeded

try:
    state = run_pipeline(
        raw_question="RAG 如何降低幻觉?",
        budget_usd=0.5,  # 给这次 run 50 美分上限
    )
except BudgetExceeded as e:
    print(f"预算超了:{e.spent:.4f} > {e.budget:.2f},已中断")
```

### 12.4.5 面试讲点

> "Agent 长跑最大的风险是成本失控,我加了一层 BudgetGuard,
> 每次 run 默认 $1 上限,超了直接抛 BudgetExceeded 中断。
> 这对应 Devin / Cognition 的设计 —— 不能让 Agent 因为 bug 把你账户跑空。
> 实现用 ContextVar 做 run 级隔离,多请求并发时互不干扰。"

---

## 12.4.5 模型路由 / GPT-Claude 中转站(整理版 Phase A)

### 12.4.5.1 是什么

整理版架构 §3 决策:把所有模型调用统一收敛到 **GPT/Claude 中转站**,业务模块只依赖三个语义角色:

```
chat       → 日常生成、写作、抽取(整理版推荐 gpt-5.5)
reasoner   → 推理、评审、决策(整理版推荐 gpt-5.5)
critical   → 关键裁决、Meta-Reviewer 终裁、Extended Thinking(整理版推荐 claude-opus-4-7)
```

### 12.4.5.2 为什么不让业务模块直接 import 模型

| 反面例子 | 真问题 |
|---|---|
| `from openai import OpenAI; client = OpenAI(model="gpt-4o")` 散落在 m1/m4/m5 | 涨价/换供应商时要改 N 处 |
| 不同模块 hardcode 不同 model 名 | A/B 测试不可能,成本统计不准 |
| Claude 调用直接 `anthropic.Anthropic()` | 中转站换 base_url 时整套代码崩 |

整理版方案:

```
业务模块  →  get_llm("chat" | "reasoner" | "critical")  →  factory  →  按 USE_RELAY 路由
```

业务代码永远不知道当前用的是 GPT/Claude/DeepSeek 哪一个,也不知道 base_url 长啥样。

### 12.4.5.3 怎么开/关中转站

`.env`:
```
USE_RELAY=true
RELAY_GPT_BASE_URL=https://right.codes/codex/v1
RELAY_GPT_API_KEY=sk-xxx
RELAY_CLAUDE_BASE_URL=https://www.right.codes
RELAY_CLAUDE_API_KEY=sk-xxx
RELAY_MODEL_CHAT=gpt-5.5
RELAY_MODEL_REASONER=gpt-5.5
RELAY_MODEL_CRITICAL=claude-opus-4-7
```

代码侧零改动。

### 12.4.5.4 关键设计:复用 DeepSeek/Claude 客户端,而不是新写

GPT 中转站走 OpenAI 兼容协议,DeepSeekClient 已经基于 openai SDK,只需让 `__init__` 接受 `base_url/api_key/family` 参数,即可同一份代码同时跑 DeepSeek 与 GPT 中转站。Claude 中转站走 Anthropic 原生协议,ClaudeClient 同样参数化即可复用。

### 12.4.5.5 成本控制策略(整理版 §3.4)

```
80%-90% 调用走 chat (cheapest)
10%-20% 调用走 reasoner
1%-5%   调用走 critical (Claude Extended Thinking,贵但准)
```

critical 节点采用双阶段:Stage1 GPT reasoner 初裁 → Stage2 触发条件满足时 Claude critical 复核。
触发条件:M4 reviewer 方差大 / final_rating 灰区 5.5-7.0 / blocking issue 但 Meta 想通过 / M8 多分支评分接近 / 高风险方向 / 用户显式 strong_review=true。

---

## 12.5 三件基础设施的协作

```
┌──────────────────────────────────────────────────────────────┐
│                      一次完整 run                             │
├──────────────────────────────────────────────────────────────┤
│  run_pipeline(q, budget=1.0)                                 │
│    │                                                          │
│    ├─ setup_langsmith()    ← 观测:trace 上报开始             │
│    │                                                          │
│    └─ with budget_guard(1.0):    ← 成本:累计 + 硬上限        │
│         graph.invoke(initial)                                 │
│            │                                                  │
│            ├─ m1, m2, m3 ...                                 │
│            │                                                  │
│            ├─ m4 Meta Reviewer(Claude)                       │
│            │    └─ thinking_budget=4000  ← 推理:深度思考     │
│            │                                                  │
│            └─ m5, m6, m7, appendix_reflect                    │
│                                                               │
│    → 每次 LLM 调用:                                          │
│        ├─ LangSmith 自动 trace(env var 驱动)                │
│        ├─ CostTracker 记 cost                                │
│        └─ budget_guard.charge() 累加,超限抛 BudgetExceeded  │
└──────────────────────────────────────────────────────────────┘
```

**三件事互相正交**:
- 观测性只"看",不改行为
- 推理预算只改 Claude 单次调用
- 成本护栏只中断,不改路径

每件独立开关,互不影响。

---

## 12.6 测试

所有测试在 `backend/tests/test_phase3_infra.py`,14 个测试默认全跑(不需联网、不花钱):

```bash
pytest tests/test_phase3_infra.py -v
# 14 passed in 2.6s
```

覆盖:
- Part A(4 个):LangSmith 开关、env 导出、idempotent
- Part B(4 个):Extended Thinking 按 purpose 分流、显式覆盖、0 关闭
- Part C(6 个):Budget 累计、超限抛、run 隔离、端到端 CostTracker 联动

---

## 12.7 面试叙事(三件事串讲)

> "我这个项目加了三层生产级基础设施:
>
> 第一,**观测性** — LangSmith tracing,每条 run 都能在 Web 回放,面试时可以直接
> 分享 trace 链接给你看。
>
> 第二,**推理预算** — Claude Extended Thinking,对应 Anthropic 2025 API,Meta 终裁
> 这种高风险节点自动开 4000+ token 的思考预算。按 purpose 分级,普通节点不开,
> 不浪费。
>
> 第三,**成本护栏** — Budget Guard,用 ContextVar 做 run 级隔离,每次跑默认 $1
> 上限,超了抛 BudgetExceeded 硬中断,防 Agent bug 把账户打爆。对应 Devin /
> Cognition 的设计。
>
> 这三件事合起来让项目从 Demo 级 **能看见、能兜住、能扩展**,是真能上生产的。"

---

## 12.8 进阶练手

1. **把 CostTracker 也接到 LangSmith**:LangSmith 有 `trace.add_metadata({'cost': x})`
   接口,把每次调用的成本也上报,可以在 Web 端看成本分布
2. **Extended Thinking thinking block 日志化**:Claude 的 `resp.content` 里 `type=="thinking"`
   的 block 包含完整推理链,落盘以便回溯(现在被代码忽略了)
3. **Budget Guard 升级成 Token Guard**:不只看美元,也看 token 总量(避免某个 bug
   疯狂生成长文本)
4. **按节点细分预算**:m2 上限 $0.1、m4 上限 $0.3、m7 上限 $0.2,更精细

---

## 12.9 参考

- [LangSmith 文档](https://docs.smith.langchain.com/)
- [Anthropic Extended Thinking](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking)
- [Devin 技术博客(成本控制)](https://cognition.ai/blog)
- [OpenAI o1/o3 Reasoning](https://platform.openai.com/docs/guides/reasoning)
