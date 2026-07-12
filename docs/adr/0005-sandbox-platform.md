---
title: Sandbox platform
status: accepted-partial
scope: research_forge
---

# ADR-005：v0.1 正式执行平台限定为 Linux/WSL2

## Status

Accepted — partially implemented. Linux/WSL2 and the dedicated broker are the formal boundary, but
broker completed-result recovery persists checksummed records across restart and the VS-001 runtime supports the documented
prebuilt/offline subset rather than every planned environment-preparation mode.

## Context

Research Forge 当前在 Windows 开发，但 Docker、seccomp、AppArmor、路径、Named Pipe 与宿主权限边界在 Windows 和 Linux 上不同。不能宣称两者安全等价。

## Decision

- Windows 原生：允许文档、前端和普通开发；
- WSL2/Linux：正式 Worker、Broker、Sandbox 和发布安全验收；
- 威胁模型不防御已经控制本地用户账户的攻击者；
- API/普通 Worker 不访问 Docker Socket；
- 独立 Sandbox Broker 是唯一 Docker 权限持有者。

## Two-stage Execution

### ENV_PREPARE

- 使用预构建镜像或固定 Lockfile；
- 网络仅允许固定包源/Wheelhouse；
- 输出不可变 Image/Environment Digest；
- v0.1 优先预构建镜像。

### RUN

- 完全离线；
- 使用已冻结环境；
- 固定命令参数数组；
- 不通过 Shell；
- 输出受大小限制的日志和 Artifact。

## Container Baseline

- 非 root；
- Read-only RootFS；
- `cap_drop=ALL`；
- `no-new-privileges`；
- Docker 默认 seccomp；
- 固定镜像 Digest；
- PID/CPU/内存/磁盘/时间限制；
- 独立 Workspace Mount；
- 默认 `network=none`；
- 无 Docker Socket；
- stdout/stderr 上限；
- 安全解压；
- 禁止不可信 Pickle。

## Should Have

- Rootless Docker/Podman；
- AppArmor/SELinux；
- Wheelhouse；
- SBOM/Dependency/License Scan；
- Broker 本地 ACL。

## Later

- gVisor/Kata/microVM；
- eBPF Runtime Detection；
- 跨平台安全等价层。

## Secret Handling

不建设独立 Vault。定义 `SecretProvider` Port：

- Bootstrap 从环境或本地 Keyring 读取；
- Outbound Adapter 按名称短时取得；
- Secret 不进入 Domain/Application DTO/Prompt/Checkpoint；
- Canary 覆盖日志、异常、Trace、Artifact 和 Bundle。

## Validation

- Network None；
- Read-only RootFS；
- Capabilities/PID/Memory/Timeout；
- Path/Symlink/Archive；
- Canary Secret；
- Broker 是唯一 Docker Client；
- 正式安全 Gate 只在 Linux/WSL2 运行。

## Rollback

若独立 Broker 在第一阶段阻塞开发，可以使用进程外最小 Broker 原型，但不得退回 API/Agent 直接持有 Docker Socket，也不得对外宣称安全完成。
