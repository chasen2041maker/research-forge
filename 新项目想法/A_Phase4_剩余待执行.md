# A 方向 · Phase 4 执行手册(在线 Demo + 架构演进博客)

> **这份文档是独立可执行的**:无论你之后是自己推进,还是让另一个 Claude 会话接力,都能按这个直接开干,不用回忆之前的上下文。

---

## ⚡ 当前项目状态快照(2026-04-23 后定格)

A 方向 3 个 Phase 已完成,**全部通过测试,零回归**。

### 已落地的能力

| Phase | 新增模块 | 能讲什么 |
|---|---|---|
| **Phase 1** MCP 适配层 | `m2_retriever/mcp_servers/`、`mcp_client.py`、11 章教学文档 | 对齐 2024.11 Anthropic MCP 标准,检索源可被 Claude Desktop/Cursor 复用 |
| **Phase 2** Orchestrator-Subagent | `m4_critique/orchestrator.py`、12 测试、05 章新增 5.11 节 | 对齐 2025.4 Anthropic 多 Agent 架构,动态选 Reviewer + devil 强制 + 降级兜底 |
| **Phase 3** 生产级基础设施 | `utils/observability.py`、`utils/budget_guard.py`、Claude Extended Thinking、12 章新建 | LangSmith trace + Extended Thinking + run 级成本护栏 |

### 现有的关键开关(settings.py)

```python
USE_MCP: bool = False                      # True 走 MCP Server 模式
M4_USE_ORCHESTRATOR: bool = True           # 动态选 Reviewer
LANGSMITH_TRACING: bool = False            # 设 True 并配 Key 开 trace
CLAUDE_THINKING_BUDGET_META: int = 0       # 建议 4000
CLAUDE_THINKING_BUDGET_DEFAULT: int = 0
RUN_BUDGET_USD: float = 1.0                # 单次 run 成本上限
```

### 测试状态

```bash
cd backend
python -m pytest tests/ --ignore=tests/test_smoke.py -q
# 91 passed, 9 skipped  (test_smoke 因本地未装 langgraph 被排除,非代码 bug)
```

### 当前目录结构(只列新增 / 改动的部分)

```
agent3/
├── README.md                                    ← 顶部架构图已标注 MCP 层
├── backend/
│   ├── requirements.txt                         ← 已加 mcp>=1.0.0
│   ├── co_scientist/
│   │   ├── config/settings.py                   ← 已加 USE_MCP / LANGSMITH / Budget 等开关
│   │   ├── graph.py                             ← build_graph 里挂 LangSmith,run_pipeline 包 budget_guard
│   │   ├── llm/claude.py                        ← chat() 支持 thinking_budget
│   │   ├── modules/m2_retriever/
│   │   │   ├── mcp_servers/                     ← 新增,3 个 Server + _common + __init__
│   │   │   ├── mcp_client.py                    ← 新增
│   │   │   └── retriever.py                     ← feature flag 分流
│   │   ├── modules/m4_critique/
│   │   │   ├── orchestrator.py                  ← 新增
│   │   │   ├── reviewers.py                     ← 新增 REVIEWER_REGISTRY
│   │   │   └── roundtable.py                    ← 接入 Orchestrator
│   │   ├── prompts/templates.py                 ← 新增 SYSTEM_M4_ORCHESTRATOR
│   │   └── utils/
│   │       ├── observability.py                 ← 新增
│   │       ├── budget_guard.py                  ← 新增
│   │       └── cost_tracker.py                  ← add() 内联 budget hook
│   └── tests/
│       ├── conftest.py                          ← 注册 net / eval marker
│       ├── test_mcp_integration.py              ← 新增 9 测试
│       ├── test_orchestrator.py                 ← 新增 12 测试
│       └── test_phase3_infra.py                 ← 新增 14 测试
└── 教学资料/
    ├── README.md                                ← 章节表扩到 12 章
    ├── 05-多Agent协作/README.md                 ← 5.1 节 + 新增 5.11 节
    ├── 09-进化与对抗/README.md                  ← 落地表加 Evals 一行
    ├── 11-MCP与外部集成/                        ← 新建整章
    └── 12-生产级基础设施/                       ← 新建整章
```

---

## 🎯 Phase 4 要做什么

| 任务 | 工期 | 前置要求 | 产出 |
|---|---|---|---|
| **4.1** 在线 Demo 部署 | 1 天 | 选定平台 + 有账号 | 一个可访问的公网 URL |
| **4.2** 架构演进博客(中文) | 1 天 | 4.1 完成(博客引用 Demo URL) | 3000-5000 字 |
| **4.3** 架构演进博客(英文) | 半天 | 4.2 完成(翻译压缩) | 1500-2500 字 |

**总投入**:2-3 天专注时间 / 1-2 周业余时间。

---

## 📦 Phase 4.1:在线 Demo 部署

### 决策树:选哪个平台

```
你的目标是什么?
├─ 最快出一个能公开访问的链接(优先速度)
│    → Hugging Face Spaces(零成本、5 分钟部署、自带 Gradio UI)
│
├─ 想完整展示前后端分离的架构(优先完整度)
│    → Railway(有 Docker 支持、有 Postgres/Redis、$5/月)
│
└─ 想把 Next.js 前端做得好看(优先前端视觉)
      → 前端 Vercel + 后端 Railway 分开部
```

**推荐路径**(按性价比排序):

1. 🥇 **Hugging Face Spaces**:最快,适合演示用
2. 🥈 **Railway**(单容器):适合展示完整项目
3. 🥉 **Vercel + Railway**(前后端分离):最完整但最复杂

---

### 路径 A:Hugging Face Spaces(推荐新手)

#### 优势
- 免费(16GB RAM / 2 vCPU)
- 自动 HTTPS
- Gradio 界面自带
- 不用自己买域名
- Claude Code / 面试官打开就能用

#### 劣势
- 只跑一个 Python 进程(前端 Next.js 没法用,除非 Docker Space)
- LLM Key 要在 HF Settings 里设为 Secret

#### 执行步骤

**Step 1**:在 HF 注册 → https://huggingface.co/

**Step 2**:建 Space
- Space SDK 选 **Docker**(要跑完整后端 + 可能的 Next.js)
- 或选 **Gradio**(最简单,只演示主 pipeline)

**Step 3**(Gradio 路径,最简):在 `backend/` 新建 `app.py`
```python
"""
Hugging Face Spaces 入口。
跑一个极简 Gradio 界面,让访客能输入研究问题看全流程跑。
"""
import gradio as gr
from co_scientist.graph import run_pipeline

def research(question: str) -> str:
    if not question.strip():
        return "请输入研究问题"
    state = run_pipeline(
        raw_question=question,
        execution_mode="generate_only",  # 不开 Docker 沙箱
        budget_usd=0.3,  # HF 上每次跑 30 美分上限防滥用
    )
    pico = state.get("pico", {})
    meta = state.get("meta_decision", {})
    return (
        f"### PICO\n{pico}\n\n"
        f"### Meta 终裁\n{meta}\n\n"
        f"### Orchestrator 选了\n{meta.get('orchestrator', {}).get('reviewers', [])}"
    )

demo = gr.Interface(
    fn=research,
    inputs=gr.Textbox(label="研究问题", placeholder="RAG 如何降低 LLM 幻觉?"),
    outputs=gr.Markdown(),
    title="AI Co-Scientist (演示版)",
    description="LangGraph + Orchestrator-Subagent + MCP + Reflexion"
)
if __name__ == "__main__":
    demo.launch()
```

**Step 4**:在 HF Space Settings → Repository secrets 加:
- `DEEPSEEK_API_KEY`
- `ANTHROPIC_API_KEY`
- `RUN_BUDGET_USD=0.3`

**Step 5**:`git push` 到 HF 仓库,等 2-3 分钟构建完成。

**验收**:打开 `https://huggingface.co/spaces/你的用户名/co-scientist`,输入问题能看到结果。

---

### 路径 B:Railway(推荐进阶)

#### 优势
- 有 Postgres / Redis / 多服务
- Docker Compose 原生支持
- CLI 友好
- `$5/月` 起

#### 执行步骤

**Step 1**:安装 Railway CLI
```bash
npm install -g @railway/cli
railway login
```

**Step 2**:初始化项目
```bash
cd agent3
railway init
# 选 "Empty Project"
```

**Step 3**:写 `Dockerfile`(backend 目录)
```dockerfile
FROM python:3.13-slim

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# 用 uvicorn 跑 FastAPI(如果没有 FastAPI,先用 gradio app.py)
CMD ["uvicorn", "co_scientist.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Step 4**:Railway Variables 界面加 Key
- `DEEPSEEK_API_KEY`
- `ANTHROPIC_API_KEY`
- `RUN_BUDGET_USD=0.5`

**Step 5**:部署
```bash
railway up
```

Railway 会给一个 `xxx.up.railway.app` 域名,等 2 分钟就能访问。

**Step 6**(可选):加 Postgres
- Railway 界面 → Add Plugin → PostgreSQL
- 自动注入 `DATABASE_URL` 环境变量
- 修改 `settings.POSTGRES_URL` 读这个

---

### 路径 C:Vercel + Railway(前后端分离)

**前端**(Next.js)部 Vercel,**后端**部 Railway。

**前端 .env**:
```
NEXT_PUBLIC_API_URL=https://你的-railway-app.up.railway.app
```

**后端 CORS**:要在 FastAPI 里加 `CORSMiddleware` 允许 Vercel 域名。

复杂度比 B 高 50%,**除非前端要给视觉印象分,否则建议先做 B**。

---

### 通用部署 checklist

- [ ] API Key 只放 platform secret,**绝不 commit 到 git**
- [ ] `settings.RUN_BUDGET_USD` 降到 $0.3-0.5(防陌生人滥用)
- [ ] `settings.CODE_EXECUTION_MODE="generate_only"`(云平台一般跑不了 Docker 沙箱)
- [ ] 关 MCP(`USE_MCP=False`)—— MCP 需要启子进程,云环境可能受限
- [ ] LangSmith 开(`LANGSMITH_TRACING=true`)—— 免费额度够,面试演示时能分享 trace
- [ ] README 顶部加一行 "🚀 在线 Demo: https://xxx" 超链接
- [ ] 录一段 30 秒 GIF(用 LICEcap / peek 录屏),放 README

---

## 📝 Phase 4.2:架构演进博客(中文)

### 目标
**一篇文章讲透你这个项目的架构演进故事**,从 agent1_upgrade 的流水线多 Agent,一路升级到 agent3 的 Orchestrator-Subagent + MCP + 观测性。

**预期读者**:
- 准备面试的 AI/Agent 工程师
- 想学"怎么把 Demo 做成生产级"的同行
- 未来面试官(你把链接贴简历里)

### 平台选择

| 平台 | 适合 | 劣势 |
|---|---|---|
| **知乎** | 中文技术内容曝光最高 | 图片上传体验差 |
| **掘金** | 前端/全栈圈子活跃 | Agent 话题不是主流 |
| **个人站**(Astro/Hugo) | 有品牌 + 可控 | 要自己搭 |
| **公众号** | 老板同事能看到 | SEO 不友好 |

**推荐**:个人站(如果已有)+ 知乎。

### 文章完整大纲

```markdown
# 从 LangGraph 流水线到 Orchestrator-Subagent:一个 Agent 项目的架构演进

> 一年前我写了一个 LangGraph 4-Agent 的 NLP 考试系统。
> 现在我把它升级成了对齐 2026 前沿架构的 AI Co-Scientist。
> 这篇文章讲我在这一年里踩过的架构坑,和每一次迭代的真实取舍。

## 一、起点:LangGraph 4-Agent 考试系统(agent1_upgrade)

把老项目简单描述一下:
- 4 个 Agent:examiner / grader / proctor / reporter
- LangGraph DAG 编排
- ChromaDB RAG
- 纯"角色分工 + 串行流水线"

讲它的局限性:
- 所有 Agent 都共享上下文(anchoring bias)
- Reviewer 间的评审会互相"传染"
- 成本不可预测
- 改一个 prompt 不知道回归到什么程度

---

## 二、第一次架构升级:从"流水线"到"刻意独立"

引出 AI Co-Scientist 的核心设计:
- 为什么多 Agent 评审要**刻意隔离上下文**
- asyncio.gather + 独立 LLM 调用
- PICO 结构化问题 → 多 Reviewer 并行 → 方差触发 Devil → Meta 终裁

配一张 DAG 流程图(教学资料 05 章 5.8 节已有)。

---

## 三、第二次升级(2025 前沿):Orchestrator-Subagent

### 为什么要这次升级

对齐 Anthropic 2025.4 《How we built our multi-agent research system》。

老版本的痛点:
- 永远固定 5 个 Reviewer,纯理论问题也调 Reproducibility → 浪费
- 无法根据问题动态调整团队组成

### 新架构

贴 `orchestrator.py` 的核心代码片段,讲:
- devil 为什么硬编码必选
- 人数 3-5 的边界为什么写在代码里
- Orchestrator 用 chat 档而不是 reasoner 的成本考量
- 失败降级全量的原则

### 对比表

| 范式 | 上下文 | 谁决定下一步 | 代表 |
|---|---|---|---|
| GroupChat(AutoGen) | 共享 | Manager 每轮选 | 早期 |
| 结构化并行 | 独立 | 代码写死 | 2024 |
| Orchestrator-Subagent | 独立 | Orchestrator 一次性选 | 2025-2026 ⭐ |

---

## 四、第三次升级:工具层独立(MCP 标准)

讲 MCP 是什么、为什么是 2026 业界事实标准。

贴 `arxiv_server.py` 核心 10 行代码:
```python
@mcp.tool(description="Search arXiv")
async def search_arxiv(query: str, max_results: int = 20) -> list[dict]:
    ...
```

讲 feature flag 分流的工程美学:`settings.USE_MCP` 切换,原代码一字未改。

讲怎么把这个 Server 挂到 Claude Desktop / Cursor。

---

## 五、第四次升级:生产级基础设施

讲三件事:
1. **LangSmith 观测**:每条 run 可回放
2. **Extended Thinking**:Meta 终裁开 4000 token 推理预算
3. **Budget Guard**:ContextVar 做 run 级成本硬上限

为什么对标 Devin / Cognition / Claude Code 这些长跑 Agent。

---

## 六、踩过的三个坑(最值得讲的部分)

坑 1: 最早想让 Reviewer 自己带工具调用(ReAct)
- 结果:每人搜到的论文不同 → 评分没可比性
- 解决:工具统一在 DAG 层调度,Reviewer 只做判断
- 教训:**公平性 > 灵活性**

坑 2: Orchestrator 一开始让 LLM 自由选
- 结果:偶尔选出"全员温和"的组合,圆桌没有火药味
- 解决:devil 硬编码必选
- 教训:**LLM 不是确定性程序,关键约束要写代码**

坑 3: 没有 Budget Guard 时跑测试烧过 $8
- 结果:某次 bug 让 LLM 在循环里反复调,半小时烧了预算
- 解决:ContextVar 做 run 级护栏
- 教训:**Agent 长跑,成本上限必做**

---

## 七、最后反思:Agent 架构的三代演进

画一张演进图:
- 代码做一切(rule-based)
- LLM 做一切(naive agent)
- 代码 + LLM 各司其职(compound AI)← 我现在所处

讲 Berkeley BAIR 2024 的 Compound AI Systems 论文,为什么这是方向。

---

## 八、资源

- GitHub 仓库:xxx
- 在线 Demo:xxx
- 教学资料(12 章 Markdown):xxx
- 相关论文:
  - Reflexion(NeurIPS 2023)
  - Voyager(2023)
  - Anthropic Multi-agent Research(2025.4)
  - MCP Spec
```

### 写作要点

1. **真实故事 > 炫技**:踩过的坑、做过的权衡比"我用了 X"有说服力 10 倍
2. **每个升级都配具体代码片段**(不超过 20 行,要能读)
3. **架构图要有**(mermaid 即可)
4. **每一节结尾有 takeaway**(1 句话读者能带走)
5. **避免营销腔**,保持工程味

### 写作时长建议
- 大纲 + 代码片段选取:2 小时
- 初稿写作:4 小时
- 修改打磨:2 小时
- **一整天(8h)**能出一篇可发布的版本

---

## 📝 Phase 4.3:架构演进博客(英文)

英文版不是翻译,是**为国际读者重新组织**。

### 区别于中文版

| 维度 | 中文版 | 英文版 |
|---|---|---|
| 长度 | 3000-5000 字 | 1500-2500 英文 |
| 开头 | 故事铺垫 | 一句话 TLDR + 三段核心发现 |
| 代码 | 保留(英文读者也能看 Python) | 保留 |
| 引用 | 论文中英混合 | 全英文 |
| 读者 | 中国开发者 / 面试官 | Twitter / HN / Reddit r/LocalLLaMA |

### 发布平台

- Medium / personal blog
- Twitter 线程 +  HN Show HN
- Reddit r/LocalLLaMA / r/LangChain

### 推广技巧

- 发 HN Show HN:标题格式 "Show HN: I built X" 点击率最高
- 发 Twitter 线程:每条 240 字,9-12 条一组,第 1 条要能独立吸引人
- 艾特 LangChain 官方 / Anthropic Devrel:有机会被 retweet

---

## ✅ Phase 4 整体验收清单

当你完成后,以下应该都能打钩:

### Demo 层
- [ ] 公网可访问的 URL(非 localhost)
- [ ] 打开就能输入研究问题看全流程
- [ ] 有基本 rate limit / budget 保护
- [ ] README 顶部有"🚀 在线 Demo"链接
- [ ] README 有 GIF 或截图

### 博客层
- [ ] 中文版发布到至少一个平台(知乎/个人站/掘金)
- [ ] 英文版发布到 Medium 或个人站
- [ ] 两版都链接到 GitHub 仓库和 Demo URL
- [ ] 博客内至少 2 张架构图(mermaid / 手绘图)
- [ ] 包含 3 个"踩坑故事"

### 推广层(可选但推荐)
- [ ] Twitter 线程
- [ ] HN Show HN 提交
- [ ] 发到 1-2 个 Agent 相关的 Discord / 微信群

### 简历层
- [ ] 简历项目栏链接 Demo URL
- [ ] 简历项目栏链接博客
- [ ] 简历项目栏链接 GitHub
- [ ] "技术亮点"栏写对应 2026 前沿的架构关键词

---

## 🚀 未来之后想继续升级(超出 A 方向)

如果做完 Phase 4 还有兴趣继续深挖,下面是 A+ 可选项(按 ROI):

1. **加一个"Reproducibility Reviewer 的工具版"**:让它能真的 clone GitHub 跑代码,对应 L3 Autonomous Agent(当前是 L2 Orchestrated)
2. **接入一个真实用户场景**:延伸到 B1 个性化学习 Agent,复用 agent3 的所有模块
3. **Skill Library 可视化**:把 SkillLibrary 里的函数做一个前端浏览器,能搜索、复用
4. **多语言前端**:英文版 UI,方便发给海外面试官
5. **录一个 5 分钟项目讲解视频**:YouTube + B 站,面试时直接扔链接

这些都是"锦上添花",不做不影响 A 方向完成度。

---

## 📎 附:回来继续时的快速恢复命令

当你几天/几周后回来,这几条命令帮你快速确认环境还在位:

```bash
cd /c/Users/Administrator/Desktop/毕业后code/nlp项目/agent/agent3

# 1. 确认代码还在
git status

# 2. 跑测试确认基础设施没回归
cd backend
python -m pytest tests/ --ignore=tests/test_smoke.py -q
# 应当看到 91 passed, 9 skipped

# 3. Mock 模式跑 eval,确认 LLM 层还能 mock
EVAL_MOCK=1 python -m pytest tests/evals/ --run-evals -q
# 应当看到 8 passed

# 4. 看一眼当前的 settings 开关
grep -n "USE_MCP\|M4_USE_ORCHESTRATOR\|LANGSMITH\|BUDGET\|THINKING" co_scientist/config/settings.py
```

如果 step 2 通过,项目状态和本文档定格时一样,可以按本文档继续。

如果 step 2 失败,先看是不是依赖变了(`pip install -r requirements.txt`),再看是不是代码有 uncommitted 改动(`git diff`)。

---

## 💡 当你回来让 Claude 继续时的最小 prompt

把下面这段直接粘给新 Claude 会话:

> 我之前已经完成了 AI Co-Scientist 项目 A 方向的 Phase 1-3 升级,现在要继续执行 Phase 4。
>
> 项目路径:`/c/Users/Administrator/Desktop/毕业后code/nlp项目/agent/agent3`
>
> Phase 4 的完整执行手册在:`新项目想法/A_Phase4_剩余待执行.md`
>
> 我想先做 [具体任务,如 "4.1 Hugging Face Spaces 部署" / "4.2 中文博客初稿"],请按那份手册执行。

它会读手册、确认状态、开工,不需要你再重复讲前因后果。

---

**本文档定格于** `2026-04-23` · Phase 1-3 完成 · 91 tests passed · 待执行 Phase 4
