# Sandbox Runtime Comparison for Durable Broker State

Reviewed: 2026-07-12

## Public patterns reviewed

| Project | Relevant pattern | Adopted boundary | Not adopted |
|---|---|---|---|
| [OpenHands Software Agent SDK](https://github.com/OpenHands/software-agent-sdk) | A modular agent SDK separates the agent integration from its execution environment. | Research Forge keeps Docker behind a narrow `SandboxExecutor` port and the Unix broker process. | No SDK, tool ecosystem, or agent runtime is imported. |
| [PaperBench](https://github.com/openai/frontier-evals) | Agent rollout, fresh reproduction, and grading are separate container phases. | Broker bytes are persisted before Application finalization; Bundle/CAS remain separate later facts. | Research Forge does not claim PaperBench-scale independent grading. |
| [DeerFlow provisioner](https://github.com/bytedance/deer-flow/tree/main/docker/provisioner) | Stable sandbox IDs make create/status/delete idempotent and permit lifecycle inspection. | An Operation ID hashes to a deterministic Docker name and state directory; labels bind the immutable request hash. | Kubernetes, NodePort services, and cluster-level persistence are out of scope. |
| [Agent Zero](https://github.com/agent0ai/agent-zero) | Docker-backed runtime keeps application/runtime separation and persistent user data explicit. | Broker State is a dedicated local recovery directory, not implicit process memory. | Its broad agent, desktop, and plugin features are unrelated to the bounded VS-001 runtime. |

## Research Forge decision

One Operation has one deterministic container name (`rf-<sha256(operation-id)[:20]>`) and one private Broker State directory. The broker writes immutable request binding and completed result bytes with staged `fsync` plus atomic replacement. A fresh broker validates those bytes before returning them. PostgreSQL still owns Operation status, Git owns code, CAS owns registered artifacts, and Broker State only bridges the crash window before DB/CAS finalization.

## Trade-offs and rollback

The state directory adds bounded local disk use and must be backed up only with the host recovery material; it is not a business database. A corrupt state entry fails closed rather than rerunning a possibly completed Operation. Operators may remove only an entry whose PostgreSQL Operation has been terminally reconciled and whose registered CAS artifacts have been verified. Rolling back this code requires retaining the state directory until all in-flight Operations have either finalized or been cancelled.
