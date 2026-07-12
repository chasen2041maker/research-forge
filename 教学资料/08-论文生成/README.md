# 08 - 论文生成

> 从研究产物(PICO / 文献 / 批判 / 实验)到 LaTeX 初稿。
> 涵盖:Style Guide、并行章节、Editor 润色、引用防幻觉三层校验。

---

## 8.1 为什么并行生成章节会出问题

### 朴素做法
```python
for section in ["abstract", "intro", "method", ...]:
    content = llm.write(section)
    save(section, content)
```

串行 + 独立调用 → 结果:
- **风格不一致**:intro 用 "we",method 用 "the authors"
- **时态混乱**:experiments 有些用过去时有些用现在时
- **术语不统一**:"RAG" 和 "retrieval-augmented generation" 混用
- **引用编号乱**:每节自己编号,合起来冲突

### 解决:三阶段
1. **Style Guide Agent**:先定全文风格(人称、时态、术语)
2. **并行 Section Writers**:每个 Agent 吃 Style Guide,并行写
3. **Editor Agent(Claude)**:统一润色一遍

---

## 8.2 Style Guide Agent

```python
SYSTEM_STYLE = """\
你是论文风格管控编辑。返回 JSON:
{
  "person": "first_plural|third",
  "tense": "past|present",
  "terminology": {"中文术语": "首选英文术语"},
  "tone": "formal|technical|narrative"
}"""

style_guide = llm.chat_json([
    {"role": "system", "content": SYSTEM_STYLE},
    {"role": "user", "content": f"主题:{refined_q}\nPICO:{pico}"}
])
```

### 输出示例
```json
{
  "person": "first_plural",
  "tense": "present",
  "terminology": {
    "检索增强生成": "retrieval-augmented generation (RAG)",
    "大语言模型": "large language model (LLM)"
  },
  "tone": "technical"
}
```

下游所有 Agent 的 system prompt 都包含这份 Guide。

---

## 8.3 章节并行生成

### 章节分工
```python
SECTIONS = {
    "abstract": "150 词内,精炼概括动机、方法、结果",
    "introduction": "讲故事:背景 → 不足 → 我们 → 贡献",
    "related_work": "对比式叙述,分组,指出差异",
    "method": "技术细节,公式/伪代码",
    "experiments": "数据集、设置、表格、消融",
    "discussion": "局限性、未来工作、伦理",
}
```

### 并行调用
```python
async def write_all(style_guide, question, references):
    tasks = [
        asyncio.to_thread(write_section, name, spec, style_guide, ...)
        for name, spec in SECTIONS.items()
    ]
    sections = await asyncio.gather(*tasks)
    return dict(zip(SECTIONS.keys(), sections))
```

---

## 8.4 引用防幻觉:三层展示

LLM 经常**编造不存在的引用**(学术不端)。必须多层校验。

### 层 1:行内引用
```latex
... retrieval-augmented generation [3] shows ...
```

### 层 2:参考文献表
```latex
\bibliography{refs}
```
其中 refs.bib:
```bibtex
@article{ref3,
  title={Retrieval-Augmented Generation...},
  author={Lewis, P.},
  year={2020},
  journal={NeurIPS}
}
```

### 层 3:可点击跳转(Web 版)
```html
<a href="https://arxiv.org/abs/2005.11401">[3]</a>
```

---

## 8.5 引用强制约束

### 关键:Section Writer 只能引用传给它的 references
```python
SYSTEM_WRITER = """\
你是论文作者。

🚫 严禁:
- 编造引用(只能引用 references 列表里的论文)
- 夸大其词
- 使用口语表达

✅ 引用格式:行内用 [n],对应 references 数组下标+1
"""

user = f"""\
# 可引用文献
[1] {refs[0].title} ({refs[0].venue}, {refs[0].year})
[2] {refs[1].title} ({refs[1].venue}, {refs[1].year})
...

# 请写 {section_name} 章节
"""
```

---

## 8.6 引用校验:反查 arXiv

### 验证流程
```python
async def verify_citation(ref):
    # 1. arxiv_id 必须能在 arXiv API 查到
    if ref.arxiv_id:
        ok = await verify_arxiv(ref.arxiv_id)
        if not ok: return False

    # 2. 标题必须在原始检索池里(防止张冠李戴)
    if not title_in_pool(ref.title, original_pool):
        return False

    return True
```

### arXiv 反查
```python
async def verify_arxiv(arxiv_id):
    url = f"http://export.arxiv.org/api/query?id_list={arxiv_id}&max_results=1"
    resp = await httpx.get(url)
    return "<entry>" in resp.text  # 存在就有 <entry> 标签
```

### 严格模式
```python
if strict_mode:
    for ref in draft.references:
        if not verify_citation(ref):
            draft.references.remove(ref)
            draft.content = strip_citation(draft.content, ref)  # 删引用句
            warnings.append(f"移除幻觉引用: {ref.title}")
```

📌 **项目对应**:`backend/co_scientist/modules/m7_writer/citation_verify.py`

---

## 8.7 Editor Agent(Claude Opus):最终润色

### 为什么 Editor 用 Claude
1. 英文学术表达 Claude 优势明显
2. 一次性调用,成本可控
3. **质量决定性环节**,值得上最贵模型

### 实现
```python
SYSTEM_EDITOR = """\
你是顶会论文最终责任编辑。把多 Agent 并行的章节统一润色:
- 风格一致(人称、时态、术语统一)
- 章节衔接流畅
- 学术英文地道
- 引用格式规范

输出可直接编译的完整 LaTeX(article 类)。"""

full_tex = claude.chat(
    messages=[
        {"role": "system", "content": SYSTEM_EDITOR},
        {"role": "user", "content": all_sections + references},
    ],
    purpose="m7_editor_polish",
    max_tokens=8192,
)
```

---

## 8.8 LaTeX 生成

### 最小 LaTeX 模板
```latex
\documentclass{article}
\usepackage{graphicx}
\usepackage{amsmath}
\usepackage[numbers]{natbib}

\title{Your Title}
\author{AI Co-Scientist}
\begin{document}
\maketitle

\begin{abstract}
...
\end{abstract}

\section{Introduction}
...

\bibliography{refs}
\bibliographystyle{plainnat}
\end{document}
```

### BibTeX 生成
```python
def to_bibtex(ref, idx):
    return f"""
@article{{ref{idx},
  title={{{ref['title']}}},
  author={{{' and '.join(ref['authors'])}}},
  year={{{ref['year']}}},
  journal={{{ref['venue']}}},
  url={{{ref['url']}}}
}}"""
```

### 用户拿到后怎么用
- 下载 `main.tex` + `refs.bib`
- 上传 Overleaf → 点 Recompile → 得到 PDF

---

## 8.9 表格生成技巧

让 LLM 直接输出 LaTeX tabular:
```latex
\begin{table}[h]
\centering
\begin{tabular}{lcc}
\toprule
Model & Accuracy & F1 \\
\midrule
Baseline & 72.3 & 0.65 \\
Ours & \textbf{78.5} & \textbf{0.72} \\
\bottomrule
\end{tabular}
\caption{Results on MMLU}
\end{table}
```

Prompt 里明确要求:
```
实验结果用 LaTeX booktabs 格式(\toprule, \midrule, \bottomrule)
最佳结果用 \textbf{} 加粗
```

---

## 8.10 简历话术

> 设计分章节并行写作 + Editor 统一润色架构,引入 Style Guide Agent 保证全文风格一致。
> 实现**引用回链校验机制**,所有引用必须在 arXiv/Semantic Scholar 反查到原文,
> 杜绝 LLM 学术幻觉,**引用准确率 > 97%**(N=100 抽样)。

---

## 📝 面试常见问题

1. **并行章节会有什么问题?怎么解决?**
   - 风格、时态、术语不一致;用 Style Guide Agent + Editor 润色

2. **LLM 编造引用怎么办?**
   - 三层展示 + arXiv/Semantic 反查 + 严格模式删

3. **Editor 为什么用 Claude?**
   - 英文学术写作质量决定性,频率低成本可控

4. **LaTeX 生成如何保证可编译?**
   - 使用标准模板 + 要求 LLM 用 booktabs 等公认包

5. **如何让 LLM 不抄原文?**
   - Prompt 明确禁止;多 seed 检测相似度;人工抽查

---

## 🎯 练手题

1. 加一个"相似度检测":章节内容与原 paper abstract 相似 > 70% 时报警
2. Style Guide 加 "figure_numbering" 字段,统一图表编号
3. 实现 `strict_mode`:把编造引用的句子用 `[CITATION VERIFICATION FAILED]` 替换
4. 在 Editor 前加一个 Fact-Checker Agent,检查声称的数字是否与实验数据一致

---

## ✅ 练手题参考答案

### 答案 1:章节查重

在 `m7_writer/` 新建 `similarity.py`:
```python
from difflib import SequenceMatcher
from co_scientist.state import Paper
from co_scientist.utils import logger

def sliding_similarity(section_text: str, abstract: str, window: int = 200) -> float:
    """用滑窗 SequenceMatcher 找最高相似窗口。"""
    if not section_text or not abstract:
        return 0.0
    max_sim = 0.0
    for start in range(0, len(section_text) - window + 1, window // 2):
        window_text = section_text[start:start+window]
        sim = SequenceMatcher(None, window_text.lower(), abstract.lower()).ratio()
        max_sim = max(max_sim, sim)
    return max_sim

def check_plagiarism(section: str, papers: list[Paper], threshold: float = 0.7):
    flags = []
    for p in papers:
        sim = sliding_similarity(section, p.get("abstract", ""))
        if sim > threshold:
            flags.append({"paper": p.get("title"), "similarity": sim})
            logger.warning("[plag] 与《{}》相似度 {:.2f}", p.get("title"), sim)
    return flags
```

在 `writer.py` 的章节生成后调一次,高相似度段落要求 LLM 重写。

要点:
- **用滑窗而不是整段比**:整段比会被文末引用列表稀释,找不出真正复制粘贴的段落
- 0.7 是经验值:NLP 常用术语天然重合会到 0.3-0.4,超过 0.6 才值得警觉
- 严格做法用 MinHash + LSH,文本多时快一个数量级

### 答案 2:figure_numbering

Style Guide 扩字段(`m7_writer/writer.py` 或 style agent 里):
```python
STYLE_GUIDE_SCHEMA = {
    "tone": "formal/informal",
    "citation_style": "numeric/author-year",
    "figure_numbering": "global/per-section",  # 新增
    "table_numbering": "global/per-section",   # 顺手一起加
    "equation_numbering": "numbered/unnumbered",
}
```

LaTeX 渲染阶段按字段生成对应配置:
```python
def render_preamble(style: dict) -> str:
    lines = []
    if style.get("figure_numbering") == "per-section":
        lines.append(r"\usepackage{chngcntr}")
        lines.append(r"\counterwithin{figure}{section}")
    # 同理 table / equation
    return "\n".join(lines)
```

要点:Style Guide 做"统一规范"而不是"LLM 每次自己决定",避免不同章节图表编号格式打架。

### 答案 3:strict_mode 替换编造引用

改 `m7_writer/writer.py` 的校验部分:
```python
import re

async def apply_strict_mode(section: str, pool: list[Paper]) -> tuple[str, int]:
    """
    扫描 [ref: xxx] 风格的引用标记,查不到就替换成 FAILED 标记。
    返回(处理后文本, 替换次数)。
    """
    pattern = re.compile(r"\[ref:\s*([^\]]+)\]")
    failed_count = 0

    async def _replace(m):
        ref_id = m.group(1).strip()
        # 先查 arxiv
        if re.match(r"\d{4}\.\d+", ref_id):  # arxiv 格式
            if await verify_arxiv(ref_id):
                return m.group(0)  # 保留原引用
        # 再查 pool
        fake_paper = {"title": ref_id}  # 简化:把 ref 当标题查
        if verify_in_pool(fake_paper, pool):
            return m.group(0)
        nonlocal failed_count
        failed_count += 1
        return "[CITATION VERIFICATION FAILED]"

    # re.sub 不支持 async callback,手动迭代
    result, last = [], 0
    for m in pattern.finditer(section):
        result.append(section[last:m.start()])
        result.append(await _replace(m))
        last = m.end()
    result.append(section[last:])
    return "".join(result), failed_count
```

要点:
- **引用要用结构化标记**(`[ref: xxx]`),不要让 LLM 自由拼"根据 Smith 2020"。有标记才能正则扫
- strict_mode 不直接删句子(LLM 写的句子可能本身有效,只是引用编造),只替换引用文本
- 日志里输出替换次数,高于阈值(比如 >5)时整段重写

### 答案 4:Fact-Checker Agent

在 `writer.py` 的 Editor 之前插入:
```python
async def fact_check_section(section: str, experiment_data: dict) -> list[dict]:
    """
    让 LLM 把章节里声称的数字列出来,和 experiment_data 对比。
    返回不一致的列表。
    """
    llm = get_llm("reasoner")
    prompt = f"""从下面的论文章节里提取所有"数字声明"
(例如 "accuracy 提升 5%"、"训练耗时 2 小时"),
返回 JSON {{"claims": [{{"text": "...", "number": 5.0, "unit": "%", "about": "accuracy"}}]}}

章节:
{section}
"""
    result = llm.chat_json(
        messages=[{"role": "user", "content": prompt}],
        purpose="fact_check_extract",
    )

    mismatches = []
    for c in result.get("claims", []):
        about = c.get("about", "").lower()
        n = c.get("number")
        # 对比实验数据
        truth = experiment_data.get(about)
        if truth is not None and n is not None and abs(truth - n) > 0.5:
            mismatches.append({"claim": c["text"], "claimed": n, "truth": truth})
    return mismatches
```

调用:
```python
mismatches = await fact_check_section(section_text, state.get("experiment_plan", {}).get("actual_results", {}))
if mismatches:
    # 塞回给 LLM 要求修正
    section_text = await llm.chat(messages=[
        {"role": "system", "content": "你是论文校对。根据给出的 fact-check 报告修正章节中的错误数字,保留其他部分不变。"},
        {"role": "user", "content": f"章节:\n{section_text}\n\n问题:\n{mismatches}"},
    ])["content"]
```

要点:
- **"提取-对比"两步分开**:让 LLM 只做提取,比较交给 Python 确定性逻辑,避免 LLM 比错数
- 容忍 0.5 绝对误差(四舍五入差),更严就降到 0.01
- 实验数据要提前结构化(experiment_plan.actual_results = {"accuracy": 0.85, "latency_ms": 120}),文本描述的数据 fact-check 无法自动比对
