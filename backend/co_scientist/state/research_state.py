"""
============================================================
 全局研究状态(state/research_state.py)
============================================================

🎓 教学目标
    LangGraph 的核心思想:把"流程"建模成一张状态图,
    每个节点是一个函数,输入和输出都是同一个 State 对象。

    这就要求 State 设计得好:
      - 所有节点会写到的字段都要在这里声明
      - 字段要有默认值,这样跑到一半的状态也是完整的
      - 用 dataclass / TypedDict / pydantic 都行,本项目选 TypedDict + 注解
        (LangGraph 原生支持 TypedDict,且 reducer 字段更灵活)

📌 设计要点
    1. ResearchState 涵盖整个 8 模块流程的所有产物
    2. 用 Annotated[List, operator.add] 让多个并行节点能同时往同一字段追加
       (LangGraph 的 reducer 机制)
    3. 复杂结构(论文、批判卡片等)单独定义类型,避免 dict 满天飞

------------------------------------------------------------
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from co_scientist.state.cards import (
    DecisionCard,
    EvidenceAccessStatus,
    GapCard,
    TopicCard,
)


# ============================================================
# 子结构定义:每个模块的产物
# ============================================================


# ---- 模块 1:研究问题精炼 ----
class PICO(TypedDict, total=False):
    """
    PICO 是临床研究常用的研究问题框架,本项目借用来描述 NLP/AI 研究问题。

    例如对于 "RAG 如何减少 LLM 幻觉":
      - population: 大语言模型(LLM)
      - intervention: RAG 检索增强
      - comparison: 无检索的 baseline
      - outcome: 幻觉率
    """

    population: str
    intervention: str
    comparison: str
    outcome: str
    refined_question: str  # 一句话总结的精炼问题
    clarifications: list[dict[str, str]]  # 反问历史 [{q, a}]


# ---- 模块 2:文献检索 ----
class Paper(TypedDict, total=False):
    """统一的论文表示,各检索源的结果都归一到这个结构。"""

    id: str  # 内部唯一 ID(常用 arxiv_id 或 hash)
    title: str
    abstract: str
    authors: list[str]
    year: int
    venue: str
    arxiv_id: str
    doi: str
    url: str
    source: str  # arxiv / semantic_scholar / openalex
    cited_by_count: int
    score: float  # RRF 融合分数,越大越相关
    raw: Any  # 原始 API 返回(调试用)


# ---- 模块 3:知识图谱三元组 ----
class Triple(TypedDict):
    head: str
    relation: str  # 限定为 [improves, uses, compares_with, cites, proposes, evaluates_on]
    tail: str
    source_paper_id: str  # 来自哪篇论文(可追溯)


# ---- 模块 4:批判卡片 ----
class CritiqueCard(TypedDict, total=False):
    """单个 Reviewer 的评审结果。"""

    reviewer: str  # novelty / methodology / statistics / reproducibility / devil / meta
    soundness: int  # 1-5
    contribution: int  # 1-5
    presentation: int  # 1-5
    strengths: list[str]
    weaknesses: list[str]
    questions: list[str]
    limitations: list[str]
    rating: int  # 1-10
    confidence: int  # 1-5
    rationale: str


# ---- 模块 5:实验方案 ----
class Experiment(TypedDict, total=False):
    name: str
    datasets: list[dict[str, Any]]
    baselines: list[str]
    metrics: list[str]
    expected_results: str
    ablations: list[str]
    statistical_test: dict[str, Any]


# ---- 模块 6:代码生成 ----
class CodeArtifact(TypedDict, total=False):
    files: dict[str, str]  # 文件名 -> 代码内容
    requirements: list[str]
    readme: str
    validation: dict[str, Any]  # 沙箱跑出的结果


# ---- 模块 7:论文初稿 ----
class PaperDraft(TypedDict, total=False):
    title: str
    abstract: str
    introduction: str
    related_work: str
    method: str
    experiments: str
    discussion: str
    conclusion: str
    references: list[Paper]
    style_guide: dict[str, Any]
    latex_path: str  # 生成 .tex 的路径


# ============================================================
# 主 State
# ============================================================


class ResearchState(TypedDict, total=False):
    """
    LangGraph 全局状态。

    可以把它理解成"整条科研流水线共享的一张总表":
      - 模块 1 往里写 pico
      - 模块 2 往里写 papers
      - 模块 4 往里写 critiques / meta_decision
      - 模块 7 最终把 paper_draft 填进去

    这样设计有两个直接好处:
      1. 数据流一眼可见。你看字段名就知道整条图会产出什么。
      2. 断点续跑更自然。因为某一步是否已完成,本质上就是对应字段是否已有值。
         例如 M2 里看到 state["papers"] 已非空,就能安全跳过重复检索。

    💡 关于 Annotated[T, reducer]
        LangGraph 看到字段的注解是 Annotated[list, operator.add] 时,
        会知道"多个节点同时返回这个字段时,把列表加起来,而不是后者覆盖前者"。
        没有 reducer 的字段(如 pico),后写的覆盖先写的。

    为什么 papers / critiques / triples 适合用 reducer?
      因为它们天然是"可追加集合":多路检索源、多个 reviewer、多个抽取任务
      最后都应该汇总到同一个列表。这里声明 reducer,等于把"合并策略"提前写进类型里,
      后面的节点函数就不用自己手动 extend/merge。

    为什么这里用 TypedDict 而不是 dataclass/pydantic?
      - TypedDict 运行时仍是普通 dict,最贴近 LangGraph 的状态传递方式
      - 节点只返回 patch dict,和 TypedDict 组合最自然
      - 教学上也更容易看清:State 不是带方法的对象,而是一份被图不断增量更新的数据
    """

    # ============================================================
    # 整理版字段并存策略(必读)
    # ============================================================
    #
    # 整理版 Phase A-D 引入了 7 个新字段,与老字段并存:
    #
    #   老字段(legacy)              新字段(整理版)            谁先消费
    #   ─────────────────────────   ───────────────────────  ─────────
    #   research_gaps: list[str]     gap_cards: list[GapCard]  M5(Phase B 起优先读 gap_cards)
    #   meta_decision: dict           decision_card: DecisionCard  M5.5/M8(Phase C 起读 decision_card)
    #   (无)                         topic_cards: list[TopicCard]  M0/user_select_topic
    #   (无)                         current_topic_id: str         user_select_topic 写,下游引用
    #   (无)                         current_gap_id: str           M3 写,M5 据此选 gap
    #   (无)                         evidence_access_status: list  M2.5 写,M3/M4/M5.5 据此降权
    #
    # 共存到 Phase D 完成后(已完成):
    #   - 业务模块全部切到读新字段(M5 优先 gap_cards / M4 输出 decision_card)
    #   - 老字段保留输出兼容历史代码(legacy m4 reviewer 仍写 meta_decision)
    #   - 后续大版本可考虑去掉 research_gaps,但目前没紧迫感
    #
    # ============================================================

    # ---- 输入 ----
    raw_question: str  # 用户最初输入的一句话

    # ---- 模块 0 产物(整理版 Phase B 引入) ----
    # M0 候选课题发现器输出。USE_M0_DISCOVERY=True 时主图 START 之后跑 m0_discover
    # 节点,LLM 直接基于 raw_question 生成 K 张 TopicCard 写到这里。
    #
    # 设计要点:
    # 1) 用普通 list 不加 reducer:M0 一次性写入,后续节点只读消费;
    #    加 reducer 反而让任何下游意外 return 同名字段都被累加,污染状态
    # 2) USE_M0_DISCOVERY=False 时该字段保持空 list,user_select_topic_node
    #    检查到空 list 直接跳过,主图退化为老 Phase A 的"raw_question→m1"路径
    topic_cards: list[TopicCard]
    current_topic_id: str  # user_select_topic 写入;M8 多分支时也是分支主键的索引

    # ---- 模块 1 产物 ----
    pico: PICO

    # ---- 模块 2 产物 ----
    # 用 reducer 是因为我们用 asyncio.gather 并行调多个检索源,
    # 它们返回的论文列表会被自动合并
    papers: Annotated[list[Paper], operator.add]
    rewritten_queries: list[str]

    # ---- 模块 2.5 产物(整理版 Phase C 引入) ----
    # 文献访问状态层:USE_M2_5_ACCESS_STATUS=True 时 M2 之后跑 m2_5_access 节点,
    # 启发式解析每篇论文的 fulltext/abstract_only/restricted/failed + has_code/dataset/benchmark,
    # 输出 EvidenceAccessStatus 列表,供 M3/M4/M5.5 据此降权或拦截。
    #
    # 设计要点:
    # 1) list 顺序与 state.papers 一致(用 paper_id 字段关联),消费方 zip 即可对齐
    # 2) 没启用时保持空 list,M3 build_gap_cards / M4 build_decision_card / M5.5
    #    decide_gate 看到空 list 自动跳过相关分级逻辑
    evidence_access_status: list[EvidenceAccessStatus]

    # ---- 模块 3 产物 ----
    triples: Annotated[list[Triple], operator.add]
    # legacy:M3 启发式识别出的研究空白节点列表(字符串)
    # 仍保留是因为 m4/m7 等历史模块仍可能消费;Phase B 起 M3 同时输出 gap_cards
    research_gaps: list[str]
    # 整理版 Phase B 引入:结构化 GapCard,M3 在 build_kg_node 里调 build_gap_cards 写入
    # M5 designer 优先读这个拿 datasets/baselines/metrics 当先验
    gap_cards: list[GapCard]
    # M3 写入(默认设为 gap_cards[0].gap_id);M5 据此选当前主 gap;M5.5 据此判断回退
    # M8 多分支模式下不同 fork 的 current_gap_id 可以不同(对应不同方向)
    current_gap_id: str

    # ---- 模块 4 产物 ----
    critiques: Annotated[list[CritiqueCard], operator.add]  # 多个 Reviewer 卡片
    # legacy:Meta-Reviewer 终裁字典(decision/final_rating/rationale 等)
    # Phase A/B 老路径下游(m7 写作 / appendix_reflect)仍读这个
    meta_decision: dict[str, Any]
    # 整理版 Phase C 引入:M4 输出的流程决策卡(action/target_node/branch_count 等)
    # M5.5 ResearchGate 优先读 recommended_action;M8 multi_branch 据此决定开几条分支
    # 失败兜底由 build_decision_card 内部处理,这里永远不会拿到 None
    decision_card: DecisionCard

    # ---- 附录 A:召回的历史经验(由 appendix_recall 写入,下游节点可选消费) ----
    #
    # 设计要点:
    #   1. 类型用普通 list[dict],不加 reducer。原因是召回是“一次性写入”动作:
    #      只有 appendix_recall 节点会写它,后续节点都是只读消费。
    #      如果给它加 operator.add,后面任何节点不小心 return 同名字段都会被
    #      累加,反而引入诡异的 bug。
    #   2. 不复用 metadata 字段,是因为 metadata 语义是“任意元信息”,所有节点
    #      都往里塞东西、互相覆盖很常见。把召回结果单独立顶层字段,可以让 m5
    #      这种消费方明确知道“我读的是 recalled_memories 而不是 metadata 的某个键”。
    #   3. 没召回到任何记忆时仍写入 [],这样下游不需要写 None 判断,统一走 list 逻辑。
    recalled_memories: list[dict]

    # ---- 模块 5 产物 ----
    experiment_plan: Experiment

    # ---- 模块 6 产物 ----
    code_artifact: CodeArtifact
    execution_mode: Literal["generate_only", "dry_run", "full_execute"]  # 用户选择的档位

    # ---- 模块 7 产物 ----
    paper_draft: PaperDraft

    # ---- 模块 8 / 全局元信息 ----
    fork_id: str  # 当前分叉 ID
    parent_fork_id: str  # 父分叉 ID(用于研究树)
    error_log: Annotated[list[str], operator.add]  # 各节点的失败日志
    metadata: dict[str, Any]  # 任意附加元数据


# ============================================================
# 工厂方法
# ============================================================


def make_initial_state(raw_question: str, **kwargs: Any) -> ResearchState:
    """
    创建一个干净的初始状态。

    所有列表/字典字段给空值,避免 None 在节点里触发 AttributeError。
    """
    state: ResearchState = {
        "raw_question": raw_question,
        # ◍ 整理版 Phase A-D 新字段:全部空容器初始化
        # 保证 USE_* feature flag 关闭时下游 .get() / .keys() / len() 永远拿到合法值,
        # 不需要写一堆 None 判断;启用 flag 后由对应业务节点(M0 / M2.5 / M3 / M4 / M5.5)填充
        "topic_cards": [],
        "current_topic_id": "",
        "evidence_access_status": [],
        "gap_cards": [],
        "current_gap_id": "",
        "decision_card": {},
        # ---- legacy ----
        "pico": {},
        "papers": [],
        "rewritten_queries": [],
        "triples": [],
        "research_gaps": [],
        "critiques": [],
        "meta_decision": {},
        "recalled_memories": [],
        "experiment_plan": {},
        "code_artifact": {},
        "execution_mode": "generate_only",
        "paper_draft": {},
        "fork_id": "",
        "parent_fork_id": "",
        "error_log": [],
        "metadata": {},
    }
    state.update(kwargs)  # type: ignore[typeddict-item]
    return state
