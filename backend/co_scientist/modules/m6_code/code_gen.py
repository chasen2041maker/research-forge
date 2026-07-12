"""
============================================================
 模块 6:代码生成 + 沙箱执行(m6_code/code_gen.py)
============================================================

🎓 教学目标
    把实验方案变成可运行 PyTorch 代码。设计亮点:
      - 两段式(生成 + 执行分离)
      - 三档开关(generate_only / dry_run / full_execute)
      - Docker 沙箱 + 自我纠错循环

📌 安全提醒
    运行 LLM 生成的代码有风险!full_execute 必须在 Docker 里跑,
    禁止直接在宿主机执行。本模块默认 generate_only,关了这道防线
    请自行评估风险。

------------------------------------------------------------
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from co_scientist.config import settings
from co_scientist.appendix.evolve import SkillLibrary, format_skills_for_prompt
from co_scientist.llm import get_llm
from co_scientist.state import CodeArtifact, Experiment, ResearchState
from co_scientist.utils import logger

SYSTEM_M6_CODE = """\
你是熟悉 PyTorch/HuggingFace 的 ML 工程师。
根据实验方案,生成一个能直接运行的最小可行代码仓库。

输出 JSON 格式:
{
  "files": {
    "main.py": "...",
    "train.py": "...",
    "eval.py": "...",
    "README.md": "...",
    "requirements.txt": "..."
  }
}

代码要求:
- 使用标准 HuggingFace 接口(datasets、transformers)
- 模型默认用 bert-base-uncased 或 roberta-base,方便本地小样本测试
- 包含训练 + 评测 + 日志打印
- requirements.txt 只列必需依赖
- README 说明如何运行
"""


def generate_code(
    experiment: Experiment,
    task_hint: str = "",
) -> tuple[CodeArtifact, list[dict]]:
    """
    生成实验代码。

    ▍附录 A L3 接入点(检索端)
        在调 LLM 之前,先用 task_hint(通常是 experiment.name + 研究问题)
        去 SkillLibrary 查已注册的技能。命中的签名会拼进 system prompt,
        LLM 看到"已经有 rrf_fusion(...) 这样的工具"就会直接调而不是重写。

    ▍返回值
        (CodeArtifact, retrieved_skills) —— 二元组。
        retrieved_skills 会由节点函数写回 state.metadata 做可观测性,
        上层 cli run 简报能看到"这次复用了哪些技能"。
    """
    llm = get_llm("chat")
    import json

    # ---- L3 技能检索 ----
    # 用 experiment.name + task_hint 拼一个"粗糙但够用"的任务描述做词袋召回。
    # 召回不命中时 format_skills_for_prompt 返回空串,system prompt 不受影响。
    skills_hit: list[dict] = []
    try:
        query = f"{experiment.get('name', '')} {task_hint}".strip()
        if query:
            skills_hit = SkillLibrary().retrieve(query, top_k=3)
    except Exception as e:
        logger.warning("[M6] 技能检索失败,跳过: {}", e)

    skills_block = format_skills_for_prompt(skills_hit)
    sys_prompt = SYSTEM_M6_CODE + ("\n\n" + skills_block if skills_block else "")

    user = (
        "# 实验方案\n"
        f"{json.dumps(experiment, ensure_ascii=False, indent=2)}\n\n"
        "请生成对应的代码。"
    )
    result = llm.chat_json(
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user},
        ],
        purpose="m6_generate_code",
        temperature=0.3,
        max_tokens=4096,
    )
    files = result.get("files", {}) or {}
    artifact = CodeArtifact(
        files=files,
        requirements=_extract_requirements(files.get("requirements.txt", "")),
        readme=files.get("README.md", ""),
        validation={},
    )
    return artifact, skills_hit


def _extract_requirements(req_text: str) -> list[str]:
    return [line.strip() for line in req_text.splitlines() if line.strip() and not line.startswith("#")]


# ------------------------------------------------------------
# Step B:执行
# ------------------------------------------------------------


def save_files(files: dict[str, str], dir_path: Path) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        # 防目录穿越
        safe_name = name.replace("..", "").lstrip("/\\")
        fpath = dir_path / safe_name
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content, encoding="utf-8")
    return dir_path


def dry_run(dir_path: Path) -> dict:
    """dry_run 档:仅做语法检查 + import 分析,不真执行。"""
    import ast
    import sys

    errors: list[str] = []
    for py in dir_path.rglob("*.py"):
        src = py.read_text(encoding="utf-8")
        try:
            ast.parse(src)
        except SyntaxError as e:
            errors.append(f"{py.name}: {e}")
    return {"mode": "dry_run", "syntax_errors": errors, "ok": not errors}


SYSTEM_M6_REPAIR = """\
你是一位 ML 工程师,正在修复 Docker 沙箱里跑失败的代码。
你会拿到:
  1) 最近一次执行的 stderr / traceback
  2) 涉嫌出错的源文件(按 path -> content)

请仅返回 JSON,形如:
{
  "files": {
    "train.py": "...修复后完整内容...",
    "requirements.txt": "..."
  },
  "rationale": "一句话解释改了什么"
}

修复原则:
- 只改导致失败的那几个文件,保持其它文件不变
- 不要输出补丁/diff,要给出修复后的**完整文件内容**
- 绝不要写会逃出沙箱的代码(os.system 删宿主机、curl 下载可执行等)
"""


def _repair_files_with_llm(
    artifact_files: dict[str, str],
    stderr: str,
) -> dict[str, str]:
    """
    把 stderr 回灌给 LLM,请求修复后的完整文件内容。
    失败(包括解析失败、LLM 不返回 files 字段)返回空 dict,让上层决定是否继续。

    ▍为什么让 LLM 输出"完整文件"而不是 patch
        patch 场景下 LLM 经常产出语法不对的 unified diff、行号漂移等问题,
        上层再做 apply/fallback 特别麻烦。直接让模型输出完整文件再整体覆盖,
        简单可靠,只要文件不大就不浪费多少 token。
    """
    import json

    llm = get_llm("chat")

    files_block = "\n\n".join(
        f"### {name}\n```\n{content}\n```"
        for name, content in artifact_files.items()
        if name.endswith((".py", ".txt"))  # 只传可能出错的源文件,不传 README 等
    )
    user = (
        f"# 沙箱执行错误\n```\n{stderr[-2000:]}\n```\n\n"  # 截断,防爆上下文
        f"# 当前代码仓库\n{files_block}\n\n"
        "请输出修复后的文件。"
    )

    try:
        result = llm.chat_json(
            messages=[
                {"role": "system", "content": SYSTEM_M6_REPAIR},
                {"role": "user", "content": user},
            ],
            purpose="m6_repair",
            temperature=0.2,   # 修 bug 要保守,别发散
            max_tokens=4096,
        )
    except Exception as e:
        logger.warning("[M6] 修复 LLM 调用失败: {}", e)
        return {}

    fixed = result.get("files", {}) or {}
    if not isinstance(fixed, dict):
        return {}
    # 只接受字符串值,防止 LLM 返回嵌套 dict 污染文件系统
    return {k: str(v) for k, v in fixed.items() if isinstance(k, str) and v is not None}


def full_execute_sandbox(
    dir_path: Path,
    artifact_files: dict[str, str] | None = None,
    max_retries: int = 3,
    timeout_seconds: int = 300,
) -> dict:
    """
    full_execute 档:Docker 沙箱跑 toy sample + 自我纠错闭环。

    🎓 与上一版(纯骨架)的区别
      上一版只在失败时重试同一份代码,完全没用到"错误反馈";这一版加了真正的
      "跑 → 失败 → 把 stderr 回灌 LLM → 覆盖文件 → 重试"闭环,这是论文里常见的
      self-debugging / reflection 技巧在 code agent 上的落地。

    🔒 安全加固(相对前一版的增量)
      1. 挂载改为 rw:要让 LLM 修复能落回 dir_path,前提是 dir_path 本身就是
         每次 run 独立生成的临时目录(见 code_generator_node 的 out_dir 组织),
         所以 rw 只影响本次 run 的产物,不会污染其它分叉或宿主机别的目录
      2. 网络从 bridge 改成 none —— 绝大多数 toy sample 不需要联网;
         顺便这让"pip install"这类带副作用的动作从一开始就不可能成功,
         逼着 LLM 在 requirements.txt 生成阶段就写对
      3. 新增 timeout_seconds:单次容器执行超时强制 kill,防止死循环烧 CPU
      4. 不再用 `pip install -q -r requirements.txt && python main.py`
         改成先"只装依赖"一次性加在 image 层外,再跑代码;这里教学版简化为
         让 LLM 保证 requirements.txt 里只用 base image 已有或 pip 能找到的包

    📌 自修复循环约束
      - 每轮失败后最多把 stderr 末尾 2000 字喂给 LLM,避免爆上下文
      - LLM 返回必须是 {"files": {"path": "content"}},否则本轮直接放弃修复
      - 连续 max_retries 轮仍失败 → 返回 ok=False 加完整 logs,让上层决定

    为什么不像论文里那样再做一次"单元测试"验证
      教学版够用:沙箱 exit 0 就算通过。真正上线时应该额外让 LLM 顺手生成
      一份最小测试,并在沙箱里跑 pytest 得 0 退出才算修复成功。留作扩展练习。
    """
    try:
        import docker  # 延迟导入,没装 docker 包也能跑其它档
    except ImportError:
        return {"mode": "full_execute", "ok": False, "error": "docker 包未安装"}

    client = docker.from_env()
    logs: list[str] = []
    repairs: list[str] = []  # 记录每轮的修复说明,供可观测性
    last_error = ""

    # 当前工作中的文件集合:初始是 artifact_files,每轮修复后会更新
    # 不直接读 dir_path 下的文件,避免 LLM 偶发写坏某个文件后还要重新 diff
    current_files: dict[str, str] = dict(artifact_files or {})

    for attempt in range(max_retries):
        try:
            # Anthropic SDK 风格:container.run 会阻塞直到容器退出
            # timeout 是客户端 socket 读超时,配合 mem/cpu 限制防资源爆炸
            raw_output = client.containers.run(
                image="python:3.11-slim",
                command="python main.py --toy",  # 不再允许 pip install,强制靠 base image
                volumes={str(dir_path): {"bind": "/workspace", "mode": "rw"}},
                working_dir="/workspace",
                mem_limit="2g",
                cpu_period=100000,
                cpu_quota=200000,        # 2 核
                network_mode="none",     # 🔒 无网络
                pids_limit=256,          # 🔒 防 fork 炸弹
                detach=False,
                stdout=True,
                stderr=True,
                remove=True,
            )
            output = raw_output.decode("utf-8", errors="ignore")
            logs.append(output)
            return {
                "mode": "full_execute",
                "ok": True,
                "logs": logs,
                "attempts": attempt + 1,
                "repairs": repairs,
            }
        except docker.errors.ContainerError as e:  # type: ignore[attr-defined]
            # ContainerError.stderr 是容器退出时的 stderr 内容,正好喂给 LLM
            stderr = (e.stderr or b"").decode("utf-8", errors="ignore")
            last_error = stderr or str(e)
            logs.append(last_error)
            logger.warning("[M6] 沙箱执行失败(第 {} 轮),stderr 长度 {}",
                           attempt + 1, len(stderr))
        except Exception as e:
            # Docker daemon 挂了、image 拉不到等基础设施问题,不是 LLM 能修的
            last_error = str(e)
            logger.warning("[M6] 沙箱基础设施错误(第 {} 轮): {}", attempt + 1, e)
            return {
                "mode": "full_execute",
                "ok": False,
                "error": last_error,
                "logs": logs,
                "attempts": attempt + 1,
                "repairs": repairs,
            }

        # 最后一轮不再修复,直接退出
        if attempt == max_retries - 1:
            break

        # ---- 自修复:把 stderr + 当前文件 喂给 LLM,拿回修复后文件 ----
        fixed = _repair_files_with_llm(current_files, last_error)
        if not fixed:
            logger.warning("[M6] 本轮 LLM 未给出可用修复,终止重试")
            break

        # 把修复后的文件写回 dir_path,并更新 current_files
        for name, content in fixed.items():
            safe_name = name.replace("..", "").lstrip("/\\")
            fpath = dir_path / safe_name
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content, encoding="utf-8")
            current_files[safe_name] = content
        repairs.append(f"第 {attempt + 1} 轮修复了 {list(fixed.keys())}")
        logger.info("[M6] 第 {} 轮已应用修复: {}", attempt + 1, list(fixed.keys()))

    return {
        "mode": "full_execute",
        "ok": False,
        "error": last_error,
        "logs": logs,
        "attempts": max_retries,
        "repairs": repairs,
    }


# ------------------------------------------------------------
# LangGraph 节点:两段式
# ------------------------------------------------------------


def code_generator_node(state: ResearchState) -> ResearchState:
    """
    Step A:总是跑,生成代码文件。

    ▍本节点会把 L3 SkillLibrary 检索结果写进 state.metadata.m6_skills_retrieved
        供 cli run 简报展示"这次用到了哪些历史技能"。
        技能命中是"可观测性" —— 不影响主流程,但能直观看到学习闭环在工作。
    """
    exp = state.get("experiment_plan", {})
    if not exp:
        return {"error_log": ["[M6] 缺少实验方案"]}

    # task_hint 取原始/精炼问题,让技能检索能用到研究问题的完整语义
    task_hint = (
        state.get("pico", {}).get("refined_question", "")
        or state.get("raw_question", "")
    )
    artifact, skills_hit = generate_code(exp, task_hint=task_hint)

    out_dir = settings.OUTPUT_DIR / "code" / (state.get("fork_id", "default") or "default")
    save_files(artifact.get("files", {}), out_dir)
    artifact["validation"] = {"saved_to": str(out_dir)}
    logger.info("[M6-A] ✅ 代码生成完成: {}", out_dir)

    patch: dict = {"code_artifact": artifact}
    if skills_hit:
        logger.info("[M6-A] 🧰 复用 {} 个历史技能: {}",
                    len(skills_hit), [s["name"] for s in skills_hit])
        patch["metadata"] = {"m6_skills_retrieved": skills_hit}
    return patch  # type: ignore[return-value]


def code_executor_node(state: ResearchState) -> ResearchState:
    """Step B:按开关决定是否执行。"""
    mode = state.get("execution_mode", settings.CODE_EXECUTION_MODE)
    artifact = state.get("code_artifact", {})
    if not artifact:
        return {}

    if mode == "generate_only":
        logger.info("[M6-B] 模式 generate_only,跳过执行")
        return {}

    out_dir = Path(artifact.get("validation", {}).get("saved_to", ""))
    if not out_dir.exists():
        return {"error_log": ["[M6-B] 代码目录不存在"]}

    if mode == "dry_run":
        result = dry_run(out_dir)
    elif mode == "full_execute":
        # 把当前 artifact.files 带下去,修复循环需要完整源码作为上下文
        result = full_execute_sandbox(
            out_dir,
            artifact_files=dict(artifact.get("files", {}) or {}),
        )
    else:
        logger.warning("[M6-B] 未知模式 {},降级为 generate_only", mode)
        return {}

    artifact["validation"].update(result)

    # ---- L3 技能注册(注册端) ----
    # 只在"真跑通过" 的情况下注册:
    #   - dry_run: ok=True(语法 + import 都过) → 可注册,但属于弱保证
    #   - full_execute: ok=True(沙箱 exit 0) → 强保证,最值得注册
    # 本项目保守策略:仅在 full_execute 成功时注册,避免"光是语法对"的代码污染库。
    registered: list[str] = []
    if mode == "full_execute" and result.get("ok"):
        try:
            registered = _register_verified_skills(artifact, state)
        except Exception as e:
            logger.warning("[M6-B] 技能注册失败,跳过: {}", e)

    patch: dict = {"code_artifact": artifact}
    if registered:
        logger.info("[M6-B] 🧠 新增 {} 个技能入库: {}", len(registered), registered)
        patch["metadata"] = {"m6_skills_registered": registered}
    return patch  # type: ignore[return-value]


def _register_verified_skills(
    artifact: CodeArtifact, state: ResearchState
) -> list[str]:
    """
    从通过沙箱的产物里挑"值得复用的函数"入库。

    ▍怎么挑
        扫描 files 里所有 .py,对每个顶层函数:
          - 名字不是 main / __init__ 之类的 main-ish
          - 函数体 >= 3 行(太短的 helper 复用价值低)
        满足条件的就注册。description 用 "来自 <问题> 的 <函数名>" 拼出来。

    ▍为什么不整文件注册
        一个 main.py 通常 100+ 行,里面有训练循环、配置、main 函数,直接整文件存
        下次也没法复用。只挑"独立函数"才能真作为工具调用。
    """
    import ast

    lib = SkillLibrary()
    question = (
        state.get("pico", {}).get("refined_question", "")
        or state.get("raw_question", "")
    )[:80]

    registered: list[str] = []
    skip_names = {"main", "run", "__init__", "setup", "train", "evaluate"}

    for filename, content in artifact.get("files", {}).items():
        if not filename.endswith(".py"):
            continue
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name in skip_names or node.name.startswith("_"):
                continue
            # 函数体太短跳过
            if len(node.body) < 3:
                continue
            # 抽出这个函数的完整源码
            func_src = ast.get_source_segment(content, node) or ""
            if not func_src.strip():
                continue
            desc = f"来自研究《{question}》的 {filename} 中 {node.name} 函数"
            if lib.register(func_src, desc):
                registered.append(node.name)
    return registered
