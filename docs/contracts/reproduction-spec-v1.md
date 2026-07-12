---
title: ReproductionSpec v1
status: frozen
---

# ReproductionSpec v1

> 状态：Frozen for First Vertical Slice
>
> JSON Schema：[`reproduction-spec-v1.schema.json`](reproduction-spec-v1.schema.json)
> 变更规则：不兼容修改必须发布 v2，不得原地改变 v1 语义。

## 1. 目的

`ReproductionSpec v1` 是 Research Forge v0.1 唯一允许进入执行系统的 Mission 输入契约。它把自然语言目标收缩为机器可以在调用 LLM 前完成验证的执行规范。

第一条 Vertical Slice 只支持：

```yaml
mode: reproduce
```

第二条 Slice 才支持：

```yaml
mode: repair
```

`ablation` 仅保留在 Schema 中作为后续能力；v0.1 发布前可以保持禁用。

## Runtime capability profile

This contract remains frozen even where the schema is wider than the current runtime. Read
[runtime-capability-profile-v0.1.md](runtime-capability-profile-v0.1.md) before assuming an input
is executable. In particular, VS-001 currently requires an existing local repository, `prebuilt`
setup, and `offline` execution.

## 2. 完整示例

```yaml
schema_version: 1
mode: reproduce

paper:
  artifact_id: paper-toy-001
  sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
  extraction_profile: plain-text-v1

repository:
  url_or_path: tests/fixtures/toy_reproduction_repo
  commit_sha: 0123456789abcdef0123456789abcdef01234567

execution:
  image_digest: sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
  setup_mode: prebuilt
  setup_argv: []
  run_argv:
    - python
    - evaluate.py
    - --output
    - metrics.json
  working_directory: .
  timeout_seconds: 120
  network_policy: offline
  allowed_domains: []

metric:
  artifact_path: metrics.json
  format: json
  json_pointer: /accuracy
  comparator: equals
  expected_value: 0.8
  tolerance: 0.001
  unit: ratio

change_budget:
  allowed_paths: []
  max_files: 0
  max_changed_lines: 0
  max_candidate_commits: 0
  max_candidate_runs: 0

budget:
  max_wall_time_seconds: 300
  max_cost_usd: 0
  max_artifact_bytes: 10485760
  max_log_bytes: 1048576
```

## 3. 字段语义

### 3.1 `schema_version`

- 固定为整数 `1`；
- 缺失或其他值直接拒绝；
- Validator 不猜测版本。

### 3.2 `mode`

| 值 | 行为 |
|---|---|
| `reproduce` | 只执行固定基线，不允许代码修改 |
| `repair` | 允许一个候选 Commit，使固定验收命令通过 |
| `ablation` | 用户预先给出唯一变量和值；Agent 不发明变量 |

### 3.3 `paper`

- `artifact_id`：已经登记的论文 Artifact；
- `sha256`：论文原始字节 Hash；
- `extraction_profile`：固定文本抽取器和版本；
- v1 不接受未固定版本的在线页面作为论文事实来源。

### 3.4 `repository`

- `url_or_path`：Schema accepts a string, but the current prerequisite verifier accepts only an
  existing local repository directory; remote Git fetching is planned work.
- `commit_sha`：完整 40 位 SHA；
- 禁止使用漂移的 `main`、Tag 或短 SHA 作为执行真相；
- Clone 后必须验证 HEAD 与 Spec 一致。

### 3.5 `execution`

- `image_digest`：不可变镜像 Digest，禁止可漂移 Tag；
- `setup_mode=prebuilt`：镜像已经包含依赖；
- `setup_mode=lockfile`：reserved by the frozen schema; current VS-001 rejects it.
- `run_argv`：参数数组，不接受 Shell 字符串；
- `working_directory`：相对仓库根目录，Resolve 后必须仍位于 Worktree；
- `timeout_seconds`：单次执行硬上限；
- `network_policy=offline`：正式 `RUN` 阶段无网络；
- `allowlisted` is reserved by the frozen schema; current VS-001 rejects it.

### 3.6 `metric`

- `artifact_path`：相对 Worktree 输出路径；
- `format`：v1 只支持 JSON；
- `json_pointer`：RFC 6901 Pointer；
- `comparator`：`equals/gte/lte`；
- `expected_value`：Golden 或阈值；
- `tolerance`：非负数；
- Metric Extractor 不允许 LLM 参与。

### 3.7 `change_budget`

`reproduce` 必须全部为零或空列表。

`repair`：

- `allowed_paths` 必须非空；
- `max_files` 范围 1–3；
- `max_changed_lines` 范围 1–200；
- `max_candidate_commits=1`；
- `max_candidate_runs=1`。

`ablation` 除上述预算外必须增加未来版本的明确变量字段；在该字段冻结前禁止启用。

### 3.8 `budget`

- Mission total wall-clock budget (currently checked only against the single execution timeout);
- model cost budget (currently recorded but not enforced by a runtime cost meter);
- Artifact 总大小上限；
- stdout/stderr 上限；
- 达到上限立即停止后续计费或执行动作。

## 4. 跨字段不变量

JSON Schema 负责结构校验；Application Validator 负责以下语义：

1. `reproduce` 的 Change Budget 必须为零；
2. `repair` 只允许一个 Candidate Commit 和 Run；
3. `ablation` 在 Feature Flag 关闭时拒绝；
4. `offline` 时 `allowed_domains` 必须为空；
5. `allowlisted` 的域名规则保留给未来运行时；当前 VS-001 会在该模式进入执行前拒绝；
6. `working_directory`、`artifact_path`、`allowed_paths` Resolve 后必须位于 Worktree；
7. `max_wall_time_seconds >= timeout_seconds`；
8. Paper Artifact 和 Repository Commit 必须真实存在且 Hash 一致；
9. Image Digest 必须已在允许镜像列表；
10. Metric JSON Pointer 必须能在执行结果中解析到有限数值；
11. 命令参数不得包含 NUL，且由 Sandbox 直接 Exec，不通过 Shell；
12. Spec 校验完成后生成规范化 JSON 和 `spec_sha256`，后续不可原地修改。

## 5. 停止条件

### reproduce

- 固定命令执行一次；
- 成功则验证 Metric 并生成 Bundle；
- 失败则 Mission 失败；
- 不允许自动修改代码。

### repair

- Baseline 执行一次；
- 允许生成一个 Patch；
- Patch 必须满足 Change Budget；
- 只允许一个 Candidate Commit；
- Candidate 只运行一次；
- 成功或失败后停止。

### ablation

- v1 默认禁用；
- 未冻结变量、原值、候选值和预期比较时拒绝。

## 6. 兼容性

- 新增可选字段可以在 v1 Minor Revision 中进行；
- 改变字段含义、默认权限、执行次数或成功语义必须发布 v2；
- Bundle 必须保存原始 Spec、规范化 Spec 和 `spec_sha256`；
- Eval 报告必须记录 Schema 版本和 Spec Hash。
