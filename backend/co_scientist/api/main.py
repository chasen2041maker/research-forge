"""
============================================================
 FastAPI 主入口(api/main.py)
============================================================

🎓 教学目标
    把 LangGraph pipeline 暴露为 HTTP + WebSocket 接口。
    前端可以:
      - POST /api/research/start  启动一次研究
      - GET  /api/research/{fork_id}/status  查状态
      - WS   /ws/research/{fork_id}  订阅流式输出
      - POST /api/forks/create      创建分叉

📌 重要
    真正的长任务用 Celery 跑,接口立即返回 fork_id。
    教学版为了简单,用 FastAPI BackgroundTasks。

------------------------------------------------------------
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from co_scientist.config import settings
from co_scientist.graph import run_pipeline
from co_scientist.modules.m1_refiner.refiner import check_specificity
from co_scientist.modules.m0_topic_discovery import discover_topics
from co_scientist.modules.m8_replay import ForkManager
from co_scientist.public_api import ExplorationSnapshot, export_proposal
from co_scientist.utils import get_tracker, logger, setup_logger

# 程序启动时初始化日志
setup_logger()

app = FastAPI(title="Research Studio API", version="0.1.0")

# 开发期允许所有来源,生产应限制
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

fork_manager = ForkManager()

# 内存中保存每个 fork 的运行状态(生产用 Redis)
_runs: dict[str, dict[str, Any]] = {}

PROGRESS_NODE_ORDER = [
    ("appendix_recall", "Appendix A: 召回历史经验"),
    ("m0", "M0: 候选课题发现"),
    ("user_select_topic", "M0: 用户选择候选课题"),
    ("m1", "M1: 研究问题精炼"),
    ("m2", "M2: 多源文献检索"),
    ("m2.5", "M2.5: 文献访问状态"),
    ("m3", "M3: 知识图谱与 GapCard"),
    ("m4", "M4: Evidence-grounded Roundtable"),
    ("m5", "M5: 实验方案设计"),
    ("m5.5", "M5.5: ResearchGate 质量门禁"),
    ("m6a", "M6: 验证代码生成"),
    ("m6b", "M6: 验证执行"),
    ("m7", "M7: 论文/报告草稿"),
    ("appendix_reflect", "Appendix A: 反思沉淀"),
]


def _initial_progress(*, skip_m0: bool = False) -> dict[str, Any]:
    nodes = []
    for node_id, label in PROGRESS_NODE_ORDER:
        status = "skipped" if skip_m0 and node_id in {"m0", "user_select_topic"} else "pending"
        nodes.append({"id": node_id, "label": label, "status": status})
    return {"nodes": nodes, "current_node": "", "events": []}


def _make_progress_recorder(fork_id: str):
    def _record(event: dict[str, Any]) -> None:
        run = _runs.setdefault(fork_id, {})
        progress = run.setdefault("progress", _initial_progress())
        node = event.get("node", "")
        status = event.get("status", "")
        for item in progress["nodes"]:
            if item["id"] == node:
                item["status"] = status
                if status == "running":
                    progress["current_node"] = node
                elif progress.get("current_node") == node:
                    progress["current_node"] = ""
                if event.get("error"):
                    item["error"] = event["error"]
                break
        progress["events"].append(event)
        progress["events"] = progress["events"][-80:]

    return _record


class StartRequest(BaseModel):
    question: str
    execution_mode: str = "generate_only"
    parent_fork_id: str | None = None
    skip_m0: bool = True
    selected_topic_id: str | None = None
    clarifications: list[dict[str, str]] = Field(default_factory=list)


class ForkRequest(BaseModel):
    parent_fork_id: str
    branch_node: str
    description: str = ""


class TopicDiscoverRequest(BaseModel):
    question: str
    k: int = 3
    constraints: str = ""
    seed_evidence: str = ""


class M1ClarifyRequest(BaseModel):
    question: str
    clarifications: list[dict[str, str]] = Field(default_factory=list)
    max_turns: int = 3


@app.get("/")
def root() -> dict:
    return {"service": "Research Studio", "legacy_package": "co_scientist", "version": "0.1.0"}


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# ------------------------------------------------------------
# 成本 / 可观测性接口
# ------------------------------------------------------------
# 🎓 教学目标
#   Agent 系统最容易失控的两件事:① 账单爆炸 ② 黑盒调用分布不明。
#   cost_tracker.db 里已经按 purpose(如 m4_review_red_team)打了 tag,
#   这里把它暴露成 HTTP 接口,前端/CI 就能拉到实时数据,而不是 ssh 上机 sqlite3。
#
# 📌 设计决策
#   1. 三个端点分层:
#        /api/cost        — 一句话摘要(本月总花费)
#        /api/cost/by-purpose — 按 purpose 聚合,定位调用热点
#        /api/metrics     — Prometheus 文本格式,方便接 Grafana
#   2. SQL 直连 cost_tracker.db 做聚合,不走 ORM —— 聚合类查询走 ORM
#      反而要写一堆 GROUP BY 的拼接,不如直接 SQL 清楚。
#   3. 所有接口都是只读的,加 try/except 防止 db 还没建时 500
# ------------------------------------------------------------


@app.get("/api/cost")
def cost_summary() -> dict:
    """本月累计花费 + 预算使用率,给前端 header 用。"""
    tracker = get_tracker()
    spent = tracker.month_total_usd()
    budget = settings.MONTHLY_BUDGET_USD
    return {
        "month_spent_usd": round(spent, 4),
        "monthly_budget_usd": budget,
        "usage_ratio": round(spent / budget, 4) if budget > 0 else 0.0,
    }


@app.get("/api/cost/by-purpose")
def cost_by_purpose(limit: int = 50) -> dict:
    """
    按 purpose(模块步骤名)聚合的调用分布。
    前端可以画成堆叠柱状图,一眼看出钱花在哪个环节。
    """
    import sqlite3

    db_path = settings.DATA_DIR / "cost_tracker.db"
    if not db_path.exists():
        # 还没跑过任何 LLM 调用,返回空列表而不是 500
        return {"rows": [], "note": "cost_tracker.db 尚未生成,先跑一次 run"}

    # 注意:limit 是 int,FastAPI 已经做了类型校验,拼进 SQL 安全
    #      purpose / model 字段只在 WHERE 里做等值匹配时才有注入风险,这里只 SELECT
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT purpose,
                   model,
                   COUNT(*)                    AS calls,
                   SUM(input_tokens)           AS in_tok,
                   SUM(output_tokens)          AS out_tok,
                   SUM(cache_hit_tokens)       AS cache_hit_tok,
                   ROUND(SUM(cost_usd), 6)     AS usd,
                   ROUND(AVG(latency_s), 3)    AS avg_latency_s
            FROM llm_calls
            GROUP BY purpose, model
            ORDER BY usd DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    # 手动映射成 dict,比用 row_factory 更明确
    return {
        "rows": [
            {
                "purpose": r[0],
                "model": r[1],
                "calls": r[2],
                "input_tokens": r[3],
                "output_tokens": r[4],
                "cache_hit_tokens": r[5],
                "cache_hit_rate": round((r[5] or 0) / r[3], 4) if r[3] else 0.0,
                "cost_usd": r[6],
                "avg_latency_s": r[7],
            }
            for r in rows
        ]
    }


@app.get("/api/metrics")
def metrics() -> Any:
    """
    Prometheus 文本格式 metrics。
    重点给 oncall 用:采集后可以接 Grafana 画 QPS/成本/延迟。

    为什么不用 prometheus_client 库?
      本项目就这么几个指标,手写 4 行文本比拉依赖更轻;
      真正需要 histogram/quantile 时再升级。
    """
    from fastapi.responses import PlainTextResponse

    tracker = get_tracker()
    spent = tracker.month_total_usd()
    budget = settings.MONTHLY_BUDGET_USD

    # Prometheus 文本格式:# HELP 说明 + # TYPE 类型 + 指标名 + 值
    lines = [
        "# HELP co_scientist_month_spent_usd 当月 LLM 累计花费(美元)",
        "# TYPE co_scientist_month_spent_usd gauge",
        f"co_scientist_month_spent_usd {spent:.6f}",
        "# HELP co_scientist_monthly_budget_usd 月预算上限(美元)",
        "# TYPE co_scientist_monthly_budget_usd gauge",
        f"co_scientist_monthly_budget_usd {budget:.6f}",
        "# HELP co_scientist_budget_ratio 本月预算使用率(0-1+)",
        "# TYPE co_scientist_budget_ratio gauge",
        f"co_scientist_budget_ratio {(spent / budget) if budget > 0 else 0:.6f}",
        "# HELP co_scientist_runs_active 进程内正在跑的 fork 数",
        "# TYPE co_scientist_runs_active gauge",
        f"co_scientist_runs_active {sum(1 for r in _runs.values() if r.get('status') == 'running')}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")


# ------------------------------------------------------------
# 生成物接口
# ------------------------------------------------------------


def _output_root() -> Path:
    return Path(settings.OUTPUT_DIR).resolve()


def _artifact_relpath(path: Path) -> str:
    return path.resolve().relative_to(_output_root()).as_posix()


def _artifact_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".graphml":
        return "knowledge_graph"
    if suffix in {".py", ".ipynb"}:
        return "code"
    if suffix in {".tex", ".bib"}:
        return "paper"
    if path.name.lower() in {"readme.md", "requirements.txt"}:
        return "code"
    return "file"


def _artifact_record(path: Path) -> dict[str, Any] | None:
    try:
        resolved = path.resolve()
        resolved.relative_to(_output_root())
    except ValueError:
        return None
    if not resolved.is_file():
        return None
    stat = resolved.stat()
    return {
        "path": _artifact_relpath(resolved),
        "name": resolved.name,
        "kind": _artifact_kind(resolved),
        "size": stat.st_size,
        "updated_at": stat.st_mtime,
    }


def _collect_artifacts(state: dict) -> list[dict[str, Any]]:
    """从 final state 收集前端可展示的生成物。"""
    seen: set[str] = set()
    artifacts: list[dict[str, Any]] = []

    def add_path(path_value: Any) -> None:
        if not path_value:
            return
        path = Path(str(path_value))
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                add_path(child)
            return
        record = _artifact_record(path)
        if record and record["path"] not in seen:
            seen.add(record["path"])
            artifacts.append(record)

    # M3:知识图谱是全局输出文件。
    add_path(_output_root() / "knowledge_graph.graphml")

    # M6:代码目录。
    code_artifact = state.get("code_artifact", {}) or {}
    add_path((code_artifact.get("validation") or {}).get("saved_to", ""))

    # M7:论文 tex + refs.bib。
    paper_draft = state.get("paper_draft", {}) or {}
    latex_path = paper_draft.get("latex_path")
    add_path(latex_path)
    if latex_path:
        add_path(Path(str(latex_path)).with_name("refs.bib"))

    return artifacts


@app.get("/api/artifacts/content")
def artifact_content(path: str = Query(..., min_length=1)) -> dict[str, Any]:
    """
    读取 OUTPUT_DIR 内的生成物文本内容。

    只允许读 settings.OUTPUT_DIR 之下的文件;超过 1MB 时截断预览。
    """
    root = _output_root()
    requested = (root / path).resolve()
    try:
        requested.relative_to(root)
    except ValueError:
        raise HTTPException(400, "非法文件路径")
    if not requested.is_file():
        raise HTTPException(404, "生成物不存在")

    max_bytes = 1_000_000
    raw = requested.read_bytes()
    truncated = len(raw) > max_bytes
    content = raw[:max_bytes].decode("utf-8", errors="replace")
    return {
        "path": _artifact_relpath(requested),
        "name": requested.name,
        "kind": _artifact_kind(requested),
        "size": requested.stat().st_size,
        "truncated": truncated,
        "content": content,
    }


@app.post("/api/topics/discover")
def discover_topic_cards(req: TopicDiscoverRequest) -> dict:
    """
    两阶段前端流程的第一步:只运行 M0,返回 TopicCard 列表给用户选择。

    这避免了旧实现里 M0 自动选最高分并继续跑的问题。前端拿到 cards 后,
    用户可以单选启动一条主线,也可以多选走 /api/branches/run 创建多分支。
    """
    if not req.question.strip():
        raise HTTPException(400, "question 不能为空")
    k = max(1, min(req.k, 8))
    cards = discover_topics(
        req.question,
        k=k,
        constraints=req.constraints,
        seed_evidence=req.seed_evidence,
    )
    return {"topic_cards": cards, "count": len(cards)}


def _m1_question_with_clarifications(
    question: str,
    clarifications: list[dict[str, str]],
) -> str:
    clean: list[dict[str, str]] = []
    for item in clarifications:
        answer = str(item.get("a") or item.get("answer") or "").strip()
        if not answer:
            continue
        prompt = str(item.get("q") or item.get("question") or "前端回答").strip()
        clean.append({"q": prompt or "前端回答", "a": answer})
    if not clean:
        return question
    return f"{question}\n\n补充信息:\n" + "\n".join(
        f"Q: {c['q']}\nA: {c['a']}" for c in clean
    )


@app.post("/api/m1/clarify")
def m1_clarify(req: M1ClarifyRequest) -> dict:
    """
    前端驱动的 M1 递进澄清入口。

    这个接口只做"当前问题是否足够具体"判断,不启动完整 pipeline。
    前端根据 follow_up 收集回答,最多 3 轮后再调用 /api/research/start。
    """
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "question 不能为空")

    max_turns = max(1, min(req.max_turns, 3))
    clarifications: list[dict[str, str]] = []
    for item in req.clarifications:
        answer = str(item.get("a") or item.get("answer") or "").strip()
        if not answer:
            continue
        prompt = str(item.get("q") or item.get("question") or "前端回答").strip()
        clarifications.append({"q": prompt or "前端回答", "a": answer})
        if len(clarifications) >= max_turns:
            break

    if len(clarifications) >= max_turns:
        return {
            "ready": True,
            "specific": False,
            "follow_up": "",
            "turn": len(clarifications),
            "max_turns": max_turns,
            "clarifications": clarifications,
        }

    current_q = _m1_question_with_clarifications(question, clarifications)
    is_specific, follow_up = check_specificity(current_q)
    return {
        "ready": bool(is_specific),
        "specific": bool(is_specific),
        "follow_up": "" if is_specific else follow_up,
        "turn": len(clarifications) + (0 if is_specific else 1),
        "max_turns": max_turns,
        "clarifications": clarifications,
    }


@app.post("/api/research/start")
def start_research(req: StartRequest, background: BackgroundTasks) -> dict:
    """
    启动一次研究。立即返回 fork_id,真实跑在后台。
    """
    meta = fork_manager.create_fork(
        parent_fork_id=req.parent_fork_id or "",
        branch_node="m1_refine" if req.skip_m0 else "root",
        description=req.question[:80],
        topic_id=req.selected_topic_id or "",
    )
    _runs[meta.fork_id] = {
        "status": "running",
        "state": None,
        "progress": _initial_progress(skip_m0=req.skip_m0),
    }

    def _run() -> None:
        try:
            metadata = {
                "skip_m0": req.skip_m0,
                "selected_topic_id": req.selected_topic_id or "",
            }
            if req.clarifications:
                metadata["m1_clarifications"] = req.clarifications

            final = run_pipeline(
                raw_question=req.question,
                execution_mode=req.execution_mode,
                fork_id=meta.fork_id,
                metadata=metadata,
                progress_callback=_make_progress_recorder(meta.fork_id),
            )
            _runs[meta.fork_id].update({"status": "done", "state": final})
            # 整理版 Phase C 起 M4 同时输出 decision_card,final_rating 优先取它,
            # 失败/Phase A-B 老路径回落 meta_decision。
            rating = float(
                (final.get("decision_card") or {}).get("final_rating", 0.0)
                or (final.get("meta_decision") or {}).get("final_rating", 0.0)
                or 0.0
            )
            fork_manager.update_status(meta.fork_id, "done", final_rating=rating)
        except Exception as e:
            logger.exception("run 失败")
            _runs[meta.fork_id].update({"status": "error", "error": str(e)})
            fork_manager.update_status(meta.fork_id, "abandoned")

    background.add_task(_run)
    return {"fork_id": meta.fork_id, "status": "running"}


@app.get("/api/research/{fork_id}/status")
def research_status(fork_id: str) -> dict:
    run = _runs.get(fork_id)
    if not run:
        raise HTTPException(404, "fork_id 不存在")
    return run


@app.get("/api/research/{fork_id}/proposal")
def research_proposal(fork_id: str) -> dict[str, object]:
    """Export a finished Studio run as an explicitly unverified, portable Proposal."""
    run = _runs.get(fork_id)
    if not run:
        raise HTTPException(404, "fork_id 不存在")
    if run.get("status") != "done" or not isinstance(run.get("state"), dict):
        raise HTTPException(409, "研究尚未完成，暂时不能导出 Proposal")
    snapshot = ExplorationSnapshot.create(run_id=fork_id, state=run["state"])
    return export_proposal(snapshot).to_mapping()


@app.post("/api/forks/create")
def create_fork(req: ForkRequest) -> dict:
    meta = fork_manager.create_fork(
        parent_fork_id=req.parent_fork_id,
        branch_node=req.branch_node,
        description=req.description,
    )
    return meta.__dict__


@app.get("/api/forks/tree")
def fork_tree() -> dict:
    return {"tree": fork_manager.build_tree(), "forks": fork_manager.list_forks()}


@app.get("/api/forks/{fork_id}")
def fork_detail(fork_id: str) -> dict:
    """
    单条 fork 的元数据 + 内存中的 final state(若已跑完)。

    🎓 教学目标
        前端 ForkTreeView 的"侧栏详情"功能依赖这个端点:用户在树视图点
        眼睛图标 → 这里返回 meta + snapshot → 前端折叠展开看完整数据。

    📌 设计决策
        1. meta 走 forks.db(持久,跨进程可见)
        2. snapshot 走 _runs(本进程内存,重启丢失)
        3. 这是"两套存储"的取舍:fork 元数据要做长期审计/前端列表分页,必须
           落库;final_state 几兆大,序列化进库代价高,且重启就重新跑了
        4. 生产应该把 final_state 也存 Redis/Postgres,这里教学版用 dict 简化

    ▍为什么把 in-memory _runs 也暴露
        前端要能从 fork_tree 跳到 fork 详情页直接看到 snapshot,而 _runs
        是 start_research 跑完之后唯一持有 final_state 的地方。
    """
    meta = fork_manager.get_meta(fork_id)
    if not meta:
        raise HTTPException(404, "fork_id 不存在")
    run = _runs.get(fork_id) or {}
    state = run.get("state") or {}
    return {
        "meta": meta.__dict__,
        "status": run.get("status", meta.status),
        "progress": run.get("progress"),
        "snapshot": _build_snapshot(state) if state else None,
    }


# ------------------------------------------------------------
# 整理版 Phase E:多分支 fork API
# ------------------------------------------------------------


class BranchRunRequest(BaseModel):
    """启动多分支研究:把 K 张 TopicCard 各跑一条 fork。"""

    raw_question: str
    topic_cards: list[dict]
    parent_fork_id: str = ""
    execution_mode: str = "generate_only"
    clarifications: list[dict[str, str]] = Field(default_factory=list)


class BranchMergeRequest(BaseModel):
    fork_ids: list[str]
    use_llm_compare: bool = False


@app.post("/api/branches/run")
def branches_run(req: BranchRunRequest, background: BackgroundTasks) -> dict:
    """
    整理版 §9.2 多分支跑图入口。立即返回所有 fork_id,后台串行跑 K 条 fork。

    🎓 教学目标
        把"批量启动 K 条独立 fork"这件事暴露成 HTTP 接口。前端 / 调度器 /
        测试都能调,职责单一(它不负责 compare 或 merge)。

    📌 设计决策
        1. BackgroundTasks 启异步:K 条 fork 串行可能跑几十分钟,HTTP 同步
           等会被 nginx/lb 超时掐;立即返回 fork_ids 让客户端用 WS / 轮询
           /api/forks/{id} 查进度
        2. 串行不并行:LangGraph SqliteSaver 多线程并发会有锁竞争;教学版
           先稳后快,后续可改 ProcessPool
        3. 在 HTTP 层立刻 create_fork(同步) → 返回 fork_ids;后台才跑图
           前端拿到 fork_ids 后能立刻在 ForkTreeView 看到"running"行
        4. 内部用 thin wrapper _wrapped_pipeline 同步把 final_state 灌回 _runs
           对齐 start_research 的 _runs 协议(WS 端点和 fork_detail 都依赖)

    ▍为什么 _wrapped_pipeline 写成内嵌闭包而不是模块函数
        - 闭包能直接引用 _runs / run_pipeline 这俩模块级变量,无需参数传递
        - 调用点只有这一个,提到模块顶层反而增加心智成本
        - 测试不会 patch 这个内部函数,模块级桩 run_topic_branches 即可

    跑完结果可通过 GET /api/forks/{fork_id} 查询单条详情。
    """
    if not req.topic_cards:
        raise HTTPException(400, "topic_cards 不能为空")

    metas = fork_manager.branch_from_topic_cards(
        req.topic_cards,
        parent_fork_id=req.parent_fork_id,
    )
    for m in metas:
        _runs[m.fork_id] = {
            "status": "running",
            "state": None,
            "progress": _initial_progress(skip_m0=True),
        }

    def _run_branches() -> None:
        # 延迟 import 避免循环依赖
        from co_scientist.modules.m8_replay import run_topic_branches

        # 构造一个 thin wrapper:跑完单条 fork 同步把 state 塞回 _runs
        def _wrapped_pipeline(question: str, **kwargs: Any) -> Any:
            fid = kwargs.get("fork_id")
            metadata = dict(kwargs.pop("metadata", {}) or {})
            metadata["skip_m0"] = True
            if req.clarifications:
                metadata["m1_clarifications"] = req.clarifications
            try:
                final = run_pipeline(
                    question,
                    **kwargs,
                    metadata=metadata,
                    progress_callback=_make_progress_recorder(fid) if fid else None,
                )
                if fid:
                    _runs[fid].update({"status": "done", "state": final})
                return final
            except Exception as e:
                if fid:
                    _runs[fid].update({"status": "error", "error": str(e)})
                raise

        try:
            run_topic_branches(
                req.raw_question,
                req.topic_cards,
                parent_fork_id=req.parent_fork_id,
                execution_mode=req.execution_mode,
                fork_manager=fork_manager,
                run_pipeline=_wrapped_pipeline,
            )
        except Exception as e:
            logger.exception("[api] branches_run 失败: {}", e)

    background.add_task(_run_branches)
    return {"fork_ids": [m.fork_id for m in metas]}


@app.get("/api/branches/compare")
def branches_compare(fork_ids: str) -> dict:
    """
    对比一组 fork 的 final state 摘要。

    🎓 教学目标
        前端 ForkTreeView 选中多条 fork 后,先调这个 GET 拿到所有摘要展示给
        用户看(还没 merge),用户对比满意了再调 POST /api/branches/merge。

    📌 设计决策
        1. fork_ids 走 query string 不走 body:GET 必须用 query;逗号分隔
           而非数组(?fork_ids=a&fork_ids=b)是因为 curl 友好,前端拼字符串也容易
        2. 返回 list[dict] 而不是嵌套对象:前端直接 map 渲染,无需多余结构
        3. 静默跳过不存在的 fork_id 而不是 400/404:多 fork 场景下,某个被
           外部清掉是常态,不应让整批失败
        4. 复用 multi_branch._summarize_state 而不是 _build_snapshot:
           - _summarize_state 是给 LLM compare 用的精简摘要(~10 字段)
           - _build_snapshot 是完整证据链(几十字段),太多
           前端只需要扫一眼对比表,精简版够用

    ▍为什么这里用延迟 import multi_branch
        api 模块顶部已经 import 了 ForkManager;multi_branch 只在这一个端点
        和 branches_merge 用,不放顶部 import 减少冷启动开销与循环依赖风险

    fork_ids:逗号分隔字符串(走 query string,curl 友好)。
    返回 [{fork_id, description, status, final_rating, summary}, ...]。
    """
    # ◍ 延迟 import multi_branch 避免循环依赖(它反过来 import graph.run_pipeline)
    from co_scientist.modules.m8_replay.multi_branch import _summarize_state

    ids = [s.strip() for s in fork_ids.split(",") if s.strip()]
    if not ids:
        raise HTTPException(400, "fork_ids 至少一个")

    out: list[dict] = []
    for fid in ids:
        meta = fork_manager.get_meta(fid)
        if not meta:
            continue
        state = (_runs.get(fid) or {}).get("state") or {}
        out.append({
            "fork_id": fid,
            "description": meta.description,
            "status": meta.status,
            "final_rating": meta.final_rating,
            "topic_id": meta.topic_id,
            "summary": _summarize_state(state) if state else {},
        })
    return {"branches": out}


@app.post("/api/branches/merge")
def branches_merge(req: BranchMergeRequest) -> dict:
    """
    选 winner 标记 mainline。use_llm_compare=True 用 critical 角色综合评分。

    🎓 教学目标
        实现整理版 §9.5 第一阶段:"merge = 选评分最高 / 用户确认的 winner,
        标记为 mainline"。前端 ForkTreeView 多选 + 双按钮(规则版 / LLM 版)
        映射到 use_llm_compare 参数。

    📌 设计决策
        1. 接受 fork_ids 列表而不是 parent_fork_id:用户可能跨父合并(罕见但
           合法,比如 M0 K 个候选 + M5.5 派生回退混合);用 ids 显式更灵活
        2. 用 BranchResult dataclass 包装,复用 multi_branch.merge_winner 的
           核心逻辑 — 不重复实现规则版 / LLM 评分,保证 API 路径与 Python
           直接调一致
        3. 状态映射:meta.status == "abandoned" → BranchResult.error 非空
           merge_winner 据此排除 abandoned 分支(不参与 winner 选举)
        4. 返回简化版 winner 信息(不含 final_state):前端拿到 winner_id 后
           可以再调 GET /api/forks/{id} 拿完整 snapshot

    ▍为什么 final_state 可能是 None
        进程重启后 _runs 内存丢失,但 forks.db 里的 meta 还在;此时 BranchResult
        构造时 final_state=None,merge_winner 会因 summary 为空跳过 LLM 评分
        降级到 rule-based,这种降级是预期行为
    """
    from co_scientist.modules.m8_replay import BranchResult, merge_winner
    from co_scientist.modules.m8_replay.multi_branch import _summarize_state

    branches: list[BranchResult] = []
    for fid in req.fork_ids:
        meta = fork_manager.get_meta(fid)
        if not meta:
            continue
        state = (_runs.get(fid) or {}).get("state")
        branches.append(BranchResult(
            fork_meta=meta,
            final_state=state,
            summary=_summarize_state(state) if state else {},
            error="" if meta.status != "abandoned" else "abandoned",
        ))
    if not branches:
        raise HTTPException(404, "未找到任何 fork")

    winner = merge_winner(
        branches,
        fork_manager=fork_manager,
        use_llm_compare=req.use_llm_compare,
    )
    if not winner:
        return {"winner": None, "reason": "无可 merge 分支(全部 abandoned)"}
    return {
        "winner": {
            "fork_id": winner.fork_meta.fork_id,
            "description": winner.fork_meta.description,
            "final_rating": winner.fork_meta.final_rating,
            "topic_id": winner.fork_meta.topic_id,
            "status": winner.fork_meta.status,
        },
        "summary": winner.summary,
    }


# ------------------------------------------------------------
# Snapshot 生成器:WS 与 GET /api/forks/{id} 共用
# ------------------------------------------------------------


def _build_snapshot(state: dict) -> dict:
    """
    把 ResearchState 压成前端需要的 Snapshot,包含整理版 Phase A-D 全部新字段。

    🎓 教学目标
        这是"后端 → 前端契约"的核心层。一处修改触及前端所有 Card 渲染,
        所以集中维护、字段命名稳定、加新字段必须同步前端 Snapshot interface。

    📌 设计决策
        1. 不直接 return state:ResearchState 里 papers/triples 体量大,塞进
           WS 推送会让前端解析爆炸;前端只需要"证据链"关键字段
        2. 三段分组:
           - legacy 字段(向后兼容)— pico/papers_count/critiques/meta_decision
           - 整理版 Phase A-D 新字段 — topic_cards/gap_cards/decision_card 等
           - paper_draft 子字段 — title/latex_path 单独提取
        3. metadata.research_gate 拍平到顶层 research_gate:metadata 是个
           "杂物口袋",前端不应该知道这个内部约定,在这里压平
        4. 全部用 `or {}` / `or []` 兜底:LangGraph 节点失败时字段可能缺失,
           前端拿到空容器比拿到 None 安全(不需要写一堆 ?? 空检查)

    ▍与前端 page.tsx Snapshot interface 的对齐契约
        每加/改一个字段,必须同时改 frontend/src/app/page.tsx 顶部的 Snapshot
        interface。改一处不改另一处会让 TypeScript 静默失败(字段是 optional)。

    ▍何时被调用
        - WS /ws/research/{fork_id}:run 跑完时推一帧
        - GET /api/forks/{fork_id}:前端 ForkTreeView 侧栏拉单条详情
    """
    pico = state.get("pico", {}) or {}
    paper_draft = state.get("paper_draft", {}) or {}
    metadata = state.get("metadata", {}) or {}
    return {
        # ---- 老字段(向后兼容)----
        "pico": pico,
        "papers_count": len(state.get("papers", []) or []),
        "critiques": state.get("critiques", []) or [],
        "meta_decision": state.get("meta_decision", {}) or {},
        "paper_latex_path": paper_draft.get("latex_path"),
        "errors": state.get("error_log", []) or [],
        # ---- 整理版 Phase A-D 新字段 ----
        "topic_cards": state.get("topic_cards", []) or [],
        "current_topic_id": state.get("current_topic_id", ""),
        "evidence_access_status": state.get("evidence_access_status", []) or [],
        "gap_cards": state.get("gap_cards", []) or [],
        "current_gap_id": state.get("current_gap_id", ""),
        "decision_card": state.get("decision_card", {}) or {},
        "research_gate": metadata.get("research_gate", {}) or {},
        "m1_pending_clarification": metadata.get("m1_pending_clarification", ""),
        "experiment_plan": state.get("experiment_plan", {}) or {},
        "paper_title": paper_draft.get("title", ""),
        "artifacts": _collect_artifacts(state),
    }


# ------------------------------------------------------------
# WebSocket:流式状态
# ------------------------------------------------------------


@app.websocket("/ws/research/{fork_id}")
async def ws_research(ws: WebSocket, fork_id: str) -> None:
    """
    前端订阅,每秒推送一次当前状态。
    生产应该用 pub/sub + 精细事件,这里做最简轮询。
    """
    await ws.accept()
    try:
        while True:
            run = _runs.get(fork_id)
            if run is None:
                await ws.send_json({"error": "fork_id 不存在"})
                break
            await ws.send_json({
                "status": run.get("status"),
                "progress": run.get("progress"),
            })
            if run.get("status") in ("done", "error"):
                # 最后推一帧完整状态
                state = run.get("state") or {}
                snapshot = _build_snapshot(state)
                await ws.send_json({
                    "status": run["status"],
                    "progress": run.get("progress"),
                    "snapshot": snapshot,
                    "error": run.get("error"),
                })
                break
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        logger.info("[ws] 客户端断开: {}", fork_id)
