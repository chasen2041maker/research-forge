"""
============================================================
 模块 7:论文初稿生成(m7_writer/writer.py)
============================================================

🎓 教学目标
    多 Agent 并行写章节 + 风格统一 + Editor 润色。
    教学要点:
      - Style Guide Agent 先定调
      - 6 个章节 Agent 并行生成,每个吃同样的 style guide
      - Editor Agent(Claude Opus)统一润色
      - 引用强制只能引用 references 数组里的论文(防幻觉)

📌 输出
    - LaTeX 主文件(可在 Overleaf 直接编译)
    - BibTeX 引用文件

------------------------------------------------------------
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from co_scientist.config import settings
from co_scientist.llm import get_llm
from co_scientist.prompts.templates import (
    SYSTEM_M7_EDITOR,
    SYSTEM_M7_SECTION_WRITER,
    SYSTEM_M7_STYLE_GUIDE,
)
from co_scientist.state import Paper, PaperDraft, ResearchState
from co_scientist.utils import logger


# 6 个章节的写作约束
SECTION_SPECS = {
    "abstract": "150 词内,精炼概括动机、方法、结果、结论",
    "introduction": "讲故事:背景 → 现状不足 → 我们的方案 → 贡献列表(3-5 条)",
    "related_work": "对比式叙述,把相关工作分组,指出我们与每组的差异",
    "method": "技术细节,可以用伪代码 / 公式(LaTeX)。",
    "experiments": "数据集、设置、表格化结果(用 LaTeX tabular)、消融分析",
    "discussion": "诚实讨论局限性、未来工作、伦理考量",
}


def build_style_guide(refined_question: str, pico: dict) -> dict:
    llm = get_llm("chat")
    user = f"研究主题: {refined_question}\nPICO: {pico}\n请输出本论文的统一写作风格。"
    return llm.chat_json(
        messages=[
            {"role": "system", "content": SYSTEM_M7_STYLE_GUIDE},
            {"role": "user", "content": user},
        ],
        purpose="m7_style_guide",
        temperature=0.3,
    )


def write_section(
    section_name: str,
    spec: str,
    style_guide: dict,
    refined_question: str,
    pico: dict,
    references: list[Paper],
    extra_context: str = "",
) -> str:
    """写一节内容。"""
    llm = get_llm("chat")  # 写作主力走 GPT 中转站
    refs_summary = "\n".join(
        f"[{i+1}] {p.get('title','')} ({p.get('venue','')}, {p.get('year','')})"
        for i, p in enumerate(references[:30])
    )
    user = (
        f"# 章节: {section_name}\n"
        f"# 写作要求: {spec}\n\n"
        f"# 风格指南: {style_guide}\n\n"
        f"# 研究主题: {refined_question}\n"
        f"# PICO: {pico}\n\n"
        f"# 可引用文献(必须从下面选,严禁编造)\n{refs_summary}\n\n"
        f"# 额外上下文\n{extra_context}\n\n"
        "请输出 LaTeX 片段(\\section{...} 内的内容,不含 \\section 命令本身)。"
    )
    resp = llm.chat(
        messages=[
            {"role": "system", "content": SYSTEM_M7_SECTION_WRITER},
            {"role": "user", "content": user},
        ],
        purpose=f"m7_write_{section_name}",
        temperature=0.5,
        max_tokens=2048,
    )
    return resp.get("content", "")


async def write_all_sections_parallel(
    style_guide: dict,
    refined_question: str,
    pico: dict,
    references: list[Paper],
    extra_context: dict[str, str],
) -> dict[str, str]:
    """所有章节并行写。"""
    tasks = [
        asyncio.to_thread(
            write_section,
            name,
            spec,
            style_guide,
            refined_question,
            pico,
            references,
            extra_context.get(name, ""),
        )
        for name, spec in SECTION_SPECS.items()
    ]
    sections = await asyncio.gather(*tasks)
    return dict(zip(SECTION_SPECS.keys(), sections))


def editor_polish(sections: dict[str, str], style_guide: dict, references: list[Paper]) -> str:
    """
    Editor Agent(Claude Opus 4.7)统一润色,生成完整 LaTeX 文档。
    这是 Claude 的关键节点之一(质量决定性环节)。

    为什么前面先让各 section writer 并行写,最后再集中给 Editor 收口?
      这是典型的"先并行发散,再串行收敛"。
      - 并行写的好处是快,而且每个章节可以专注自己的局部目标
      - 代价是风格会漂、术语可能不统一、贡献点可能前后表述不一致
      所以最后必须有一个更强模型站在全文视角做统一润色,不然结果会像
      6 个人拼接出来的报告,而不是一篇完整论文。

    为什么终稿阶段用 Claude Opus 而不是继续用便宜模型?
      因为这一层最看重的是跨章节一致性、长上下文整合、语言质量和结构感。
      前面 section writer 的任务更像"局部起草",便宜模型足够;但 Editor 需要同时看
      风格指南、全部章节草稿、引用列表,再做全局重写,这里模型上限会直接影响成稿观感。

    你可以把这个函数理解成论文流水线里的"总编室":
      前面模块提供素材和分稿,它负责把这些零散产物整成一个可以交付的整体。
    """
    llm = get_llm("critical")  # claude-opus-4-7
    refs_str = "\n".join(
        f"[{i+1}] {p.get('title','')}, arxiv:{p.get('arxiv_id','')}"
        for i, p in enumerate(references[:30])
    )
    sections_str = "\n\n".join(
        f"## {name}\n{content}" for name, content in sections.items()
    )
    user = (
        f"# 风格指南\n{style_guide}\n\n"
        f"# 各章节草稿\n{sections_str}\n\n"
        f"# 引用列表\n{refs_str}\n\n"
        "请输出可直接编译的完整 LaTeX 文档(article 类),包含 \\documentclass、\\title、"
        "\\author、\\maketitle、各 \\section、\\bibliography 等完整结构。"
    )
    resp = llm.chat(
        messages=[
            {"role": "system", "content": SYSTEM_M7_EDITOR},
            {"role": "user", "content": user},
        ],
        purpose="m7_editor_polish",
        temperature=0.4,
        max_tokens=8192,
    )
    return resp.get("content", "")


def save_latex(latex_text: str, refs: list[Paper], dir_path: Path) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    tex_path = dir_path / "main.tex"
    bib_path = dir_path / "refs.bib"
    tex_path.write_text(latex_text, encoding="utf-8")
    # 简易 BibTeX 生成
    bib_entries = []
    for i, p in enumerate(refs):
        bid = f"ref{i+1}"
        bib_entries.append(
            "@article{" + bid + ",\n"
            f"  title={{{p.get('title','')}}},\n"
            f"  author={{{' and '.join(p.get('authors', []))}}},\n"
            f"  year={{{p.get('year','')}}},\n"
            f"  journal={{{p.get('venue','')}}},\n"
            f"  url={{{p.get('url','')}}}\n"
            "}"
        )
    bib_path.write_text("\n\n".join(bib_entries), encoding="utf-8")
    return tex_path


def write_paper_node(state: ResearchState) -> ResearchState:
    pico = state.get("pico", {})
    refined_q = pico.get("refined_question", state.get("raw_question", ""))
    if not refined_q:
        return {"error_log": ["[M7] 缺少研究问题"]}

    references = state.get("papers", [])[:30]

    # Style Guide
    style_guide = build_style_guide(refined_q, pico)

    # 各章节附加上下文
    exp = state.get("experiment_plan", {})
    extra: dict[str, str] = {
        "experiments": f"实验方案: {exp}" if exp else "",
        "discussion": (
            "Meta-Reviewer 终裁: " + str(state.get("meta_decision", {}))
            if state.get("meta_decision")
            else ""
        ),
    }

    sections = asyncio.run(
        write_all_sections_parallel(style_guide, refined_q, pico, references, extra)
    )

    # Editor 润色(Claude Opus)
    full_tex = editor_polish(sections, style_guide, references)

    out_dir = settings.OUTPUT_DIR / "papers" / (state.get("fork_id", "default") or "default")
    tex_path = save_latex(full_tex, references, out_dir)
    logger.info("[M7] ✅ 论文初稿已保存: {}", tex_path)

    draft = PaperDraft(
        title=refined_q[:80],
        abstract=sections.get("abstract", ""),
        introduction=sections.get("introduction", ""),
        related_work=sections.get("related_work", ""),
        method=sections.get("method", ""),
        experiments=sections.get("experiments", ""),
        discussion=sections.get("discussion", ""),
        conclusion="",
        references=references,
        style_guide=style_guide,
        latex_path=str(tex_path),
    )
    return {"paper_draft": draft}
