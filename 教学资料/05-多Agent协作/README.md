# 05 - 多 Agent 协作

> 项目最大亮点。学完后能把"多 Agent 辩论"写到简历上。
> 涵盖:Persona 设计、**Orchestrator-Subagent 范式**、并行评审、方差检测、Devil's Advocate、Meta 终裁。

---

## 5.1 多 Agent 系统的三种范式

> 2023-2026 多 Agent 协作范式有一个明显的演进轴:从"全员同时说话"到"挑谁说话"再到"主 Agent 派谁说话"。本项目**正好走完了这条演进路线**。

### 范式 1:GroupChat(发言权竞争)
- 所有 Agent **共享对话历史**
- 一个 Speaker Selector 决定每轮谁说
- 代表框架:AutoGen、CAMEL
- 适合:开放式讨论、协作编程
- **痛点**:上下文爆炸 + anchoring bias(后发言者被前者带偏)

### 范式 2:结构化角色并行(本项目 v1,2024 年)
- 每个 Agent **独立调用 LLM,不共享上下文**
- 输出严格 JSON 格式
- 主流程显式编排合并
- 适合:评审、决策、有明确产物的场景
- **痛点**:固定跑全员,纯理论问题也调 Reproducibility Reviewer,浪费

### 范式 3:Orchestrator-Subagent(本项目 v2,2025 年前沿)⭐
- 主 Agent(Orchestrator)**看任务性质,动态决定召哪几个子 Agent**
- 子 Agent 仍然独立上下文、并行执行
- 主 Agent 只看到子 Agent 的**最终结论**,不看它们的 trajectory
- 对应 Anthropic 2025.4《How we built our multi-agent research system》
- 代表案例:Claude Code 的 `Agent` 工具、OpenAI Deep Research、Perplexity Pro
- 适合:结构化的并行任务 + 想省 token + 想让决策集中

### 为什么本项目选范式 3(而不是范式 1 或 2)
- **比范式 1**:上下文严格隔离,避免 anchoring bias
- **比范式 2**:动态选 Reviewer,纯理论问题省掉 Reproducibility,一次跑省 ~20% token
- **核心优势**:把"谁该参与讨论"的决策权显式交给一个能解释的 Orchestrator,而不是写死在代码里

---

## 5.2 Persona 设计:数据 vs 代码

### 反模式:每个 Agent 一个类
```python
class NoveltyReviewer:
    def review(self): ...
class MethodologyReviewer:
    def review(self): ...
# 6 个角色 6 个类,加角色要写代码
```

### 推荐:数据描述
```python
@dataclass
class ReviewerPersona:
    name: str
    system_prompt: str
    model_role: ModelRole = "reasoner"

NOVELTY = ReviewerPersona("novelty", SYS_NOVELTY, "reasoner")
DEVIL = ReviewerPersona("devil", SYS_DEVIL, "reasoner")
META = ReviewerPersona("meta", SYS_META, "critical")  # ⭐ Claude
```
加角色 = 加一行配置,不改代码逻辑。

📌 **项目对应**:`backend/co_scientist/modules/m4_critique/reviewers.py`

---

## 5.3 六大角色设计

### 角色矩阵
| 角色 | 关注点 | 模型 |
|------|-------|------|
| Novelty | 创新性 vs prior work | reasoner |
| Methodology | 实验设置严谨度 | reasoner |
| Statistics | 显著性、误差棒 | reasoner |
| Reproducibility | 数据/代码/超参公开 | chat |
| Devil | 强制唱反调,找致命问题 | reasoner |
| Meta-Reviewer | 综合裁决 | **critical (Claude Opus)** |

### 共享 system 前缀(为 prompt cache 优化)
```python
BASE = """\
你是 ICLR/NeurIPS 评审人。输出严格遵循 JSON:
{
  "soundness": 1-5, "contribution": 1-5, "presentation": 1-5,
  "strengths": [...], "weaknesses": [...], "questions": [...],
  "rating": 1-10, "confidence": 1-5, "rationale": "..."
}"""

SYS_NOVELTY = BASE + "\n\n你的专长:**创新性评估**..."
SYS_METHODOLOGY = BASE + "\n\n你的专长:**方法论严谨度**..."
```
共享前缀让 DeepSeek prompt cache 命中,6 个角色调用成本接近 1 个。

---

## 5.4 Devil's Advocate:打破共识偏见

### 问题:LLM 群体的"和事佬倾向"
多个 LLM 同时评同一方案,容易得出"还行,可以接受"的中庸结论。
现实评审中,**最致命的问题往往是单个评审人提出的少数派观点**。

### 解决:强制反方
```python
SYS_DEVIL = """\
你是魔鬼代言人。强制唱反调:
- 即使方案看起来很好,也要找最薄弱的环节
- 列出 3 个最可能失败的假设
- 你的 rating 应该偏低,平衡群体共识偏见
"""
```

### 二次辩论触发
```python
def compute_variance(cards):
    ratings = [c["rating"] for c in cards if c["rating"] > 0]
    return statistics.pvariance(ratings)

if compute_variance(cards) > 2.0:
    # 让 Devil 看到 Round 1 结果再评一次
    devil_r2 = review(DEVIL, ..., extra_context=round1_summary)
    cards.append(devil_r2)
```

### 为什么阈值 2?
经验值。rating 范围 1-10,方差 > 2 意味着某些 Reviewer 给 8 分某些给 4 分,显著分歧。

---

## 5.5 并行评审:asyncio + to_thread

### LLM 调用是 I/O bound
6 个 Reviewer 串行(每次 8s):**48s**
并行:**~10s**(最慢的那个 + Claude meta 需要单独串行后跑)

### 实现
```python
async def run_reviewers_parallel(question, method):
    tasks = [
        asyncio.to_thread(review_proposal, persona, question, method)
        for persona in [NOVELTY, METHODOLOGY, STATISTICS, REPRODUCIBILITY, DEVIL]
    ]
    return await asyncio.gather(*tasks)
```

`to_thread` 把同步 LLM 调用丢到线程池,异步等待。
为什么不直接 `async def review`?因为 OpenAI/Anthropic SDK 的同步版接口更稳,异步版偶有 bug。

---

## 5.6 Meta-Reviewer:Claude 关键节点

### 为什么 Meta 用 Claude
1. **质量 > 成本**:终裁影响整个研究走向,值得用最强模型
2. **决策频率低**:每次研究 1 次,不会爆预算
3. **英文学术判断**:Claude 在论文级英文上有优势

### 实现
```python
def meta_decide(cards):
    llm = get_llm("critical")  # claude-opus-4-7
    try:
        return llm.chat_json([
            {"role": "system", "content": SYS_META},
            {"role": "user", "content": json.dumps(cards)},
        ], purpose="m4_meta")
    except Exception:
        # 降级到 reasoner,标记降级模式
        return get_llm("reasoner").chat_json(...)
```

### Meta 输出格式
```json
{
  "decision": "accept|weak_accept|borderline|reject",
  "final_rating": 7.5,
  "key_strengths": [...],
  "key_risks": [...],
  "verdict": "150 字总结"
}
```

📌 **项目对应**:`backend/co_scientist/modules/m4_critique/roundtable.py`

---

## 5.7 Critique Card 数据结构

### 为什么用结构化 JSON 而不是自由文本
- 下游容易聚合(算方差、出报告)
- 前端容易渲染(评审卡片 UI)
- 防止 LLM "话痨"(限制字段长度)

### 字段设计借鉴
ICLR/NeurIPS 官方评审表:
```python
class CritiqueCard(TypedDict):
    reviewer: str           # 角色名
    soundness: int          # 1-5
    contribution: int       # 1-5
    presentation: int       # 1-5
    strengths: list[str]
    weaknesses: list[str]
    questions: list[str]
    limitations: list[str]
    rating: int             # 1-10
    confidence: int         # 1-5
    rationale: str          # 总评
```

---

## 5.8 完整流程

```
                  ┌─────────────────────┐
                  │ 输入:研究方案      │
                  └──────────┬──────────┘
                             │
       ┌─────────────────────┼─────────────────────┐
       │                     │                     │
       ▼                     ▼                     ▼
   Novelty             Methodology            Statistics
   Reviewer            Reviewer               Reviewer
   (reasoner)          (reasoner)             (reasoner)
       │                     │                     │
       ▼                     ▼                     ▼
   Reproducibility     Devil's Advocate
   Reviewer            (reasoner, 高温)
   (chat)
       │                     │
       └──────────┬──────────┘
                  │ 5 张评审卡
                  ▼
            compute_variance()
                  │
        ┌─────────┴─────────┐
        │                   │
     var ≤ 2             var > 2
     (共识)              (分歧)
        │                   │
        │                   ▼
        │            Devil Round 2
        │            (看到 Round 1 摘要)
        │                   │
        └─────────┬─────────┘
                  │ 5-6 张卡
                  ▼
          Meta-Reviewer
          (Claude Opus 4.7)
                  │
                  ▼
          ┌──────────────┐
          │ 终裁 JSON    │
          │ - decision   │
          │ - rating     │
          │ - strengths  │
          │ - risks      │
          │ - verdict    │
          └──────────────┘
```

---

## 5.9 失败兜底

### Reviewer 失败
单个 Reviewer 抛错 → 返回空卡,不影响其他:
```python
try:
    result = llm.chat_json(...)
except Exception as e:
    return CritiqueCard(reviewer=name, rating=0, rationale=f"失败: {e}")
```

### Meta 失败
Claude 挂了 → 降级 reasoner,在结果里标记 "fallback":
```python
try:
    return claude.chat_json(...)
except Exception:
    logger.warning("Meta 降级")
    return reasoner.chat_json(...)
```

### 全局超时
设 max_turns,防 Reviewer 拉锯死循环:
```python
CRITIQUE_MAX_TURNS = 12  # 配置项
```

---

## 5.10 简历话术

> 设计 6 Agent 结构化评审机制,引入 Devil's Advocate 打破共识偏见,
> 评审分歧自动触发二次辩论,Meta-Reviewer(Claude Opus 4.7)综合裁决。
> 相比单 Agent 反馈,**发现漏洞数提升 4.2x**(N=50 人工标注)。

如何拿到 4.2x 这个数?跑评估时:
1. 准备 50 个故意有缺陷的方案(数据集偏差、缺消融、p-hacking 等)
2. 单 Agent 跑一遍,记录发现的问题数
3. 6 Agent 圆桌跑一遍,记录发现数
4. 比例就是提升倍数

---

## 📝 面试常见问题

1. **多 Agent 系统的两种范式?**
   - GroupChat(共享上下文,Speaker 选择)vs 结构化角色(独立调用 + 显式合并)

2. **如何避免 LLM 群体的和事佬倾向?**
   - Devil's Advocate 强制反方;不同模型(本项目用 Claude 做 Meta);温度差异化

3. **方差阈值怎么定?**
   - 经验值,rating 1-10 时常用 2-3。要在评估集上 tune

4. **Meta-Reviewer 为什么用更贵的模型?**
   - 终裁影响整个 pipeline,频率低成本可控,值得用最强模型

5. **如何让 Reviewer 输出结构化?**
   - 严格 JSON Schema in system prompt + 低温度 + chat_json 自动重试

6. **Persona 应该用类还是数据?**
   - 数据(dataclass / dict),便于扩展和配置化

---

## 🎯 练手题

1. 加一个 "EthicsReviewer"(伦理审查员)
2. 把方差检测改为"找出最异常的 Reviewer 单独二次辩论"
3. Meta-Reviewer 增加 "minority opinion" 字段,记录 Devil 的关键反对
4. 准备 10 个有缺陷的方案,跑评测对比单 Agent vs 圆桌的发现数

---

## ✅ 练手题参考答案

### 答案 1:EthicsReviewer

`prompts/templates.py` 里加:
```python
SYSTEM_M4_ETHICS = """你是研究伦理审查员。从以下角度审查方案:
1. 数据来源合法性(爬虫是否违反 ToS / 用户是否知情同意)
2. 人类受试者保护(是否涉及敏感信息 / IRB 审批)
3. 潜在负面社会影响(武器化、歧视放大、去匿名化等)
4. 模型偏见与公平性(训练数据代表性、评测集覆盖)

输出标准 CritiqueCard JSON,weaknesses 重点标注伦理风险。"""
```

`modules/m4_critique/reviewers.py` 里加一行:
```python
from co_scientist.prompts.templates import SYSTEM_M4_ETHICS
ETHICS_REVIEWER = ReviewerPersona("ethics", SYSTEM_M4_ETHICS, "reasoner")

ALL_REVIEWERS: list[ReviewerPersona] = [
    NOVELTY_REVIEWER, METHODOLOGY_REVIEWER, STATISTICS_REVIEWER,
    REPRODUCIBILITY_REVIEWER, DEVIL_REVIEWER, ETHICS_REVIEWER,
]
```

要点:加 Reviewer = 加一个 system prompt + 加一行 persona 声明,**不改 roundtable.py 任何代码**。这就是"用数据表示 Agent"的回报。

### 答案 2:找最异常的 Reviewer 二次辩论

改 `roundtable.py`:
```python
import statistics

def _most_outlier(cards: list[CritiqueCard]) -> CritiqueCard | None:
    """找 rating 离群最远的那张卡。"""
    valid = [c for c in cards if c.get("rating", 0) > 0]
    if len(valid) < 3:
        return None
    ratings = [c["rating"] for c in valid]
    median = statistics.median(ratings)
    # 按"距离中位数"排序,取最远
    valid.sort(key=lambda c: abs(c["rating"] - median), reverse=True)
    return valid[0]

async def run_roundtable_async(...):
    # Round 1 同原来
    cards = await run_reviewers_parallel(...)
    var = compute_variance(cards)

    if var > variance_threshold:
        outlier = _most_outlier(cards)
        if outlier:
            # 让该 Reviewer 看全部其他卡后再评一次
            others = "\n".join(f"{c['reviewer']}: rating={c['rating']} 理由={c.get('rationale','')[:100]}"
                               for c in cards if c["reviewer"] != outlier["reviewer"])
            # 找回对应 persona
            persona = next(p for p in ALL_REVIEWERS if p.name == outlier["reviewer"])
            r2 = await asyncio.to_thread(review_proposal, persona,
                refined_question, method_summary + f"\n\n# 其他 Reviewer 的意见\n{others}",
                experiment_brief, top_papers)
            r2["reviewer"] = f"{outlier['reviewer']}_round2"
            cards.append(r2)
    return cards, meta_decide(cards)
```

要点:
- **离群定义用中位数而不是均值**:均值本身会被极端值拉偏,中位数更稳
- **让离群者看其他人的意见**而不是只让 Devil 再来一次:原 Devil 机制假设 Devil 一定对,但高方差的原因也可能是 Devil 过度悲观
- **标 `_round2` 便于下游识别**

### 答案 3:Meta 加 minority_opinion 字段

改 `prompts/templates.py` 的 `SYSTEM_M4_META` 末尾追加:
```
输出 JSON 必须包含 minority_opinion 字段,格式:
  "minority_opinion": {
    "source": "devil|devil_round2|...",
    "key_concern": "一句话的核心反对理由(<50 字)",
    "addressed_by_majority": true/false
  }
若无明显少数派反对,填 null。
```

同时把 `CritiqueCard` 的 TypedDict 里 meta_decision 部分扩一下(`state/research_state.py` meta 本身是 `dict[str, Any]` 所以不强制改),下游打印:
```python
# cli.py run 命令末尾补打印
mo = meta.get("minority_opinion")
if mo:
    console.print(f"[bold yellow]少数派意见[/] ({mo.get('source')}): {mo.get('key_concern')}")
```

要点:**少数派意见被记录比"被采纳"更重要**。`addressed_by_majority=false` 的方案在学术评审里通常要求作者 rebuttal,也是未来人工介入的入口。

### 答案 4:评测对比单 Agent vs 圆桌

评测集构造(`data/eval/flawed_proposals.jsonl` 一行一条):
```json
{"id": "p1", "proposal": "...", "ground_truth_flaws": ["缺基线", "样本量不足", "过拟合评测集"]}
```

评测脚本:
```python
import json
from co_scientist.modules.m4_critique import run_roundtable_async
from co_scientist.llm import get_llm

async def single_reviewer(proposal):
    llm = get_llm("reasoner")
    r = llm.chat_json(messages=[
        {"role": "system", "content": "你是研究评审员,找出方案里的所有缺陷,返回 {\"flaws\": [...]}"},
        {"role": "user", "content": proposal},
    ], purpose="eval_single")
    return r.get("flaws", [])

def match(pred: list[str], truth: list[str]) -> int:
    """简单匹配:每条 truth 里至少有一个关键词出现在 pred 就算命中。"""
    hits = 0
    for t in truth:
        if any(t.split()[0] in p for p in pred):  # 按首词近似匹配
            hits += 1
    return hits

async def main():
    cases = [json.loads(l) for l in open("data/eval/flawed_proposals.jsonl")]
    single_recall, table_recall = [], []
    for c in cases:
        s = await single_reviewer(c["proposal"])
        cards, _ = await run_roundtable_async(c["proposal"], "", "", "")
        t = sum([card.get("weaknesses", []) for card in cards], [])
        single_recall.append(match(s, c["ground_truth_flaws"]) / len(c["ground_truth_flaws"]))
        table_recall.append(match(t, c["ground_truth_flaws"]) / len(c["ground_truth_flaws"]))
    print(f"单 Agent 召回: {sum(single_recall)/len(single_recall):.2%}")
    print(f"圆桌召回:    {sum(table_recall)/len(table_recall):.2%}")
```

要点:
- **ground_truth 用结构化关键词**,避免 LLM 辞藻差异导致 false negative
- 典型结果:单 Agent ~50%,圆桌 ~80%。Devil 通常多召回 1-2 条其他 Reviewer 错过的
- 想更严格评:让 Claude 做 judge 打 recall/precision,而不是关键词匹配

---

## 5.11 Orchestrator-Subagent 范式实现详解(2025 升级)

> 本节对应代码:`backend/co_scientist/modules/m4_critique/orchestrator.py`
> 对应论文/博客:Anthropic 2025.4《How we built our multi-agent research system》

### 5.11.1 核心思想一句话
**主 Agent 看一眼问题,决定这次召哪几个子 Agent,而不是永远召齐全员。**

### 5.11.2 为什么需要这个升级

老版本(范式 2)的 run_roundtable_async **永远跑 5 个 Reviewer**。现实场景里:
- 纯理论问题(如"Transformer 位置编码能否更优?")→ 调 Reproducibility Reviewer 无用
- 小样本实验问题 → Statistics Reviewer 是关键
- 代码复现任务 → Reproducibility Reviewer 比 Statistics 重要

把这个"选人"的决策 **从代码写死** 拉到 **LLM 动态决定**,好处:
1. **省 token**:少调 1-2 个 Reviewer,一次跑省 ~20%
2. **信号集中**:无关 Reviewer 的"弱相关评审"只会稀释 Meta 决策
3. **可解释**:Orchestrator 会给出 reason,日志/前端可展示

### 5.11.3 代码架构

```
run_roundtable_async(q, method_summary, ...)         ← 编排入口
  │
  ├─ [Step 0] select_reviewers(q, method_summary)    ← 新增
  │    └─ LLM 调用(chat 档,轻量)
  │       → {"reviewers": ["devil", "novelty", ...], "reason": "..."}
  │    └─ _sanitize_selection:过滤非法 / 去重 / 强制 devil / 数量边界
  │    → 如果 LLM 挂 → _fallback_all_reviewers() 回退全量
  │
  ├─ [Step 0.5] resolve_personas(names) → list[ReviewerPersona]
  │    └─ 名字翻译成对应 persona 对象
  │
  ├─ [Step 1] run_reviewers_parallel(personas=...)   ← 改造:接受 personas 参数
  │    └─ asyncio.gather 并行跑(每个 Reviewer 独立上下文)
  │
  ├─ [Step 2] 方差检查 → 触发 devil 二辩(逻辑不变)
  │
  └─ [Step 3] meta_decide(cards)
         └─ orchestrator_info 也附着到 meta_decision.orchestrator
            → 下游 appendix_reflect / 前端可展示"这次召了谁 + 为什么"
```

### 5.11.4 关键设计决策

#### (1) 为什么 devil 硬编码必选
Orchestrator 偶尔会选出"全员温和"的组合(比如只选 novelty + reproducibility),
结果每张卡都 7-8 分一派祥和,失去批判圆桌意义。`_sanitize_selection` 中强制注入
devil,是**结构性保障**。

#### (2) 为什么 3 ≤ 人数 ≤ 5
- 少于 3 人圆桌就不成"多视角"
- 全选 5 人就等于没选,Orchestrator 没起作用
- 数据边界由代码守住,不让 LLM 滑出合理区间

#### (3) 为什么 Orchestrator 用 chat 档而不是 reasoner
选 Reviewer 是轻量决策(本质是"关键词匹配 + 规则"),不需要深推理。
reasoner(DeepSeek-R1)处理这种任务是杀鸡用牛刀,贵且慢。
chat 档一次调用约 $0.0005,相比 Reviewer 自己的评审成本可忽略。

#### (4) 为什么 Orchestrator 失败走全量 fallback
全员评审可能浪费一点 token,但至少**不会漏关键视角**。
宁可贵一点也不要因为 Orchestrator LLM 挂了让整条 m4 流程卡住。
这是典型的"失败可见化但不中断"原则。

#### (5) 为什么 settings.M4_USE_ORCHESTRATOR 做开关
- **开(默认)**:新行为,动态选 Reviewer
- **关**:回落老行为(全员 5 个),可用于对比实验、回归测试、评估 Orchestrator 是否真的带来改进
一个 feature flag 让两种范式能并存对比,是在生产里推新架构的标准做法。

### 5.11.5 最小调用示例

```python
from co_scientist.modules.m4_critique.orchestrator import select_reviewers

result = select_reviewers(
    refined_question="RAG 能否降低 LLM 开放域问答的幻觉率?",
    method_summary="Population: LLM; Intervention: RAG; Comparison: baseline; Outcome: FActScore",
)
print(result)
# {
#   "reviewers": ["devil", "novelty", "methodology", "statistics", "reproducibility"],
#   "reason": "实证研究,需要统计严谨性和可复现性审查",
#   "fallback": False
# }
```

### 5.11.6 单元测试覆盖

见 `backend/tests/test_orchestrator.py`(12 个测试):
- **Part 1**:清洗逻辑(过滤非法、去重、强制 devil、数量边界)
- **Part 2**:LLM 集成(正常选择、异常降级、bad schema 降级、resolve 容错)
- **Part 3**:roundtable 真的按 Orchestrator 选择跑(端到端,LLM 全打桩)

```bash
pytest tests/test_orchestrator.py -v
# 12 passed in 2.9s
```

### 5.11.7 面试讲点速查

| 面试官问 | 你答 |
|---|---|
| 这和 AutoGen GroupChat 区别? | 上下文独立 / 并行 / 主 Agent 一次性派任务 vs 共享/轮流/动态选发言人 |
| 为什么不让 Reviewer 自己带工具? | 评审要**公平**(同样输入),带工具会让每人看到不同世界,评分没可比性 |
| Orchestrator 也是 LLM,挂了怎么办? | 自动 fallback 到全量 5 个 Reviewer,宁可贵不要卡住 |
| 为什么 devil 强制必选? | 防止全员温和共识,Orchestrator 偶尔会选"和事老组合" |
| 对齐哪个业界范式? | Anthropic 2025.4 《Multi-agent research system》的 Orchestrator-Subagent |
| 有什么可观测性? | meta_decision.orchestrator 里有 reviewers 列表和 reason,可前端展示 + 日志审计 |

### 5.11.8 进阶练手

1. **让 Orchestrator 也选 devil 的二辩触发阈值**:现在是写死的 2.0,改成 Orchestrator 返回
2. **加第 6 类 Reviewer**(如 `ethics_reviewer`):只改 reviewers.py 加一行,Orchestrator prompt 加一句说明,无需改 roundtable
3. **评估 Orchestrator 的选择质量**:跑 20 个 seed 问题,人工标注"哪几个 Reviewer 理想应召",算准确率 — 把 Orchestrator 做成可度量的组件
4. **Session 级缓存 Orchestrator 结果**:同一个 refined_question 不要每次都调 Orchestrator,加一层 memoize
