# Sandbox Durability Implementation Result

Reviewed: 2026-07-12

## Implemented

- Each sandbox operation derives exactly one Docker name: `rf-<sha256(operation-id)[:20]>`. Containers are labelled with both `research-forge.operation` and an immutable request hash; Docker's anonymous `--rm` lifecycle is not used.
- Broker State is a host-local recovery directory controlled only by the sandbox broker. It persists `request.json`, `result.json`, `stdout.bin`, `stderr.bin`, declared output bytes, and a terminal `cancelled.json` marker in a hashed operation directory. Request creation is exclusive across processes; writes use a staging file, file `fsync`, atomic replacement where replacement is appropriate, and directory `fsync`.
- A new broker validates schema version, operation ID, immutable request payload/hash, payload sizes, and SHA-256 values before returning a durable `SandboxResult`. Unsafe directories, escaping paths, and symbolic-link traversal fail closed.
- A broker first reloads durable completed state, then rediscovers an existing named container by immutable label. Concurrent brokers converge on the same container; a different request for the same operation ID is rejected.
- Timeouts and cancellation use `docker stop --time 2`, then `docker kill` if necessary, then `docker rm -f`. A terminal-state file lock serializes cancellation against result persistence across broker processes: a cancellation that wins first prevents any success result; a result that is already durable remains completed. A later retry removes an orphaned named container only after its immutable label is verified.
- Docker log collection drains both pipes while retaining no more than the requested total log budget.

## Changed files

- `backend/research_forge/adapters/outbound/sandbox/broker_state.py` — durable state schema, exclusive request binding, completion/cancellation terminal-state arbitration, atomic persistence, integrity validation, and containment/link protections.
- `backend/research_forge/adapters/outbound/sandbox/docker_broker.py` and `unix_broker.py` — deterministic named-container lifecycle, recovery, bounded logs, idempotent cancellation, and lossless empty-byte protocol fields.
- `backend/research_forge/bootstrap/production.py`, `backend/research_forge/bootstrap/sandbox_broker.py`, and `deploy/research-forge/research-forge.env.example` — `RF_BROKER_STATE_ROOT` production composition.
- `backend/tests/research_forge/test_broker_state.py`, `test_unix_sandbox_broker.py`, `test_sandbox_boundary.py`, and `test_docker_e2e.py` — durable-state/unit-protocol coverage plus Linux Docker lifecycle/process coverage without `chmod 777`.
- `docs/review/SANDBOX_RUNTIME_COMPARISON.md`, ADR-003, and the deployment runbook — external comparison, state ownership, GC, and rollback policy.

The exact `python -m mypy backend/research_forge` gate also exposed pre-existing typing-only issues. `backend/research_forge/adapters/outbound/persistence/sqlalchemy_uow.py` now explicitly treats ORM update results as `CursorResult`, and `mypy.ini` disables only the untyped-import diagnostic for the one supported `jsonschema` consumer. Neither changes runtime behavior.

## External research conclusions

The detailed source links and comparison are in [SANDBOX_RUNTIME_COMPARISON.md](SANDBOX_RUNTIME_COMPARISON.md). OpenHands supports a narrow execution-environment boundary; PaperBench separates rollout, reproduction, and grading; DeerFlow demonstrates stable sandbox identity and idempotent lifecycle management; and Agent Zero keeps application and Docker runtime responsibilities separate. Research Forge adopts only stable identity, guarded persistence, and isolation boundaries; it does not import their agent runtimes, service topology, or licenses.

## Recovery algorithm

1. Bind an operation ID to one canonical request payload/hash using exclusive durable creation.
2. Return a validated durable result if one exists; safely remove a matching leftover container.
3. Otherwise inspect the deterministic container name. Matching running/exited containers are adopted; a mismatched immutable label fails closed.
4. Wait, collect bounded logs and declared output bytes, persist all result bytes and hashes, then remove the container.
5. On cancellation or timeout, stop for two seconds, kill if still present, then force-remove. No success result is persisted on that path.

## Added test coverage

- Durable state recovery from a fresh store, input conflict, request/result tamper detection, symlink rejection, and cancellation winning the terminal-state race.
- Unix broker round trip with empty stdout/stderr, preserving valid zero-byte Docker logs.
- Command construction and cancellation order.
- Real Unix-socket Broker A/B recovery after a completed Docker operation.
- Real Broker A interruption while the container runs, then Broker B adoption.
- Two independently started broker processes converging on one concurrent operation.
- Same operation ID with different input rejection, cancellation cleanup, and timeout cleanup.

## Evidence from this workspace

| Check | Result |
| --- | --- |
| `python -m pytest backend/tests/research_forge -q` | `69 passed, 9 skipped` on the clean branch worktree before the final CI-only fixes |
| `python -m ruff check backend/research_forge backend/tests/research_forge` | Passed |
| `python -m mypy backend/research_forge` | Passed: 94 source files |
| CI-equivalent mypy command | Passed: 94 source files |
| `python backend/scripts/check_research_forge_secrets.py` | Passed |
| Frozen evaluator | `31/31` passed; manifest SHA-256 `7e66c8a9724e0208f9055dae8788d0c83577a36252b1e8f3bdd67202bf398aa2` |
| GitHub Actions run `29191694541` | All jobs passed: architecture, Linux Docker, PostgreSQL, supply chain, and frozen evaluation |

The frozen evaluation report generated locally is intentionally not committed; CI publishes equivalent reports as build artifacts.

## Linux Docker acceptance gate

The current host is native Windows without Docker or an installed WSL distribution. The Docker-specific tests therefore skipped locally; they were not represented as successful Docker execution. The clean GitHub Actions Ubuntu run executed the required gate successfully: [run 29191694541](https://github.com/chasen2041maker/research-forge/actions/runs/29191694541), `7 passed in 24.66s`.

On another Linux/WSL2 host with Docker Engine, run:

```bash
python -m pytest backend/tests/research_forge/test_docker_e2e.py -q -m docker
```

The gate covers normal completion followed by a broker restart, two real broker processes recovering completed work, two brokers converging on one concurrently started container, process loss while a container is still running, cancellation, timeout cleanup, and an immutable-input conflict.

## State ownership, GC, and remaining risks

Broker State is recovery material, not a second business database: PostgreSQL owns Operation status, CAS owns registered artifacts, and Git owns commits. An operator may remove an operation directory only after terminal PostgreSQL reconciliation, verified CAS registration, and no unpublished Outbox work; the deployment runbook records that audited procedure.

Remaining operational risks are deliberately fail-closed: corrupt recovery bytes or a request/hash mismatch require investigation rather than an automatic re-run; local disk capacity and backup policy remain host-operator responsibilities; and a multi-host deployment needs host-affine routing or a separately designed shared state store with equivalent atomic-file guarantees.

## Release-gate decision

**Satisfied for this branch.** The clean GitHub Actions run linked above passed the required Linux Docker gate, architecture, PostgreSQL, supply-chain, and frozen-evaluation jobs. The local Windows host remains unsuitable for formal Docker acceptance, but it is no longer the source of release-gate evidence.
