# secret_service · реестр тестов

**Стек:** pytest + `httpx.AsyncClient(ASGITransport)` + **реальный PostgreSQL** через `tests/docker-compose.test.yml`. Изоляция — per-test SAVEPOINT поверх внешней транзакции (зеркало `server_service/tests/conftest.py`). introspect-вызовы к `auth_service` подменены `_FAKE_INTROSPECT` dict'ом в `tests/unit/conftest.py` — токены тестов отдают predetermined identity без сети.

## Запуск

```bash
# Unit-тесты в Docker (CI / полная изоляция)
make test-secret

# Unit-тесты в devcontainer (быстрее, без Docker, postgres из devcontainer'а)
make test-dev-secret

# Один файл / тест
make test-dev TEST=secret_service/tests/unit/test_credentials_endpoints.py
make test-dev TEST=secret_service/tests/unit/test_secrets_service.py::TestEncryptDecrypt::test_round_trip
```

## Unit (~269 тестов)

| Зона | Файлы | Кол-во (≈) | Что покрывает |
|---|---|---|---|
| Smoke (Phase 1) | `tests/test_health.py` | 5 | `/health` / `/ready` / 404 / security headers / X-Request-ID. |
| Models + repo (Phase 2) | `tests/unit/test_models_credential.py`, `test_models_role_acl.py`, `test_models_dept_grant.py` | ~33 | CHECK invariants (scope ↔ owner_user_id/owner_dept_id), UNIQUE constraints, envelope-pattern для `secret_encrypted`. |
| Encryption (Phase 3) | `tests/unit/test_secrets_service.py` | ~35 | AES-256-GCM encrypt/decrypt round-trip, AAD swap-resistance, version-roundtrip, HKDF deriviation, key-version fallback (v1 legacy / v2 HKDF), `ENCRYPTION_KEY_MISSING`, `PLAINTEXT_TOO_LARGE`, `ENCRYPT_INPUT_INVALID`, `DECRYPT_FAILED`. |
| Auth + audit (Phase 4) | `tests/unit/test_auth_identity.py`, `test_audit_service.py`, `test_audit_events.py`, `test_middleware_audit.py`, `test_middleware_https_guard.py`, `test_lockout_service.py`, `test_reveal_throttle.py` | ~50 | introspect happy/timeout/auth_unavailable, `secret_service in allowed_services`, audit-emit fail-loud, audit-events `_DEFAULT_SEVERITY` coverage (каждый action имеет severity-default), middleware `http.*` outcomes, HTTPS-guard в prod, lockout per-actor на denied-failures, reveal-throttle 5-мин окно (первый — CRITICAL, повторные — INFO+`count`). |
| Access policy + repos (Phase 5) | `tests/unit/test_access_service.py`, `test_repositories_credentials.py`, `test_repositories_role_acls.py`, `test_repositories_dept_grants.py` | ~66 | scope-зависимые проверки (`personal` / `department` / `cross_department`), reader/operator vs service_admin vs account_admin, last-resort `SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT` / `CREDENTIAL_BLOCKED` / `CREDENTIAL_ACCESS_DENIED`, repo CRUD + курсорная пагинация + filter'ы. |
| HTTP endpoints (Phase 6) | `tests/unit/test_credentials_endpoints.py`, `test_role_acl_endpoints.py`, `test_dept_grant_endpoints.py`, `test_internal_endpoints.py` | ~57 | CRUD credentials + reveal/transfer/recover, RoleACL CRUD, DeptGrant CRUD, internal lifecycle-эндпоинты под internal bearer. Error envelopes по каталогу `API_ENDPOINTS.md`. |
| Lifecycle + sweep (Phase 7) | `tests/unit/test_lifecycle_handle_user_deleted.py`, `test_lifecycle_handle_dept_deleted.py`, `test_lifecycle_handle_dept_revoke.py`, `test_sweep_expired_blocked.py` | ~23 | Cascade при `delete_user` (blocked / orphan delete), `delete_dept` (owner blocked + recipient cascade), `revoke department_service_access` cascade, sweep hard-delete за `BLOCKED_RETENTION_DAYS`. |

## Integration (Phase 8)

Кросс-сервисные тесты `tests/integration/` появятся в Phase 8: реальный auth_service в Docker + secret_service + secret-postgres, токен issuance через `/api/auth/v1/login`, full-cycle CRUD/reveal сценарии, проверка lifecycle-callback'ов из auth_service в `/internal/lifecycle/*`. Точное количество — после реализации.

## Инфраструктура

`tests/conftest.py` (root) + `tests/unit/conftest.py`:

- **Дефолтные env'ы** — `DATABASE_URL`, `AUTH_SERVICE_URL`, `SECRET_ENCRYPTION_KEY`, `SECRET_ENCRYPTION_KEY_VERSION=2`, `HKDF_SALT_HEX`, `APP_ENV=test`, `SWEEP_ENABLED=false`. Settings проходит валидаторы без сети.
- **per-test SAVEPOINT** — внешняя транзакция, SAVEPOINT внутри, ROLLBACK после теста.
- **`client`** — `AsyncClient(ASGITransport(app))`, базовый `http://test`.
- **`_FAKE_INTROSPECT`** — dict `token → identity payload`, подменяет httpx-call к auth_service.
- **Token-фабрики** — `make_token`, `account_admin_token`, `service_admin_token_a`, `dep_admin_token_a`, `reader_token_a`, `operator_token_a` (и `_b` вариант для cross-dep сценариев).
- **Cred-фабрики** — `make_personal_cred`, `make_dept_cred`, `make_cross_dep_cred`, `make_blocked_cred`.

## Изоляция

- SAVEPOINT-обёртка вокруг каждой `client`-фикстуры (как в `server_service`).
- Reveal-throttle и lockout-counter сбрасываются между тестами (in-memory state pinned к `app.state`).
- Sweep-loop выключен в test-env (`SWEEP_ENABLED=false`); тесты дёргают `sweep_service.sweep_expired_blocked()` напрямую.
- `audit_service.emit` мокается в большей части тестов; интеграционные тесты, которые проверяют сам audit-emit, поднимают локальный httpx-mock для `/api/logging/v1/events`.
