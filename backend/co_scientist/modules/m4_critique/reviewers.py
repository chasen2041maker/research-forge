"""
============================================================
 模块 4:Reviewer 抽象基类(m4_critique/reviewers.py)
============================================================

🎓 教学目标
    多 Agent 系统的核心思想:**同一个类,不同的人设**。
    所有 Reviewer 共享评审流程,只有 system prompt 不同。
    这让我们:
      - 加 Reviewer 只改一份数据(不改代码)
      - 每个 Reviewer 可以用不同模型(便宜的常规 Reviewer + Claude 终裁)

💡 为什么"用数据表示 Agent"而不是"用类继承"
    新手常见写法:
        class Reviewer(ABC): ...
        class NoveltyReviewer(Reviewer): ...
        class MethodologyReviewer(Reviewer): ...   # 5 个子类
    问题:
      - 每加一个 Reviewer 就要写一个类,代码越滚越多
      - system prompt 被藏在类里,想做 Prompt A/B 很难动
      - 5 个子类本质上只在 system prompt 不同,继承没带来任何价值
    本项目的写法:`ReviewerPersona` 数据类 + 常量实例,5 个 Reviewer 就是
    5 行声明。加 Reviewer(例如 EthicsReviewer)就是加一行,prompt 可被
    PromptABTester 动态替换也是小改一处。

💡 为什么每个 Reviewer 独立调 LLM,不共享上下文
    共享 history 会让后发言的 Reviewer 看到前面的评审,产生"从众效应"
    (心理学叫 anchoring bias:第一个给 8 分,其他人倾向往 8 分靠)。
    独立调用 → 并行 asyncio.gather → 再在 meta 阶段"汇总讨论",这是
    学术圆桌评审的经典设计,也是为什么 m4_critique 比 m1/m5 复杂得多。

💡 model_role 的分配逻辑
    - novelty / methodology / statistics / devil:都要推理 → reasoner
    - reproducibility:checklist 式判断,用便宜的 chat 足够 → 降成本
    - meta:最终终裁,直接决定整个研究方向 → critical(Claude Opus)
    这个分配平均下来每次评审成本约 $0.02(5 reviewers + 1 meta),
    比全上 Claude 的 $0.15 便宜一个数量级。

📌 设计决策
    - 用 dataclass + 实例常量表示人设,不用继承
    - 每次评审独立调用 LLM,不共享上下文

------------------------------------------------------------
"""

from __future__ import annotations

from dataclasses import dataclass

from co_scientist.llm import ModelRole, get_llm
from co_scientist.prompts.templates import (
    SYSTEM_M4_DEVIL,
    SYSTEM_M4_META,
    SYSTEM_M4_METHODOLOGY,
    SYSTEM_M4_NOVELTY,
    SYSTEM_M4_REPRODUCIBILITY,
    SYSTEM_M4_STATISTICS,
    USER_M4_REVIEW_PROPOSAL,
)
from co_scientist.state import CritiqueCard
from co_scientist.utils import logger


@dataclass
class ReviewerPersona:
    """
    一个 Reviewer 的定义。

    name: 人设标识
    system_prompt: 角色提示词
    model_role: 用哪种模型(chat/reasoner/critical)
    """

    name: str
    system_prompt: str
    model_role: ModelRole = "reasoner"  # 评审需要推理,默认 reasoner


# ---- 六个角色的实例 ----
# 分工(每个人设的 system prompt 细节见 prompts/templates.py):
#   novelty        → "有没有新东西?和已有工作对比亮点在哪?"
#   methodology    → "方法是否严谨?实验设计有无漏洞?"
#   statistics     → "统计检验是否充分?样本是否够?"
#   reproducibility→ "别人能不能照着跑出来?数据 / 代码 / 超参是否齐备"
#   devil          → 魔鬼代言人,专门挑其他 Reviewer 漏掉的潜在问题
#   meta           → 终裁,读所有卡决定 accept / reject / major_revision
NOVELTY_REVIEWER = ReviewerPersona("novelty", SYSTEM_M4_NOVELTY, "reasoner")
METHODOLOGY_REVIEWER = ReviewerPersona("methodology", SYSTEM_M4_METHODOLOGY, "reasoner")
STATISTICS_REVIEWER = ReviewerPersona("statistics", SYSTEM_M4_STATISTICS, "reasoner")
REPRODUCIBILITY_REVIEWER = ReviewerPersona(
    "reproducibility", SYSTEM_M4_REPRODUCIBILITY, "chat"
)
DEVIL_REVIEWER = ReviewerPersona("devil", SYSTEM_M4_DEVIL, "reasoner")
META_REVIEWER = ReviewerPersona("meta", SYSTEM_M4_META, "critical")  # ⭐ Claude Opus

# ALL_REVIEWERS 不包括 META。原因:
#   - 常规 Reviewer 并行跑 → 拿到 cards
#   - 判方差决定是否让 Devil 二次辩论
#   - 最后 META 单独读所有 cards 做终裁
# 如果 META 也混进并行评审,它会跟其他 Reviewer 地位一样,就失去"终裁"语义了。
ALL_REVIEWERS: list[ReviewerPersona] = [
    NOVELTY_REVIEWER,
    METHODOLOGY_REVIEWER,
    STATISTICS_REVIEWER,
    REPRODUCIBILITY_REVIEWER,
    DEVIL_REVIEWER,
]


# ------------------------------------------------------------
# REVIEWER_REGISTRY:按名字索引的 Reviewer 字典
# ------------------------------------------------------------
# 为什么要单独开一个 dict,不直接让调用方去 ALL_REVIEWERS 里 for..if:
#   Orchestrator(orchestrator.py)的返回值是字符串列表(LLM 天然友好的格式),
#   ["novelty", "statistics"] 这样。拿到后要快速查到对应 persona 对象。
#   每次 O(n) 线性找太啰嗦,直接 dict 查 O(1) 最清爽。
#
# 为什么不包含 meta:
#   meta 是终裁角色,不参与 Orchestrator 动态选派,永远固定出现。
#   把它排除在注册表外,防止 Orchestrator LLM 一不小心返回 "meta" 把终裁也当成
#   可并行评审的普通 Reviewer,语义被搅乱。
REVIEWER_REGISTRY: dict[str, ReviewerPersona] = {
    r.name: r for r in ALL_REVIEWERS
}


def review_proposal(
    persona: ReviewerPersona,
    refined_question: str,
    method_summary: str,
    experiment_brief: str = "",
    top_papers: str = "",
) -> CritiqueCard:
    """
    用指定人设评审一次研究方案。

    返回 CritiqueCard(结构化评审卡片)。

    ▍temperature=0.6 的选取
        评审不是格式转换,也不是自由创作,是"结构化打分 + 讨论"。
        0.6 让不同 Reviewer 在同样问题上能给出略有差异的视角
        (这正是圆桌的价值),但又不会离谱到评分完全随机。

    ▍失败兜底:为什么返回 rating=0 的空卡而不是抛异常
        如果这里抛异常,asyncio.gather 会让整条评审流水线挂。
        返回 rating=0 的占位卡,下游 roundtable.py 的 compute_variance
        会自动过滤 rating<=0 的卡,相当于"这位 Reviewer 弃权",整体逻辑
        仍然能正确走完。这是典型的"单个 Agent 失败不影响整体"的兜底。

    ▍为什么所有数字字段都 `int(... or 0)`
        LLM 偶发返回 None / "5" / 5.0 三种类型,统一转 int 前用 `or 0` 兜底,
        避免 `int(None)` 抛错。这是处理 LLM 结构化输出的常用技巧。
    """
    llm = get_llm(persona.model_role)

    user_msg = USER_M4_REVIEW_PROPOSAL.format(
        refined_question=refined_question,
        method_summary=method_summary,
        experiment_brief=experiment_brief or "(未提供)",
        top_papers=top_papers or "(未提供)",
    )

    try:
        result = llm.chat_json(
            messages=[
                {"role": "system", "content": persona.system_prompt},
                {"role": "user", "content": user_msg},
            ],
            purpose=f"m4_review_{persona.name}",
            temperature=0.6,  # 适度多样性
            max_tokens=2048,
        )
    except Exception as e:
        logger.error("[M4-{}] 评审失败: {}", persona.name, e)
        # 失败兜底:给一张空卡,保证下游不崩
        return CritiqueCard(
            reviewer=persona.name,
            rationale=f"评审失败: {e}",
            rating=0,
            confidence=0,
            strengths=[],
            weaknesses=[],
            questions=[],
            limitations=[],
        )

    # 映射到 CritiqueCard
    card = CritiqueCard(
        reviewer=persona.name,
        soundness=int(result.get("soundness", 0) or 0),
        contribution=int(result.get("contribution", 0) or 0),
        presentation=int(result.get("presentation", 0) or 0),
        strengths=list(result.get("strengths", []) or []),
        weaknesses=list(result.get("weaknesses", []) or []),
        questions=list(result.get("questions", []) or []),
        limitations=list(result.get("limitations", []) or []),
        rating=int(result.get("rating", 0) or 0),
        confidence=int(result.get("confidence", 0) or 0),
        rationale=str(result.get("rationale", "")),
    )
    logger.info(
        "[M4-{}] ✅ rating={} conf={}",
        persona.name,
        card["rating"],
        card["confidence"],
    )
    return card
