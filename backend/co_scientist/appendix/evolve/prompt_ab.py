"""
============================================================
 附录 A:Prompt A/B 自动进化(appendix/evolve/prompt_ab.py)
============================================================

🎓 教学目标
    思路来自 DSPy / OPRO:跑评测集 → 记录表现 → 失败时让 LLM 改 prompt
    → A/B 测试 → 胜者上位。
    让"system prompt"从硬编码字符串变成可被持续优化的对象。

📌 教学版简化
    只实现单变量 A/B 框架(没有多臂老虎机/EXP3 等高级路由策略),
    真正"自动进化"的部分(LLM 改 prompt)只跑一轮,留接口让你后续扩展。

💡 这层只负责四件事
    1. register     :把候选 prompt 存起来(还没参与排名)
    2. record_score :外部跑完评测把分数记到这条变体上
    3. best_for     :按平均分挑当前最优变体(供运行时调用)
    4. evolve_prompt:基于失败样例自动生成新候选(并自动 register)

    它故意不绑定具体业务模块。Prompt 进化是一层"可插拔的优化层":主流程没有
    A/B 数据时照常用硬编码 prompt,有数据后再透明替换。

📌 与主流程的挂接点
    - 读:m5_experiment/designer.py 调 best_for("m5_experiment")
    - 写:cli.py 的 prompt-ab-register / prompt-ab-evolve 命令

------------------------------------------------------------
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from co_scientist.config import settings
from co_scientist.llm import get_llm
from co_scientist.utils import logger


@dataclass
class PromptVariant:
    """
    返回给调用方的"当前最优变体"快照。

    为什么不直接返回数据库 row,而是包成 dataclass:
      - 调用方(m5_experiment)只关心 text 和元信息,不应该接触 SQL 字段名;
      - 加 avg_score / runs 让调用方可以做"信心阈值"判断
        (例如 runs<3 时不切换变体,只观察)。
    """
    pid: str
    name: str
    text: str
    avg_score: float
    runs: int


class PromptABTester:
    """
    教学版 Prompt A/B 控制器。后端仍是单文件 SQLite,和 EvolvingMemory 一样
    选择"零外部依赖、能跑能讲"。
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or settings.DATA_DIR / "prompts_ab.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        # 用 total_score + runs 而不是 avg_score 直接存,是为了:
        #   1. record_score 只做一次 UPDATE 加法,无需先 SELECT 旧均值;
        #   2. 调用方读 avg_score 时只算一次除法,精度更可控。
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS prompts (
                    pid TEXT PRIMARY KEY,
                    name TEXT,
                    text TEXT,
                    total_score REAL DEFAULT 0,
                    runs INTEGER DEFAULT 0
                )"""
            )

    def register(self, name: str, text: str) -> str:
        """
        登记一个新候选 prompt,返回 pid。

        新变体 runs=0 → best_for() 默认不会选它(WHERE runs>0 过滤),
        防止"未评测的变体"直接上线。需要外部跑评测 + record_score 后才参与排名。
        """
        pid = uuid.uuid4().hex[:8]
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO prompts VALUES (?, ?, ?, ?, ?)",
                (pid, name, text, 0.0, 0),
            )
        return pid

    def record_score(self, pid: str, score: float) -> None:
        """累加一次评测分数(由外部评测/灰度流量调用)。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE prompts SET total_score=total_score+?, runs=runs+1 WHERE pid=?",
                (score, pid),
            )

    def best_for(self, name: str) -> PromptVariant | None:
        """
        选出某个任务名下当前平均分最高的 prompt 变体。

        ▍语义
            - 没注册过任何变体 → 返回 None
            - 全部变体 runs=0(注册了但没评分)→ 返回 None
              这是"没数据时不切换"的策略,让调用方安全地回退到默认 prompt。

        ▍可以理解成一个简化的"线上路由器"
            主流程问"m5_experiment 现在谁最好?",至于历史上测过多少版本、
            每版跑过多少次,都由这张表内部维护,业务模块完全不关心。
        """
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT pid, name, text, total_score, runs FROM prompts WHERE name=? AND runs>0",
                (name,),
            ).fetchall()
        if not rows:
            return None
        rows.sort(key=lambda r: r[3] / max(1, r[4]), reverse=True)
        pid, name, text, total, runs = rows[0]
        return PromptVariant(pid, name, text, total / runs, runs)

    def evolve_prompt(self, name: str, current_text: str, failure_examples: list[str]) -> str:
        """
        让 LLM 看失败样例,自动改 prompt,新版自动 register。

        ▍设计要点
            - 用 reasoner 模型做这件事,因为"诊断 failure → 给改进方案"是推理任务;
            - temperature=0.6:既要发散(探索新写法)又要收敛(不能离题);
            - 限制最多看 5 个失败案例,防止 prompt 过长;
            - LLM 失败时返回原 prompt(不阻塞主流程,也不污染表)。

        ▍局限(故意留给读者扩展)
            - 没做"小改 vs 大改"控制(可加 diff 大小约束)
            - 没做"自动评测"(改完之后没人跑分,best_for 永远选不到它)
              真实流程应该:evolve → 自动跑 N 个评测样本 → 自动 record_score
        """
        llm = get_llm("reasoner")
        sys = "你是 Prompt 工程专家。基于失败案例,改进 system prompt 让模型更好处理这些 case。"
        user = (
            f"# 当前 Prompt\n{current_text}\n\n"
            f"# 失败案例\n" + "\n---\n".join(failure_examples[:5]) + "\n\n"
            "请直接输出改进后的 prompt 全文,不要解释。"
        )
        try:
            resp = llm.chat(
                messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
                purpose="evolve_prompt",
                temperature=0.6,
            )
            new_text = resp.get("content", "").strip()
        except Exception as e:
            logger.warning("[evolve] prompt 进化失败: {}", e)
            return current_text

        if new_text:
            self.register(name, new_text)
            logger.info("[evolve] {} 注册了新变体", name)
            return new_text
        return current_text
