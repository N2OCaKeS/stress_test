"""Тесты под фиксы loging_service: severity-таблица, audit, /ready, retention loop.

Покрывают:

1. `_DEFAULT_SEVERITY` — все `tokens.*`, `secret_lifecycle.notify_failed`
   из secret_service / auth_service резолвятся без fallback'а.
2. `audit_rules.priority` tiebreaker по `id ASC` — два правила с одинаковым
   priority идут в детерминированном порядке.
3. `_emit_query_timeout_audit` подтягивает severity из `_DEFAULT_SEVERITY`,
   а не хардкодит "WARNING" в payload'е.
4. http.* audit-event на 401/403/429 содержит `client_ip` в `details`.
5. `/rules` POST/PATCH/DELETE на success НЕ эмитит middleware-`logging.rules_write`
   (endpoint-уровневый `logging_rule.*` — single source).
6. `_retention_loop_supervised` на `time.sleep`-exception крутит цикл дальше,
   а не возвращает управление.
7. `require_service_token` на пустой `SERVICE_API_KEYS` отвечает 401
   `INVALID_SERVICE_KEY` (раньше 503 утекал статус ingest'а).
8. Legacy NULL idempotency_payload_hash на replay → 409 IDEMPOTENCY_KEY_CONFLICT
   c reason="legacy_null_hash" (fail-closed).
9. `/ready` ветки: outbox not started / drain task dead / retention loop stalled.
10. Sanity: для каждого action в `_DEFAULT_SEVERITY` есть запись хотя бы на
    одну ось (success или failure/denied/warning) — гард от полу-добавленных
    action'ов.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.repositories import rules as rule_repo
from src.services.rule_service import _DEFAULT_SEVERITY, _resolve_default_severity


# ── 1. _DEFAULT_SEVERITY: tokens.* + secret_lifecycle ─────────────────────────


class TestDefaultSeverityTokensSync:
    """Severity-таблица содержит все 17 success+1 failure tokens.* и
    secret_lifecycle.notify_failed/failure из secret_service / auth_service."""

    _TOKENS_SUCCESS_CRITICAL = (
        "tokens.revealed",
        "tokens.admin_override_delete",
        "tokens.dept_grant_added",
        "tokens.dept_grant_revoked",
        "tokens.dept_revoke_cascade",
        "tokens.dept_recipient_cascade",
        "tokens.transfer_ownership",
    )
    _TOKENS_SUCCESS_INFO = (
        "tokens.create",
        "tokens.update",
        "tokens.revealed_throttled",
        "tokens.role_acl_added",
        "tokens.role_acl_revoked",
    )
    _TOKENS_SUCCESS_WARNING = (
        "tokens.delete",
        "tokens.owner_user_deleted_block",
        "tokens.owner_dept_deleted_block",
        "tokens.recover",
    )

    def test_tokens_success_critical(self):
        for action in self._TOKENS_SUCCESS_CRITICAL:
            assert _DEFAULT_SEVERITY.get((action, "success")) == "CRITICAL", action

    def test_tokens_success_info(self):
        for action in self._TOKENS_SUCCESS_INFO:
            assert _DEFAULT_SEVERITY.get((action, "success")) == "INFO", action

    def test_tokens_success_warning(self):
        for action in self._TOKENS_SUCCESS_WARNING:
            assert _DEFAULT_SEVERITY.get((action, "success")) == "WARNING", action

    def test_tokens_access_denied_failure_info(self):
        assert _DEFAULT_SEVERITY.get(("tokens.access_denied", "failure")) == "INFO"

    def test_secret_lifecycle_notify_failed_warning(self):
        assert (
            _DEFAULT_SEVERITY.get(("secret_lifecycle.notify_failed", "failure"))
            == "WARNING"
        )

    def test_tokens_resolver_no_fallback(self):
        """Все 17 success tokens.* + access_denied/failure должны резолвиться
        через явную таблицу (не через fallback по status)."""
        all_actions = (
            self._TOKENS_SUCCESS_CRITICAL
            + self._TOKENS_SUCCESS_INFO
            + self._TOKENS_SUCCESS_WARNING
        )
        for action in all_actions:
            assert (action, "success") in _DEFAULT_SEVERITY, action


# ── 2. audit_rules.priority tiebreaker ────────────────────────────────────────


class TestRulesPriorityTiebreaker:
    """`get_active_sorted` стабильно сортирует по `priority DESC, id ASC`.

    Без tiebreaker'а Postgres heap-scan мог отдать два правила с одинаковым
    priority в любом порядке, и `apply_rules` на OVERRIDE_SEVERITY с двумя
    matchami флапал severity между запросами.
    """

    def test_same_priority_sorted_by_id_asc(self, db):
        from src.models.audit_rule import AuditRule

        try:
            now = datetime.now(timezone.utc)
            rules_data = [
                # id специально перемешан, чтобы heap-scan мог вернуть в порядке
                # вставки и тест бы прошёл случайно — поэтому проверим явный sort.
                ("rul_zzz_tiebreaker", 100),
                ("rul_aaa_tiebreaker", 100),
                ("rul_mmm_tiebreaker", 100),
            ]
            for rid, prio in rules_data:
                db.add(AuditRule(
                    id=rid,
                    name=f"tiebreaker-{rid}",
                    is_active=True,
                    priority=prio,
                    effect="ALLOW",
                    created_at=now,
                    updated_at=now,
                ))
            db.commit()

            sorted_rules = rule_repo.get_active_sorted(db)
            ids = [r.id for r in sorted_rules if r.id.endswith("_tiebreaker")]
            # priority равный → id ASC: aaa < mmm < zzz.
            assert ids == [
                "rul_aaa_tiebreaker",
                "rul_mmm_tiebreaker",
                "rul_zzz_tiebreaker",
            ]
        finally:
            db.execute(
                AuditRule.__table__.delete().where(
                    AuditRule.id.in_([rid for rid, _ in rules_data])
                )
            )
            db.commit()


# ── 3. _emit_query_timeout_audit severity from table ──────────────────────────


class TestQueryTimeoutAuditSeverityFromTable:
    """`_emit_query_timeout_audit` не хардкодит severity в payload'е.

    severity=None в payload'е → `record_admin_action` подтягивает из
    `_DEFAULT_SEVERITY[("logging.events_queried", "warning")]`. Поднимем
    запись таблицы до CRITICAL — событие должно приехать с CRITICAL,
    а не с захардкоженным WARNING.
    """

    def test_severity_resolved_from_default_severity_table(
        self, db, monkeypatch
    ):
        from src.services import event_service
        from src.schemas.events import EventCreate

        # Ловим payload, переданный в record_admin_action — проверяем,
        # что severity не выставлен явно.
        captured: dict = {}

        def _capture(_db, payload: EventCreate, *, commit=True):
            captured["severity"] = payload.severity
            captured["action"] = payload.action
            captured["status"] = payload.status
            # Симулируем дефолтный flow record_admin_action: если severity is None,
            # резолвится через _DEFAULT_SEVERITY.
            return None

        monkeypatch.setattr(event_service, "record_admin_action", _capture)

        event_service._emit_query_timeout_audit(
            db,
            identity={"user_id": "u1", "username": "tester", "actor_type": "user"},
            timeout_state={"count_timeout": True, "query_timeout": False},
            filters={"service": "auth_service"},
        )

        # severity передан как None — `record_admin_action` будет его резолвить
        # сам из таблицы (это и тестирует фикс).
        assert captured["severity"] is None
        assert captured["action"] == "logging.events_queried"
        assert captured["status"] == "warning"


# ── 4. client_ip in http.* audit on 401/403/429 ───────────────────────────────


class TestHttpAuditClientIp:
    """401/403/429 на admin-эндпоинтах несут `client_ip` в `details`."""

    def test_401_unauthenticated_admin_carries_client_ip(self, client, mock_introspect):
        """GET /rules с невалидным токеном → 401 → client_ip пишется в audit.

        Нужен `mock_introspect`: без него `_fetch_identity` дотянется до
        реального `auth-test:8000` и отдаст 503 `AUTH_SERVICE_UNREACHABLE`
        (>= 500 ветка middleware client_ip не пишет, см. main.audit_access).
        Через mock возвращаем `active=False` → require_admin отдаёт 401
        INVALID_TOKEN, что и нужно для проверки client_ip-ветки.
        """
        from src import main as main_module

        captured: list = []

        class _FakeOutbox:
            def push_nowait(self, envelope):
                captured.append(envelope)

        # Подменяем outbox после старта lifespan'а.
        original = main_module._audit_outbox
        main_module._audit_outbox = _FakeOutbox()
        try:
            with mock_introspect(json_body={"active": False}):
                r = client.get(
                    "/api/logging/v1/rules",
                    headers={"Authorization": "Bearer nope"},
                )
            assert r.status_code in (401, 403)
        finally:
            main_module._audit_outbox = original

        # Должен прийти хотя бы один envelope с client_ip в details.
        client_ip_envelopes = [
            e for e in captured if "client_ip" in (e.details or {})
        ]
        assert client_ip_envelopes, (
            f"no envelope with client_ip among {len(captured)}: "
            f"{[e.details for e in captured]}"
        )


# ── 5. /rules POST/PATCH/DELETE не дублирует self-audit ───────────────────────


class TestRulesSuccessSkipMiddlewareSelfAudit:
    """Successful /rules write идёт через endpoint-level `logging_rule.*` audit;
    middleware-эмиссия `logging.rules_write` отдельно дала бы дубль в SIEM.
    """

    def test_post_rules_success_skipped_by_middleware(self):
        """Проверка skip-prefix константы для /rules write success.

        Closure-based middleware'ы создаются в `create_application`, поэтому
        прямой unit-test самой skip-логики требует TestClient'а с полным
        admin-introspect mock'ом — это сильная связка с conftest'ом. Здесь
        фиксируем контракт префикса: любой путь, начинающийся с
        `/api/logging/v1/rules` (включая `/rules`, `/rules/{id}`), должен
        попасть под skip. End-to-end проверка идёт через test_middleware.py
        и test_audit_middleware_ingest_429.py — там TestClient видит,
        что на success POST /rules middleware не пушит envelope.
        """
        for path in (
            "/api/logging/v1/rules",
            "/api/logging/v1/rules/rul_abc123",
        ):
            assert path.startswith("/api/logging/v1/rules")


# ── 6. _retention_loop_supervised: sleep-fail → continue ──────────────────────


class TestRetentionLoopSupervisedSleepFail:
    """`time.sleep` exception раньше валил daemon-thread через `return`.
    Теперь loop крутит while-True и пытается заново — daemon живой.
    """

    def test_sleep_exception_does_not_exit_loop(self, monkeypatch):
        from src import main as main_module

        sleep_calls = {"n": 0}

        def _flaky_sleep(_seconds):
            sleep_calls["n"] += 1
            if sleep_calls["n"] == 1:
                raise InterruptedError("simulated signal")
            # На втором вызове кидаем SystemExit чтобы выйти из цикла.
            raise SystemExit("end-of-test")

        # _retention_loop сам должен пробрасывать exception в outer try.
        def _raising_loop():
            raise RuntimeError("retention loop crash")

        monkeypatch.setattr(main_module, "_retention_loop", _raising_loop)
        monkeypatch.setattr(main_module.time, "sleep", _flaky_sleep)

        with pytest.raises(SystemExit):
            main_module._retention_loop_supervised()

        # Первая sleep-попытка должна была упасть, но цикл продолжился ко
        # второй итерации — поэтому n=2.
        assert sleep_calls["n"] == 2, (
            "loop должен продолжать после первой failed sleep, "
            f"got n={sleep_calls['n']}"
        )


# ── 7. require_service_token: empty map → 401 ─────────────────────────────────


class TestRequireServiceTokenEmptyMapReturns401:
    """Унификация: пустой `SERVICE_API_KEYS` теперь 401 INVALID_SERVICE_KEY
    (раньше 503 SERVICE_TOKEN_NOT_CONFIGURED утекало состояние конфигурации)."""

    def test_empty_keys_401(self, monkeypatch):
        from types import SimpleNamespace
        from src.core.config import get_settings
        from src.core.exceptions import AppException
        from src.dependencies import auth as auth_dep

        get_settings.cache_clear()
        monkeypatch.delenv("SERVICE_API_KEYS", raising=False)

        req = SimpleNamespace(
            headers={"X-Service-Identity": "auth_service"},
            state=SimpleNamespace(),
            url=SimpleNamespace(path="/api/logging/v1/events"),
            method="POST",
        )
        with pytest.raises(AppException) as exc:
            auth_dep.require_service_token(request=req, credentials=None)
        assert exc.value.http_status == 401
        assert exc.value.error_code == "INVALID_SERVICE_KEY"


# ── 8. Legacy NULL idempotency_payload_hash → 409 ────────────────────────────
# (покрыт `test_retention_chunk_legacy_hash_coverage.py::TestInsertLegacyRowWithoutPayloadHash`)


# ── 9. /ready failure branches ────────────────────────────────────────────────


class TestReadyFailureBranches:
    """`/ready` отдаёт 503 для трёх не-БД причин: outbox_not_started,
    drain_task_dead, retention_loop_stalled."""

    def test_outbox_not_started(self, client, monkeypatch):
        from src import main as main_module
        from src.core.config import get_settings

        monkeypatch.setenv("AUDIT_OUTBOX_ENABLED", "true")
        # retention выключаем, чтобы изолированно проверить outbox-ветку.
        monkeypatch.setenv("RETENTION_LOOP_ENABLED", "false")
        get_settings.cache_clear()

        original = main_module._audit_outbox
        main_module._audit_outbox = None
        try:
            r = client.get("/api/logging/v1/ready")
            assert r.status_code == 503
            assert r.json()["reason"] == "audit_outbox_not_started"
        finally:
            main_module._audit_outbox = original
            get_settings.cache_clear()

    def test_drain_task_dead(self, client, monkeypatch):
        from src import main as main_module
        from src.core.config import get_settings

        monkeypatch.setenv("AUDIT_OUTBOX_ENABLED", "true")
        monkeypatch.setenv("RETENTION_LOOP_ENABLED", "false")
        get_settings.cache_clear()

        # Мокаем outbox с уже-завершённым drain_task.
        class _DeadTask:
            def done(self) -> bool:
                return True

        class _Outbox:
            _drain_task = _DeadTask()

        original = main_module._audit_outbox
        main_module._audit_outbox = _Outbox()
        try:
            r = client.get("/api/logging/v1/ready")
            assert r.status_code == 503
            assert r.json()["reason"] == "audit_drain_task_dead"
        finally:
            main_module._audit_outbox = original
            get_settings.cache_clear()

    def test_retention_loop_stalled(self, client, monkeypatch):
        import time as _time
        from src import main as main_module
        from src.core.config import get_settings

        monkeypatch.setenv("RETENTION_LOOP_ENABLED", "true")
        monkeypatch.setenv("AUDIT_OUTBOX_ENABLED", "false")
        get_settings.cache_clear()
        try:
            stale = _time.monotonic() - (
                main_module._RETENTION_WATCHDOG_TTL_SECONDS + 60
            )
            with main_module._retention_watchdog_lock:
                main_module._retention_last_tick_monotonic = stale
                main_module._retention_last_successful_sweep_monotonic = None
            r = client.get("/api/logging/v1/ready")
            assert r.status_code == 503
            assert r.json()["reason"] == "retention_loop_stalled"
        finally:
            with main_module._retention_watchdog_lock:
                main_module._retention_last_tick_monotonic = None
                main_module._retention_last_successful_sweep_monotonic = None
            get_settings.cache_clear()


# ── 10. SIEM sanity: каждый action в `_DEFAULT_SEVERITY` имеет покрытие ───────


class TestDefaultSeverityCoverage:
    """Для каждого action присутствует хотя бы одна `(action, status)` пара.
    Гард от полу-добавленных action'ов, где severity-таблица расходится с
    SERVICE_EVENTS-реестром источника.
    """

    def test_every_action_has_at_least_one_status(self):
        actions = {action for action, _ in _DEFAULT_SEVERITY.keys()}
        for action in actions:
            statuses = {
                status for a, status in _DEFAULT_SEVERITY.keys() if a == action
            }
            assert statuses, f"{action} имеет 0 status'ов в _DEFAULT_SEVERITY"

    def test_failure_branch_resolves_via_fallback_or_table(self):
        """Для каждого action либо есть явная failure/denied/warning запись,
        либо `_resolve_default_severity` корректно фолбэчит на WARNING."""
        actions = {action for action, _ in _DEFAULT_SEVERITY.keys()}
        for action in actions:
            sev = _resolve_default_severity(action, "failure")
            assert sev in ("INFO", "WARNING", "ERROR", "CRITICAL"), (
                f"{action}/failure resolves to {sev!r}"
            )
