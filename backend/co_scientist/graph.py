"""
============================================================
 LangGraph 主编排(graph.py)
============================================================

🎓 教学目标
    把 8 个模块作为节点串成一张状态图。这是整个项目的"大脑"。
    学会:
      - 如何用 StateGraph 添加节点和边
      - interrupt_before 怎样实现"暂停 → 等用户决策 → 继续"
      - SqliteSaver 怎样让流程可中断、可回放
      - 失败兜底:每个节点用 try/except + 写 error_log

📌 流程(含附录 A 的"学习闭环")
    START
      → appendix_recall        # 附录A前置:召回历史经验
      → m1_refine → m2_retrieve → m3_kg → m4_critique
      → [interrupt for execution_mode]
      → m5_experiment(消费 recalled_memories + PromptABTester 变体)
      → m6_generate → m6_execute → m7_writer
      → appendix_reflect       # 附录A后置:沉淀新经验
      → END

💡 为什么把 appendix 的 recall/reflect 做成"头尾两个节点"而不是塞进某个模块
    1. 位置决定语义:
         - recall 必须在 m1 之前跑,才能让所有下游节点都有机会看到历史经验;
         - reflect 必须在 m7 之后跑,这时 state 已经收集齐"问题/方案/终裁/错误日志",
           反思材料最完整。
    2. 放进模块内部(比如放到 m1 或 m7 里)会把"学习闭环"这个横切关注点藏进业务代码,
       未来想替换记忆方案(从 SQLite 词袋换成向量库)要改好几个模块。
    3. 作为独立节点,整条 DAG 的"先取历史经验 → 跑主流程 → 沉淀新经验"一目了然,
       也方便单独关掉(想跑一次不想写记忆库,把这两个节点的边旁路即可)。

------------------------------------------------------------
"""

from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable

from co_scientist.config import settings
from co_scientist.appendix.evolve import EvolvingMemory, PromptABTester
from co_scientist.modules.m0_topic_discovery import topic_discovery_node
from co_scientist.modules.m1_refiner import refine_question_node
from co_scientist.modules.m2_5_access_status import access_status_node
from co_scientist.modules.m2_retriever import retrieve_node
from co_scientist.modules.m3_kg import build_kg_node
from co_scientist.modules.m4_critique import critique_node
from co_scientist.modules.m5_5_research_gate import research_gate_node
from co_scientist.modules.m5_experiment import design_experiment_node
from co_scientist.modules.m6_code import code_executor_node, code_generator_node
from co_scientist.modules.m7_writer import write_paper_node
from co_scientist.state import ResearchState, make_initial_state
from co_scientist.utils import logger


ProgressCallback = Callable[[dict[str, Any]], None]
_progress_callback: ContextVar[ProgressCallback | None] = ContextVar(
    "co_scientist_progress_callback",
    default=None,
)


NODE_LABELS: dict[str, str] = {
    "appendix_recall": "Appendix A: 召回历史经验",
    "m0": "M0: 候选课题发现",
    "user_select_topic": "M0: 用户选择候选课题",
    "m1": "M1: 研究问题精炼",
    "m2": "M2: 多源文献检索",
    "m2.5": "M2.5: 文献访问状态",
    "m3": "M3: 知识图谱与 GapCard",
    "m4": "M4: Evidence-grounded Roundtable",
    "m5": "M5: 实验方案设计",
    "m5.5": "M5.5: ResearchGate 质量门禁",
    "m6a": "M6: 验证代码生成",
    "m6b": "M6: 验证执行",
    "m7": "M7: 论文/报告草稿",
    "appendix_reflect": "Appendix A: 反思沉淀",
}


def _emit_progress(name: str, status: str, **extra: Any) -> None:
    """Best-effort node progress event for API/WebSocket observers."""
    cb = _progress_callback.get()
    if cb is None:
        return
    payload = {
        "node": name,
        "label": NODE_LABELS.get(name, name),
        "status": status,
        **extra,
    }
    try:
        cb(payload)
    except Exception as e:
        logger.debug("[progress] callback failed: {}", e)


def safe_node(name: str, fn: Callable) -> Callable:
    """
    把任意节点函数包一层 try/except,失败时:
      - 写错误日志到 state.error_log
      - 不抛异常,让流程继续
    符合技术方案"失败兜底策略"原则。

    为什么这里要做一个统一包装器,而不是每个模块自己写 try/except?
      1. LangGraph 的每个节点本质上都是独立函数,如果每个节点各写一套异常处理,
         很快就会出现日志格式不一致、漏写错误上下文、某些节点直接把异常抛穿的情况。
      2. 把兜底逻辑集中在这里,相当于给整张图加了一层"统一失败语义":
         节点可以失败,但流程默认不断;失败信息会沉淀到 error_log 供最后汇总。
      3. 这也是 Agent 工程里常见的思路:把"业务逻辑"和"运行时保障"拆开。
         fn 只关心自己产出什么字段;safe_node 负责稳定性、可观测性、故障隔离。

    返回值为什么仍然是一个普通函数?
      因为 LangGraph 不关心你是不是装饰器,它只要求拿到一个
      `Callable[[ResearchState], dict]` 形状的节点函数。wrapped 满足这个接口,
      所以可以像原函数一样注册进 add_node。
    """

    def wrapped(state: ResearchState) -> dict:
        _emit_progress(name, "running")
        try:
            patch = fn(state) or {}
            _emit_progress(name, "done", output_keys=list(patch.keys()))
            return patch
        except Exception as e:
            logger.exception("[{}] 节点执行失败", name)
            _emit_progress(name, "error", error=f"{type(e).__name__}: {e}")
            return {"error_log": [f"[{name}] {type(e).__name__}: {e}"]}

    wrapped.__name__ = fn.__name__
    return wrapped


def user_select_topic_node(state: ResearchState) -> dict:
    """
    M0 后置节点:展示 topic_cards 让用户选 1 张,把 candidate_question 注入主流程。

    🎓 教学目标
        把"用户在多个候选中选一个"建模成 LangGraph 节点。用户选择必须来自
        前端/API 写入的 metadata.selected_topic_id;后端节点不读 stdin。

    📌 设计决策
        1. API/前端已显式选择 selected_topic_id 时,按 ID 命中 TopicCard。
        2. 未显式选择时自动选 score 最高,避免节点卡死。
        3. 通过覆盖 raw_question 而不是新加字段:让下游 M1 完全无感知,
           "切换 topic 后跑一次 M1" = "就当用户原本输入的就是这个 candidate_question"
        4. 已选过(current_topic_id 非空)时优先沿用该 ID,支持断点续跑

    ▍交互模式
        - 前端两阶段流程:先 /api/topics/discover,再把 selected_topic_id 传给 /api/research/start
        - 后端兜底:未传 selected_topic_id 时自动选 score 最高那张

    ▍输出
        - current_topic_id:被选中的 TopicCard.topic_id
        - raw_question:覆盖成所选 TopicCard.candidate_question(让 M1 直接精炼这个问题)

    ▍向后兼容
        topic_cards 为空(M0 未启用 / 失败)时本节点是空操作,保留原 raw_question。
    """
    cards = state.get("topic_cards", []) or []
    if not cards:
        return {}
    meta = state.get("metadata", {}) or {}
    selected_topic_id = state.get("current_topic_id") or meta.get("selected_topic_id", "")

    chosen = None
    if selected_topic_id:
        chosen = next((c for c in cards if c.get("topic_id") == selected_topic_id), None)
        if chosen is None:
            logger.warning("[M0] selected_topic_id={} 未命中候选卡片,自动选最高分", selected_topic_id)

    # 默认 fallback:score 最高。discover_topics 已排序;这里不再等待终端输入。
    if chosen is None:
        chosen = cards[0]

    cq = chosen.get("candidate_question") or chosen.get("title", "")
    logger.info("[M0] 选定 topic={} title='{}'",
                chosen.get("topic_id", ""), chosen.get("title", "")[:60])
    return {
        "current_topic_id": chosen.get("topic_id", ""),
        "raw_question": cq,  # 让 M1 围绕这个候选问题精炼
    }


def appendix_recall_node(state: ResearchState) -> dict:
    """
    附录 A 前置节点:主流程开始前,按原始问题召回历史经验。

    ▍它做什么
        1. 从 state 里取 raw_question 作为召回 query
        2. 走 EvolvingMemory.recall(词袋匹配,见 appendix/evolve/memory.py)
        3. 把命中的记忆列表写回 state["recalled_memories"]

    ▍为什么放在 START 之后、m1 之前
        - 放在更靠后会有尴尬问题:比如 m1 会改写问题,从 m1 之后再用 refined_question
          召回,新问题的措辞未必跟历史记忆库相同,反而更难命中。
        - 放在 START 后紧跟着 m1,既能用到最原始的用户措辞(召回召到的是过去同类问题),
          又能让后续所有节点都能从 state 里读到 recalled_memories。

    ▍为什么失败不抛异常
        - 本节点已经被 safe_node 包了一层 try/except(见 build_graph);
        - 即使 DB 坏了、记忆库不存在,recall 返回 []、节点返回空 patch,主流程继续。

    ▍本教学版的 recall 是词袋匹配,真实生产要换 embedding
        - 文档 09-进化与对抗/README.md 的 9.3 节给了 embedding 改造方案;
        - 这里先做能跑、能讲、零外部依赖的版本,让读者把注意力放在"闭环"上,
          而不是基础设施上。

    ▍这里故意不传 mem_type,做"全量召回"
        - 本节点位于流程起点(m1 之前),此时还不知道下游会进入哪个场景,
          召回所有类型是最安全的默认值;
        - 分层召回(如 mem_type="failure" / "strategy")留给各模块按需自取:
            - m4_critique 想看历史踩坑 → EvolvingMemory().recall(q, mem_type="failure")
            - m5_experiment 想借鉴有效套路 → recall(q, mem_type="strategy")
          接口已开通,是否接入由各模块自己决定。
    """
    query = state.get("raw_question", "")
    if not query:
        return {}
    memories = EvolvingMemory().recall(query)
    if memories:
        logger.info("[appendix] 召回 {} 条历史经验", len(memories))
    return {"recalled_memories": memories}


def appendix_reflect_node(state: ResearchState) -> dict:
    """
    附录 A 后置节点:主流程结束后,让 LLM 反思并把新经验沉淀到记忆库。

    ▍它做什么
        把"跑完一次研究"涉及的关键字段打包成一段自然语言摘要,交给 LLM:
          - 原始问题 / 精炼问题 / Meta 终裁 / 实验方案 / 错误日志
        LLM 按附录 A 的 REFLECT_SYSTEM 约定返回 {"memories": [...]},
        再由 EvolvingMemory.reflect_and_save 按类型入库。

    ▍为什么在这里才反思(而不是每个模块跑完就反思一次)
        - 只有到流程末尾,state 才同时持有"任务目标、最终产物、踩坑记录"三类信息。
          模块级反思会丢掉"最终结果是好是坏"这个最重要的信号。
        - 这也符合 Reflexion 论文(NeurIPS 2023)原意:反思必须基于"完整轨迹 + 结果",
          而不是单步。

    ▍摘要里 5 个字段够不够
        够用做 MVP。若将来想更精细(例如要对比 Round1/Round2 的 critique 变化),
        再扩 summary 即可 —— 反思的入口只有这一处,改这里不会扩散到业务模块。

    ▍为什么把计数写进 metadata
        metadata 是 state 里预留的"任意附加元数据"字段。把反思结果数写在这里,
        一方面不污染核心产物字段,另一方面上层 CLI/API 可以透出这个数给用户看
        ("本次沉淀了 N 条新经验")。
    """
    summary = (
        f"# 原始问题\n{state.get('raw_question', '')}\n\n"
        f"# 精炼问题\n{state.get('pico', {}).get('refined_question', '')}\n\n"
        f"# Meta 终裁\n{state.get('meta_decision', {})}\n\n"
        f"# 实验方案\n{state.get('experiment_plan', {})}\n\n"
        f"# 错误日志\n{state.get('error_log', [])}"
    )
    count = EvolvingMemory().reflect_and_save(summary)

    # ---- A/B 闭环:给本次用过的 prompt 变体回写分数 ----
    # 为什么在 reflect 节点里做而不是再开一个节点:
    #   A/B 回写和反思都是"整条流程跑完后的副作用",合成一个节点省一次 state 传递,
    #   读者也更容易看懂"学习闭环"就发生在流程末端这一小段里。
    ab_scored = {}
    meta = state.get("metadata", {}) or {}
    variant_info = meta.get("m5_prompt_variant") or {}
    pid = variant_info.get("pid")
    if pid:
        # 用 meta_decision.final_rating 作为这次跑法的质量分(1-10)
        rating = float((state.get("meta_decision") or {}).get("final_rating", 0.0))
        if rating > 0:
            try:
                PromptABTester().record_score(pid, rating)
                ab_scored = {"pid": pid, "score": rating}
                logger.info("[appendix] A/B 回写 pid={} score={}", pid, rating)
            except Exception as e:
                logger.warning("[appendix] A/B 回写失败: {}", e)

    # 两个副作用都汇总进 metadata,下游 CLI/API 可一把读到
    patch_meta: dict = {"evolving_memory_saved": count}
    if ab_scored:
        patch_meta["ab_scored"] = ab_scored
    return {"metadata": patch_meta}


def _try_setup_langsmith_once() -> None:
    """
    延迟初始化 LangSmith tracing。

    ▍为什么放在 build_graph 里而不是模块顶层
        - settings 是在第一次调用 get_settings() 时 lazy 加载的,太早 import 拿不到
        - 模块 import 时做 side effect 是 Python 工程的雷区(单元测试难 mock)
        - 放在 build_graph 入口能保证"真正要跑图时"才尝试启用,对 CLI / API 都透明
    """
    try:
        from co_scientist.utils.observability import setup_langsmith
        setup_langsmith()
    except Exception as e:
        # 观测性是"锦上添花",挂了绝对不能影响业务
        logger.warning("[observability] LangSmith 初始化失败,继续跑: {}", e)


def build_graph(
    *,
    interrupt_before_code: bool = True,
    use_postgres: bool = False,
):
    """
    构建并编译 LangGraph。

    Args:
        interrupt_before_code: 是否在代码生成前暂停,等用户选 execution_mode
        use_postgres: 是否用 Postgres Checkpointer(否则 SQLite)

    Returns:
        编译好的可调用图(graph.invoke / astream / stream)
    """
    from langgraph.graph import END, START, StateGraph

    # 延迟启用 LangSmith —— build_graph 是所有运行路径(CLI / API / 测试)的必经入口
    _try_setup_langsmith_once()

    g = StateGraph(ResearchState)

    # ---- 添加节点 ----
    # 附录 A 的 recall/reflect 和业务模块一样挂 safe_node:
    # 反思写入记忆库失败(SQLite 锁、LLM 超时)时不应该把整条研究流程搞崩。
    g.add_node("appendix_recall", safe_node("appendix_recall", appendix_recall_node))
    # 整理版 Phase B:M0 候选课题发现器 + 用户选择节点。仅在 USE_M0_DISCOVERY=True 时进入。
    g.add_node("m0_discover", safe_node("m0", topic_discovery_node))
    g.add_node("user_select_topic", safe_node("user_select_topic", user_select_topic_node))
    g.add_node("m1_refine", safe_node("m1", refine_question_node))
    g.add_node("m2_retrieve", safe_node("m2", retrieve_node))
    # 整理版 Phase C:M2.5 文献访问状态层(可选,USE_M2_5_ACCESS_STATUS=True 启用)
    g.add_node("m2_5_access", safe_node("m2.5", access_status_node))
    g.add_node("m3_kg", safe_node("m3", build_kg_node))
    g.add_node("m4_critique", safe_node("m4", critique_node))
    g.add_node("m5_experiment", safe_node("m5", design_experiment_node))
    # 整理版 Phase C:M5.5 ResearchGate(可选,USE_M5_5_GATE=True 启用)
    g.add_node("m5_5_gate", safe_node("m5.5", research_gate_node))
    g.add_node("m6_generate", safe_node("m6a", code_generator_node))
    g.add_node("m6_execute", safe_node("m6b", code_executor_node))
    g.add_node("m7_writer", safe_node("m7", write_paper_node))
    g.add_node("appendix_reflect", safe_node("appendix_reflect", appendix_reflect_node))

    # ---- 串边 ----
    # 整条 DAG 相当于"先取历史经验 → 跑主业务 → 最后沉淀新经验",
    # 外层的 recall/reflect 把整条主流程包成了一个"学习闭环"(Reflexion 思想)。
    #
    # 🎓 教学点:为什么整理版 Phase B/C 的新节点用 if/else 静态串边
    #            而不用 LangGraph 的 conditional_edges?
    #   1. conditional_edges 是"运行时根据 state 路由",每次跑都会判断;
    #      我们这里是"启动时根据 settings 决定整张图长什么样",静态决策 → 静态边
    #   2. 启动时 build_graph 一次,运行时 invoke 不变图,符合"feature flag 编译期固化"
    #      的工程思路;改 .env 改图,改 state 不改图
    #   3. 调试更友好:画出来的 DAG 形状是固定的,排查时拿 settings 一对就清楚
    #      conditional_edges 形成的是"动态形状",日志里看到的边可能不对应代码上的边
    #   4. 测试更简单:测 USE_M0_DISCOVERY=True 与 False 是"两张不同的图",直接断言
    #      而不需要构造特定 state 触发不同路由
    g.add_edge(START, "appendix_recall")

    # ---------- 整理版 Phase B:Direction Intake 条件路由 ----------
    # ◍ USE_M0_DISCOVERY=True  → appendix_recall → m0_discover → user_select_topic → m1
    # ◍ USE_M0_DISCOVERY=False → appendix_recall → m1(老行为,Phase A 之前的默认)
    if settings.USE_M0_DISCOVERY:
        g.add_edge("appendix_recall", "m0_discover")
        g.add_edge("m0_discover", "user_select_topic")
        g.add_edge("user_select_topic", "m1_refine")
    else:
        g.add_edge("appendix_recall", "m1_refine")

    g.add_edge("m1_refine", "m2_retrieve")

    # ---------- 整理版 Phase C:M2.5 文献访问状态层 ----------
    # ◍ M2.5 必须在 M3 之前跑,因为 M3 build_gap_cards 想消费 evidence_level
    # ◍ 关闭时直接 m2 → m3,M3 / M4 看到 evidence_access_status=[] 自动降级
    if settings.USE_M2_5_ACCESS_STATUS:
        g.add_edge("m2_retrieve", "m2_5_access")
        g.add_edge("m2_5_access", "m3_kg")
    else:
        g.add_edge("m2_retrieve", "m3_kg")

    g.add_edge("m3_kg", "m4_critique")
    g.add_edge("m4_critique", "m5_experiment")

    # ---------- 整理版 Phase C:M5.5 ResearchGate ----------
    # ◍ Phase C 阶段 M5.5 仅写 state.metadata.research_gate,不实际回边
    # ◍ 实际回边由 Phase D 的 multi_branch.runner 在外部调度(它读 final_state
    #   的 research_gate 字段,决定要不要 branch_from_gate_decision 派生新 fork)
    # ◍ 这样图本身保持简单线性,Git-like 语义全在 multi_branch 那一层
    if settings.USE_M5_5_GATE:
        g.add_edge("m5_experiment", "m5_5_gate")
        g.add_edge("m5_5_gate", "m6_generate")
    else:
        g.add_edge("m5_experiment", "m6_generate")
    g.add_edge("m6_generate", "m6_execute")
    g.add_edge("m6_execute", "m7_writer")
    g.add_edge("m7_writer", "appendix_reflect")
    g.add_edge("appendix_reflect", END)

    # ---- Checkpointer:决定流程是否可中断/回放 ----
    checkpointer = _make_checkpointer(use_postgres=use_postgres)

    # ---- interrupt:在代码执行档位选择前暂停 ----
    interrupt_nodes = ["m6_execute"] if interrupt_before_code else []

    return g.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_nodes,
    )


def _make_checkpointer(use_postgres: bool):
    """
    创建 Checkpointer。
    优先级:Postgres > SQLite,出错时降级到 MemorySaver(进程退出丢失)。
    """
    try:
        if use_postgres and settings.POSTGRES_URL:
            from langgraph.checkpoint.postgres import PostgresSaver

            cp = PostgresSaver.from_conn_string(settings.POSTGRES_URL)
            cp.setup()
            logger.info("使用 Postgres Checkpointer")
            return cp
    except Exception as e:
        logger.warning("Postgres Checkpointer 不可用,降级 SQLite: {}", e)

    try:
        from langgraph.checkpoint.sqlite import SqliteSaver

        db_path = Path(settings.CHECKPOINT_DIR) / "graph.sqlite"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # SqliteSaver 在新版本里需要 connection 而不是路径
        import sqlite3

        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        cp = SqliteSaver(conn)
        logger.info("使用 SQLite Checkpointer: {}", db_path)
        return cp
    except Exception as e:
        logger.warning("SQLite Checkpointer 不可用,降级 MemorySaver: {}", e)
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()


# ----------------------------------------------------
# 便捷接口:一次性跑完
# ----------------------------------------------------


def run_pipeline(
    raw_question: str,
    *,
    execution_mode: str = "generate_only",
    fork_id: str | None = None,
    config: dict | None = None,
    budget_usd: float | None = None,
    progress_callback: ProgressCallback | None = None,
    metadata: dict[str, Any] | None = None,
) -> ResearchState:
    """
    跑一次完整的研究流程(带 Budget Guard)。

    Args:
        raw_question: 用户输入的原始研究问题
        execution_mode: 模块 6 执行档位
        fork_id: 当前分叉 ID,默认用 raw_question 哈希
        budget_usd: 单次 run 成本上限(USD),None 时用 settings.RUN_BUDGET_USD

    Returns:
        最终的 ResearchState

    ▍为什么加 budget_guard 包一层
        Agent 长跑最大风险是"某个节点 bug 导致 LLM 反复调用,一次跑把账户打爆"。
        budget_guard 设一个硬性上限,超了直接抛 BudgetExceeded,
        安全节点(safe_node)捕获后写 error_log,流程优雅结束,钱包安全。
        对应 Devin / Cognition 等长跑 Agent 的标配设计。
    """
    import hashlib

    from co_scientist.utils.budget_guard import budget_guard

    fork_id = fork_id or hashlib.md5(raw_question.encode("utf-8")).hexdigest()[:12]

    # 用户没传 budget 就用 settings 默认值
    effective_budget = budget_usd if budget_usd is not None else settings.RUN_BUDGET_USD

    graph = build_graph()
    initial = make_initial_state(
        raw_question,
        execution_mode=execution_mode,
        fork_id=fork_id,
        metadata=metadata or {},
    )

    # LangGraph 用 thread_id 区分不同对话,我们用 fork_id 充当
    cfg = {"configurable": {"thread_id": fork_id}}
    if config:
        cfg.update(config)

    # 所有 LLM 调用在 budget_guard 上下文里累加,超限抛 BudgetExceeded
    token = _progress_callback.set(progress_callback)
    try:
        with budget_guard(effective_budget):
            final_state = graph.invoke(initial, config=cfg)
    finally:
        _progress_callback.reset(token)
    return final_state  # type: ignore[return-value]
