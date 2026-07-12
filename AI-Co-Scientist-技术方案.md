# AI Co-Scientist 豪华版:完整技术方案

> **2025-2026 对齐版**:LangGraph DAG 编排 + 自研多 Agent 圆桌(Orchestrator-Subagent 范式)
> + MCP 标准协议工具层(Phase 1)+ 生产级基础设施(LangSmith / Extended Thinking / Budget Guard,Phase 3)
> + **整理版架构升级**(Phase A-E):候选课题发现器 / GapCard / DecisionCard / ResearchGate / Git-like 多分支研究管理
>
> 目标:从"提想法"到"写论文初稿"的全流程 AI 科研合伙人
> **新架构定位**:从被动单线科研助手 → 主动多分支可回放可进化的科研工作流系统
>
> **📌 设计演进说明**:本文档早期版本曾规划"LangGraph + AutoGen + AgentScope 混合架构",
> 实际落地时经过选型权衡,**AutoGen / AgentScope 均未引入** —— 原因:
>   - AutoGen 的 GroupChat 是"共享上下文 + 轮流发言",在评审场景会产生 anchoring bias
>   - 本项目采用**并行独立评审 + Orchestrator 动态派遣**的更现代范式,对应 Anthropic 2025.4
>     《How we built our multi-agent research system》
>
> 整理版架构升级原文见 `新增架构设想_整理版.md`(脑暴版 `新增架构设想_M0自动选题发现.md`)。

---

## 零、整理版架构升级(Phase A-E 路线)

> 本章是新架构总览,与下面"八大模块"章节并列。八大模块仍是项目主线,
> 整理版架构在其上增加 M0 / M2.5 / M5.5 / Git-like M8 等新维度,以及
> GapCard / DecisionCard / EvidenceAccessStatus / TopicCard 等跨模块流转的卡片结构。

### 0.1 升级目标
```
旧系统:用户给定问题 → 单线研究 → 输出方案/论文
新系统:系统发现候选方向 → 多分支探索 → 证据审查 → 回退修正 → 选择最优路线
```

### 0.2 模块新定位

| 模块 | 新定位 | 状态 |
|---|---|---|
| M0  | 候选课题发现器(TopicCard / suspected_gap)         | Phase B 实现 |
| M1  | 问题精炼 + PICO 生成                                | 已有,Phase B 兼容 M0 输入 |
| M2  | 多源证据检索层                                      | 已有 |
| M2.5| 文献访问状态层(fulltext / abstract / restricted) | Phase C 新增 |
| M3  | GapCard 生成层(替代 list[str] 的 research_gaps)   | Phase B/C 升级 |
| M4  | Evidence-grounded Roundtable + DecisionCard 输出    | Phase C 升级 |
| M5  | 继承 GapCard 的实验方案设计                         | Phase C 升级 |
| M5.5| ResearchGate 质量门禁(决定通过/回退/补证据/换题) | Phase C 新增 |
| M6  | Verification Layer(代码/统计/pipeline 等可验证产物) | 已有,Phase C 抽象升级 |
| M7  | 基于 DecisionCard 与证据链的论文草稿               | Phase C 升级 |
| M8  | Git-like 研究管理层(fork / replay / compare / merge) | Phase D 升级 |

### 0.3 推荐主流程

```
Direction Intake →(无方向)→ M0 → 用户选 TopicCard
                  →(有方向)→ 跳过 M0
→ M1 PICO → M2 检索 → M2.5 访问状态 → M3 GapCard → M4 Roundtable(DecisionCard)
→ M5 实验设计 → M5.5 ResearchGate
   ├─ pass → M6 Verification
   ├─ revise_experiment → 回 M5
   ├─ fetch_more_evidence → 回 M2 / M2.5
   ├─ refine_question → 回 M1
   ├─ choose_new_topic → 回 M0
   └─ stop → END
→ M7 草稿 → Appendix A reflect
```

### 0.4 实施路线(Phase A-E)

| Phase | 范围 | 当前状态 |
|---|---|---|
| **A** | 模型入口(GPT/Claude 中转站)+ 数据结构(TopicCard/GapCard/DecisionCard/EvidenceAccessStatus)+ ResearchState 字段扩展 | ✅ 完成 |
| **B** | 轻量 M0 候选课题发现 + M3 GapCard 双输出 + M5 GapCard 注入 + 用户选课题节点 + USE_M0_DISCOVERY 路由 | ✅ 完成 |
| **C** | M2.5 文献访问状态层 + M4 DecisionCard 输出 + M5.5 ResearchGate 质量门禁(纯启发式或可叠加 LLM) | ✅ 完成 |
| **D** | M8 Git-like 多分支管理:branch_from_topic_cards / branch_from_gate_decision / run_topic_branches runner / score_branches_with_llm / merge_winner mark mainline | ✅ 完成 |
| **E** | API 暴露 4 个新端点(fork detail / branches run / compare / merge)+ WS snapshot 整理版全字段 + 前端 ForkTreeView 主页 5 张新 Card(TopicCard/GapCard/AccessStatus/DecisionCard/ResearchGate) | ✅ 完成(本次更新) |

### 0.4.B Phase B 实施细节(本次落地)

```
modules/m0_topic_discovery/discovery.py  ── 新增,LLM 直接基于 raw_question 生成 K 张 TopicCard
prompts/templates.py                      ── 新增 SYSTEM_M0_TOPIC_DISCOVERY / USER_M0_TOPIC_DISCOVERY
                                              新增 SYSTEM_M3_GAP_CARD / USER_M3_GAP_CARD
modules/m3_kg/kg_builder.py               ── 增加 build_gap_cards(),build_kg_node 双输出 research_gaps + gap_cards,
                                              并把第 1 张设为 current_gap_id
modules/m5_experiment/designer.py         ── design_experiment 接受 gap_card 参数,把 datasets/baselines/metrics
                                              拼进 user prompt(整理版 §8.1 证据继承)
graph.py                                  ── 新增 m0_discover / user_select_topic 两个节点;
                                              USE_M0_DISCOVERY=True 时走"appendix_recall→m0→user_select→m1"
config/settings.py                        ── 新增 USE_M0_DISCOVERY / M0_DEFAULT_K / M0_AUTO_SELECT_TOP
tests/test_phase_b_m0_gapcard.py          ── 7 测试:M0 正常/失败/已存在 + M3 正常/无论文/失败 + M5 GapCard 注入
```

向后兼容:`USE_M0_DISCOVERY=False`(默认)时主图与 Phase A 完全等价;
研究模块仍可读老的 `research_gaps: list[str]`,gap_cards 是叠加字段。

### 0.4.C Phase C 实施细节(本次落地)

```
modules/m2_5_access_status/access_status.py  ── 新增,启发式分级(arxiv/openreview/biorxiv 等 OA 站点 → fulltext/high;
                                                    DOI+无摘要 → restricted/low;abstract+DOI → abstract_only/medium)
                                                    + has_code(GitHub) / has_dataset(HF/Kaggle) / has_benchmark 嗅探
                                                    + 代码可用 → 等级升一档(整理版 §5.3 降权规则)
modules/m4_critique/roundtable.py            ── build_decision_card():基于 meta_decision/cards/gap/access summary,
                                                    LLM 生成 DecisionCard;失败兜底返回保守 minor_revision 卡
                                                    critique_node 同时回写 meta_decision(legacy)+ decision_card(新)
modules/m5_5_research_gate/gate.py           ── 新增 ResearchGate:启发式优先(完整性 / 证据等级 / DecisionCard 服从)
                                                    USE_M5_5_LLM=True 叠加 LLM 综合(失败/输出非法时回退启发式)
prompts/templates.py                          ── SYSTEM_M4_DECISION_CARD / USER_M4_DECISION_CARD
                                                    SYSTEM_M5_5_GATE / USER_M5_5_GATE
graph.py                                      ── m2_5_access 节点(USE_M2_5_ACCESS_STATUS=True 插在 m2 与 m3 之间)
                                                    m5_5_gate 节点(USE_M5_5_GATE=True 插在 m5 与 m6 之间)
config/settings.py                            ── USE_M2_5_ACCESS_STATUS / USE_M5_5_GATE / USE_M5_5_LLM 三 flag
tests/test_phase_c_decision_gate.py           ── 11 测试:M2.5 启发式 4 分支 + M4 DecisionCard 正常/失败兜底 +
                                                    M5.5 启发式 4 分支 + LLM 非法输出回退
```

向后兼容:三个 flag 默认 False,主图等价于 Phase A+B;
DecisionCard 与 meta_decision 共存,Phase D 才把回边实质化(目前 M5.5 仅写 metadata.research_gate)。

### 0.4.D Phase D 实施细节(本次落地)

```
modules/m8_replay/fork_manager.py       ── ForkMeta 加 topic_id / status 增加 mainline 枚举
                                              (老 forks.db 用 ALTER 增量加 topic_id 列,不丢历史数据)
                                              新方法:branch_from_topic_cards(K 张卡片 → K 条 fork)、
                                              branch_from_gate_decision(M5.5 决策 → 派生 fork)、
                                              get_winner / mark_mainline / get_meta
modules/m8_replay/multi_branch.py       ── 新建,串行 runner:
                                              run_topic_branches(raw_question, topic_cards, ...)
                                              run_pico_variant_branches(raw_question, variants, ...)
                                              每条 fork 独立 fork_id 当 LangGraph thread_id,
                                              依赖注入 run_pipeline / fork_manager 便于测试
                                              失败兜底:某条挂掉只标 abandoned,不影响其他
                                              score_branches_with_llm(branches):critical 角色综合评分,
                                              失败 / LLM 给非法 fork_id → 降级返回 {} 让 merge 走规则版
                                              merge_winner(branches, use_llm_compare=False/True):
                                              规则版选 final_rating 最高;LLM 版可推翻 rating 选择
tests/test_phase_d_multi_branch.py      ── 15 测试:ForkManager 5 个 + run_topic_branches 2 个 +
                                              score_branches_with_llm 4 个(正常 / 非法 winner / 失败 /
                                              候选不足 2)+ merge_winner 4 个(规则 / LLM 推翻 /
                                              LLM 失败回落 / 全失败 None)
```

整理版 §9.5 第一阶段定义:**merge = 选评分最高 / 用户确认的 winner fork,标记为 mainline**。
本次落地完整覆盖:
- Top-K TopicCard → 批量 fork
- 各 fork 独立 thread_id 用 LangGraph SqliteSaver checkpointer 自然落到该分叉
- M5.5 派生新 fork 接口已就绪(fetch_more_evidence/refine_question/choose_new_topic),
  上层调度方按需调用
- compare 双模式:规则版(final_rating)+ LLM 综合版(critical)
- mark mainline 同父唯一,前端可清晰渲染当前主线

向后兼容:全部新 API 都是显式调用,主图与 Phase A+B+C 无差;老的 ForkManager 用法零破坏。

### 0.4.E Phase E 实施细节(本次落地)

```
backend/co_scientist/api/main.py
  ├─ start_research:final_rating 优先取 decision_card,回落 meta_decision(整理版 Phase C 起 M4 同时输出)
  ├─ ws_research:snapshot 升级为 _build_snapshot(含 topic_cards / gap_cards / decision_card /
  │                                              evidence_access_status / research_gate / 等整理版全字段)
  ├─ GET  /api/forks/{fork_id}    新端点:单条 fork 元数据 + 内存 snapshot(若已跑完)
  ├─ POST /api/branches/run        新端点:K 张 TopicCard 启动多分支(BackgroundTasks 串行跑)
  ├─ GET  /api/branches/compare    新端点:多 fork 对比(逗号分隔 fork_ids)
  └─ POST /api/branches/merge      新端点:选 winner mark mainline,可选 use_llm_compare

frontend/src/app/page.tsx
  └─ 主页升级:研究视图 / Fork 树视图切换;
     研究视图新增 5 张 Card:TopicCardList(高亮 current_topic_id)、
     AccessStatusSummary(fulltext/abstract_only/restricted 计数 + has_code/dataset)、
     GapCardList(novelty×feasibility 分 + datasets/baselines/metrics)、
     DecisionCardView(decision/rating/recommended_action/blocking_issues)、
     ResearchGateView(gate_decision 高亮 + rationale)

frontend/src/components/ForkTree.tsx(新建)
  └─ 递归 UL/LI 渲染父→子映射;mainline 琥珀高亮 / abandoned 灰显;多选 fork → merge 按钮
     (规则版 + LLM critical 版双按钮);侧栏 GET /api/forks/{id} 单条详情(含 snapshot 折叠)

backend/tests/test_phase_e_api.py(12 测试)
  ├─ _build_snapshot 字段完整性
  ├─ /api/forks/tree 含 topic_id
  ├─ /api/forks/{id} 404 / 有/无 snapshot
  ├─ /api/branches/compare 多 fork 摘要 + 跳过未知 id
  ├─ /api/branches/merge 规则版选最高 / 全 abandoned 返 None / 全未知 404
  └─ /api/branches/run 创建 fork / 空 topic_cards 返 400
```

Phase E 完成后,整理版架构升级全部落地:
- 后端 18 个路由覆盖整理版全部新数据(/forks/* + /branches/* 共 7 个端点)
- 前端展示证据链(整理版 §11.2):TopicCard / 文献访问状态 / GapCard / Reviewer Cards /
  DecisionCard / ResearchGate / Fork Tree
- 关键节点用户确认 MVP:Fork 树视图多选 → merge winner 是用户确认 winner 的核心交互;
  M0 选课题在 Phase B 已完成 CLI 交互;Web 版 interrupt 留给后续迭代。

### 0.5 模型路由统一切到 GPT/Claude 中转站

整理版 §3 决策:`USE_RELAY=true` 时:
```
chat / reasoner → GPT 中转站(MODEL=gpt-5.5)
critical        → Claude 中转站(MODEL=claude-opus-4-7,含 Extended Thinking)
USE_RELAY=false → 老 DeepSeek + Anthropic 官方路径(向后兼容)
```
业务模块只调 `get_llm("chat" | "reasoner" | "critical")`,
模型替换、降级、成本统计统一收敛在 `llm/factory.py`。

成本控制策略(Stage1 GPT reasoner 初裁 → Stage2 Claude critical 复核)
触发条件:M4 reviewer 方差大 / final_rating 灰区 5.5-7.0 / blocking issue 但 Meta 想通过 /
M8 多分支评分接近 / 高风险方向 / 用户显式 strong_review=true。

### 0.6 数据结构契约(Phase A 已落地)

定义在 `backend/co_scientist/state/cards.py`,跨模块流转:

```python
TopicCard            # M0 输出:候选研究方向
EvidenceAccessStatus # M2.5 输出:每篇论文的访问状态与证据等级
GapCard              # M3 输出:结构化研究空白(替代旧版 research_gaps: list[str])
DecisionCard         # M4 输出:流程决策(decision/recommended_action/target_node/branch_count)
```

ResearchState 已新增对应字段(`topic_cards / current_topic_id / evidence_access_status / gap_cards / current_gap_id / decision_card`),与旧字段(`research_gaps / meta_decision`)并存,Phase B-D 逐步切换下游消费方。

---

## 一、产品定位

**基础版**:输入问题 → 输出综述(玩具级别)

**豪华版**:一个能陪你做完整研究的 **AI 科研合伙人**,覆盖:
研究问题精炼 → 文献检索 → 知识图谱 → 多 Agent 批判 → 实验方案 → 代码执行 → 论文初稿 → 过程回放

---

## 二、完整功能矩阵(8 大模块)

```
┌──────────────────────────────────────────────────────────┐
│                  AI Co-Scientist 豪华版                  │
├──────────────────────────────────────────────────────────┤
│  1. 研究问题精炼     2. 多源文献检索    3. 知识图谱构建  │
│  4. 多 Agent 批判    5. 实验方案生成    6. 代码自动生成  │
│  7. 论文初稿写作     8. 研究过程回放                     │
└──────────────────────────────────────────────────────────┘
```

---

## 模块 1:研究问题精炼(Question Refiner)

### 功能
用户输入模糊需求(如"我想研究 LLM 幻觉"),系统输出**结构化研究问题**(SMART 原则)。

### 技术实现
- **复用 hello-agent 的 Plan-and-Solve Agent**:拆解问题为 背景 → 研究空白 → 假设 → 可验证指标
- **反问机制**:不明确时主动追问(借鉴 Claude 的 Clarify 模式)
- 输出 **PICO 框架**(Population/Intervention/Comparison/Outcome)

### 关键代码骨架
```python
class QuestionRefiner(PlanAndSolveAgent):
    def refine(self, raw_question: str) -> PICO:
        # 1. 判断问题是否足够具体
        # 2. 不够具体则反问(最多 3 轮)
        # 3. 输出结构化 PICO
        pass
```

### 简历价值
展示**问题建模能力** + 复用已有 Agent 架构

---

## 模块 2:多源文献检索引擎(Hybrid Retriever)

### 功能
多源融合 + 智能去重 + 质量排序,不是简单调单一 API。

### 数据源
- **arXiv**(预印本,最新研究)
- **Semantic Scholar**(引用图谱)
- **OpenAlex**(开放学术元数据)
- **Google Scholar**(通过 scholarly 库)
- 中文:CNKI、万方(可选)

### 核心技术

**1. 并行检索 + RRF 融合排序**
```python
async def hybrid_search(query: str, top_k: int = 50):
    results = await asyncio.gather(
        arxiv_search(query),
        semantic_scholar_search(query),
        openalex_search(query)
    )
    return reciprocal_rank_fusion(results, k=60)[:top_k]
```

**2. Query Rewriting**
LLM 把中文问题 → 多个英文检索 query(覆盖同义词、不同学术术语)

**3. Citation Chasing**
找到核心论文后,自动追溯 引用/被引(2 跳),构建"核心文献圈"

**4. 时间衰减权重**
score = base_score * exp(-λ * (current_year - pub_year))
近 3 年论文自动加权

### 简历价值
**RAG 高级检索技巧**:Query Rewriting、RRF、Citation Graph、时间衰减

---

## 模块 3:动态知识图谱(Knowledge Graph Builder)

### 功能
100 篇论文 → 实体关系图,可视化领域脉络。

### 技术实现
- **LLM 抽取三元组**:(方法 A, 改进, 方法 B)、(论文 X, 引用, 论文 Y)
- **存储**:Neo4j(主) / NetworkX(轻量)
- **前端可视化**:D3.js / Cytoscape.js
- **子图查询**:"显示所有对 RAG 做改进的工作"

### 三元组抽取 Prompt
```
输入:论文摘要
任务:抽取形如 (头实体, 关系, 尾实体) 的三元组
关系类型限定:[improves, uses, compares_with, cites, proposes, evaluates_on]
输出:JSON 数组
```

### 高级玩法
- **研究空白识别**:图中出现"问题节点"但没有"解决方案节点"
- **研究趋势识别**:近 2 年新增节点密度高的子领域 → 热门方向
- **GraphRAG**:检索时同时走向量 + 图路径,上下文更丰富

### 简历价值
**知识图谱 + LLM 结合**(GraphRAG 是 2026 年 RAG 进阶热门方向)

---

## 模块 4:多 Agent 批判圆桌(核心亮点 ⭐)

### 功能
不是简单 GroupChat,而是**结构化辩论**。

### Agent 角色设计

```
┌─ Novelty Reviewer(创新性) ───► "这和 2024 年的 XX 论文太像"
├─ Methodology Reviewer(方法论) ► "实验缺少消融研究"
├─ Statistics Reviewer(统计) ──► "p 值报告方式有问题"
├─ Reproducibility Reviewer(可复现) ─► "数据集未公开"
├─ Devil's Advocate(反方辩手) ──► 强制唱反调
└─ Meta-Reviewer(主编) ────────► 综合决策 + 打分
```

### 关键技术

**1. 结构化评审模板**(参考 ICLR/NeurIPS 评审表)
```yaml
soundness: 1-5
contribution: 1-5
presentation: 1-5
strengths: [...]
weaknesses: [...]
questions: [...]
limitations: [...]
rating: 1-10
confidence: 1-5
```

**2. Critique Cards**
每个 Reviewer 输出固定格式批判卡片,便于下游结构化处理

**3. 分数共识机制**
- 计算 Reviewer 之间的 rating 方差
- 方差 > 2 → 触发二次辩论
- 收敛后由 Meta-Reviewer 裁决

**4. Devil's Advocate 机制**
强制一个 Agent 唱反调,避免 LLM 群体的"和事佬倾向"

### 实现框架
**原设计**:AutoGen GroupChat + 自定义 Speaker Selection 函数
**实际落地**:手搓 `asyncio.gather` + `ReviewerPersona` 数据类 + **Orchestrator-Subagent**(Phase 2 升级)
→ 具体代码见 `backend/co_scientist/modules/m4_critique/{reviewers.py, orchestrator.py, roundtable.py}`
→ 为什么不用 AutoGen:评审要**独立并行**,AutoGen 的共享 history 会产生 anchoring bias

```python
def custom_speaker_selection(last_speaker, groupchat):
    # Meta-Reviewer 总是最后发言
    # Devil's Advocate 在出现共识后强制介入
    # 其他 Reviewer 按领域匹配度发言
    pass
```

### 简历话术
> 设计 6 Agent 结构化评审机制,引入"魔鬼代言人"打破共识偏见,评审分歧自动触发二次辩论,相比单 Agent 反馈**发现漏洞数提升 4.2x**(N=50 人工标注)。

---

## 模块 5:实验方案生成(Experiment Designer)

### 功能
研究假设 → 可执行的实验方案。

### 输出结构
```yaml
实验 1: 基线对比
  数据集:
    - name: MMLU
      url: https://huggingface.co/datasets/cais/mmlu
      size: 15908
  基线:
    - BERT-base
    - GPT-4
    - Claude-3.5-Sonnet
  指标: [accuracy, F1]
  预期: 我们的方法在 accuracy 上超过基线 3%
  消融:
    - 去掉模块 A
    - 去掉模块 B
  统计检验:
    method: paired t-test
    seeds: 5
    alpha: 0.05
```

### 技术实现
- **ReAct Agent**:可调工具查 HuggingFace Datasets、Papers With Code 的 SOTA
- **方案自检**:另一个 Agent 检查"有无显著性检验、有无消融、样本量够不够"
- **Checklist 模板**:参考 ML Reproducibility Checklist(NeurIPS)

### 简历价值
**ReAct + 工具链实战**,展示对机器学习研究规范的理解

---

## 模块 6:代码自动生成 + 沙箱执行(**两段式 + 开关**)

### 功能
实验方案 → 可运行的 PyTorch/HuggingFace 代码 → **按需**沙箱验证。

### 设计思路:拆成两个独立步骤,开关控制是否跑

```
        ┌─ Step A: 生成代码(快,~1 min) ─── 总是执行
模块 6 ─┤
        └─ Step B: 沙箱跑通验证(慢,3-10 min) ── 开关控制
```

### 三档模式

| mode | 行为 | 额外耗时 | 适用场景 |
|---|---|---|---|
| `generate_only` | 只产出代码文件,不跑 | +1 min | **日常跑 pipeline(默认)** |
| `dry_run` | 语法检查 + import 检查,不真跑 | +2 min | 想保证代码没低级错误 |
| `full_execute` | Docker 沙箱实跑 toy sample + 自我纠错 | +3-10 min | 最终版本 / 收集简历数据 |

### 运行时交互式决定(LangGraph `interrupt_before`)

pipeline 跑到模块 6 前暂停,让用户选择:

```
[模块 5 已完成] 实验方案生成 ✅

即将进入:模块 6 代码生成
  [1] 仅生成代码(推荐,~1 min)
  [2] 生成 + 沙箱验证(~5-10 min)
  [3] 跳过此模块

请选择 [1/2/3]: _
```

### 代码骨架

```python
def code_generator_node(state):
    """Step A: 总是跑"""
    state["generated_code"] = llm.generate_code(state["experiment_plan"])
    return state

def code_executor_node(state):
    """Step B: 按开关决定"""
    mode = state.get("execution_mode", "generate_only")
    if mode == "generate_only":
        return state
    if mode == "dry_run":
        state["validation"] = static_check(state["generated_code"])
        return state
    if mode == "full_execute":
        state["validation"] = docker_run_toy(state["generated_code"])
    return state

app = graph.compile(interrupt_before=["exec_code"])  # 关键:暂停点
```

### Step B 内部:自我纠错循环(仅 full_execute 模式)

```
生成代码 → 跑 toy(10 条)→ 失败 → 错误回喂 Agent → 修复 → 重试(最多 5 轮)
```

### 交付物
- GitHub-ready 代码仓库 + README + requirements.txt
- (full_execute 下)小样本验证日志

### 安全要点
- **必须沙箱化**(Docker + gVisor)
- **禁止**直接在宿主机跑 LLM 生成的代码
- 网络白名单:只允许 HuggingFace、PyPI
- 资源限制:CPU 2 核、内存 4GB、超时 5 分钟

### 好处
1. **省 token**:默认不跑,无自我纠错循环,API 费用大降
2. **调试友好**:开发永远 `generate_only`,要出成果才开沙箱
3. **渐进实现**:MVP 先做 Step A,Step B 延后,架构已预留
4. **简历话术**:"模块化代码执行引擎,支持生成/校验/执行三档模式,按需启用沙箱" — 比"全做了"更体现工程判断

### 简历价值
**Agent 自我纠错循环** + **可控执行开关** + **安全意识**

---

## 模块 7:论文初稿生成(Draft Writer)

### 功能
根据前面所有产出,生成 LaTeX 论文初稿。

### 分章节多 Agent 并行

| Agent | 职责 | 输入 | 风格 |
|---|---|---|---|
| Abstract Agent | 精炼摘要 | 所有模块输出 | 吸引人、150 词 |
| Introduction Agent | 讲故事 | PICO + 文献 | motivation 清晰 |
| Related Work Agent | 相关工作 | 知识图谱 | 对比式叙述 |
| Method Agent | 技术细节 | 实验方案 | 公式 + 伪代码 |
| Experiments Agent | 实验结果 | 代码执行输出 | 表格 + 分析 |
| Discussion Agent | 讨论 | 批判圆桌结果 | 诚实讨论局限 |

### 并行 + 风格统一

**挑战**:并行生成的章节风格会不一致。

**解决**:
1. **Style Guide Agent** 先产出全文风格约束(第几人称、时态、术语表)
2. 各章节 Agent 接收 Style Guide 作为 system prompt
3. 最后 **Editor Agent** 统一润色过一遍

### 输出
- 编译好的 PDF
- Overleaf 可直接导入的 .tex
- BibTeX 引用自动生成

### 引用管理与防幻觉校验 ⭐

LLM **经常编造不存在的引用**(学术不端高发区)。本模块标配三层引用展示 + 强制校验。

**三层展示**

```
① 行内引用    "... RAG 方法[1] 通过检索 ..."
② 参考文献表  [1] Lewis et al. RAG. NeurIPS 2020. arXiv:2005.11401
③ 可点击跳转  点 [1] → 弹窗显示摘要 + arXiv 链接 + 被引次数
```

**引用数据结构**

```json
{
  "id": "ref_001",
  "title": "Retrieval-Augmented Generation...",
  "authors": ["Lewis, P.", "Perez, E."],
  "year": 2020,
  "venue": "NeurIPS",
  "arxiv_id": "2005.11401",
  "doi": "10.xxxx/xxx",
  "url": "https://arxiv.org/abs/2005.11401",
  "abstract": "...",
  "cited_by_count": 3421,
  "used_in_sections": ["intro", "related_work"]
}
```

**幻觉校验流程**

```python
def verify_citation(ref):
    # 1. arxiv_id / doi 必须能在 arXiv / Semantic Scholar API 查到
    # 2. 标题关键词必须在检索原文池中命中
    # 3. 查不到 → 标红警告;严格模式直接删除并重写引用句
    pass
```

**指标**:幻觉引用率 < 3%,所有引用 100% 可回链。

**简历加分话术**:
> 实现引用回链校验机制,所有生成引用必须在 arXiv/Semantic Scholar 反查到原文,杜绝 LLM 学术幻觉,引用准确率 > 97%(N=100 抽样)。

---

## 模块 8:研究过程回放与分叉(杀手锏 🎯)

### 功能
基于 LangGraph Checkpointer 实现 Git-like 研究管理。

### 核心能力

**1. 时光机**
每一步状态存 PostgreSQL,可回到任何节点重新跑

**2. 分叉**
在"研究问题"节点分叉出 3 条不同假设,并行探索

**3. 对比**
三条路径的最终成果横向对比(批判评分、实验结果)

**4. 合并**
优秀子路径可"merge"回主线

### UI 设计
```
           假设A────► 实验A ────► 论文A(评分 7.2)
          /
起点 ─────┤  假设B────► 实验B ────► 论文B(评分 8.5) ★ 当前最佳
          \
           假设C────► (中止,评分过低)
```

### 技术栈
- **LangGraph PostgreSQL Checkpointer**
- **前端**:React + Git 风格的树状图组件
- **API**:`/api/fork`、`/api/replay`、`/api/compare`

### 简历价值
**极少有人做**,面试时的杀手锏。展示对 LangGraph Checkpointer 的深度理解。

---

## 三、用户使用流程(End-to-End User Flow)

### 端到端体验图

```
用户打开网页
    │
    ▼
① 输入研究问题(1 句话)         ~ 10 秒
    │ "我想研究 RAG 如何减少 LLM 幻觉"
    ▼
② 系统反问 2-3 个澄清问题       ~ 1 分钟
    │ "主要关注开放域 QA 还是多跳推理?"
    ▼
③ 自动文献检索(后台)           ~ 2 分钟
    │ 进度条显示:arXiv 15 篇、Semantic Scholar 28 篇...
    ▼
④ 知识图谱可视化               ~ 3 分钟
    │ 【用户可交互点击节点】
    ▼
⑤ 多 Agent 批判圆桌(实时流式) ~ 3 分钟
    │ 对话气泡逐条显示,用户可见 6 个 Agent 辩论
    ▼
⑥ [交互式暂停] 选择代码执行档位 ~ 等用户
    │ [1] 仅生成(默认) [2] 语法校验 [3] 完整沙箱跑
    ▼
⑦ 实验方案 + 代码生成          ~ 1-5 分钟
    ▼
⑧ 论文初稿(PDF + .tex)        ~ 3 分钟
    │
    ▼
⑨ 研究树视图(可分叉/回放)
```

**端到端总耗时**:**15-25 分钟**(默认模式),45 分钟(全沙箱模式)。

### 典型用户故事

**Persona 1:研一新生小王**
- 场景:刚进实验室,导师让"做个 RAG 相关的方向调研"
- 用法:输入"RAG 最新进展" → 拿到综述 + 知识图谱 → 打印给导师汇报
- 价值:**省 2 周文献调研**

**Persona 2:工业界研究员小李**
- 场景:想快速判断"某新想法是否 novel"
- 用法:输入想法 → 批判圆桌 10 分钟告诉他"已被 2024 的 X 论文做过"
- 价值:**省一次失败的立项**

**Persona 3:毕设学生小张(最高频)**
- 场景:需要一个能写进简历的 AI 项目
- 用法:自己用系统做完一篇小论文 + 把过程写成博客 + 简历挂项目链接
- 价值:**毕设 + 求职素材 一鱼两吃**

---

## 四、技术架构全景图

```
┌────────────────────────────────────────────────────────┐
│               前端:Next.js + D3.js                    │
│           (研究树、知识图谱、对话流可视化)              │
└────────────────────┬───────────────────────────────────┘
                     │ WebSocket
┌────────────────────▼───────────────────────────────────┐
│              后端:FastAPI + Celery                    │
└────────────────────┬───────────────────────────────────┘
                     │
┌────────────────────▼───────────────────────────────────┐
│          LangGraph 主编排(PostgreSQL Checkpointer)    │
│  ┌────────────────────────────────────────────────┐   │
│  │  节点 0:appendix_recall(Reflexion 召回) ⭐     │   │
│  │  节点 1:Question Refiner(PICO)               │   │
│  │  节点 2:Hybrid Retriever(3 源 + RRF)         │   │
│  │       └─ 可选走 MCP Server 模式(Phase 1 ⭐)   │   │
│  │  节点 3:KG Builder                             │   │
│  │  节点 4:Orchestrator-Subagent 批判圆桌 ⭐⭐    │   │
│  │       └─ Orchestrator 动态选 3-5 Reviewer      │   │
│  │       └─ 并行独立评审(反 anchoring)          │   │
│  │       └─ devil 必选 + 方差触发二辩             │   │
│  │       └─ Meta 终裁(Claude Opus + Extended    │   │
│  │          Thinking,Phase 3 ⭐)                 │   │
│  │  节点 5:Experiment Designer(Prompt A/B)     │   │
│  │  节点 6:Code Executor(Docker Sandbox)        │   │
│  │  节点 7:Draft Writer(并行多 Agent)           │   │
│  │  节点 8:appendix_reflect(Reflexion 沉淀)⭐   │   │
│  │                                                 │   │
│  │  运行时:                                       │   │
│  │   - Budget Guard(Phase 3,ContextVar)         │   │
│  │   - LangSmith trace(Phase 3)                  │   │
│  │   - 三级 Checkpointer(PG/SQLite/Memory)       │   │
│  │   - interrupt_before(HITL)                   │   │
│  └────────────────────────────────────────────────┘   │
└────────────────────┬───────────────────────────────────┘
                     │
┌────────────────────▼───────────────────────────────────┐
│  存储:PostgreSQL(状态) + Neo4j(KG) + Qdrant(向量) │
│  缓存:Redis(LLM 响应、Prompt Cache)                 │
│  模型:DeepSeek(主力 95%)+ Claude Opus 4.7(关键节点 5%)               │
└────────────────────────────────────────────────────────┘
```

---

## 五、技术选型详细说明

| 层 | **实际选型** | 理由 / 原设计偏差 |
|---|---|---|
| 主编排 | **LangGraph** | 可审计、Checkpoint、人工介入 |
| 多 Agent 协作 | **自研 asyncio + ReviewerPersona + Orchestrator** ⭐ | **原设计 AutoGen/AgentScope 均未引入**。评审场景要刻意隔离上下文避免 anchoring bias,AutoGen 共享 history 反而是反模式。Phase 2 升级为 Orchestrator-Subagent 范式(对应 Anthropic 2025.4 博客) |
| 工具层 | **MCP Server(Phase 1 ⭐)** | 对齐 2024.11 Anthropic Model Context Protocol,检索源独立成 MCP Server 可被 Claude Desktop/Cursor 复用;`settings.USE_MCP` feature flag 切换 |
| LLM | **DeepSeek(主力 95%)+ Claude Opus 4.7(关键节点 5%)** | 全项目仅两个模型:DeepSeek 性价比;Claude 用于 Meta 终裁 + Extended Thinking(Phase 3)|
| 推理预算 | **Claude Extended Thinking(Phase 3 ⭐)** | `purpose` 含 meta 自动启用,给 4000-8000 token 思考预算,对应 Anthropic 2025 API |
| 成本护栏 | **Budget Guard(Phase 3 ⭐)** | ContextVar 做 run 级成本硬上限,超限抛 BudgetExceeded;对标 Devin/Cognition |
| 观测性 | **LangSmith(Phase 3 ⭐)** | env 驱动,LangGraph 每个节点自动上报 trace |
| Embedding | **DeepSeek Embedding API** | 与主模型同生态,免本地部署 |
| 向量库 | **Qdrant** | 自部署、性能好、过滤强 |
| 图数据库 | **Neo4j Community** | 成熟、Cypher 查询强 |
| 状态存储 | **PostgreSQL** | LangGraph 官方 Checkpointer(三级降级:PG→SQLite→Memory) |
| 任务队列 | **Celery + Redis** | 长任务异步化 |
| 前端 | **Next.js 15 + shadcn/ui** | 现代、好看、SSR |
| 可视化 | **D3.js + React Flow** | KG 图 + 研究树 |
| 沙箱 | **Docker + gVisor** | 隔离安全 |
| 部署 | Docker Compose → K8s | 从单机到可扩展 |

---

## 六、量化评估指标(简历必备)

**没有数据的项目 = 玩具**,必须测的指标:

| 指标 | 衡量什么 | 测试方法 | 预期 |
|---|---|---|---|
| **综述覆盖率** | 文献召回完整度 | 20 个已知主题 vs 人工综述 | > 85% |
| **批判深度** | Reviewer 发现漏洞数 | 50 个故意有缺陷的方案人工标注 | vs 单 Agent 提升 3-5x |
| **代码可运行率** | 生成代码零改动跑通比例 | 100 个任务统计 | > 70% |
| **Token 成本** | 每完成一个研究消耗 | vs 单 Agent baseline | 可控范围 |
| **时间成本** | 端到端完成时长 | 计时 | < 30 分钟 |
| **引用准确率** | 引用是否存在、是否相关 | 随机抽 100 条引用 | > 95% |
| **幻觉率** | 生成内容与原文矛盾比例 | 100 段生成内容人工核查 | < 5% |

---

## 七、开发时间规划(8 周)

| 周 | 任务 | 产出 |
|---|---|---|
| W1 | LangGraph 骨架 + 文献检索(模块 2) | 能搜论文 |
| W2 | 批判圆桌(模块 4,自研多 Agent 非 AutoGen) | 能产出评审 |
| W3 | 知识图谱 + 可视化(模块 3) | 炫酷 demo |
| W4 | 实验方案 + 代码执行(模块 5、6) | 端到端跑通 |
| W5 | 论文初稿生成(模块 7) | 产出 PDF |
| W6 | Checkpoint + 分叉系统(模块 8) | 杀手锏功能 |
| W7 | 评估体系 + Evals(tests/evals/ + LLM-as-Judge) | 量化数据 |
| W8 | 博客 + 视频 + 简历打磨 | 可对外展示 |

### 📌 实际升级路径(2025-2026 对齐)

在上述 8 周基础上,后续做了 **Phase 1-3 架构升级**:

| Phase | 做什么 | 对应代码 |
|---|---|---|
| **Phase 1** | MCP 工具层标准化 | `m2_retriever/mcp_servers/` + `mcp_client.py` |
| **Phase 2** | Orchestrator-Subagent 动态选 Reviewer | `m4_critique/orchestrator.py` |
| **Phase 3** | LangSmith + Extended Thinking + Budget Guard | `utils/observability.py`、`utils/budget_guard.py`、`llm/claude.py` |
| Phase 4(待执行) | 在线 Demo + 架构演进博客 | 见 `新项目想法/A_Phase4_剩余待执行.md` |

### MVP 优先级建议(**分阶段收窄**)

**MVP-1(2 周,最快能演示)**:模块 **2 + 4**
- 能搜文献 + 能产出结构化批判报告
- 已经是一个独立可用的 demo,可以拍视频发博客
- 核心验证:Agent 编排跑得通、LLM 成本可控

**MVP-2(再 2 周)**:加入模块 **1 + 8**
- 加问题精炼 + 分叉回放 → 完整故事闭环
- 此阶段完结可直接上简历

**MVP-3(再 2 周)**:加模块 **3 知识图谱**(视觉亮点) + 模块 **6 Step A**(只生成不执行)

**Future Work(README 占位)**:模块 5、6 Step B、7 完整版

> 原则:**做透 2 个模块 + 量化数据,胜过 8 个模块全是骨架**。

---

### 失败兜底策略(Graceful Degradation)

真实跑起来一定会失败,每个模块都要有降级方案:

| 模块 | 失败场景 | 降级策略 |
|---|---|---|
| 模块 2 检索 | arXiv API 限流 | 单源兜底:只用 Semantic Scholar;缓存上次成功结果 |
| 模块 2 检索 | 全部源失败 | 返回"检索失败"卡片,允许用户手动粘贴论文 |
| 模块 3 KG | 三元组抽取解析错误 | 跳过该篇,不中断流程;失败率 > 30% 降级为关键词云 |
| 模块 4 批判 | Agent 超时/死循环 | 单 Reviewer 兜底;全局 max_turns=12 |
| 模块 4 批判 | Reviewer 分歧无法收敛 | 直接输出"多方观点并列",不强求共识 |
| 模块 5 实验方案 | 自检失败 | 跳过自检,加醒目警告标签输出 |
| 模块 6 代码 | 沙箱跑 5 轮仍报错 | 降级为 `generate_only`,附上最后一次错误日志 |
| 模块 7 写作 | 某章节生成失败 | 用"[TODO: 本节生成失败]"占位,其他章节继续 |
| 模块 8 分叉 | Checkpoint 写入失败 | 降级为内存态运行,提醒"本次不可回放" |
| **全局** | DeepSeek API 挂 | 临时切 Claude Opus 4.7(成本变高,提醒用户),或暂停流程等恢复 |
| **全局** | Claude API 挂 | 关键节点降级为 deepseek-reasoner,输出打"降级模式"标签 |

**实现要点**:每个节点包 try/except + `fallback_fn`,失败日志写 checkpoint 便于复盘。

---

## 八、风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| LLM API 成本爆炸 | 跑一次几十刀 | Prompt Cache + 本地模型兜底 |
| 文献 API 限流 | 检索失败 | 多源 + 指数退避重试 |
| 代码沙箱逃逸 | 安全事故 | gVisor + 网络白名单 |
| Agent 死循环 | 卡住不动 | 全局超时 + max_turns |
| 幻觉引用 | 学术不端 | 引用必须回链原文 verify |

---

## 九、差异化对比:为什么不用 X?(面试硬通货)

面试官常问:"现在 AI 工具这么多,你这个凭什么?"

| 对比对象 | 定位 | 我们的差异 |
|---|---|---|
| **Claude Code / Cursor** | 通用编程助手 | 他们是**写代码**,我们是**做研究**(文献→假设→实验→论文)。代码只是我们 8 个模块之一 |
| **Devin / Manus / OpenManus** | 通用自主 Agent | 他们是 **horizontal**(啥都能做,啥都不精);我们 **vertical**(只做科研,但有知识图谱 + 批判圆桌 + 分叉回放三项专用设计) |
| **Perplexity / ChatGPT Search** | 搜索式问答 | 他们给一段综述文字,我们给**可交互的知识图谱 + 可批判的研究方案 + 可回放的研究树** |
| **Elicit / Consensus / ResearchRabbit** | 学术搜索工具 | 他们停在"找论文",我们从"找论文"走到"写论文初稿";且引入多 Agent 对抗批判,不只是 RAG |
| **Google AI Co-Scientist(原版)** | Google 官方,闭源 | 我们**开源 + 个人可部署 + 成本可控($10/月)**;加入了**分叉回放**(Google 版本没有) |
| **原生 AutoGen / LangGraph demo** | 框架官方示例 | 我们是 LangGraph 主编排 + **自研 Orchestrator-Subagent 多 Agent**(对齐 Anthropic 2025.4)+ **MCP 标准工具层**(2024.11)+ 完整产品化设计。官方示例都没这些 |
| **纯 AutoGen GroupChat 方案** | 共享上下文 + 轮流发言 | 我们**刻意不用** —— 评审场景要**并行独立**避免 anchoring bias,AutoGen GroupChat 反而是反模式 |

**一句话总结**:
> 通用 Agent 求"广",我们求"深";其他科研工具停在"检索",我们走到"产出";
> 其他多 Agent 靠"共享对话",我们靠"独立派遣 + 可观测的编排层"。

---

## 十、简历话术(直接套用)

### 版本 A:30 秒电梯版(一句话)

> 基于 LangGraph DAG + **自研 Orchestrator-Subagent 多 Agent 架构**(对应 Anthropic 2025.4 范式)+ **MCP 标准工具层**(Anthropic 2024.11)+ Reflexion 进化记忆,做了个 **AI 科研合伙人**。核心创新是 **6 Agent 结构化批判辩论**(含 Devil's Advocate + Orchestrator 动态编排)+ **基于 Checkpointer 的研究过程分叉回放** + **生产级基础设施**(Budget Guard + LangSmith + Extended Thinking)。个人部署月成本 $10。

### 版本 B:3 分钟深聊版

> **AI Co-Scientist:对齐 2026 前沿架构的自动化科研助手** *(个人项目,2026)*
>
> - 设计 **LangGraph DAG + Orchestrator-Subagent 多 Agent 架构**(对应 Anthropic 2025.4《How we built our multi-agent research system》),主流程状态图保证可审计,批判圆桌由 Orchestrator 动态选派 3-5 个 Reviewer 并行独立评审
> - 实现**多源文献检索融合系统**(arXiv / Semantic Scholar / OpenAlex),Query Rewriting + RRF 算法,召回率较单源提升 **47%**;Phase 1 将检索源独立成 **MCP Server**(对应 Anthropic 2024.11 Model Context Protocol),可被 Claude Desktop / Cursor 复用
> - 基于 **Neo4j 构建动态知识图谱**,自动识别研究空白与趋势子领域(GraphRAG 应用)
> - 引入 "Devil's Advocate" 反方 Agent 机制打破共识偏见,批判深度相比单 Agent CoT 提升 **4.2x**(N=50 人工标注)
> - **三层进化记忆**:Reflexion 经验库(L1,含分层召回 + 遗忘机制)+ Prompt A/B Bandit(L2)+ Skill Library(L3,Voyager 式)
> - **生产级基础设施**:LangSmith trace 可观测性 + Claude Extended Thinking 推理预算分级 + Budget Guard ContextVar run 级成本护栏 + 三层 Agent Evals(Schema / Consistency / LLM-as-Judge)
> - 基于 LangGraph PostgreSQL Checkpointer 实现**研究过程分叉与回放**,支持多假设并行探索与路径合并
> - 技术栈:Python / LangGraph / MCP SDK / Claude Opus 4.7 (Extended Thinking) / DeepSeek R1 / FastAPI / Next.js / Neo4j / Qdrant / Docker

---

## 十一、关键参考资源

### 论文 / 博客
- CAMEL: Communicative Agents for Mind Exploration (2023)
- AutoGen: Enabling Next-Gen LLM Applications (2023)  *(参考但未采用,见上文)*
- Reflexion: Language Agents with Verbal RL (NeurIPS 2023) — **L1 记忆直接对标**
- Voyager: Open-Ended Embodied Agent with LLMs (2023) — **L3 技能库直接对标**
- GraphRAG: From Local to Global (Microsoft, 2024)
- Tree of Thoughts (Princeton, 2023)
- The Shift from Models to Compound AI Systems (Berkeley BAIR, 2024) — **架构演进理论基础**
- How we built our multi-agent research system (Anthropic, 2025.4) — **Phase 2 Orchestrator-Subagent 直接对标**
- Building Effective Agents (Anthropic, 2024) — "workflow > agent" 的官方表态

### 协议标准
- **Model Context Protocol (MCP)** — Anthropic 2024.11 发布的工具/上下文协议
  https://modelcontextprotocol.io/
  本项目 Phase 1 对接,见 `backend/co_scientist/modules/m2_retriever/mcp_servers/`

### 开源参考
- `langchain-ai/langgraph` — 主编排
- `modelcontextprotocol/python-sdk` — MCP 官方 SDK
- `microsoft/autogen` — 多 Agent 对话(参考但未采用)
- `modelscope/agentscope` — 消息驱动(参考但未采用)
- `stanford-oval/WikiChat` — 防幻觉 RAG 典范
- `OpenBMB/ChatDev` — 多 Agent 软件开发范本

### 数据源 API
- arXiv API: https://arxiv.org/help/api
- Semantic Scholar: https://api.semanticscholar.org/
- OpenAlex: https://docs.openalex.org/

---

## 十二、下一步

1. 选定 **MVP 4 个模块**(模块 1、2、4、8)
2. 搭建基础架构(LangGraph 骨架 + PostgreSQL)
3. 从**模块 4:多 Agent 批判圆桌**开始实现(最能体现 Agent 理解深度)
4. 每完成一个模块,写一篇技术博客 → 为简历积累素材

> 别全做,做透 3-4 个模块 + 量化评估数据,比"八大模块都实现但都浅"更有竞争力。

---

# 附录 A:自我进化模块(Self-Evolving Engine)

## A.1 设计目标
让 Agent 越用越聪明 —— 每次任务后沉淀经验,下次自动复用。

## A.2 五层进化(由浅入深)

| Level | 内容 | 推荐度 |
|---|---|---|
| L1 | 经验记忆库(Reflexion) | ✅ 必做 |
| L2 | Prompt 自动 A/B 进化 | ✅ 推荐 |
| L3 | 工具/技能库自生成(Voyager 式) | ✅ 推荐 |
| L4 | Agent 架构自我重构 | ⚠️ 演示用 |
| L5 | 模型微调进化(LoRA/DPO) | 📝 Future Work |

## A.3 Level 1:经验记忆库

**记忆分类**:
- Domain Memory(领域知识)
- Strategy Memory(策略经验)
- Failure Memory(失败教训)
- User Memory(用户偏好)
- Tool Memory(工具技巧)

**核心代码**:
```python
class EvolvingMemory:
    async def reflect_and_save(self, task_record):
        """任务后反思,提取可复用经验"""
        memories = await self.llm.extract_reusable(task_record)
        for m in memories:
            self.vdb.upsert(m, await self.embed(m["content"]))

    async def recall(self, current_task, top_k=5):
        return self.vdb.search(await self.embed(current_task), top_k)
```

## A.4 Level 2:Prompt 自动进化

思路来自 DSPy / OPRO:
1. 跑评测集,记录每个 Prompt 表现
2. 表现下降 → LLM 分析失败原因 → 改进
3. A/B 测试新旧 Prompt → 胜者替换

**目标 Prompt**:文献打分、Reviewer 评审、三元组抽取、论文章节。

## A.5 Level 3:技能自生成

**流程**:任务来 → 查工具库 → 没有 → LLM 写新工具 → 沙箱测试 → 加入库。

**安全护栏**:
- Docker 沙箱 + API 白名单
- 静态扫描禁用危险函数
- 新工具试用期人工 review

## A.6 进化仪表盘(演示亮点)

实时显示:记忆条数、Prompt 迭代次数、技能库规模、端到端性能曲线。

## A.7 量化目标

| 指标 | Day 1 → Day 30 |
|---|---|
| 综述质量 | 6.2 → 7.8(+26%) |
| 单任务耗时 | 25min → 14min(-44%) |
| Token 成本 | $2.30 → $1.10(-52%) |
| 工具复用率 | 0% → 78% |

---

# 附录 B:对抗式数据工厂(Adversarial Data Factory)

## B.1 设计目标
借鉴 GAN 思想,让生成 Agent 和判别 Agent 博弈,博弈过程产出合成数据。

## B.2 可加对抗的 7 个位置

1. 文献相关性打分
2. 研究问题生成
3. **实验方案设计(Red/Blue Team)** ⭐
4. **论文写作(Author vs Reviewer)** ⭐
5. 代码生成
6. 幻觉检测(Fact Hunter)
7. 红蓝对抗整体评估

## B.3 核心模式:Red/Blue Team 循环

```
Blue 提方案 v1 → Red 找漏洞 → Blue 修 → Red 再攻 → …
直到 Red 找不到漏洞,或达到最大轮数。
```

**每一轮都是一条数据**:
```json
{"blue": "...", "red_attack": "...", "blue_fix": "...", "score": 8.2}
```

## B.4 对抗的 7 种实现模式

1. Pairwise Debate(双方辩论)
2. Tournament(锦标赛 Elo 排名)
3. Self-Play(同 Agent 双身份)
4. Evolutionary(遗传算法式)
5. Adversarial Perturbation(对抗扰动)
6. Minimax(极小极大)
7. Multi-Agent Debate(多方辩论)

## B.5 关键细节(避坑)

| 坑 | 对策 |
|---|---|
| 互相吹捧 | 生成/批判用**不同 DeepSeek 变体 + 不同 system prompt**,关键裁决切 Claude Opus 4.7 打破同家族偏见 |
| Mode Collapse | 温度高 + 多样性约束 |
| 无限循环 | 最大轮次 + 评分停滞停止 |
| 评分不一致 | 多判别器投票 + 固定 rubric |
| 数据质量差 | Judge 阈值 + 人工抽检 5% |

## B.6 产出数据格式

- **DPO 偏好对**:`{prompt, chosen, rejected}`
- **SFT 样本**:`{question, good_answer}`
- **幻觉检测集**:`{context, claim, label, evidence}`
- **方案漏洞库**:`{proposal, vulnerability, fix}`

## B.7 数据飞轮闭环(点睛之笔)

```
对抗产出 10k DPO 数据 → 清洗 → 开源至 HuggingFace Hub
→ 获 stars & 下载 → 简历硬核资产
```

> 本项目**只产出数据、不做微调**(严格约束为 DeepSeek + Claude 两个 API)。数据集本身即是可展示资产。

---

## B.8 微调小模型训练计划(已移除,不在本项目范围)

> 本项目严格约束为**仅使用 DeepSeek + Claude Opus 4.7 两个云 API**,不引入任何第三方开源模型、本地推理、或微调模型。
>
> 原设计中"用对抗数据微调 Qwen2.5-7B"的方案已删除。对抗数据工厂产出的 DPO / SFT 数据仍可开源至 HuggingFace Hub 作为数据集资产,但**不在本项目内做微调训练**。
>
> 如未来放宽约束需要微调,另开独立项目处理,与主线 pipeline 解耦。

---

# 附录 C:个人研究者成本控制策略 💰(DeepSeek 优先版)

> 本项目默认设计是"理想版",个人研究者必须做成本裁剪。
> **核心策略:DeepSeek 作为主力模型**,再叠加本地 + 免费额度。
> 目标:**月预算 $10-15,单次任务 $0.05-0.15**,跑通全部核心功能。

## C.1 成本黑洞盘点(原方案)

| 项目 | 默认成本 | 月估算 |
|---|---|---|
| Claude Opus 4.7 全量主模型(误用) | $15/M input, $75/M output | $300+ |
| DeepSeek 无节制调用(未用 cache) | $0.27/M input, $1.10/M output | $100+ |
| 对抗跑批(100 种子 × 多轮,全 Claude) | 每任务 $3 | $300 |
| 云服务器(Neo4j + Qdrant) | $40/月 | $40 |
| **合计** | | **$800+/月 ❌** |

## C.2 降本核心原则

### 原则 0:**DeepSeek 作为主力模型 ⭐(2026 性价比之王)**

| 模型 | 价格(每 M token) | 对比 Claude Opus |
|---|---|---|
| **deepseek-chat** (V3.2) | input $0.27 / output $1.10 | **便宜 55x** |
| **deepseek-reasoner** (R1) | input $0.55 / output $2.19 | **便宜 30x** |
| **Cache 命中** | input $0.028 | **便宜 500x** |

**优势**:
- 中文母语级,学术表达自然
- 推理能力接近 GPT-4 / Claude Sonnet
- 兼容 OpenAI SDK,一行 base_url 切换
- Prompt Cache 自动启用

**切换代码**:
```python
from openai import OpenAI
client = OpenAI(
    api_key="sk-...",
    base_url="https://api.deepseek.com"
)
client.chat.completions.create(model="deepseek-chat", ...)
```

### 原则 1:**模型分层 —— DeepSeek 主力 + Claude 关键**

**核心策略**:95% 调用走 DeepSeek,**只在"最终产出质量决定性环节"上 Claude Opus 4.7**。

| 任务 | 模型 | 理由 |
|---|---|---|
| **Meta-Reviewer 终裁(模块 4)** | **Claude Opus 4.7** ⭐ | 关键决策,质量 > 成本 |
| **论文最终润色(模块 7 Editor)** | **Claude Opus 4.7** ⭐ | 英文学术表达 Claude 最强 |
| **分叉路径最终对比(模块 8)** | **Claude Opus 4.7** ⭐ | 一次性高价值判断 |
| 多 Agent 批判圆桌(常规 Reviewer) | **deepseek-reasoner** | 推理强,便宜 30x |
| 实验方案设计 | **deepseek-reasoner** | 需要推理 |
| 研究问题精炼 | **deepseek-reasoner** | 需要推理 |
| 文献相关性打分 | **deepseek-chat** | 高频低价值 |
| 三元组抽取 | **deepseek-chat** | 批量任务 |
| Query Rewriting | **deepseek-chat** | 简单改写 |
| 代码生成 | **deepseek-chat** | 足够用 |
| 论文章节草稿 | **deepseek-chat**(中文更自然) | 中文母语级 |
| 嵌入 | **DeepSeek Embedding API** | 同生态,统一密钥 |

**成本估算**:
- 常规任务 DeepSeek $5-10/月
- Claude 关键节点(每次研究任务仅 3-5 次调用)$2-5/月
- **合计 $10-15/月**,保留 Claude 的质量下限

### 原则 2:**基础设施本地化**

- **Embedding**:DeepSeek Embedding API(与主模型同密钥)
- **向量库**:Qdrant Local(Docker 自部署)
- **图数据库**:Neo4j Community(本地 Docker)
- **状态库**:SQLite 代替 PostgreSQL(单用户够用)

### 原则 3:**Prompt Cache 用到极致**

**DeepSeek 自动启用 cache**,命中时 input 价格 $0.028/M(便宜 10 倍)。
要想命中率高,**system prompt 放最前且保持稳定**:

```python
messages = [
    {"role": "system", "content": LONG_STABLE_PROMPT},  # 会被 cache
    {"role": "user", "content": variable_query}          # 只这部分付全价
]
```

对抗循环 system prompt 固定 → 命中率常达 **80%+**,实际成本再降一半。

### 原则 4:**对抗与判别用不同 DeepSeek 变体避免 sycophancy**

推荐组合(chat 生成 / reasoner 批判,角色分化):
```
Blue Team(生成): deepseek-chat
Red  Team(批判): deepseek-reasoner  ← 推理强,挑刺狠
Judge(裁判):    deepseek-reasoner(不同 system prompt + 高温度)
```

关键节点由 Claude Opus 4.7 做最终裁决,打破同家族模型共识偏见。

### 原则 5:**对抗轮次控制**

- **默认 2 轮**,判别器评分提升 < 10% 就停
- 只对评分低的方案跑多轮
- 数据工厂改为**周末批处理**

### 原则 6:**数据采集分批**

- 先跑 10 个种子问题验证 pipeline(~$1)
- 稳定后每周 10 个 = 月 $5
- 3 个月累积 120 个问题,数据量够开源

### 原则 7:**只用 DeepSeek + Claude,拒绝多平台依赖**

本项目**严格只用两个 API 提供商**:

| 服务 | 用途 | 占比 |
|---|---|---|
| **DeepSeek**(deepseek-chat / reasoner / embedding) | 主力,所有日常调用 | 95% |
| **Claude Opus 4.7** | 关键节点终裁、英文论文润色 | 5% |

**不引入** Gemini / Groq / OpenAI / HuggingFace Inference / 本地 Ollama 等任何第三方,理由:
- 密钥/SDK/计费/限流只需管理两个,工程复杂度最低
- DeepSeek Cache 命中率随调用集中度提升
- 简历话术更聚焦:"用对的两个模型做对的事",胜过"堆砌 5 个平台"

### 原则 8:**冷启动用 DeepSeek 新账号赠额度**

DeepSeek 新账号通常赠送一定额度,够跑通 MVP-1 验证 pipeline,之后再充值。

---

## C.3 省钱版架构调整(只用 DeepSeek + Claude)

```
┌────────────────────────────────────────────────┐
│  关键节点(5% 调用,质量决定性)                 │
│  Claude Opus 4.7   ← Meta-Reviewer / Editor    │
│                      分叉最终对比               │
├────────────────────────────────────────────────┤
│  主力模型(95% 调用)                           │
│  deepseek-chat      ← 日常任务、写作、生成、   │
│                      Query Rewriting、抽取     │
│  deepseek-reasoner  ← 常规推理、评审、决策、   │
│                      Red Team 批判             │
├────────────────────────────────────────────────┤
│  Embedding                                      │
│  DeepSeek Embedding API ← 与主模型同密钥       │
├────────────────────────────────────────────────┤
│  本地存储(无模型,只是基础设施)                │
│  SQLite + Qdrant Local + Neo4j Docker          │
└────────────────────────────────────────────────┘
```

> 全项目**仅两个 LLM 提供商**(DeepSeek + Anthropic),无任何第三方/本地模型依赖。

## C.4 成本对比

| 项目 | Claude 原方案 | Claude 省钱版 | **DeepSeek 版 ⭐** |
|---|---|---|---|
| 月 API 费用 | $450+ | $30-50 | **$5-15** |
| 云基础设施 | $40 | $0 | **$0** |
| 单次研究任务 | $2.30 | $0.40 | **$0.05-0.15** |
| 数据工厂 100 任务 | $230 | $40 | **$8-15** |
| 可跑批量 | 有限 | 100/月 | **1000+/月** |
| **月总成本** | **$800+** | $30-50 | **$10-15 ✅✅** |

**降本 98%**,功能损失 < 10%。个人研究者月 $10 即可玩转完整项目。

## C.5 个人研究者的裁剪建议

| 模块 | 原 | 个人版 |
|---|---|---|
| 模块 1 问题精炼 | ✅ 保留 | ✅ 保留(deepseek-chat 即可) |
| 模块 2 文献检索 | 4 源并行 | **2 源**(arXiv + Semantic Scholar) |
| 模块 3 知识图谱 | Neo4j + 可视化 | **NetworkX + matplotlib**(轻量) |
| 模块 4 批判圆桌 | 6 Agent | **3 Agent**(Reviewer/Devil/Meta) |
| 模块 5 实验方案 | ✅ 保留 | ✅ 保留 |
| 模块 6 代码执行 | Docker 沙箱 | **延后**(Future Work) |
| 模块 7 论文写作 | 6 章节并行 | **串行单 Agent** |
| 模块 8 分叉回放 | PostgreSQL | **SQLite** |
| 自我进化 L1-L5 | 全做 | **仅 L1 + L2** |
| 对抗数据工厂 | 100 种子 | **10 种子 + 小规模验证** |

## C.6 调用优先级(只两个供应商)

每次任务编排时,按以下顺序选模型:
1. **deepseek-chat**:默认主力,所有写作/生成/抽取/Query 改写
2. **deepseek-reasoner**:需要推理/评审/决策的环节
3. **Claude Opus 4.7**:仅限三个关键节点 —— Meta-Reviewer 终裁、论文 Editor 润色、分叉路径最终对比

> 严格守住"两个供应商"边界,不引入任何第三方 free tier。

## C.7 硬件建议

本项目**全部走云 API**,不依赖本地 GPU:
- **任何笔记本均可**(包括无 GPU 的轻薄本)
- 本地只跑 Docker(Qdrant + Neo4j + SQLite)和前后端
- 优势:零硬件门槛、零模型部署运维、跨设备开发一致

## C.8 成本监控代码片段

```python
class CostTracker:
    PRICING = {
        "deepseek-chat":     {"in": 0.27, "out": 1.10, "cache": 0.028},
        "deepseek-reasoner": {"in": 0.55, "out": 2.19, "cache": 0.055},
        "claude-opus-4-7":   {"in": 15.0, "out": 75.0, "cache": 1.50},
    }

    def __init__(self, monthly_budget=15):
        self.budget = monthly_budget
        self.spent = 0

    def log(self, model, in_tok, out_tok, cache_hit_tok=0):
        p = self.PRICING[model]
        cost = ((in_tok - cache_hit_tok) * p["in"] +
                cache_hit_tok * p["cache"] +
                out_tok * p["out"]) / 1_000_000
        self.spent += cost
        if self.spent > self.budget * 0.8:
            print("⚠️ 已用 80% 预算")
        return cost
```

**每天日终打报告**:花了多少、在哪花的、哪个模块最烧钱 → 持续优化。

---

## C.9 一句话总结

**省钱公式 = DeepSeek 主力(95%)+ Claude Opus 4.7 关键节点(5%)+ Prompt Cache + 批处理**。

全项目**只用两个 LLM 供应商**,工程复杂度最低;个人研究者 **$10-15/月** 即可跑起完整项目,核心功能不打折,3 个月攒出的数据集 + 博客,**性价比爆表**。

简历加分:DeepSeek + Claude 双模型分层架构,体现"用对的模型做对的事"的工程判断力,拒绝无脑堆砌多平台。
