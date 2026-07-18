"""
============================================================
 模块 4:Orchestrator(m4_critique/orchestrator.py)
============================================================

🎓 教学目标
    从"静态多 Agent"升级到"**动态多 Agent**"。学会:
      - Orchestrator-Subagent 范式(Anthropic 2025.4《How we built our
        multi-agent research system》)
      - 为什么"让主 Agent 决定召谁"比"全员固定到场"更聪明
      - 失败降级:Orchestrator 崩了怎么办(→ 回退到全量 Reviewer)

📌 这个模块解决什么问题
    原来的 run_roundtable_async 永远调同一批 5 个 Reviewer。但现实里:
      - 纯理论问题调 Reproducibility Reviewer 纯属浪费 —— 没有"可复现性"可言
      - 小样本问题调 Statistics Reviewer 是关键 —— 样本不足就是硬伤
    让一个"聪明的 Orchestrator"看一眼问题性质,挑出当次真正需要的几个 Reviewer,
    **既省成本,又提升信号密度**(不相关 Reviewer 的"弱相关评审"只会稀释决策)。

📌 和 2023 年 AutoGen 的 GroupChat 到底区别在哪
    | 维度              | AutoGen GroupChat          | 本项目 Orchestrator-Subagent |
    |-------------------|----------------------------|-----------------------------|
    | 谁决定下一步       | Manager 每轮选发言人        | Orchestrator 开场一次性定 team|
    | 上下文共享         | 全员共享 history            | 每个子 Agent 独立上下文       |
    | 主 Agent 感受到的  | 全员的对话流                | 只有每个子 Agent 的最终结论   |
    | 适合                | 开放式讨论/自由辩论         | 结构化并行任务(评审/研究)   |

    本项目的评审场景更匹配后者 —— 追求"独立意见 + 快速收敛",不是"辩论"。

💡 为什么 devil 是 Orchestrator 必选项
    不设"必选"时,LLM 偶尔会选一组"全员温和"的 Reviewer(比如只挑 novelty 和
    reproducibility),结果每张卡都 7-8 分一派祥和 —— 完全失去批判圆桌的意义。
    把 devil 设为 hard-coded 必选,**强制让至少一个唱反调者存在**,是结构性保障。

💡 为什么允许 settings.M4_USE_ORCHESTRATOR 开关
    - 开(默认):新行为,动态选 3-5 个 Reviewer
    - 关:回落到老行为,永远跑全 5 个
    这让读者能对比两种模式的实际表现(跑同一问题看评审差异),也保证向后兼容
    ——原有的 eval 和单元测试在默认开关下继续通过。

------------------------------------------------------------
"""

from __future__ import annotations

from time import perf_counter
from typing import Any

from co_scientist.llm import get_llm
from co_scientist.modules.m4_critique.reviewers import (
    ALL_REVIEWERS,
    DEVIL_REVIEWER,
    REVIEWER_REGISTRY,
    ReviewerPersona,
)
from co_scientist.prompts.templates import (
    SYSTEM_M4_ORCHESTRATOR,
    USER_M4_ORCHESTRATE,
)
from co_scientist.observability import emit_agent_event
from co_scientist.utils import logger


# ------------------------------------------------------------
# 常量:Orchestrator 行为的硬性边界
# ------------------------------------------------------------
# 这些不是调用方参数(避免过度配置化),而是业务规则的"不变量":
#   - 最少 3 个:少于 3 个 Reviewer 圆桌就失去"多视角"意义
#   - 最多 5 个:全员都上就等于没选,Orchestrator 没起作用
#   - devil 必选:硬性保证批判性信号
_MIN_REVIEWERS = 3
_MAX_REVIEWERS = 5
_MANDATORY_NAME = DEVIL_REVIEWER.name  # "devil"


def _sanitize_selection(raw: list[str]) -> list[str]:
    """
    把 LLM 返回的名字列表清洗成合法的 Reviewer 名字列表。

    清洗规则(按顺序):
      1. 过滤掉不在 REVIEWER_REGISTRY 里的名字(LLM 偶发幻觉出 "ethics" 之类新角色)
      2. 去重(保留顺序,防止 LLM 一个名字写两次)
      3. 强制加入 devil(如果 LLM 漏了)
      4. 多于 _MAX_REVIEWERS 截断;少于 _MIN_REVIEWERS 用剩余 Reviewer 补齐

    ▍为什么要这么多道"清洗"
        Orchestrator 是 LLM,不是确定性程序,输出会漂移:
          - 偶尔拼错("Methodology" 大写)
          - 偶尔发明新角色("ethics")
          - 偶尔只选一两个(懒得选)
        清洗层把这些都吸收掉,让下游永远拿到合法、饱满的选择,符合 Agent 工程
        "边界层强兜底,业务层假设数据干净"的分层原则。
    """
    # Step 1: 只留合法名字(大小写归一 + 去前后空白)
    valid = [
        n.strip().lower()
        for n in raw
        if isinstance(n, str) and n.strip().lower() in REVIEWER_REGISTRY
    ]

    # Step 2: 去重保持顺序(dict.fromkeys 的经典用法)
    valid = list(dict.fromkeys(valid))

    # Step 3: 强制 devil
    if _MANDATORY_NAME not in valid:
        valid.insert(0, _MANDATORY_NAME)  # 放在首位,语义上突出

    # Step 4: 数量边界
    if len(valid) > _MAX_REVIEWERS:
        valid = valid[:_MAX_REVIEWERS]
    if len(valid) < _MIN_REVIEWERS:
        # 从全量 Reviewer 里按顺序补齐,跳过已选的
        for r in ALL_REVIEWERS:
            if r.name not in valid:
                valid.append(r.name)
                if len(valid) >= _MIN_REVIEWERS:
                    break

    return valid


def _fallback_all_reviewers() -> dict[str, Any]:
    """
    Orchestrator 完全失败时的兜底:返回全量 Reviewer。

    这是"最安全的退路" —— 全员评审可能浪费一点 token,但至少不会漏关键视角。
    宁可贵一点也不要因为 Orchestrator LLM 挂了让整条 m4 卡住。
    """
    return {
        "reviewers": [r.name for r in ALL_REVIEWERS],
        "reason": "Orchestrator 不可用,退回全量 Reviewer",
        "fallback": True,
    }


def select_reviewers(
    refined_question: str,
    method_summary: str,
) -> dict[str, Any]:
    """
    调 Orchestrator LLM 选 Reviewer。

    Args:
        refined_question: m1 精炼后的研究问题
        method_summary: 方法摘要(PICO 拼起来的那段)

    Returns:
        {
            "reviewers": ["devil", "novelty", ...],  # 清洗后的合法名字列表
            "reason": "Orchestrator 给出的选择理由",
            "fallback": bool,  # True 表示走了降级路径
        }

    ▍为什么用 chat 档而不是 reasoner
        选 Reviewer 是"轻量决策",不需要深推理 —— Claude-reasoner 级别的推理能力
        对这种"看关键词匹配规则"的任务是杀鸡用牛刀,贵且慢。
        用 chat 档,一次调用约 $0.0005,相比 Reviewer 自己的评审成本可忽略。

    ▍为什么结果也存 `reason` 字段
        调试 + 面试讲解都需要。日志里写一条 "Orchestrator: 选了 [devil, novelty,
        methodology],理由是'纯理论问题不需要可复现性'",比只写 "[devil, novelty,
        methodology]" 好理解十倍。

    ▍为什么所有异常都包成 fallback 而不抛
        调用方(roundtable.py)希望 select_reviewers 总能返回一个合法 list,
        不必 try/except。这是典型的"失败可见化但不中断"原则。
    """
    started = perf_counter()
    llm = get_llm("chat")  # 轻量决策用 chat 档即可

    def record(result: dict[str, Any]) -> dict[str, Any]:
        emit_agent_event(
            "orchestrator.selection",
            step_id="m4.orchestrator",
            agent_name="Reviewer Orchestrator",
            agent_role="orchestrator",
            model_role="chat",
            input_summary=(
                f"refined_question_chars={len(refined_question)}, "
                f"method_summary_chars={len(method_summary)}"
            ),
            output_summary=(
                f"reviewers={','.join(result['reviewers'])}; "
                f"reason={str(result['reason'])[:160]}"
            ),
            duration_ms=round((perf_counter() - started) * 1000),
            fallback=bool(result["fallback"]),
            parent_step_id="m4",
            outcome="DEGRADED" if result["fallback"] else "SUCCEEDED",
            details={"selected_reviewers": list(result["reviewers"])},
        )
        return result

    try:
        result = llm.chat_json(
            messages=[
                {"role": "system", "content": SYSTEM_M4_ORCHESTRATOR},
                {
                    "role": "user",
                    "content": USER_M4_ORCHESTRATE.format(
                        refined_question=refined_question,
                        method_summary=method_summary,
                    ),
                },
            ],
            purpose="m4_orchestrate",
            temperature=0.3,  # 决策要稳定,温度低
            max_tokens=512,
        )
    except Exception as e:
        logger.warning("[M4-orch] LLM 失败,走全量降级: {}", e)
        return record(_fallback_all_reviewers())

    raw_list = result.get("reviewers") or []
    if not isinstance(raw_list, list):
        logger.warning("[M4-orch] reviewers 字段格式错误({}),走全量降级", type(raw_list))
        return record(_fallback_all_reviewers())

    cleaned = _sanitize_selection(raw_list)
    reason = str(result.get("reason", "")).strip() or "(无理由)"

    logger.info(
        "[M4-orch] 选了 {} 位 Reviewer:{} | 理由:{}",
        len(cleaned),
        cleaned,
        reason[:80],
    )
    return record({
        "reviewers": cleaned,
        "reason": reason,
        "fallback": False,
    })


def resolve_personas(names: list[str]) -> list[ReviewerPersona]:
    """
    把名字列表翻译成 ReviewerPersona 对象列表。

    中间做一道"合法性检查"原因:调用方可能绕过 select_reviewers 直接传一份名字
    进来(比如测试场景),这里再兜一次能防止下游 review_proposal 拿到 None 崩溃。
    """
    personas: list[ReviewerPersona] = []
    for n in names:
        p = REVIEWER_REGISTRY.get(n)
        if p is None:
            logger.warning("[M4-orch] 忽略非法 Reviewer 名: {}", n)
            continue
        personas.append(p)
    return personas
