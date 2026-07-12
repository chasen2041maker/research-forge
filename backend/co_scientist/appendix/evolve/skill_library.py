"""
============================================================
 附录 A · L3:工具/技能库自生成(appendix/evolve/skill_library.py)
============================================================

🎓 教学目标
    Voyager 思想:Agent 在解决任务的过程中会写出很多小工具函数,
    把"通过沙箱验证的"代码注册到技能库,下次遇到类似任务时先查库,
    能复用就复用,不能再让 LLM 新写。久了 Agent 就积累出一本"工具手册"。

    对应论文:Wang et al., "Voyager: An Open-Ended Embodied Agent with
    Large Language Models" (2023).

💡 和附录 A 其他两层的关系
    - L1 EvolvingMemory:记"经验"(自然语言教训/策略)
    - L2 PromptABTester:记"prompt 哪版更好"
    - L3 SkillLibrary :记"代码级工具"(可直接调用)
    三层对应"经验 / 表达 / 工具"三个不同维度的记忆。

📌 本教学版的简化
    - 单文件 Python 代码,不支持多文件工具
    - 召回走词袋(和 L1 memory.py 一样,embedding 留给读者扩展)
    - 没做版本管理:同名函数后注册的直接覆盖前者
      Voyager 原论文是"新版本通过测试才覆盖",简化起见本版就是覆盖
    - 没做沙箱重验证:注册时相信调用方(m6)已经在沙箱里跑通过了

💡 和 m6_code 的挂接点
    注册:m6_code/code_gen.py 的 full_execute_sandbox 成功后调 register()
    检索:m6 起点(或 m5 prompt 拼接阶段)调 retrieve() 拿候选技能签名
    这样主流程"越用越聪明":产过的可复用函数会帮下次 m6 省 LLM 调用。

💡 为什么 "signature + description" 单独存一列
    LLM 做 tool_call 或 prompt 拼接时只需要看"函数叫什么、参数有什么、做什么",
    真实代码要等决定用这个工具时再取。分开存能让检索阶段只扫小字段、快。

------------------------------------------------------------
"""

from __future__ import annotations

import ast
import hashlib
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from co_scientist.config import settings
from co_scientist.utils import logger


@dataclass
class Skill:
    """
    一个已注册的技能。

    字段说明:
      sid         : 技能唯一 ID(短 hex)
      name        : 函数名(Python 标识符),同时作为"重名淘汰"的 key
      signature   : 人读的签名串,如 "rrf_fusion(result_lists: list, k: int)"
      description : 这个技能是干嘛的(注册方提供,一句话)
      code        : 完整源代码(函数体 + imports)
      created_at  : 注册时间戳
      uses        : 被 retrieve 命中的次数(未来做淘汰用)
    """
    sid: str
    name: str
    signature: str
    description: str
    code: str
    created_at: float
    uses: int = 0


class SkillLibrary:
    """
    教学版工具/技能库。

    API 只有 4 个:
      register(code, description) → 解析代码提取函数名和签名,入库
      retrieve(task, top_k)       → 按任务描述召回 top-k 技能元信息
      get_code(name)              → 拿到完整代码(真用时才取)
      delete(name)                → 删除指定技能
    """

    def __init__(self, db_path: Path | None = None) -> None:
        # 存 settings.DATA_DIR/skills.db,和 memory.db / prompts_ab.db 并列。
        # 允许传自定义路径主要是为了单元测试隔离。
        self.db_path = Path(db_path or settings.DATA_DIR / "skills.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        # name 做 UNIQUE:同名技能注册视为"新版本覆盖老版本"。
        # 这也是为什么 register 用 INSERT OR REPLACE,简化版本管理。
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS skills (
                    sid TEXT PRIMARY KEY,
                    name TEXT UNIQUE,
                    signature TEXT,
                    description TEXT,
                    code TEXT,
                    created_at REAL,
                    uses INTEGER DEFAULT 0
                )"""
            )

    # ----------------------------------------------------------
    # 注册
    # ----------------------------------------------------------
    def register(self, code: str, description: str) -> str | None:
        """
        把一段 Python 代码注册为技能。

        ▍流程
            1. ast.parse 解析代码 → 找第一个顶层 def,作为"主函数"
            2. 拼签名:name(arg1, arg2, ...)
            3. INSERT OR REPLACE 到表,同名即覆盖(新版本上位)

        ▍返回
            - 成功 → sid(新注册 / 覆盖都返回)
            - 解析失败 / 找不到函数 → None,上层可以选择 log 忽略

        ▍为什么不做"必须通过沙箱"的强制
            本函数不知道调用方是否跑过沙箱。强制校验会让测试和单元测试难写,
            而 m6_code 里的调用点已经保证"沙箱 exit==0 才 register",
            职责分离更干净。
        """
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            logger.warning("[skill] 代码语法错误,拒绝注册: {}", e)
            return None

        funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
        if not funcs:
            logger.warning("[skill] 代码里没找到顶层函数定义")
            return None

        f = funcs[0]
        arg_strs: list[str] = []
        for a in f.args.args:
            if a.annotation is not None:
                # 取 annotation 的源码片段(如 "list[int]")
                try:
                    ann = ast.unparse(a.annotation)
                except Exception:
                    ann = "Any"
                arg_strs.append(f"{a.arg}: {ann}")
            else:
                arg_strs.append(a.arg)
        signature = f"{f.name}({', '.join(arg_strs)})"

        sid = uuid.uuid4().hex[:10]
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO skills VALUES (?, ?, ?, ?, ?, ?, 0)",
                (sid, f.name, signature, description, code, time.time()),
            )
        logger.info("[skill] 注册 {} :: {}", f.name, signature)
        return sid

    # ----------------------------------------------------------
    # 检索
    # ----------------------------------------------------------
    def retrieve(self, task: str, top_k: int = 3) -> list[dict]:
        """
        按任务描述召回 top-k 技能(只返回元信息,不含 code)。

        ▍算法
            词袋重叠度,沿用 L1 memory.py 的简易版,没命中就返回 []。
            生产版应替换成 embedding,接口形状不变。

        ▍为什么返回不含 code
            prompt 拼接场景只需要"我有哪些工具、签名是什么",
            代码太长会把 context 挤爆。真要用再调 get_code()。

        ▍顺带 bump uses 计数吗
            不。retrieve 是"候选召回",不代表最终用了。
            真正用上的时候上层应该调 bump_uses(name) 显式增计数。
            这样 uses 才是"被真正复用的次数"而不是"被搜到过的次数"。
        """
        if not task:
            return []
        terms = set(task.lower().split())
        if not terms:
            return []

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT sid, name, signature, description, uses FROM skills"
            ).fetchall()
        scored: list[tuple[int, dict]] = []
        for sid, name, sig, desc, uses in rows:
            # 描述 + 签名 + 函数名都参与匹配
            haystack = f"{name} {sig} {desc}".lower()
            score = sum(1 for t in terms if t in haystack)
            if score > 0:
                scored.append(
                    (score, {"sid": sid, "name": name, "signature": sig,
                             "description": desc, "uses": uses})
                )
        scored.sort(reverse=True, key=lambda x: x[0])
        return [s for _, s in scored[:top_k]]

    def get_code(self, name: str) -> str | None:
        """按函数名取完整源码。找不到返回 None。"""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT code FROM skills WHERE name = ?", (name,)
            ).fetchone()
        return row[0] if row else None

    def bump_uses(self, name: str) -> None:
        """上层真的用了这个技能,显式调一次 +1。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE skills SET uses = uses + 1 WHERE name = ?", (name,))

    def delete(self, name: str) -> bool:
        """按函数名删除。返回是否真的删了一行。"""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("DELETE FROM skills WHERE name = ?", (name,))
            return cur.rowcount > 0

    def list_all(self) -> list[Skill]:
        """列出所有技能(按 uses 降序 → 用得最多的在前)。"""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT sid, name, signature, description, code, created_at, uses "
                "FROM skills ORDER BY uses DESC, created_at DESC"
            ).fetchall()
        return [Skill(*r) for r in rows]


def format_skills_for_prompt(skills: list[dict]) -> str:
    """
    把 retrieve 的结果格式化成能直接拼进 LLM prompt 的段落。

    ▍为什么单独提出来做工具函数
        拼接格式是"固定格式"(签名 + 描述,一行一个),上层 m5/m6 都会用。
        写成独立函数避免两处重复。
    """
    if not skills:
        return ""
    lines = ["# 可复用技能库(已验证的历史工具)"]
    for s in skills:
        lines.append(f"- `{s['signature']}` — {s['description']}")
    lines.append("若本次任务用得到,直接调用;用不到忽略即可。")
    return "\n".join(lines) + "\n\n"
