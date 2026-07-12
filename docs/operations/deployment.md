---
title: VS-001 deployment runbook
status: active
---

# Research Forge VS-001 deployment and recovery runbook

## Scope and trust boundary

This runbook deploys the implemented VS-001 baseline path: PostgreSQL source of truth, Redis Attempt transport, durable Outbox publishing, local CAS, pinned local Git fixtures, and a Linux Docker sandbox broker. It is intentionally not a generic container platform.

The API validates a frozen local repository path and persists that exact path in the Mission specification. Docker also receives that path as a bind-mount source. Therefore the API, publisher, and worker must run on the same Linux host filesystem; only PostgreSQL and Redis are composed as containers. Running the worker inside a conventional container would make the Docker daemon see a different bind-mount path and would break the pinned-workspace guarantee.

The `research-forge` user must not be a general administrator. Only `research-forge-sandbox-broker.service` is placed in the `docker` group. The API, publisher, and worker do not have Docker access; the worker can reach only the broker's fixed Unix-socket protocol.

## Prerequisites

- Linux or WSL2 for formal sandbox execution; native Windows is not an acceptance environment.
- Docker Engine and Docker Compose plugin for PostgreSQL and Redis.
- Python 3.13 virtual environment with `python -m pip install -r /opt/research-forge/deploy/research-forge/requirements.txt`.
- Python 3.11+, Git, and a dedicated Linux account named `research-forge`.
- An installed checkout at `/opt/research-forge`, a virtual environment at `/opt/research-forge/.venv`, and the host directories below owned by `research-forge`.

```bash
sudo install -d -o research-forge -g research-forge -m 0750 \
  /srv/research-forge/{repositories,papers,workspaces,cas}
sudo install -d -o research-forge -g research-forge -m 0750 /var/lib/research-forge/broker
sudo install -d -o root -g research-forge -m 0750 /etc/research-forge
```

Place each permitted source repository under `/srv/research-forge/repositories` and each registered paper artifact under `/srv/research-forge/papers`. The policy records every paper path relative to `RF_PAPER_ROOT`; startup rejects missing, symlinked, or root-escaping entries, and Mission creation recomputes its SHA-256 before work is queued. Repositories are read-only inputs for the service user; worktrees and CAS are the only writable execution roots.

## Configure policy and state services

1. Copy `deploy/research-forge/research-forge.env.example` to `/etc/research-forge/research-forge.env`, set a long random API token and the database password, then restrict it to `0640 root:research-forge`.
2. Copy `deploy/research-forge/execution-policy.example.json` to `/etc/research-forge/execution-policy.json`. Register the exact paper SHA-256 and a relative `paper_artifact_paths` entry for every paper, plus each approved immutable Docker image digest. Do not use image tags as policy keys.
3. Put `RF_POSTGRES_PASSWORD` in a root-readable Compose environment file outside the repository. Start only the durable dependencies:

```bash
docker compose --env-file /etc/research-forge/compose.env \
  -f /opt/research-forge/deploy/research-forge/compose.dependencies.yml up -d
```

4. Wait until both health checks pass, then run migrations from the checked-out release. `RF_DATABASE_URL` overrides the Alembic URL only for this command, keeping credentials out of `alembic.ini`.

```bash
set -a
. /etc/research-forge/research-forge.env
set +a
cd /opt/research-forge/backend
/opt/research-forge/.venv/bin/alembic upgrade head
/opt/research-forge/.venv/bin/python -m research_forge.bootstrap.runtime healthcheck
```

Before enabling service, verify that the image reference in policy resolves to the same digest. The sandbox broker rejects any digest not listed in that policy, and every Mission checks it again before a worktree is created.

## Install process roles

Install the five supplied units, reload systemd, and enable the Docker broker before the worker:

```bash
sudo cp /opt/research-forge/deploy/research-forge/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now research-forge-api research-forge-publisher research-forge-sandbox-broker research-forge-reconciler research-forge-worker
sudo systemctl status research-forge-api research-forge-publisher research-forge-sandbox-broker research-forge-reconciler research-forge-worker
```

Keep the API bound to `127.0.0.1:8080`. Put a TLS-terminating reverse proxy in front of it if remote reviewers need the Forge console. Proxy only the API endpoint, configure `RF_CORS_ORIGINS` to the exact console origin, and never expose PostgreSQL, Redis, or the Docker socket.

## Normal operation checks

```bash
curl --fail http://127.0.0.1:8080/healthz
sudo -u research-forge -H bash -lc '
  set -a; . /etc/research-forge/research-forge.env; set +a
  cd /opt/research-forge/backend
  /opt/research-forge/.venv/bin/python -m research_forge.bootstrap.runtime healthcheck
'
docker compose -f /opt/research-forge/deploy/research-forge/compose.dependencies.yml ps
journalctl -u research-forge-worker -u research-forge-publisher -u research-forge-reconciler -u research-forge-sandbox-broker --since "15 minutes ago"
```

The runtime healthcheck verifies PostgreSQL, Redis, and the broker Unix socket. Queue messages are only Attempt IDs. PostgreSQL remains the source of truth; the worker acknowledges a Redis message only after the complete Mission path has durably finished. The reconciler scans stale `PREPARED` and `EXECUTING` Operations every 30 seconds and atomically emits one Outbox redelivery for the original Attempt; recovery reuses its original idempotency key. During a sandbox run the worker renews its owned Attempt lease every 10 seconds and stops that monitor before finalization, so the persisted optimistic version cannot race the evidence write. A repeated delivery after a crash is expected. Operation idempotency, CAS hashes, and the broker's atomically published checksummed result records are durable protections: after restart, the broker reloads a matching completion before it can invoke Docker again. Include `RF_BROKER_STATE_ROOT` in backups with the workspace and CAS roots.

## Recovery, backup, and rollout

- **Publisher, reconciler, or worker restart:** restart the unit. Unpublished Outbox events remain in PostgreSQL, stale ledger operations are requeued by the reconciler, and unacknowledged Redis deliveries remain available for retry.
- **Cancellation or failed sandbox execution:** a cancellation request is durable first; the worker observes it, sends the bounded cancel operation to the broker, records `CANCELLED`, and then acknowledges the Attempt delivery without registering later artifacts. Inspect the Mission evidence and worker journal. Do not hand-edit worktrees, CAS bytes, operations, or approval records; cancel the Mission or create a new frozen Mission instead. Bundle replay rejects traversal, links, non-file members, excess member count, oversized members, excess extraction size, and compression-ratio bombs.
- **Unexpected repair Attempt:** the shipped production worker fails closed and leaves it unacknowledged because no LLM DecisionEngine is configured. Stop the worker, investigate the durable approval and policy, and deploy a separately reviewed repair worker before retrying. Do not point the production service at the test-only fixed-patch adapter.
- **Database backup:** use a consistent `pg_dump` from the Postgres container, then separately snapshot the CAS and workspace roots. A database-only restore cannot reproduce artifact bytes.

```bash
docker exec -t $(docker compose -f /opt/research-forge/deploy/research-forge/compose.dependencies.yml ps -q postgres) \
  pg_dump -U research_forge -Fc research_forge > research-forge-$(date +%F).dump
```

- **Release:** run the frozen evaluator and review its JSON report before changing units. Stop the worker first, apply migrations, restart API and publisher, then restart the worker. Roll back code only with a database-compatible release; never downgrade a live database until its backup and Alembic downgrade have been rehearsed on a restore.

```bash
cd /opt/research-forge
/opt/research-forge/.venv/bin/python backend/scripts/run_frozen_research_forge_eval.py \
  --output-dir /var/lib/research-forge/eval-reports
```

## Boundaries not represented as shipped production capability

VS-001 deliberately does not ship an LLM DecisionEngine, remote repository fetching, remote artifact fetching, or autonomous pull-request creation. A future repair worker must receive only the decision port and must preserve the existing patch hash, approval, lease, operation-ledger, offline sandbox, and evidence gates.
