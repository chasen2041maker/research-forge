"""
============================================================
 新架构核心数据结构(state/cards.py)
============================================================

本文件集中定义"整理版架构"中跨模块流转的四种核心卡片数据结构:

    TopicCard            ── M0 候选课题发现器输出
    GapCard              ── M3 升级后的研究空白(替代旧版 research_gaps: list[str])
    DecisionCard         ── M4 Evidence-grounded Roundtable 输出的流程决策
    EvidenceAccessStatus ── M2.5 文献访问状态(决定证据等级)

📌 为什么单独立一个文件,而不是塞进 research_state.py
    1. research_state.py 已经很长(模块产物 7 个 + 主 State),再往里塞 4 个新结构
       可读性下降;
    2. 这些卡片在新架构中是"跨模块流转的契约",未来 M0/M2.5/M3/M4/M5/M5.5/M8
       多个模块都要 import 它们,放独立文件 import 路径更短;
    3. 教学上把"基础设施 / 状态 / 业务模块"分层清晰也是 LangGraph 项目的常见做法。

📌 与旧字段的兼容关系
    - GapCard 不替换 research_gaps,而是与之并存:M3 既输出 list[GapCard],
      也保留 list[str] 给老下游(M5/M7)继续使用,直到全链路升级完毕。
    - TopicCard 是全新结构,M0 升级时引入。
    - DecisionCard 与 meta_decision 并存:M4 同时写两份,新下游(M8)消费 DecisionCard,
      老下游继续读 meta_decision。
    - EvidenceAccessStatus 给 M2.5 用,与 papers 列表通过 paper_id 一一对应。

🔗 跨模块契约总览
    各卡片在主流水线里的"生产者 → 消费者"链路:

      TopicCard           M0 生产 → user_select_topic 选 → 写入 raw_question (Phase B)
                                  → ForkManager.branch_from_topic_cards 用 (Phase D)
      EvidenceAccessStatus M2.5 生产 → M3.build_gap_cards 调权 → M4.build_decision_card 引用
                                    → M5.5.decide_gate 判低证据降级 (Phase C)
      GapCard             M3 生产 → M5.design_experiment 注入先验 → M5.5 引用 missing_piece
                                  → 前端 GapCardList 渲染 (Phase B/E)
      DecisionCard        M4 生产 → M5.5.decide_gate 服从 → M8.merge_winner 用 final_rating
                                  → 前端 DecisionCardView 渲染 (Phase C/E)

    任何模块改字段名或加字段必须同步:
      1) 这里的 TypedDict 定义
      2) 上游生产模块的写入逻辑
      3) 下游消费模块的读取逻辑
      4) state/research_state.py 主 State 字段(如 current_gap_id 引用 GapCard.gap_id)
      5) frontend/src/app/page.tsx Snapshot interface(如果前端展示)

------------------------------------------------------------
"""

from __future__ import annotations

from typing import TypedDict


# ============================================================
# M0:候选课题发现器输出
# ============================================================
class TopicCard(TypedDict, total=False):
    """
    候选研究方向卡片。M0 一次会生成 K 条 TopicCard,后续可由用户、M4 或 M8 筛选。

    字段对应整理版 §4.3 TopicCard 定义。
    """

    topic_id: str                  # 内部唯一 ID(e.g. "tc-001")
    title: str                     # 一句话标题
    research_direction: str        # 大方向描述(如"RAG 在医学问答的鲁棒性")
    candidate_question: str        # 待精炼的候选研究问题
    suspected_gap: str             # 初步识别的研究空白(待 M3 验证)
    key_evidence: list[str]        # 关键证据论文/数据集/benchmark id 列表
    novelty_rationale: str         # 为什么这个方向有创新潜力
    feasibility_rationale: str     # 为什么这个方向当前可行(数据/工具/baseline 是否齐备)
    risk_factors: list[str]        # 风险因素(数据敏感、成本高、需要硬件等)
    score: float                   # 综合评分(0-10),供 Top-K 排序


# ============================================================
# M2.5:文献访问状态
# ============================================================
class EvidenceAccessStatus(TypedDict, total=False):
    """
    单篇论文的访问状态。M2.5 解析后写入,M3/M4/M5 据此调权。

    字段对应整理版 §5.2 EvidenceAccessStatus。
    """

    paper_id: str
    access_status: str             # fulltext / abstract_only / restricted / failed
    has_code: bool                 # GitHub / 代码仓是否可用
    has_dataset: bool              # 公开数据集是否可获取
    has_benchmark: bool            # 是否有公开 benchmark 评测
    evidence_level: str            # high / medium / low(整理版 §5.3 降权规则)
    notes: list[str]               # 解析过程的补充说明


# ============================================================
# M3:GapCard(替代旧版 research_gaps: list[str])
# ============================================================
class GapCard(TypedDict, total=False):
    """
    结构化研究空白。从知识图谱 + 证据中合成,供 M4 评审与 M5 设计实验直接消费。

    字段对应整理版 §6.2 GapCard。
    与旧版 research_gaps: list[str] 共存,逐步替代。
    """

    gap_id: str
    title: str                     # 空白标题
    problem: str                   # 问题描述
    evidence_papers: list[str]     # 证据论文 id 列表
    existing_methods: list[str]    # 已有方法
    missing_piece: str             # 缺失的关键拼图
    datasets: list[str]            # 可用数据集(传给 M5 直接用)
    baselines: list[str]           # 可用 baseline(传给 M5 直接用)
    metrics: list[str]             # 推荐评测指标
    novelty_score: float           # 0-10
    feasibility_score: float       # 0-10
    evidence_level: str            # high / medium / low,从 M2.5 汇总
    risks: list[str]               # 风险标签


# ============================================================
# M4:DecisionCard(Evidence-grounded Roundtable 流程决策)
# ============================================================
class DecisionCard(TypedDict, total=False):
    """
    M4 Roundtable 输出的流程决策。给 M5/M5.5/M8 消费。

    字段对应整理版 §7.4 DecisionCard。
    与旧版 meta_decision dict 并存,逐步替代。

    ▍recommended_action 与 target_node 的关系
        recommended_action 是"语义动作"(refine_question / fetch_more_evidence / ...),
        target_node 是"具体跳到哪个节点"(m0 / m1 / m2 / m3 / m5 / m6 / m7 / end)。
        M8 据此决定 fork 内部回退或开新分支。
    """

    passed: bool
    decision: str                  # pass / minor_revision / major_revision / reject / stop
    final_rating: float            # 1-10
    recommended_action: str        # continue / refine_question / fetch_more_evidence /
                                   # rebuild_gap / revise_experiment / choose_new_topic / stop
    target_node: str               # m0 / m1 / m2 / m3 / m5 / m6 / m7 / end
    branch_count: int              # M8 应据此开几条分支(默认 1)
    branch_variants: list[str]     # 分支变体描述(可选)
    blocking_issues: list[str]     # 阻塞性问题
    required_fixes: list[str]      # 必须修复的问题清单
    reason: str                    # 综合理由
