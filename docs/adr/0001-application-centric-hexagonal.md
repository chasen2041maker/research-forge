---
title: Application-centric hexagonal architecture
status: accepted-implemented
scope: research_forge
---

# ADR-001：采用 Application-Centric Hexagonal Architecture

## Status

Accepted — implemented for `research_forge`. This ADR does not impose a rewrite of the legacy
Research Studio package.

## Context

上一版把 Runtime 作为独立顶层层次，并允许它依赖 Application Ports。这可能让 LangGraph/Agent 同时承担决策与副作用编排，形成第二个 Application 层。

## Decision

采用以下物理结构：

```text
domain/
application/
adapters/
  inbound/
  decision/
  outbound/
bootstrap/
```

- Domain：业务状态机与不变量；
- Application：唯一用例、事务、Policy、幂等和副作用协调中心；
- Inbound Adapter：API/CLI/Worker；
- Decision Adapter：LangGraph/Prompt/LLM，只实现 `DecisionEngine`；
- Outbound Adapter：DB/Git/CAS/Sandbox/Queue/Research；
- Bootstrap：唯一 Composition Root。

`DecisionEngine` 只返回 `ActionProposal`，不得持有或调用任何副作用 Port。

## Dependency Rules

```text
Inbound → Application → Domain
Decision → DecisionEngine Port/DTO
Outbound → Application Ports/DTO + Domain Public Types
Bootstrap → all
```

禁止：

- Decision Adapter import Git/Sandbox/Artifact/Queue/Persistence；
- Inbound Adapter import Outbound Adapter；
- Application import Adapter；
- Domain import 框架；
- Bootstrap 被其他层 import。

## Alternatives

### 保留独立 Runtime 层

拒绝。容易产生两个编排中心。

### 所有 Agent 逻辑放 Application

部分拒绝。Application 定义 Port 和使用时机，但 LangGraph/LLM 是可替换实现，应放 Decision Adapter。

## Consequences

优点：

- Application 是唯一副作用协调中心；
- LLM 决策实现可以替换；
- 第一条无 LLM Slice 不需要 Decision Adapter；
- 依赖边界可用 Import Test 自动验证。

代价：

- 需要 DTO/Port；
- Adapter 与 Domain/ORM 之间需要映射；
- Bootstrap 需要显式工厂。

## Validation

- import-linter 核心规则通过；
- Decision Adapter 无 Side-effect Import；
- API/Worker 只调用 Use Case；
- 第一条 Slice 在没有 `adapters/decision` 时可完整运行。

## Rollback

若 DecisionEngine 只存在一个简单实现且长期不需替换，可将其实现移动到 Application 内部服务，但仍禁止其直接执行副作用。
