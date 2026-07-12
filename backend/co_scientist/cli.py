"""
============================================================
 CLI 入口(cli.py)
============================================================

🎓 教学目标
    用 typer 写优雅的 CLI。子命令分两组:

    主流程:
      - run    :跑一次完整研究
      - cost   :查看当月花费
      - forks  :列出所有分叉

    附录工具(独立子命令,不进主 DAG):
      - adversarial-run     :跑一轮 Blue/Red/Judge 对抗
      - adversarial-build   :批量产 DPO 训练数据集
      - prompt-ab-register  :给某模块注册新的 prompt 变体
      - prompt-ab-best      :查看某模块当前胜出的 prompt 变体
      - prompt-ab-evolve    :用 LLM 自动改 prompt(基于失败案例)

📌 为什么附录功能要独立成子命令而不是塞进 run
    1. 语义不同:run 产出"一次研究的结果"(论文/方案);adversarial-* 产出
       的是"训练数据";prompt-ab-* 操作的是"配置/注册表"。混在一个命令里
       用户根本搞不清这次跑出来的是什么。
    2. 触发频率不同:run 是核心流程,普通用户高频用;附录工具只在做数据飞轮
       或调优时偶尔用。塞进主流程会增加默认成本(每次都跑对抗 = LLM 账单暴涨)。
    3. 失败影响不同:adversarial-build 跑 100 个种子失败 30 个属于正常,run 主流程
       任何节点失败都要兜底到 error_log。

用法:
    python -m co_scientist.cli run --question "RAG 减少幻觉"
    python -m co_scientist.cli cost
    python -m co_scientist.cli forks
    python -m co_scientist.cli adversarial-run --proposal "..."
    python -m co_scientist.cli adversarial-build --input seeds.txt
    python -m co_scientist.cli prompt-ab-register --name m5_experiment --file p.txt
    python -m co_scientist.cli prompt-ab-best --name m5_experiment

------------------------------------------------------------
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from co_scientist.appendix.adversarial import run_factory, run_multi_round, run_round
from co_scientist.appendix.evolve import PromptABTester, SkillLibrary
from co_scientist.config import settings
from co_scientist.graph import run_pipeline
from co_scientist.modules.m8_replay import ForkManager
from co_scientist.utils import get_tracker, setup_logger

setup_logger()
app = typer.Typer(help="AI Co-Scientist CLI")
console = Console()


@app.command()
def run(
    question: str = typer.Option(..., "--question", "-q", help="研究问题"),
    execution_mode: str = typer.Option(
        "generate_only",
        "--exec-mode",
        "-m",
        help="generate_only / dry_run / full_execute",
    ),
) -> None:
    """跑一次完整研究流程。"""
    console.rule(f"[bold cyan]AI Co-Scientist 启动: {question}")
    state = run_pipeline(raw_question=question, execution_mode=execution_mode)

    # 打印简报
    console.rule("[bold green]研究完成")
    pico = state.get("pico", {})
    console.print(f"[bold]精炼问题:[/] {pico.get('refined_question', '')}")
    console.print(f"[bold]检索论文:[/] {len(state.get('papers', []))} 篇")
    console.print(f"[bold]三元组数:[/] {len(state.get('triples', []))} 条")
    console.print(f"[bold]批判卡片:[/] {len(state.get('critiques', []))} 张")
    meta = state.get("meta_decision", {})
    if meta:
        console.print(
            f"[bold]Meta 终裁:[/] {meta.get('decision')} "
            f"(rating={meta.get('final_rating')})"
        )
    draft = state.get("paper_draft", {})
    if draft.get("latex_path"):
        console.print(f"[bold]论文初稿:[/] {draft['latex_path']}")

    # ---- 附录 A 学习闭环的可观测性 ----
    recalled = state.get("recalled_memories", []) or []
    console.print(f"[bold]召回历史经验:[/] {len(recalled)} 条")

    meta_data = state.get("metadata", {}) or {}
    saved = meta_data.get("evolving_memory_saved")
    if saved is not None:
        console.print(f"[bold]本次沉淀新经验:[/] {saved} 条")

    variant = meta_data.get("m5_prompt_variant")
    if variant:
        console.print(
            f"[bold]m5 使用 Prompt 变体:[/] pid={variant.get('pid')} "
            f"avg_score={variant.get('avg_score'):.2f} runs={variant.get('runs')}"
        )
    ab_scored = meta_data.get("ab_scored")
    if ab_scored:
        console.print(
            f"[bold]A/B 本次回写:[/] pid={ab_scored.get('pid')} "
            f"score={ab_scored.get('score')}"
        )

    # ---- L3 技能库可观测性 ----
    skills_hit = meta_data.get("m6_skills_retrieved") or []
    if skills_hit:
        names = [s.get("name") for s in skills_hit]
        console.print(f"[bold]m6 复用历史技能:[/] {names}")
    skills_new = meta_data.get("m6_skills_registered") or []
    if skills_new:
        console.print(f"[bold]m6 新增入库技能:[/] {skills_new}")

    if state.get("error_log"):
        console.print("[bold red]错误日志:[/]")
        for err in state["error_log"]:
            console.print(f"  - {err}")


@app.command()
def cost() -> None:
    """查看当月花费。"""
    tracker = get_tracker()
    total = tracker.month_total_usd()
    console.print(f"[bold]本月 LLM 花费:[/] ${total:.4f}")


@app.command()
def forks() -> None:
    """列出所有研究分叉。"""
    fm = ForkManager()
    rows = fm.list_forks()
    if not rows:
        console.print("(暂无分叉)")
        return

    table = Table(title="研究分叉列表")
    table.add_column("fork_id")
    table.add_column("parent")
    table.add_column("branch_node")
    table.add_column("status")
    table.add_column("rating")
    table.add_column("description")
    for r in rows:
        table.add_row(
            r["fork_id"],
            r["parent_fork_id"] or "-",
            r["branch_node"],
            r["status"],
            f"{r['final_rating']:.2f}",
            r["description"][:40],
        )
    console.print(table)


def _load_proposals(input_path: Path) -> list[str]:
    """
    把种子方案文件加载成字符串列表,兼容两种格式:
      - .json:期望是字符串数组,例如 ["方案A", "方案B"]
      - 其他后缀(默认按 txt 处理):一行一条,空行自动跳过

    选这两种最朴素的格式而不是 yaml/csv,是因为附录数据工厂的种子通常就是
    人工随手写的几十条研究问题,jsonl 反而麻烦。
    """
    text = input_path.read_text(encoding="utf-8")
    if input_path.suffix.lower() == ".json":
        data = json.loads(text)
        if isinstance(data, list):
            return [str(item) for item in data if str(item).strip()]
    return [line.strip() for line in text.splitlines() if line.strip()]


@app.command("adversarial-run")
def adversarial_run(
    proposal: str = typer.Option(..., "--proposal", help="待对抗的研究方案"),
) -> None:
    """
    跑一轮 Blue/Red/Judge 对抗,把三方文本和裁决打印到终端。

    适合开发期"看看对抗到底长什么样"。要批量产数据集请用 adversarial-build。
    """
    r = run_round(proposal)
    console.rule("[bold magenta]Adversarial Round")
    console.print(f"[bold]Blue 原始方案:[/]\n{r.blue_original}")
    console.print(f"\n[bold]Red 攻击:[/]\n{r.red_attack}")
    console.print(f"\n[bold]Blue 修复:[/]\n{r.blue_fixed}")
    console.print(f"\n[bold]Judge:[/] {r.judgment}")


@app.command("adversarial-build")
def adversarial_build(
    input_path: Path = typer.Option(..., "--input", exists=True, readable=True, help="种子方案文件 (json 列表或一行一条的 txt)"),
    output_path: Path | None = typer.Option(None, "--output", help="输出 jsonl 路径"),
    multi_round: bool = typer.Option(False, "--multi-round", help="启用多轮对抗(每轮都写一条 DPO)"),
    max_rounds: int = typer.Option(3, "--max-rounds", help="多轮模式的上限"),
) -> None:
    """
    批量把种子方案加工为 DPO 训练集 (jsonl)。

    单轮(默认):每个种子产 1 条 (chosen, rejected)。
    多轮:每个种子产 N 条(N = 实际轮数,Red 找不到新漏洞就提前停)。
    """
    proposals = _load_proposals(input_path)
    out = run_factory(
        proposals,
        output_path=output_path,
        multi_round=multi_round,
        max_rounds=max_rounds,
    )
    console.print(f"[bold]对抗数据集已生成:[/] {out}")


@app.command("adversarial-multi")
def adversarial_multi(
    proposal: str = typer.Option(..., "--proposal", help="待对抗的研究方案"),
    max_rounds: int = typer.Option(3, "--max-rounds", help="最大轮数"),
) -> None:
    """
    显式跑一次多轮 Red/Blue 对抗,逐轮打印。

    对应设计文档 B.3 的"Blue 修 → Red 再攻 → …"收敛循环。
    """
    mr = run_multi_round(proposal, max_rounds=max_rounds)
    for i, r in enumerate(mr.rounds, 1):
        console.rule(f"[bold magenta]Round {i}")
        console.print(f"[bold]Red 攻击:[/]\n{r.red_attack}")
        console.print(f"\n[bold]Blue 修复:[/]\n{r.blue_fixed}")
        console.print(f"\n[bold]Judge:[/] {r.judgment}")
    console.rule("[bold green]Summary")
    console.print(f"总轮数: {len(mr.rounds)}  停止原因: {mr.stopped_reason}")


@app.command("prompt-ab-register")
def prompt_ab_register(
    name: str = typer.Option(..., "--name", help="任务名,如 m5_experiment"),
    file_path: Path = typer.Option(..., "--file", exists=True, readable=True, help="prompt 文本文件"),
) -> None:
    """
    给某个任务名注册一个新的 prompt 变体。

    注册后这个变体 runs=0,best_for() 暂时不会选它(避免没评测就上线)。
    需要你在外部跑评测、调用 PromptABTester.record_score(pid, score) 后才会参与排序。
    """
    pid = PromptABTester().register(name, file_path.read_text(encoding="utf-8"))
    console.print(f"[bold]已注册 prompt 变体:[/] {pid}")


@app.command("prompt-ab-best")
def prompt_ab_best(
    name: str = typer.Option(..., "--name", help="任务名,如 m5_experiment"),
) -> None:
    """
    查看某任务当前得分最高的 prompt 变体。

    返回空 → m5_experiment 等节点会回退到默认硬编码 SYSTEM prompt。
    """
    best = PromptABTester().best_for(name)
    if not best:
        console.print("(暂无已评分 prompt 变体)")
        return
    console.print({
        "pid": best.pid,
        "name": best.name,
        "avg_score": best.avg_score,
        "runs": best.runs,
        "text": best.text,
    })


@app.command("evolve-dashboard")
def evolve_dashboard() -> None:
    """
    进化仪表盘:一眼看清记忆库 + Prompt A/B 的当前状态。

    对应技术方案附录 A.6 "进化仪表盘"。
    故意用 SQL 直连两个 SQLite 库查而不是走 EvolvingMemory/PromptABTester 的 API,
    是因为仪表盘需要聚合统计,每次都走领域 API 会绕很多弯。
    """
    import sqlite3

    # ---- 记忆库统计 ----
    mem_db = settings.DATA_DIR / "memory.db"
    mem_table = Table(title="🧠 EvolvingMemory")
    mem_table.add_column("type")
    mem_table.add_column("count", justify="right")
    mem_table.add_column("latest", overflow="fold")
    total_mem = 0
    has_embedding = 0
    if mem_db.exists():
        with sqlite3.connect(mem_db) as conn:
            for t, cnt in conn.execute(
                "SELECT type, COUNT(*) FROM memories GROUP BY type"
            ).fetchall():
                last = conn.execute(
                    "SELECT content FROM memories WHERE type=? ORDER BY created_at DESC LIMIT 1",
                    (t,),
                ).fetchone()
                mem_table.add_row(t, str(cnt), (last[0] if last else "")[:60])
                total_mem += cnt
            (has_embedding,) = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE embedding != ''"
            ).fetchone()
    console.print(mem_table)
    console.print(
        f"[bold]记忆总数:[/] {total_mem}  "
        f"[bold]带 embedding:[/] {has_embedding}  "
        f"[bold]DB:[/] {mem_db}"
    )

    # ---- Prompt A/B 统计 ----
    ab_db = settings.DATA_DIR / "prompts_ab.db"
    ab_table = Table(title="🧪 PromptABTester")
    ab_table.add_column("name")
    ab_table.add_column("pid")
    ab_table.add_column("runs", justify="right")
    ab_table.add_column("avg_score", justify="right")
    ab_table.add_column("preview", overflow="fold")
    if ab_db.exists():
        with sqlite3.connect(ab_db) as conn:
            rows = conn.execute(
                "SELECT name, pid, runs, total_score, text FROM prompts ORDER BY name, runs DESC"
            ).fetchall()
            for name, pid, runs, total, text in rows:
                avg = (total / runs) if runs else 0.0
                ab_table.add_row(name, pid, str(runs), f"{avg:.2f}", (text or "")[:60])
    console.print(ab_table)
    console.print(f"[bold]DB:[/] {ab_db}")

    # ---- SkillLibrary 统计 ----
    sk_db = settings.DATA_DIR / "skills.db"
    sk_table = Table(title="🧰 SkillLibrary (L3)")
    sk_table.add_column("name")
    sk_table.add_column("signature", overflow="fold")
    sk_table.add_column("uses", justify="right")
    sk_table.add_column("description", overflow="fold")
    total_skills = 0
    if sk_db.exists():
        with sqlite3.connect(sk_db) as conn:
            rows = conn.execute(
                "SELECT name, signature, uses, description FROM skills "
                "ORDER BY uses DESC, created_at DESC"
            ).fetchall()
            for name, sig, uses, desc in rows:
                sk_table.add_row(name, sig, str(uses), (desc or "")[:60])
                total_skills += 1
    console.print(sk_table)
    console.print(f"[bold]技能总数:[/] {total_skills}  [bold]DB:[/] {sk_db}")


@app.command("skill-list")
def skill_list() -> None:
    """列出技能库里所有已注册的技能(按被复用次数降序)。"""
    lib = SkillLibrary()
    items = lib.list_all()
    if not items:
        console.print("(技能库为空 —— 跑过 full_execute 成功后会自动入库)")
        return
    t = Table(title="🧰 SkillLibrary")
    t.add_column("name"); t.add_column("signature", overflow="fold")
    t.add_column("uses", justify="right"); t.add_column("description", overflow="fold")
    for s in items:
        t.add_row(s.name, s.signature, str(s.uses), s.description[:60])
    console.print(t)


@app.command("skill-show")
def skill_show(
    name: str = typer.Option(..., "--name", help="技能函数名"),
) -> None:
    """查看某个技能的完整代码。"""
    code = SkillLibrary().get_code(name)
    if code is None:
        console.print(f"[red]未找到技能 {name}[/red]")
        raise typer.Exit(code=1)
    console.rule(f"[bold]{name}")
    console.print(code)


@app.command("skill-delete")
def skill_delete(
    name: str = typer.Option(..., "--name", help="技能函数名"),
) -> None:
    """删除指定技能。"""
    ok = SkillLibrary().delete(name)
    console.print(f"{'已删除' if ok else '未找到'}: {name}")


@app.command("prompt-ab-score")
def prompt_ab_score(
    pid: str = typer.Option(..., "--pid", help="变体 ID(由 prompt-ab-register 返回)"),
    score: float = typer.Option(..., "--score", help="本次评测的得分 (1-10)"),
) -> None:
    """
    手动给某个 prompt 变体回写一次评测分数。

    场景:
      - 跑 run 命令时没走 A/B(例如外部评测/人评)
      - 想先给新变体打几条人工分,让它快速脱离 runs=0 进入 best_for 排名
    """
    PromptABTester().record_score(pid, score)
    console.print(f"[bold]已记录:[/] pid={pid} score={score}")


@app.command("prompt-ab-evaluate")
def prompt_ab_evaluate(
    name: str = typer.Option(..., "--name", help="任务名,目前仅支持 m5_experiment"),
    questions_file: Path = typer.Option(
        ...,
        "--questions",
        exists=True,
        readable=True,
        help="评测问题集(一行一条)。每条跑一次 m5 并由 Judge 打分",
    ),
    only_unscored: bool = typer.Option(
        True,
        "--only-unscored/--all",
        help="默认只评 runs=0 的新变体;--all 则所有变体都再评一次",
    ),
) -> None:
    """
    对某任务下的 prompt 变体做自动评测并回写分数。

    做法:
      1. 读取 questions_file 作为小评测集
      2. 对每个变体,临时把它作为 best_for 返回值,让 m5 用这版 prompt 生成方案
      3. 用 critical 模型(Claude)给生成的方案打 1-10 分
      4. 每条评测分别 record_score(pid, score)

    说明:
      - 只支持 m5_experiment(项目里目前只有这里挂了 AB)
      - 一次评测是"一个变体 × 一个问题",多个问题会产生多次 record_score
    """
    if name != "m5_experiment":
        console.print(f"[red]目前只支持 --name=m5_experiment,收到 {name}[/red]")
        raise typer.Exit(code=1)

    import json as _json
    import sqlite3

    from co_scientist.appendix.evolve import PromptVariant
    from co_scientist.llm import get_llm
    from co_scientist.modules.m5_experiment.designer import design_experiment
    from co_scientist.prompts.templates import SYSTEM_M5_EXPERIMENT

    questions = [
        line.strip()
        for line in questions_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not questions:
        console.print("[red]评测问题集为空[/red]")
        raise typer.Exit(code=1)

    # 直接从库里读所有候选,不走 best_for(它只返回单一胜者)
    ab_db = settings.DATA_DIR / "prompts_ab.db"
    with sqlite3.connect(ab_db) as conn:
        rows = conn.execute(
            "SELECT pid, name, text, total_score, runs FROM prompts WHERE name=?",
            (name,),
        ).fetchall()

    candidates: list[tuple[str, str]] = []
    for pid, _name, text, _total, runs in rows:
        if only_unscored and runs > 0:
            continue
        candidates.append((pid, text))
    if not candidates:
        console.print("(没有需要评测的变体)")
        return

    judge = get_llm("critical")
    judge_sys = (
        "你是 ML 实验方案评审专家。看用户给出的实验方案,基于数据集、基线、指标、消融、"
        "显著性检验五个维度给一个 1-10 的整体分。返回 JSON: "
        '{"score": <int 1-10>, "reason": "..."}'
    )

    # 用 monkey-patch PromptABTester.best_for,强制 m5 用当前正在评测的变体
    from co_scientist.appendix.evolve import prompt_ab as _ab_mod

    original_best_for = _ab_mod.PromptABTester.best_for
    try:
        for pid, text in candidates:
            forced = PromptVariant(pid=pid, name=name, text=text, avg_score=0.0, runs=0)
            _ab_mod.PromptABTester.best_for = lambda self, n: forced  # type: ignore[assignment]

            for q in questions:
                try:
                    exp, _ = design_experiment(q, {"refined_question": q})
                    resp = judge.chat_json(
                        messages=[
                            {"role": "system", "content": judge_sys},
                            {
                                "role": "user",
                                "content": f"问题: {q}\n\n方案:\n{_json.dumps(dict(exp), ensure_ascii=False)}",
                            },
                        ],
                        purpose="prompt_ab_evaluate",
                        temperature=0.2,
                    )
                    score = float(resp.get("score", 0))
                    if score > 0:
                        PromptABTester().record_score(pid, score)
                        console.print(
                            f"[green]pid={pid}[/] q=『{q[:30]}...』 score={score}"
                        )
                except Exception as e:
                    console.print(f"[red]pid={pid} q=『{q[:30]}』 失败: {e}[/red]")
    finally:
        _ab_mod.PromptABTester.best_for = original_best_for  # type: ignore[assignment]

    # 降级兜底:如果用户用的默认 SYSTEM,还可以作为基准对照(不参与 best_for 但留个参考)
    _ = SYSTEM_M5_EXPERIMENT
    console.print("[bold]评测完成[/]")


@app.command("prompt-ab-evolve")
def prompt_ab_evolve(
    name: str = typer.Option(..., "--name", help="任务名,如 m5_experiment"),
    current: Path = typer.Option(..., "--current", exists=True, readable=True, help="当前 prompt 文件"),
    failures: Path = typer.Option(..., "--failures", exists=True, readable=True, help="失败案例,一行一条"),
) -> None:
    """
    根据失败案例让 LLM 自动改 prompt,并把新版本注册到表里(DSPy/OPRO 思想)。

    新版本仍要走评测才会被 best_for() 选中,这里只负责"产生候选 + 入库"。
    """
    cur_text = current.read_text(encoding="utf-8")
    fail_list = [line.strip() for line in failures.read_text(encoding="utf-8").splitlines() if line.strip()]
    new_text = PromptABTester().evolve_prompt(name, cur_text, fail_list)
    console.print("[bold]已生成新变体(前 200 字):[/]")
    console.print(new_text[:200])


if __name__ == "__main__":
    app()
