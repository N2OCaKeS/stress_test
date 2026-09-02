# Integration tests — full DBOS stack

Shared integration / E2E test infrastructure. Spins up every DBOS service
(auth, logging, server, worker) against shared postgres + redis plus a real
openssh-server target, then runs pytest with rich fixtures for end-to-end
scenarios.

## Layout

```
tests/integration/
├── README.md                       # this file
├── docker-compose.test.yml         # full-stack E2E compose
├── init_test_db.sql                # creates the four *_db_test DBs
├── init_ssh_keys.sh                # one-shot: generates mgmt ed25519 keypair
├── conftest.py                     # pytest fixtures (shared across scenarios)
├── test_auth_logging.py            # cross-service audit tests (auth ↔ logging)
├── test_bot_lifecycle_audit.py     # bot lifecycle audit
└── test_groups_and_roles_audit.py  # roles audit
```

## Running

```bash
# Full E2E (all 4 services + openssh-server + worker)
make test-integration                  # bring up, run pytest, tear down

# Manual control
docker compose -f tests/integration/docker-compose.test.yml up -d --wait
docker compose -f tests/integration/docker-compose.test.yml run --rm test-runner
make logs-test-integration             # tail logs of all services
docker compose -f tests/integration/docker-compose.test.yml down -v

# Devcontainer mode (auth ↔ logging only, no docker)
# Server/worker/ssh-based fixtures will skip in this mode.
make test-dev-integration
```

## Stack

| service           | image                                   | url                                  |
|-------------------|-----------------------------------------|--------------------------------------|
| test-postgres     | postgres:16                             | test-postgres:5432 (4 DBs)           |
| test-redis        | redis:7-alpine                          | test-redis:6379                      |
| ssh-target        | lscr.io/linuxserver/openssh-server      | ssh-target:2222 (dbosroot / test-root-pw — sudo-capable bootstrap acct)|
| auth-service      | (built from auth_service/)              | http://auth-service:8000             |
| logging-service   | (built from loging_service/)            | http://logging-service:8001          |
| server-service    | (built from server_service/)            | http://server-service:8002           |
| server-worker     | (built from server_worker/)             | (no HTTP, taskiq)                    |
| init-ssh-keys     | alpine:3.20 (one-shot)                  | writes mgmt-keys volume              |
| seeder            | (auth image, runs scripts/seed_dev.py)  | one-shot                             |

Bootstrap order: postgres + redis → init-ssh-keys + ssh-target → logging →
auth → server → seeder → server-worker → test-runner.

The seeder generates the worker bot PAT and writes it to a shared
`bootstrap` volume; the worker reads it on startup. The
`init-ssh-keys` one-shot generates an ed25519 keypair in a `mgmt-keys`
volume mounted into both the worker (`/keys/id_ed25519`) and the test-runner
(read-only, for the `ssh_test_host` fixture).

## Fixtures (conftest.py)

See the long fixture map in `conftest.py` for full signatures and usage.
Quick reference for scenario authors:

| fixture                | scope    | purpose                                   |
|------------------------|----------|-------------------------------------------|
| `integration_stack`    | session  | sentinel that every backend is healthy    |
| `auth_client`          | session  | httpx.Client → auth_service               |
| `logging_client`       | session  | httpx.Client → logging_service (admin JWT)|
| `logging_service_client`| session | httpx.Client → logging_service (svc key)  |
| `server_client`        | session  | httpx.Client → server_service             |
| `admin_server_client`  | session  | server_client with admin Bearer pre-set   |
| `admin_token`          | session  | account_admin JWT                         |
| `loging_admin_token`   | session  | loging_admin JWT                          |
| `ssh_test_host`        | session  | dict: host/port/root_pw/mgmt_user/keys    |
| `ssh_session`          | function | paramiko helper (root or mgmt key)        |
| `auth_db_engine`       | session  | SQLAlchemy Engine → auth_db_test          |
| `loging_db_engine`     | session  | SQLAlchemy Engine → logging_db_test       |
| `server_db_engine`     | session  | SQLAlchemy Engine → server_db_test        |
| `worker_db_engine`     | session  | SQLAlchemy Engine → worker_db_test        |
| `reset_state`          | function | truncates per-scenario tables             |
| `make_user(...)`       | session  | factory                                   |
| `make_bot(...)`        | session  | factory                                   |
| `login_token(u, p)`    | session  | helper                                    |
| `pat_token(...)`       | session  | helper                                    |
| `wait_for_event(...)`  | -        | module-level: poll /events                |

## Gotchas

- `audit_events` table — not `events`. Direct DB queries should target
  `audit_events` (defined in `loging_service/src/models/audit_event.py`).
- Audit is fire-and-forget: poll via `wait_for_event(...)` or accept a brief
  delay before `SELECT FROM audit_events`.
- The openssh-server image takes ~5s to fully initialise SSH (look for
  `[ls.io-init] done` in its logs). The healthcheck waits on the TCP port;
  paramiko connections may need one retry on the very first scenario.
- The worker reads its bot PAT from `/shared/.worker_pat` written by the
  seeder. If you bypass the seeder, set `WORKER_BOT_TOKEN` in the worker's
  environment directly.
- `reset_state` truncates `audit_events` — only request it for scenarios
  that need a clean audit slate. Tests that assert "service.started was
  emitted" should NOT use `reset_state`.
- The `mgmt-keys` volume is generated once and reused across compose runs.
  To force regeneration, `docker compose ... down -v`.
