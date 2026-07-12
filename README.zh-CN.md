<p align="center">
  <strong>RESEARCH FORGE</strong><br />
  面向冻结实验的、证据门控的科研复现控制平面。
</p>

<p align="center">
  <a href="README.md">English</a>
</p>

> 将论文、固定版本的代码仓库、不可变执行规范和确定指标，变成可审计的实验与可重放 Bundle。

Research Forge 是将研究方向转化为可复核结论的产品。它不会因为 worker 报告“通过”就接受实验结果：每个完成结果都必须关联固定 Git commit、操作账本、内容寻址产物、确定性指标、证据链接和可复核 Bundle。

**当前状态：** 已实现并由 CI 覆盖 VS-001 基线纵向切片、受限修复流程、持久化审批、Studio → Forge JSON 交接、最小 API/UI、SQLAlchemy 持久化适配器、Alembic 迁移、Linux Docker 门禁，以及面向 PostgreSQL/Redis/Docker broker 的主机进程部署组合与运维手册。旧版 AI Co-Scientist 演示系统仍独立保留在 [`backend/co_scientist`](backend/co_scientist)。

## 一个产品，两种诚实模式

| 模式 | 负责什么 | 输出状态 |
| --- | --- | --- |
| [Research Studio](frontend/src/app/studio/page.tsx) | 探索问题、文献、研究空白、批判、实验计划与代码建议。 | `UNVERIFIED` `ResearchProposal v1` |
| [Forge Runtime](frontend/src/app/forge/page.tsx) | 冻结 Spec、校验 pin 与预算、执行、提取指标、闭合证据并封存 Bundle。 | 仅在完成正常证据闭环后产生 `VERIFIED` `VerifiedResult v1` |
| 完整研究闭环 | 用户明确补全 Studio 缺少的 hash、commit、镜像、命令、指标和预算，再创建普通 Forge Mission。 | 在 Forge 证明前，Proposal 始终未验证 |

```text
Research Studio                         Forge Runtime
问题 -> 文献 -> 计划          Proposal + 人工补全 -> 冻结的 ReproductionSpec
           |                                |
           +---- UNVERIFIED JSON ---------->| 固定 Git -> sandbox -> metric -> evidence -> Bundle
```

Studio 与 Forge 不会导入对方的 graph、state、worker、adapter 或持久化模型。共享词汇仅位于
[`backend/research_contracts`](backend/research_contracts)，单向交接位于
[`backend/research_gateway`](backend/research_gateway)。边界与 API 见[产品架构](docs/product/overview.md)。

## 为什么需要 Research Forge？

科研自动化很容易以不易察觉的方式失真：

- 指标没有精确对应产生它的 Git commit；
- 被终止的 worker 重试副作用，造成相互冲突的状态；
- 结果被接受后，关联产物仍可能被替换；
- 未经可追溯的人工审批就提交候选补丁；
- 看板把进程内存中的临时状态伪装为持久化事实。

Research Forge 有意保持窄范围。在 Mission 完成前，它为一次复现提供确定的证据链。

```text
冻结的 ReproductionSpec
        |
        v
Mission -> Task -> 由租约拥有的 Attempt -> Operation Ledger
        |                                  |
        |                                  v
        +-> 固定 Git worktree -> 离线 sandbox -> CAS 产物
                                                    |
                                                    v
                                  metric -> claim -> evidence -> Bundle
```

## 已具备的能力

- `ReproductionSpec v1` JSON Schema 校验、跨字段规则、前置条件校验和不可变规范标准化。
- 持久化的 Mission / Task / Attempt 状态、乐观版本、租约 epoch、心跳、取消、审计事件与 Outbox 事件。运行中的沙箱 Attempt 每 10 秒续租；最终落库前会停止监控，从而固定最终使用的乐观锁版本。
- 基线与受限候选 Git worktree；每个 Git、CAS 和 sandbox 副作用都有幂等操作记录。
- 通过 SHA-256 校验的内容寻址产物，以及安全、确定的 Bundle 重放解压；每个 Bundle 同时保存原始与规范化 Spec，并包含绑定 Spec hash 的结构化评测报告。
- Linux 上的离线 Docker 执行：`--network none`、只读根文件系统、移除 capability、非 root 用户和独立 broker 边界。
- 确定性指标提取、已验证的 Claim 和完整的 Evidence 链。
- 一个受限修复闭环：提案 -> 持久化审批 -> 新建子 Attempt -> 候选 commit -> 候选执行 -> 证据门控 Bundle。
- 使用本地 Bearer Token 的 FastAPI：Mission 状态、取消、Bundle 下载、审批决定和 `POST /v1/proposals/handoff`；Next.js Forge 控制台只读取该持久化状态，不保存第二份业务真相。
- 版本化 JSON 单向桥梁：完成的 Studio run 可从 `GET /api/research/{fork_id}/proposal` 导出，再由用户补全后进入 Forge 原有的 `ReproductionSpec v1` Mission 边界。
- SQLAlchemy 真相源适配器、静态 Alembic 版本、迁移升级/降级 CI 校验，以及真实 PostgreSQL service 门禁。
- 冻结的 16 用例发布清单；其中基线端到端证明连续运行 10 次，恢复场景也会重复运行，并生成可保留的 JSON 报告。
- 宿主进程日志使用结构化 JSON、有限的关联字段和凭据脱敏；持久化 Audit/Outbox 仍是业务证据的事实来源。
- 可部署的主机进程组合：Compose 仅负责 PostgreSQL 与 Redis；API、Outbox 发布器和有 Docker 权限的专用 worker 在同一 Linux 主机路径上运行。

## 10 秒验证

在仓库根目录执行：

```powershell
python -m pip install -r deploy/research-forge/requirements.txt httpx mypy pytest ruff
python -m pytest backend/tests/research_forge backend/tests/research_integration -q
python -m ruff check backend/research_forge backend/research_contracts backend/research_gateway backend/co_scientist/public_api backend/tests/research_forge backend/tests/research_integration
python backend/scripts/run_frozen_research_forge_eval.py
```

构建 Forge 控制台：

```powershell
cd frontend
npm install
npm run build
```

GitHub Actions 会在每次推送到 `main` 时运行 mypy、秘密扫描、非 Docker 测试、架构门禁、Alembic 升级/降级契约、独立的 Linux Docker 端到端门禁、依赖漏洞与许可证报告，以及 16 用例冻结评测。自定义 AST 门禁会验证 inbound/outbound/decision 边界、平台 SDK 归属、公开签名形状以及内部导入图无环。评测任务会保留包含所有 Case 结果与 Manifest SHA-256 的 JSON artifact。

## Studio → Forge 交接

1. 在 Studio 完成一次探索后，请求 `GET /api/research/{fork_id}/proposal`。
2. 将返回的 `ResearchProposal v1` 视为建议；其结构化状态明确为 `UNVERIFIED`。
3. 在 Forge 控制台的 **Studio → Forge handoff** 面板粘贴 Proposal，并明确填写论文产物 hash、仓库 commit、镜像 digest、setup/run 参数、指标、修改预算和运行预算。
4. Forge 调用原本的冻结 Spec Mission 创建路径。无效 JSON Schema、不可用 pin、不在允许列表的镜像或不满足的前置条件仍会被拒绝。

交接绝不会自动把 Studio 建议复制成执行字段。后续 Studio 可以读取 `VerifiedResult v1` 形成报告，但不能改变证据门禁，也不能把未验证输出伪装成已验证结果。

## 核心概念

| 概念 | 含义 | 价值 |
| --- | --- | --- |
| `Mission` | 标准化且不可变的复现规范与顶级生命周期。 | 每个结果都有稳定身份。 |
| `Task` / `Attempt` | 工作单元与某次拥有租约的具体执行。 | 旧 worker 无法完成更新后的工作。 |
| `Operation` | 跨存储副作用的幂等记录。 | 恢复不会重复执行 Git、CAS 或 sandbox 效果。 |
| CAS 产物 | 以 SHA-256 定位的执行日志、指标、源码归档或 Bundle。 | 可检测产物篡改。 |
| Claim + Evidence | 指向支撑产物的指标陈述。 | UI 不会把无证据的数据展示成事实。 |
| Approval | 对一个高风险修复补丁 hash 的持久化决定。 | worker 不阻塞；变更后的补丁不能复用审批。 |
| Bundle | 确定性的重放交付物。 | 完成的 Mission 可被独立核验。 |

## 快速架构

```text
Inbound API / Worker
        |
        v
Application use cases
        |
        +--> Domain: Mission, Attempt, Approval, Operation, Evidence
        |
        +--> Ports: UoW, Git, Sandbox, CAS, Decision Engine
                    |
                    v
          PostgreSQL / Git / Docker broker / local CAS
```

架构以 Application 为中心：

- FastAPI 路由和 worker 只调用 use case，不直接访问 ORM、Git、Docker 或 DecisionEngine 的副作用能力。
- PostgreSQL 是业务真相源；Git 管理代码状态；CAS 管理产物字节。
- `DecisionEngine` 只能返回不可信的 `ActionProposal`。只有 Application 策略能校验路径预算、审批、补丁 hash 和操作账本，并允许 Git 提交。
- Windows 原生环境只用于 UI/开发；正式容器安全验收环境为 Linux/WSL2。

## 修复流程

```text
修复模式下的基线验证失败
        |
        v
修复 worker 读取已验证的基线日志
        |
        v
DecisionEngine 仅提出一个补丁
        |
        v
持久化补丁 SHA-256 审批；worker 退出
        |
        v
审核者批准 -> 子 Attempt + Outbox 事件
        |
        v
修复 worker 验证匹配补丁、只提交一次、只执行一次、验证指标
```

仓库内提供的是确定性的测试适配器 `FixedPatchDecisionEngine`。**并未把基于 LLM 的修复运行时作为已交付功能。** 未来的 LLM 决策适配器必须满足同一条窄 `DecisionEngine` port，且不能获得 Git、Docker、CAS、Queue 或数据库能力。

## Forge 控制台

位于 [`frontend/src/app/forge`](frontend/src/app/forge) 的 Next.js UI 是本地控制平面，而不是第二份真相源。它提供：

1. 用冻结规范和本地 API Token 创建 Mission。
2. 展示持久化 Task / Attempt 时间线、租约 epoch 与失败状态。
3. 对高风险补丁进行带审核者身份的显式审批。
4. 只有证据闭环完成后才可下载已验证 Bundle。

本地 API 默认只监听回环地址，并要求 Bearer Token。CORS 仅允许配置的本地来源。

## 生产部署与安全边界

- 正式运行阶段必须使用 `--network none`，不允许执行阶段联网。
- Docker broker 是独立 Unix socket 服务，也是唯一可调用 Docker 的进程。API、Outbox 发布器和 worker 都没有 Docker socket 权限；worker 只能向 broker 发送带类型的离线请求。
- 候选 commit 受允许路径、文件数、变更行数、一次提交和一次执行的硬限制。
- Bundle 解压拒绝路径穿越、绝对路径、链接和未声明成员。
- 审批绑定 scope、Task、父 Attempt、决策身份、到期时间和准确的补丁 hash。
- 取消、租约丢失、过期 epoch 与过期乐观版本都是持久化状态迁移，而不是 UI 标记；运行中的取消会先停止 broker 操作，worker 再确认队列消息。

生产环境必须让 API、发布器与 worker 看到同一 Linux 主机文件系统：Mission 持久化的本地仓库路径必须和 Docker bind mount 的真实源路径完全一致。因此 Compose 仅管理 PostgreSQL 与 Redis；不要把 worker 随意放入普通容器中。

完整安装、运维、备份和恢复步骤见 [VS-001 部署与恢复手册](docs/operations/deployment.md)。

## 项目结构

```text
backend/research_forge/
  domain/                 Mission、审批、操作、产物、证据规则
  application/            use case、DTO 与 Ports
  adapters/inbound/       FastAPI 与租约拥有的 workers
  adapters/outbound/      SQLAlchemy、Git、CAS、sandbox、system 适配器
  bootstrap/              显式组合根与生产进程入口
frontend/src/app/forge/   本地证据门控控制台
deploy/research-forge/    Compose 依赖服务、systemd 单元和配置样例
docs/                     规范、ADR、架构、审查与运维材料
```

## 路线图与范围

后续工作必须构建在现有可复现性与可恢复性不变量之上：

- 能力受限的 LLM `DecisionEngine` 适配器，前提是完成策略与供应链门禁。
- 面向干净机器运行的文档与演示 fixture。

Research Forge 当前**不宣称**支持浏览器自动化、MCP、Skills、多候选搜索、自主创建 PR 或通用科研写作。

## 延伸阅读

- [ReproductionSpec v1](docs/contracts/reproduction-spec-v1.md)
- [历史 VS-001 实施计划](docs/history/implementation-plans/vs-001.md)
- [系统总览](docs/architecture/system-overview.md)
- [分层与架构治理规则](docs/architecture/core-governance.md)
- [架构决策记录](docs/adr/README.md)
- [生产部署与恢复手册](docs/operations/deployment.md)
- [Research Studio 说明](docs/studio/overview.md)

## 许可证

本仓库使用 [Apache License 2.0](LICENSE) 许可证。
