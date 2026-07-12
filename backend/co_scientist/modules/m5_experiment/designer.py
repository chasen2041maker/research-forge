"""
============================================================
 模块 5:实验方案生成(m5_experiment/designer.py)
============================================================

🎓 教学目标
    把"研究问题 → 可执行实验方案"建模为一次结构化生成 + 自检。
    教学要点:
      - 让 LLM 输出严格 schema(用 chat_json + JSON Schema 约束)
      - 加一个"自检 Agent",检查方案是否符合 ML reproducibility checklist
      - ★ 本模块是附录 A 两个附加能力的"消费方":
          1. PromptABTester:运行时挑当前得分最高的 system prompt 变体
          2. recalled_memories:把上一次研究沉淀的经验拼进 prompt

📌 真实做法可以扩到 ReAct(让 Agent 调工具查 HuggingFace Datasets / PWC SOTA)
    本教学版先做 LLM 直出版,把 ReAct 留作扩展练习。

💡 为什么附录 A 的两个能力都挂在 m5 而不是 m1/m4/m7
    - m5 产出的是高度结构化的实验方案(JSON schema),非常容易观察"历史经验是否
      真的改变了决策"。m1/m7 产出偏自由文本,改进不好量化。
    - 附录 A 原推荐就是"先挑一个结构化模块接入,其余模块照抄即可"。教学上先把
      闭环讲通,再让读者自己扩展到其他模块。

------------------------------------------------------------
"""

from __future__ import annotations

from co_scientist.llm import get_llm
from co_scientist.appendix.evolve import PromptABTester
from co_scientist.prompts.templates import SYSTEM_M5_EXPERIMENT
from co_scientist.state import Experiment, GapCard, ResearchState
from co_scientist.utils import logger

# 自检清单(可扩展)
CHECKLIST = [
    ("有数据集", lambda e: bool(e.get("datasets"))),
    ("至少 2 个基线", lambda e: len(e.get("baselines", [])) >= 2),
    ("有评测指标", lambda e: bool(e.get("metrics"))),
    ("有消融实验", lambda e: bool(e.get("ablations"))),
    ("有显著性检验", lambda e: bool(e.get("statistical_test"))),
]


def design_experiment(
    refined_question: str,
    pico: dict,
    recalled_memories: list[dict] | None = None,
    gap_card: GapCard | None = None,
) -> tuple[Experiment, dict]:
    """
    设计一份实验方案。整理版 Phase B 起接受 gap_card 参数作为先验。

    🎓 教学目标
        把 GapCard(M3 输出)的 datasets/baselines/metrics 注入到 M5 prompt,
        实现整理版 §8.1 "M5 = GapCard → ExperimentPlan" 证据继承思想。
        让 M5 不再凭空想象数据集,而是优先复用 M3 已经验证过的现成资源。

    📌 设计决策(三个关键取舍)
        1. GapCard 注入是"软提示"而非"硬约束":
           - prompt 里写"优先复用,缺失才自己补",LLM 仍能根据上下文判断
           - 硬约束(用 schema 强制)会让 LLM 失去灵活性,某些研究方向 GapCard
             给的 baseline 确实不对路,LLM 应该被允许换
        2. current_gap_id 命中失败的兜底:
           - 优先按 current_gap_id 选 gap;命中失败用 gap_cards[0]
           - 都没有(USE_M0/USE_M2_5/USE_M5_5 全关闭)→ gap_card=None,跳过 gap_block
           - 三层 fallback 让 M5 在任何 feature flag 组合下都能跑
        3. recalled_memories 与 gap_card 共存于 user prompt:
           - 历史经验是"软建议",GapCard 是"M3 当前结论"
           - 都是软提示,顺序上 GapCard 在前(更直接相关本次任务)
           - 历史经验在后(全局先验,可被本次任务的 GapCard 覆盖)

    ▍三步业务逻辑
        步骤 1:PromptABTester 决定 system prompt(L2 Prompt A/B,详见上方注释)
        步骤 2:把 recalled_memories 拼成 bullet block(L1 Reflexion)
        步骤 2.5:整理版 Phase B 新增——把 GapCard 拼成"先验 block"
        步骤 3:LLM chat_json 生成结构化 Experiment

    Returns:
        (Experiment, variant_meta) — variant_meta 给上层节点写到 metadata
    """
    """
    生成一份实验方案。

    本函数是附录 A 两个机制的"真正落地点",逻辑可以分成三步来读:

    ──── 步骤 1:挑 system prompt(PromptABTester) ────
        默认 system prompt 硬编码在 prompts/templates.py 的 SYSTEM_M5_EXPERIMENT。
        附录 A 的思路是:允许注册多个变体,按历史评分挑"当前最优"那一版。
          - 没注册过任何变体 → best_for() 返回 None → 继续用默认 prompt
          - 有变体、runs>0  → 用胜出的变体文本
        所以哪怕从未跑过 A/B,这里也是"零行为变化",纯后向兼容。

    ──── 步骤 2:拼历史经验(Reflexion 召回) ────
        state.recalled_memories 由图头部的 appendix_recall 节点写入。
        设计选择:
          - 只取前 5 条:词袋召回偶尔会混入弱相关的长文本,top-5 已足够;
          - 以 bullet + 类型标签的形式拼进 user message(不改 system),
            让 LLM 把它当作"参考经验"而不是"硬指令"。原文的 REFLECT_SYSTEM
            也要求记忆是可迁移策略,不是规则。
          - 空列表不拼任何段落:保持 prompt 干净,LLM 在零经验时也不受影响。

    ──── 步骤 3:走常规 chat_json + 转成 Experiment TypedDict ────
        和原来的实现完全一致,只是 system prompt / user 拼接的来源不同。

    返回值:
        (Experiment, variant_meta)
        variant_meta 用来让节点层把"这次用的是哪个变体"写进 state.metadata,
        方便后面追问:"本次到底用了哪版 prompt?" —— 可观测性很重要。
    """
    llm = get_llm("reasoner")  # 设计需要推理

    # 步骤 1:PromptABTester 决定 system prompt
    variant = PromptABTester().best_for("m5_experiment")
    system_prompt = variant.text if variant else SYSTEM_M5_EXPERIMENT

    # 步骤 2:把召回到的历史经验拼成可粘贴的 bullet list
    mem_block = ""
    if recalled_memories:
        lines = [
            f"- ({m.get('type', '?')}) {m.get('content', '')}"
            for m in recalled_memories[:5]
        ]
        mem_block = "# 历史经验提示\n" + "\n".join(lines) + "\n\n"

    # 步骤 2.5:整理版 Phase B 新增——把 GapCard 中的数据集/baseline/metric 作为先验注入
    # 整理版 §8.1 强调"M5 = GapCard → ExperimentPlan",不应凭空生成实验元素。
    # 这里只做提示而非硬约束:LLM 仍可补充更合适的选择,但默认从 GapCard 抄会更稳。
    gap_block = ""
    if gap_card:
        datasets = ", ".join(gap_card.get("datasets", []) or []) or "(待补)"
        baselines = ", ".join(gap_card.get("baselines", []) or []) or "(待补)"
        metrics = ", ".join(gap_card.get("metrics", []) or []) or "(待补)"
        missing = gap_card.get("missing_piece", "")
        gap_block = (
            "# GapCard 先验(优先复用,缺失才自己补)\n"
            f"- 标题: {gap_card.get('title', '')}\n"
            f"- 缺失拼图: {missing}\n"
            f"- 推荐数据集: {datasets}\n"
            f"- 推荐 baseline: {baselines}\n"
            f"- 推荐指标: {metrics}\n\n"
        )

    user = (
        f"研究问题: {refined_question}\n\n"
        f"PICO:\n{pico}\n\n"
        + gap_block
        + mem_block
        + "请设计一个完整的实验方案,确保包含数据集、基线、指标、消融、显著性检验。"
    )

    # 步骤 3:结构化生成
    result = llm.chat_json(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user},
        ],
        purpose="m5_design",
        temperature=0.5,
        max_tokens=2048,
    )
    exp = Experiment(
        name=result.get("name", ""),
        datasets=result.get("datasets", []) or [],
        baselines=result.get("baselines", []) or [],
        metrics=result.get("metrics", []) or [],
        expected_results=result.get("expected_results", ""),
        ablations=result.get("ablations", []) or [],
        statistical_test=result.get("statistical_test", {}) or {},
    )
    variant_meta = (
        {
            "pid": variant.pid,
            "name": variant.name,
            "avg_score": variant.avg_score,
            "runs": variant.runs,
        }
        if variant
        else {}
    )
    return exp, variant_meta


def self_check(exp: Experiment) -> list[str]:
    """
    简单的方案自检,返回缺失项列表。

    ▍为什么这里的 except 很窄,只捕 AttributeError / TypeError / KeyError
        CHECKLIST 里的 predicate 只做"取字段 + 判真假",合理的失败只可能是:
          - AttributeError: exp 不是预期 TypedDict(比如被 LLM 打成 None)
          - TypeError: 字段值不是可 bool 化的对象
          - KeyError: 字段缺失
        任何其它异常(比如 OSError / MemoryError)说明是系统性问题,
        不应该被当成"方案缺项"吞掉,让它向上冒出去便于排查。
        这是比 bare `except Exception` 更好的写法。
    """
    missing = []
    for name, fn in CHECKLIST:
        try:
            if not fn(exp):
                missing.append(name)
        except (AttributeError, TypeError, KeyError) as e:
            logger.debug("[M5] self_check 断言 {} 执行异常,计为缺失: {}", name, e)
            missing.append(name)
    return missing


def design_experiment_node(state: ResearchState) -> ResearchState:
    """
    LangGraph 节点:从 state 取输入 → 调 design_experiment → 回写 patch。

    本节点和其他 m* 节点相比,多两件事:
      1. 把 state.recalled_memories 传进 design_experiment,让历史经验参与生成;
      2. 把 prompt 变体信息写进 state.metadata.m5_prompt_variant,方便做可观测性:
         跑完一次流程后可以回头看"到底用了哪个 prompt 版本,它跑过多少次、平均分多少"。

    失败兜底:
      - self_check 缺失项 → 不抛异常,只在方案里打 _missing 标记(与旧行为一致)。
      - design_experiment 本身如果 LLM 调用失败 → safe_node 包一层会转成 error_log。
    """
    pico = state.get("pico", {})
    refined_q = pico.get("refined_question", state.get("raw_question", ""))
    if not refined_q:
        return {"error_log": ["[M5] 缺少研究问题"]}

    # ---- 整理版 Phase B:从 state 取当前 GapCard 作为实验设计先验 ----
    #
    # ◍ 三层 fallback 选 gap_card:
    #   1. 命中 current_gap_id(M3 写入的默认值或 M8 多分支不同 fork 的覆盖值)
    #   2. 命中失败但 gap_cards 非空 → 取第 0 张(M3 build_gap_cards 已按 novelty*feasibility 排序)
    #   3. gap_cards 空 → chosen_gap=None,design_experiment 跳过 gap_block 注入
    # ◍ 这种 fallback 设计让 M5 能在任意 USE_* 组合下平滑跑:
    #   - USE_M0/USE_M2_5 全关闭 → gap_cards=[],M5 退化到 Phase A 老行为
    #   - 启用部分 flag → M5 能拿到部分先验,渐进增强
    gap_cards = state.get("gap_cards", []) or []
    current_gap_id = state.get("current_gap_id", "")
    chosen_gap: GapCard | None = None
    if gap_cards:
        if current_gap_id:
            for gc in gap_cards:
                if gc.get("gap_id") == current_gap_id:
                    chosen_gap = gc
                    break
        if chosen_gap is None:
            chosen_gap = gap_cards[0]

    exp, variant_meta = design_experiment(
        refined_q,
        pico,
        recalled_memories=state.get("recalled_memories", []),
        gap_card=chosen_gap,
    )
    missing = self_check(exp)

    patch: dict = {}
    if missing:
        logger.warning("[M5] 方案缺失项: {}", missing)
        # 失败兜底原则:不阻塞流程,加 metadata 标记即可
        exp_with_warning = dict(exp)
        exp_with_warning["_missing"] = missing  # type: ignore[typeddict-unknown-key]
        patch["experiment_plan"] = exp_with_warning  # type: ignore[assignment]
    else:
        logger.info("[M5] ✅ 实验方案完成: {}", exp.get("name", ""))
        patch["experiment_plan"] = exp

    # 用了 A/B 变体才写 metadata,未命中变体时保持 state 干净。
    if variant_meta:
        patch["metadata"] = {"m5_prompt_variant": variant_meta}
    return patch  # type: ignore[return-value]
