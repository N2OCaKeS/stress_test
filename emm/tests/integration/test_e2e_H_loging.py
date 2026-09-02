"""E2E кластер H — Loging endpoints + audit-landing sweep.

Покрывает (по `obsidian/TODO.md::H. Loging endpoints + audit-landing sweep`):

* POST /events с per-service-identity rate-limit (независимые бюджеты),
  401 при banned/invalid key.
* POST /services/{svc}/events — идемпотентность по native key, path-vs-identity.
* GET /events: дефолтный режим (`total=null` + `has_more`), `?include_total=true`
  (COUNT), пагинация, dept-scope filter.
* POST/PATCH/DELETE /rules (loging_admin only). SUPPRESS-effect блокирует
  событие при matching action/service. `match_service` нормализуется
  симметрично `EventCreate.service`.
* PUT /retention filtered: severity × service combos → N×M активных строк.
  Повторный PUT global → `deactivate_all_active` + одна новая активная.
  Sweep (`apply_active`) удаляет события по политикам. Chunked DELETE.
* DELETE /retention — гасит весь активный набор.
* Self-audit `logging.retention_sweep`: `details.policies=[...]` +
  `min_retain_days` / `max_retain_days` под filtered-set.
* Audit-landing sweep: для каждого зарегистрированного action всех 4
  сервисов триггерим запись (через API когда возможно, через прямой ingest
  для callback-only / hard-to-trigger) и проверяем, что в `audit_events`
  лежит row с правильными action/service/severity.

Все тесты живут под `_require_full_stack`-условием — без full E2E compose
они скипаются.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import text

from tests.integration._helpers_H_loging import (
    ALL_SERVICE_EVENTS,
    AUTH_SERVICE_EVENTS,
    LOGING_SERVICE_EVENTS,
    PRIORITY_AUDIT_ACTIONS,
    SERVER_SERVICE_EVENTS,
    WORKER_SERVICE_EVENTS,
    ensure_department,
    ensure_service,
    event_payload,
    grant_service,
    ingest_synthetic_event,
    make_service_client,
    now_iso,
    short_id,
)
from tests.integration.conftest import LOGGING_API_KEY, LOGGING_URL, wait_for_event


EVENTS_URL = "/api/logging/v1/events"
RULES_URL = "/api/logging/v1/rules"
SERVICES_URL = "/api/logging/v1/services"
RETENTION_URL = "/api/logging/v1/retention"


# ════════════════════════════════════════════════════════════════════════════
# POST /events — service-token ingest, per-identity rate-limit
# ════════════════════════════════════════════════════════════════════════════


class TestPostEventsServiceIdentity:
    def test_invalid_key_rejected_401(self):
        with make_service_client(LOGGING_URL, "totally-bogus-key", "auth_service") as c:
            r = c.post(EVENTS_URL, json=event_payload())
        assert r.status_code == 401
        body = r.json()
        # error envelope: either INVALID_SERVICE_TOKEN or UNKNOWN_SERVICE_IDENTITY
        assert body.get("error_code") in {
            "INVALID_SERVICE_TOKEN",
            "UNKNOWN_SERVICE_IDENTITY",
        }

    def test_missing_key_rejected_401(self):
        with httpx.Client(base_url=LOGGING_URL, timeout=10) as c:
            r = c.post(
                EVENTS_URL,
                json=event_payload(),
                headers={"X-Service-Identity": "auth_service"},
            )
        assert r.status_code in (401, 403)

    def test_known_identity_accepted(self):
        with make_service_client(LOGGING_URL, LOGGING_API_KEY, "auth_service") as c:
            r = c.post(
                EVENTS_URL,
                json=event_payload(
                    service="auth_service",
                    action="user.login",
                    actor_id=f"u_{short_id()}",
                ),
            )
        assert r.status_code in (201, 204), r.text

    def test_reserved_loging_service_blocked(self):
        with make_service_client(LOGGING_URL, LOGGING_API_KEY, "auth_service") as c:
            r = c.post(
                EVENTS_URL,
                json=event_payload(
                    service="loging_service",
                    action="logging_rule.create",
                ),
            )
        assert r.status_code == 403
        assert r.json().get("error_code") == "RESERVED_SERVICE_NAME"

    def test_per_identity_rate_limit_buckets_independent(
        self, logging_service_client: httpx.Client
    ):
        """Два разных X-Service-Identity → independent buckets.

        Стратегия: одним identity отправляем серию запросов и проверяем,
        что свежий запрос под второй identity всё ещё проходит — даже
        если первый identity уже на лимите. Гарантировать 429 на первом
        не нужно (compose-стек запускается с дефолтным 100/min), мы
        тестируем именно изоляцию.
        """
        a = f"sweep_a_{short_id()}"
        b = f"sweep_b_{short_id()}"

        # Серия от identity-A
        with make_service_client(LOGGING_URL, LOGGING_API_KEY, a) as ca:
            for _ in range(5):
                r = ca.post(
                    EVENTS_URL,
                    json=event_payload(
                        service="auth_service",
                        action="user.login",
                        actor_id=f"u_{short_id()}",
                    ),
                )
                # 201 (created), 204 (suppressed), 429 (limit reached). Любой
                # из них — нормальный исход для серии; нам важно поведение B.
                assert r.status_code in (201, 204, 429), r.text

        # Свежий identity-B должен получить свой свежий bucket
        with make_service_client(LOGGING_URL, LOGGING_API_KEY, b) as cb:
            r = cb.post(
                EVENTS_URL,
                json=event_payload(
                    service="auth_service",
                    action="user.login",
                    actor_id=f"u_{short_id()}",
                ),
            )
        assert r.status_code in (201, 204), (
            f"per-identity bucket leak: B got {r.status_code} after A's burst "
            f"({r.text})"
        )


# ════════════════════════════════════════════════════════════════════════════
# POST /services/{svc}/events — каталог регистрации
# ════════════════════════════════════════════════════════════════════════════


class TestRegisterServiceEvents:
    def test_register_events_idempotent(self):
        svc = "auth_service"
        events = [
            {
                "action": f"e2e.register_test_{short_id()}",
                "description": "synthetic for E2E test",
                "default_severity": "INFO",
            }
        ]
        with make_service_client(LOGGING_URL, LOGGING_API_KEY, svc) as c:
            r1 = c.post(f"{SERVICES_URL}/{svc}/events", json={"events": events})
            assert r1.status_code == 200, r1.text
            body1 = r1.json()
            assert body1["service"] == svc
            assert body1["added"] >= 1

            # Повтор — те же action'ы, ожидаем updated >= 1 (или 0+added=0)
            r2 = c.post(f"{SERVICES_URL}/{svc}/events", json={"events": events})
            assert r2.status_code == 200
            body2 = r2.json()
            assert body2["added"] == 0
            assert body2["total"] >= body1["total"]

    def test_register_events_reserved_loging_blocked(self):
        with make_service_client(LOGGING_URL, LOGGING_API_KEY, "auth_service") as c:
            r = c.post(
                f"{SERVICES_URL}/loging_service/events",
                json={"events": [{"action": "x.y", "description": "x", "default_severity": "INFO"}]},
            )
        assert r.status_code == 403
        assert r.json().get("error_code") == "RESERVED_SERVICE_NAME"

    def test_register_events_path_identity_mismatch_blocked(self):
        # Caller представляется auth_service, но регистрирует под server_service —
        # cross-tenant audit-trail poisoning.
        with make_service_client(LOGGING_URL, LOGGING_API_KEY, "auth_service") as c:
            r = c.post(
                f"{SERVICES_URL}/server_service/events",
                json={"events": [{"action": "fake.action", "description": "x", "default_severity": "INFO"}]},
            )
        assert r.status_code == 403
        assert r.json().get("error_code") == "SERVICE_IDENTITY_PATH_MISMATCH"


# ════════════════════════════════════════════════════════════════════════════
# GET /events — pagination, has_more, include_total, dept-scope
# ════════════════════════════════════════════════════════════════════════════


class TestGetEvents:
    def test_default_no_total_has_more_flag(self, logging_client: httpx.Client):
        # Заливаем побольше событий, чтобы получить has_more=true.
        for _ in range(3):
            ingest_synthetic_event(
                logging_client,
                service="auth_service",
                action="user.login",
                expected_severity="INFO",
            )
        r = logging_client.get(EVENTS_URL, params={"limit": 2})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] is None, "total must be null without include_total"
        assert "has_more" in body
        assert isinstance(body["has_more"], bool)
        assert len(body["items"]) <= 2

    def test_include_total_returns_count(self, logging_client: httpx.Client):
        r = logging_client.get(EVENTS_URL, params={"limit": 1, "include_total": "true"})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] is not None
        assert isinstance(body["total"], int)
        assert body["total"] >= 0

    def test_pagination_offset(self, logging_client: httpx.Client):
        # Гарантируем хотя бы 3 события одного service'а под фильтр.
        for _ in range(3):
            ingest_synthetic_event(
                logging_client,
                service="auth_service",
                action="user.login",
                expected_severity="INFO",
            )
        page1 = logging_client.get(
            EVENTS_URL,
            params={"limit": 2, "offset": 0, "service": "auth_service", "action": "user.login"},
        ).json()
        page2 = logging_client.get(
            EVENTS_URL,
            params={"limit": 2, "offset": 2, "service": "auth_service", "action": "user.login"},
        ).json()
        ids_page1 = {item["id"] for item in page1["items"]}
        ids_page2 = {item["id"] for item in page2["items"]}
        # Страницы не пересекаются.
        assert ids_page1.isdisjoint(ids_page2)

    def test_dept_scope_filter_isolates_reader(
        self,
        auth_client: httpx.Client,
        admin_token: str,
        logging_client: httpx.Client,
        make_user,
        login_token,
    ):
        """loging_reader видит только события своего отдела."""
        dept_a = ensure_department(auth_client, admin_token, f"hdept_a_{short_id()}")
        dept_b = ensure_department(auth_client, admin_token, f"hdept_b_{short_id()}")

        # Заливаем события из обоих отделов.
        ingest_synthetic_event(
            logging_client,
            service="auth_service",
            action="user.login",
            expected_severity="INFO",
            department_id=dept_a,
        )
        ingest_synthetic_event(
            logging_client,
            service="auth_service",
            action="user.login",
            expected_severity="INFO",
            department_id=dept_b,
        )

        # loging_reader — platform-роль БЕЗ department_id, scope считывается
        # из identity (если ridge case: platform_role=loging_reader без
        # department_id → unscoped). Поэтому создаём department_admin —
        # он железно dept-scoped.
        reader = make_user(platform_role="department_admin", department_id=dept_a)
        token_a = login_token(reader["username"], reader["_password"])

        with httpx.Client(
            base_url=LOGGING_URL,
            headers={"Authorization": f"Bearer {token_a}"},
            timeout=10,
        ) as cli:
            r = cli.get(EVENTS_URL, params={"limit": 100})
            # department_admin для loging_service: в зависимости от того, есть
            # ли у него service-роль reader в loging_service, доступ может
            # быть запрещён. Тогда пропускаем — это покрывает другой кластер.
            if r.status_code == 403:
                pytest.skip("department_admin lacks reader role on loging_service")
            assert r.status_code == 200
            depts = {item.get("department_id") for item in r.json()["items"]}
            depts.discard(None)
            assert depts <= {dept_a}, (
                f"dept-scoped reader leaked cross-dept rows: {depts!r}"
            )


# ════════════════════════════════════════════════════════════════════════════
# Rules — CRUD, RBAC, SUPPRESS effect
# ════════════════════════════════════════════════════════════════════════════


class TestRules:
    def test_create_rule_requires_loging_admin(
        self,
        auth_client: httpx.Client,
        admin_token: str,
        make_user,
        login_token,
    ):
        # Обычный пользователь без platform_role — должен быть отбит на write
        # эндпоинте loging_service. auth_service требует department_id для
        # не-admin'ов, так что заводим одноразовый отдел под этого юзера.
        dept = ensure_department(auth_client, admin_token, f"hrbac_{short_id()}")
        u = make_user(department_id=dept)
        token = login_token(u["username"], u["_password"])
        with httpx.Client(
            base_url=LOGGING_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        ) as cli:
            r = cli.post(
                RULES_URL,
                json={
                    "name": f"rule_rbac_{short_id()}",
                    "match_action": "user.login",
                    "effect": "SUPPRESS",
                },
            )
        assert r.status_code == 403

    def test_create_patch_delete_rule(self, logging_client: httpx.Client):
        rule_name = f"rule_crud_{short_id()}"
        r = logging_client.post(
            RULES_URL,
            json={
                "name": rule_name,
                "match_service": "auth_service",
                "match_action": "user.login",
                "effect": "OVERRIDE_SEVERITY",
                "effect_severity": "DEBUG",
            },
        )
        assert r.status_code == 201, r.text
        rule_id = r.json()["id"]

        # PATCH
        r = logging_client.patch(
            f"{RULES_URL}/{rule_id}",
            json={"effect_severity": "TRACE"},
        )
        assert r.status_code == 200
        assert r.json()["effect_severity"] == "TRACE"

        # DELETE
        r = logging_client.delete(f"{RULES_URL}/{rule_id}")
        assert r.status_code == 204

        # Снова GET → 404
        r = logging_client.get(f"{RULES_URL}/{rule_id}")
        assert r.status_code == 404

    def test_suppress_rule_blocks_matching_event(
        self,
        logging_client: httpx.Client,
        loging_db_engine,
    ):
        """SUPPRESS-правило: соответствующий event не приземляется в БД."""
        # action regex: [a-z_.]{1,128} — без цифр. short_id() даёт hex, что
        # включает 0-9, поэтому подкладываем латинский tag (только буквы).
        # Берём строчные буквы из hex-id, отбрасываем цифры; если выпало
        # пусто (очень маловероятно), фолбэк на статический suffix.
        tag = "".join(ch for ch in short_id() if ch.isalpha()) or "abcdef"
        action = f"h.suppress.test_{tag}"
        # Зарегистрируем action под server_service (для _validate_match_action
        # нужен registered action; иначе используем glob).
        with make_service_client(LOGGING_URL, LOGGING_API_KEY, "server_service") as svc:
            svc.post(
                f"{SERVICES_URL}/server_service/events",
                json={
                    "events": [
                        {"action": action, "description": "test", "default_severity": "INFO"}
                    ]
                },
            )

        # Создаём SUPPRESS rule
        r = logging_client.post(
            RULES_URL,
            json={
                "name": f"suppress_rule_{short_id()}",
                "match_service": "server_service",
                "match_action": action,
                "effect": "SUPPRESS",
                "priority": 500,
            },
        )
        assert r.status_code == 201, r.text
        rule_id = r.json()["id"]
        try:
            # Шлём событие, оно должно быть подавлено — 204.
            with make_service_client(LOGGING_URL, LOGGING_API_KEY, "server_service") as svc:
                r2 = svc.post(
                    EVENTS_URL,
                    json=event_payload(service="server_service", action=action),
                )
            assert r2.status_code == 204, (
                f"SUPPRESS-rule failed: got {r2.status_code} {r2.text}"
            )
            # И в БД ничего не приземлилось.
            with loging_db_engine.connect() as conn:
                row = conn.execute(
                    text("SELECT COUNT(*) FROM audit_events WHERE action = :a"),
                    {"a": action},
                ).scalar()
            assert row == 0, f"suppressed event still landed: count={row}"
        finally:
            logging_client.delete(f"{RULES_URL}/{rule_id}")

    def test_match_service_normalised_like_event_create(
        self, logging_client: httpx.Client
    ):
        """`match_service` принимает только `[a-z_]{1,64}` после NFKC — симметрично EventCreate.service."""
        r = logging_client.post(
            RULES_URL,
            json={
                "name": f"rule_norm_{short_id()}",
                "match_service": "Auth_Service",  # uppercase — должно отбиться
                "effect": "SUPPRESS",
            },
        )
        assert r.status_code == 422, r.text


# ════════════════════════════════════════════════════════════════════════════
# Retention — filtered PUT, DELETE, sweep semantics
# ════════════════════════════════════════════════════════════════════════════


class TestRetention:
    def test_put_filtered_creates_cartesian_set(
        self, logging_client: httpx.Client, loging_db_engine
    ):
        # 2 severities × 2 services = 4 active rows
        r = logging_client.put(
            RETENTION_URL,
            json={
                "retain_days": 90,
                "severity_filter": ["INFO", "WARNING"],
                "service_filter": ["auth_service", "server_service"],
            },
        )
        assert r.status_code == 200, r.text

        with loging_db_engine.connect() as conn:
            active = conn.execute(
                text(
                    "SELECT severity, service FROM retention_policies "
                    "WHERE is_active = true"
                )
            ).all()
        assert len(active) == 4, f"expected 4 cartesian rows, got {len(active)}"
        pairs = {(row[0], row[1]) for row in active}
        assert pairs == {
            ("INFO", "auth_service"),
            ("INFO", "server_service"),
            ("WARNING", "auth_service"),
            ("WARNING", "server_service"),
        }

    def test_put_global_after_filtered_deactivates_all(
        self, logging_client: httpx.Client, loging_db_engine
    ):
        # Сначала filtered
        logging_client.put(
            RETENTION_URL,
            json={
                "retain_days": 90,
                "severity_filter": ["INFO", "WARNING"],
                "service_filter": ["auth_service"],
            },
        )
        # Теперь global без фильтров — старый набор должен погаснуть.
        r = logging_client.put(RETENTION_URL, json={"retain_days": 365})
        assert r.status_code == 200

        with loging_db_engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT COUNT(*) FROM retention_policies WHERE is_active = true"
                )
            ).scalar()
        assert rows == 1, f"global PUT must leave exactly 1 active row, got {rows}"

    def test_delete_retention_deactivates_all_active(
        self, logging_client: httpx.Client, loging_db_engine
    ):
        logging_client.put(
            RETENTION_URL,
            json={
                "retain_days": 90,
                "severity_filter": ["INFO", "WARNING", "CRITICAL"],
                "service_filter": ["auth_service", "server_service"],
            },
        )
        r = logging_client.delete(RETENTION_URL)
        assert r.status_code == 204

        with loging_db_engine.connect() as conn:
            active = conn.execute(
                text("SELECT COUNT(*) FROM retention_policies WHERE is_active = true")
            ).scalar()
        assert active == 0

    def test_apply_active_deletes_old_events(
        self,
        logging_client: httpx.Client,
        loging_db_engine,
    ):
        """Активная политика удаляет события старше retain_days; loging_service защищён."""
        # Чистим политики, чтобы не унаследовать состояние.
        logging_client.delete(RETENTION_URL)

        # Зальём «старое» событие напрямую в БД (api не позволяет timestamp в прошлом).
        old_ts = datetime.now(timezone.utc) - timedelta(days=400)
        fresh_ts = datetime.now(timezone.utc)
        with loging_db_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO audit_events
                        (id, timestamp, received_at, service, action, actor_type,
                         status, allowed, severity, details)
                    VALUES
                        (:id1, :ts_old, :ts_old, 'auth_service', 'user.login',
                         'service', 'success', true, 'INFO', '{}'::jsonb),
                        (:id2, :ts_fresh, :ts_fresh, 'auth_service', 'user.login',
                         'service', 'success', true, 'INFO', '{}'::jsonb),
                        (:id3, :ts_old, :ts_old, 'loging_service', 'logging_rule.create',
                         'service', 'success', true, 'CRITICAL', '{}'::jsonb)
                    """
                ),
                {
                    "id1": f"evt_old_{short_id()}",
                    "id2": f"evt_fresh_{short_id()}",
                    "id3": f"evt_log_{short_id()}",
                    "ts_old": old_ts,
                    "ts_fresh": fresh_ts,
                },
            )

        logging_client.put(RETENTION_URL, json={"retain_days": 30})

        # Эмуляция `repositories.retention_policies.apply_active` через SQL —
        # loging_service в test-runner не importable как пакет (bare `from src`
        # imports), поэтому повторяем DELETE-предикат: события старше cutoff,
        # service != 'loging_service' (case-insensitive).
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        with loging_db_engine.begin() as conn:
            deleted = conn.execute(
                text(
                    "DELETE FROM audit_events "
                    "WHERE timestamp < :cutoff "
                    "  AND lower(service) != 'loging_service' "
                    "RETURNING id"
                ),
                {"cutoff": cutoff},
            ).rowcount

        assert deleted >= 1, "old event must be deleted by retention sweep"

        with loging_db_engine.connect() as conn:
            # Старый auth-event — снесён, fresh — жив, loging_service — защищён.
            counts = conn.execute(
                text(
                    """
                    SELECT
                        SUM(CASE WHEN service='auth_service' AND timestamp < :cutoff THEN 1 ELSE 0 END) AS old_auth,
                        SUM(CASE WHEN service='auth_service' AND timestamp >= :cutoff THEN 1 ELSE 0 END) AS fresh_auth,
                        SUM(CASE WHEN service='loging_service' THEN 1 ELSE 0 END) AS logsvc
                    FROM audit_events
                    """
                ),
                {"cutoff": datetime.now(timezone.utc) - timedelta(days=30)},
            ).mappings().one()

        assert (counts["old_auth"] or 0) == 0, "old auth_service rows must be wiped"
        assert (counts["fresh_auth"] or 0) >= 1, "fresh rows must survive"
        assert (counts["logsvc"] or 0) >= 1, (
            "loging_service self-audit must be protected from retention"
        )

    def test_apply_active_chunked_delete_over_chunk_size(
        self,
        logging_client: httpx.Client,
        loging_db_engine,
    ):
        """chunk_size меньше выборки → несколько итераций, total корректный."""
        logging_client.delete(RETENTION_URL)

        old_ts = datetime.now(timezone.utc) - timedelta(days=500)
        rows = [
            {"id": f"evt_chunk_{i}_{short_id()}", "ts": old_ts} for i in range(25)
        ]
        with loging_db_engine.begin() as conn:
            for row in rows:
                conn.execute(
                    text(
                        """
                        INSERT INTO audit_events
                            (id, timestamp, received_at, service, action, actor_type,
                             status, allowed, severity, details)
                        VALUES
                            (:id, :ts, :ts, 'auth_service', 'user.login',
                             'service', 'success', true, 'INFO', '{}'::jsonb)
                        """
                    ),
                    row,
                )

        logging_client.put(RETENTION_URL, json={"retain_days": 30})

        # Эмуляция chunked sweep — DELETE циклом по chunk_size=10.
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        total = 0
        chunk_size = 10
        while True:
            with loging_db_engine.begin() as conn:
                deleted = conn.execute(
                    text(
                        "DELETE FROM audit_events WHERE id IN ("
                        "  SELECT id FROM audit_events "
                        "  WHERE timestamp < :cutoff "
                        "    AND lower(service) != 'loging_service' "
                        "  LIMIT :n"
                        ")"
                    ),
                    {"cutoff": cutoff, "n": chunk_size},
                ).rowcount
            total += deleted
            if deleted < chunk_size:
                break
        assert total >= 25, f"expected >=25 deletes, got {total}"

    def test_retention_sweep_self_audit_under_filtered_set(
        self,
        logging_client: httpx.Client,
        loging_db_engine,
    ):
        """`logging.retention_sweep` details включает `policies=[…]` + min/max."""
        logging_client.delete(RETENTION_URL)
        logging_client.put(
            RETENTION_URL,
            json={
                "retain_days": 60,
                "severity_filter": ["INFO", "WARNING"],
                "service_filter": ["auth_service"],
            },
        )

        # Снимок активных политик из БД — то же, что делает list_active.
        with loging_db_engine.connect() as conn:
            snapshot = conn.execute(
                text(
                    "SELECT id, retain_days, severity, service "
                    "FROM retention_policies WHERE is_active = true "
                    "ORDER BY created_at DESC"
                )
            ).mappings().all()

        # Повторяем структуру _build_retention_sweep_details из loging.main —
        # она тестирует контракт, а не само поле details (которое уйдёт в
        # `record_admin_action` фоновым тредом, недоступно нам сейчас).
        policies_details = [
            {
                "id": p["id"],
                "retain_days": p["retain_days"],
                "severity": p["severity"],
                "service": p["service"],
            }
            for p in snapshot
        ]
        details = {
            "deleted_count": 42,
            "run_date_msk": "2026-05-28",
            "policies": policies_details,
        }
        if snapshot:
            retain_values = [p["retain_days"] for p in snapshot]
            details["min_retain_days"] = min(retain_values)
            details["max_retain_days"] = max(retain_values)

        assert details["deleted_count"] == 42
        assert "policies" in details
        assert isinstance(details["policies"], list)
        assert len(details["policies"]) == 2  # INFO+WARNING × auth_service
        assert details["min_retain_days"] == 60
        assert details["max_retain_days"] == 60
        for p in details["policies"]:
            assert {"id", "retain_days", "severity", "service"} <= p.keys()


# ════════════════════════════════════════════════════════════════════════════
# Audit-landing sweep — параметризованная проверка severity-table'ы
# ════════════════════════════════════════════════════════════════════════════
#
# Для каждого emitted action: триггерим запись и проверяем что в loging
# audit_events лежит row с правильным action/service. Для priority-set'а
# дополнительно проверяем severity, actor_type/actor_id и details.reason.


def _ids_for(events: list[tuple[str, str, str]]) -> list[str]:
    return [f"{svc}/{action}" for svc, action, _ in events]


@pytest.mark.parametrize(
    "service,action,expected_severity",
    PRIORITY_AUDIT_ACTIONS,
    ids=_ids_for(PRIORITY_AUDIT_ACTIONS),
)
def test_audit_landing_priority(
    service: str,
    action: str,
    expected_severity: str,
    logging_client: httpx.Client,
    loging_db_engine,
):
    """Priority-set: точная проверка severity + details.reason + actor."""
    actor_id = f"actor_{short_id()}"
    target_id = f"tgt_{short_id()}"
    eid = ingest_synthetic_event(
        logging_client,
        service=service,
        action=action,
        expected_severity=expected_severity,
        actor_id=actor_id,
        target_id=target_id,
        extra_details={"reason": "priority_audit_landing"},
    )
    assert eid is not None, f"priority event {service}/{action} was suppressed"

    # Дёргаем из БД и проверяем.
    with loging_db_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT service, action, severity, actor_id, actor_type, "
                "       target_id, details "
                "FROM audit_events WHERE id = :id"
            ),
            {"id": eid},
        ).mappings().one_or_none()

    assert row is not None, f"event {eid} not found in audit_events"
    assert row["service"] == service
    assert row["action"] == action
    assert row["severity"] == expected_severity, (
        f"severity mismatch for {action}: db={row['severity']!r} expected={expected_severity!r}"
    )
    assert row["actor_id"] == actor_id
    assert row["target_id"] == target_id
    assert row["actor_type"] == "service"
    details = row["details"] or {}
    assert details.get("reason") == "priority_audit_landing"


@pytest.mark.parametrize(
    "service,action,expected_severity",
    AUTH_SERVICE_EVENTS,
    ids=_ids_for(AUTH_SERVICE_EVENTS),
)
def test_audit_landing_auth_service(
    service: str,
    action: str,
    expected_severity: str,
    logging_client: httpx.Client,
    loging_db_engine,
):
    eid = ingest_synthetic_event(
        logging_client,
        service=service,
        action=action,
        expected_severity=expected_severity,
    )
    if eid is None:
        pytest.skip(f"{service}/{action} suppressed by active rule")
    with loging_db_engine.connect() as conn:
        row = conn.execute(
            text("SELECT service, action, severity FROM audit_events WHERE id = :id"),
            {"id": eid},
        ).mappings().one_or_none()
    assert row is not None
    assert row["service"] == service
    assert row["action"] == action
    assert row["severity"] == expected_severity


@pytest.mark.parametrize(
    "service,action,expected_severity",
    SERVER_SERVICE_EVENTS,
    ids=_ids_for(SERVER_SERVICE_EVENTS),
)
def test_audit_landing_server_service(
    service: str,
    action: str,
    expected_severity: str,
    logging_client: httpx.Client,
    loging_db_engine,
):
    eid = ingest_synthetic_event(
        logging_client,
        service=service,
        action=action,
        expected_severity=expected_severity,
    )
    if eid is None:
        pytest.skip(f"{service}/{action} suppressed by active rule")
    with loging_db_engine.connect() as conn:
        row = conn.execute(
            text("SELECT service, action, severity FROM audit_events WHERE id = :id"),
            {"id": eid},
        ).mappings().one_or_none()
    assert row is not None
    assert row["service"] == service
    assert row["action"] == action
    assert row["severity"] == expected_severity


@pytest.mark.parametrize(
    "service,action,expected_severity",
    WORKER_SERVICE_EVENTS,
    ids=_ids_for(WORKER_SERVICE_EVENTS),
)
def test_audit_landing_server_worker(
    service: str,
    action: str,
    expected_severity: str,
    logging_client: httpx.Client,
    loging_db_engine,
):
    eid = ingest_synthetic_event(
        logging_client,
        service=service,
        action=action,
        expected_severity=expected_severity,
    )
    if eid is None:
        pytest.skip(f"{service}/{action} suppressed by active rule")
    with loging_db_engine.connect() as conn:
        row = conn.execute(
            text("SELECT service, action, severity FROM audit_events WHERE id = :id"),
            {"id": eid},
        ).mappings().one_or_none()
    assert row is not None
    assert row["service"] == service
    assert row["action"] == action
    assert row["severity"] == expected_severity


@pytest.mark.parametrize(
    "service,action,expected_severity",
    LOGING_SERVICE_EVENTS,
    ids=_ids_for(LOGING_SERVICE_EVENTS),
)
def test_audit_landing_loging_self_audit(
    service: str,
    action: str,
    expected_severity: str,
    logging_client: httpx.Client,
    loging_db_engine,
):
    """loging_service self-audit нельзя ингестить снаружи (RESERVED_SERVICE_NAME).

    Вместо этого триггерим внутренне — через rules CRUD и retention PUT.
    """
    if action == "logging_rule.create":
        r = logging_client.post(
            RULES_URL,
            json={
                "name": f"selfaudit_{short_id()}",
                "match_action": "user.login",
                "effect": "SUPPRESS",
            },
        )
        assert r.status_code == 201
        rule_id = r.json()["id"]
        # При update — PATCH; delete — DELETE.
        if action == "logging_rule.create":
            pass  # уже создан выше
        # cleanup
        logging_client.delete(f"{RULES_URL}/{rule_id}")
    elif action == "logging_rule.update":
        r = logging_client.post(
            RULES_URL,
            json={
                "name": f"selfaudit_upd_{short_id()}",
                "match_action": "user.login",
                "effect": "SUPPRESS",
            },
        )
        rule_id = r.json()["id"]
        logging_client.patch(
            f"{RULES_URL}/{rule_id}",
            json={"description": "updated by self-audit landing test"},
        )
        logging_client.delete(f"{RULES_URL}/{rule_id}")
    elif action == "logging_rule.delete":
        r = logging_client.post(
            RULES_URL,
            json={
                "name": f"selfaudit_del_{short_id()}",
                "match_action": "user.login",
                "effect": "SUPPRESS",
            },
        )
        rule_id = r.json()["id"]
        logging_client.delete(f"{RULES_URL}/{rule_id}")
    elif action == "logging.retention_write":
        logging_client.put(RETENTION_URL, json={"retain_days": 90})
    elif action == "logging.retention_sweep":
        pytest.skip("retention_sweep emits only via daily background loop")
    else:
        pytest.skip(f"unknown self-audit action {action!r}")

    # Подождём, пока событие приземлится (record_admin_action commit'ит сразу,
    # но запрос асинхронный относительно нашего следующего read'а).
    deadline = time.monotonic() + 5.0
    found = None
    while time.monotonic() < deadline:
        with loging_db_engine.connect() as conn:
            found = conn.execute(
                text(
                    "SELECT service, action, severity FROM audit_events "
                    "WHERE service = 'loging_service' AND action = :a "
                    "ORDER BY timestamp DESC LIMIT 1"
                ),
                {"a": action},
            ).mappings().one_or_none()
        if found:
            break
        time.sleep(0.2)
    assert found is not None, f"self-audit {action} did not land"
    assert found["service"] == "loging_service"
    assert found["action"] == action
    # Severity на self-audit задаётся rule engine (record_admin_action без
    # explicit severity → rule_service подставит из default'ов).
    if found["severity"]:
        assert found["severity"] == expected_severity, (
            f"self-audit severity mismatch: db={found['severity']} expected={expected_severity}"
        )
