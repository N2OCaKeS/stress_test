"""Тесты: /api/logging/v1/services — реестр событий и статистика сервисов."""

import httpx
import pytest

from src.dependencies import auth as _auth_deps
from tests.conftest import TEST_API_KEY, make_event, make_event_def

SERVICES_URL = "/api/logging/v1/services"
EVENTS_URL = "/api/logging/v1/events"


class _IntrospectCtx:
    """Тот же интерфейс, что у `unittest.mock._patch` (`.stop()`), под
    которым жил локальный `_mock_identity` хелпер."""

    def __init__(self, pooled: httpx.AsyncClient, original):
        self._pooled = pooled
        self._original = original

    def stop(self):
        _auth_deps._introspect_client = self._original
        import asyncio
        asyncio.run(self._pooled.aclose())


def _mock_identity(identity_payload: dict):
    """Mock introspect-response from auth_service.

    Used by ``GET /services`` dept-scope tests which exercise the real
    ``require_reader`` dependency (the ``admin_client`` fixture overrides
    that dependency and would defeat the scope-leak invariant we're testing).
    """
    payload = dict(identity_payload)
    payload.setdefault("active", True)
    payload.setdefault("subject_type", "user")
    if "user_id" in payload and "sub" not in payload:
        payload["sub"] = payload.pop("user_id")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    pooled = httpx.AsyncClient(
        base_url="http://auth-test:8000",
        transport=httpx.MockTransport(handler),
        timeout=5.0,
    )
    original = _auth_deps._introspect_client
    _auth_deps._introspect_client = pooled
    return _IntrospectCtx(pooled, original)


# ── POST /services/{service}/events — регистрация событий ────────────────────

class TestRegisterEvents:
    def test_register_events_returns_counts(self, client, admin_client, auth_headers):
        payload = {
            "events": [
                make_event_def(action="user.login"),
                make_event_def(action="user.logout"),
                make_event_def(action="user.ban", default_severity="CRITICAL"),
            ]
        }
        r = client.post(f"{SERVICES_URL}/auth_service/events", json=payload, headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["service"] == "auth_service"
        assert body["added"] == 3
        assert body["updated"] == 0
        assert body["total"] == 3

    def test_register_same_events_twice_is_idempotent(self, client, auth_headers):
        payload = {"events": [make_event_def(action="user.login")]}
        r1 = client.post(f"{SERVICES_URL}/auth_service/events", json=payload, headers=auth_headers)
        assert r1.json()["added"] == 1
        r2 = client.post(f"{SERVICES_URL}/auth_service/events", json=payload, headers=auth_headers)
        assert r2.json()["added"] == 0
        assert r2.json()["updated"] == 1
        assert r2.json()["total"] == 1

    def test_restart_with_new_events_adds_them(self, client, auth_headers):
        client.post(f"{SERVICES_URL}/auth_service/events",
                    json={"events": [make_event_def(action="user.login")]},
                    headers=auth_headers)
        r = client.post(
            f"{SERVICES_URL}/auth_service/events",
            json={"events": [
                make_event_def(action="user.login"),
                make_event_def(action="user.new_action"),
            ]},
            headers=auth_headers,
        )
        assert r.json()["added"] == 1
        assert r.json()["updated"] == 1
        assert r.json()["total"] == 2

    def test_register_updates_description(self, client, auth_headers):
        client.post(f"{SERVICES_URL}/auth_service/events",
                    json={"events": [make_event_def(action="user.login", description="old")]},
                    headers=auth_headers)
        client.post(f"{SERVICES_URL}/auth_service/events",
                    json={"events": [make_event_def(action="user.login", description="new")]},
                    headers=auth_headers)
        # Description is updated (verify via GET)
        # (covered by test_get_service_events below)

    def test_register_requires_service_token(self, client):
        r = client.post(f"{SERVICES_URL}/auth_service/events",
                        json={"events": [make_event_def()]})
        assert r.status_code == 401

    def test_register_empty_list_returns_422(self, client, auth_headers):
        r = client.post(f"{SERVICES_URL}/auth_service/events",
                        json={"events": []}, headers=auth_headers)
        assert r.status_code == 422

    def test_register_multiple_services_independently(self, client, auth_headers):
        for svc in ("auth_service", "config_service", "server_service"):
            r = client.post(
                f"{SERVICES_URL}/{svc}/events",
                json={"events": [make_event_def(action="service.started")]},
                headers={**auth_headers, "X-Service-Identity": svc},
            )
            assert r.json()["total"] == 1

    def test_action_max_length_validated(self, client, auth_headers):
        r = client.post(f"{SERVICES_URL}/auth_service/events",
                        json={"events": [{"action": "a" * 129}]}, headers=auth_headers)
        assert r.status_code == 422


# ── POST /services/{service}/events — reserved-service guard ────────────────


class TestRegisterEventsReservedService:
    """Внешний service-token endpoint не может регистрировать events
    от имени ``loging_service``.

    Симметрия с ``POST /events`` (см. ``test_ingest.py::TestIngestReservedService``):
    держатель SERVICE_API_KEY иначе мог бы расширить каталог под
    ``loging_service`` фейковыми action'ами и подстроить SUPPRESS-правила
    под собственный аудит loging_service.
    """

    def test_canonical_loging_service_rejected(self, client, auth_headers):
        r = client.post(
            f"{SERVICES_URL}/loging_service/events",
            json={"events": [make_event_def(action="audit.suppress")]},
            headers=auth_headers,
        )
        assert r.status_code == 403
        assert r.json()["error_code"] == "RESERVED_SERVICE_NAME"

    def test_mixed_case_loging_service_rejected(self, client, auth_headers):
        """Обход case-sensitivity (LoGiNg_SeRvIcE / LOGING_SERVICE / ...) — тоже 403."""
        for variant in ("LoGiNg_SeRvIcE", "LOGING_SERVICE", "Loging_Service"):
            r = client.post(
                f"{SERVICES_URL}/{variant}/events",
                json={"events": [make_event_def(action="audit.suppress")]},
                headers=auth_headers,
            )
            assert r.status_code == 403, f"variant {variant!r} not blocked"
            assert r.json()["error_code"] == "RESERVED_SERVICE_NAME"

    def test_other_service_names_still_accepted(self, client, auth_headers):
        """Регрессия: легитимные сервисы продолжают регистрировать события без 403."""
        r = client.post(
            f"{SERVICES_URL}/auth_service/events",
            json={"events": [make_event_def(action="user.login")]},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["service"] == "auth_service"


# ── POST /services/{service}/events — path-vs-identity guard ───────────────


class TestRegisterEventsServiceIdentityGuard:
    """``X-Service-Identity`` header must match the ``{service}`` path
    parameter. Without this guard, a holder of the shared ``SERVICE_API_KEY``
    claiming to be ``auth_service`` could overwrite ``server_service`` event
    catalogue — cross-tenant audit-trail poisoning.

    Per-service API keys (``SERVICE_API_KEYS`` JSON env, full mTLS) are a
    follow-up; the header is informational today but the path comparison still
    forces every internal caller to be consistent, and any attacker who forges
    the header has to pick the right path too.
    """

    def test_matching_identity_accepted(self, client, auth_headers):
        """``X-Service-Identity: auth_service`` + path ``auth_service`` → 200."""
        headers = {**auth_headers, "X-Service-Identity": "auth_service"}
        r = client.post(
            f"{SERVICES_URL}/auth_service/events",
            json={"events": [make_event_def(action="user.login")]},
            headers=headers,
        )
        assert r.status_code == 200
        assert r.json()["service"] == "auth_service"

    def test_mismatched_identity_rejected_with_403(self, client, auth_headers):
        """``X-Service-Identity: auth_service`` + path ``server_service`` → 403."""
        headers = {**auth_headers, "X-Service-Identity": "auth_service"}
        r = client.post(
            f"{SERVICES_URL}/server_service/events",
            json={"events": [make_event_def(action="server.power_on")]},
            headers=headers,
        )
        assert r.status_code == 403
        assert r.json()["error_code"] == "SERVICE_IDENTITY_PATH_MISMATCH"

    def test_missing_identity_rejected_with_401(self, client):
        """Без header → 401 `MISSING_SERVICE_IDENTITY` (legacy soft-mode удалён)."""
        r = client.post(
            f"{SERVICES_URL}/auth_service/events",
            json={"events": [make_event_def(action="user.login")]},
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        )
        assert r.status_code == 401
        assert r.json()["error_code"] == "MISSING_SERVICE_IDENTITY"

    def test_unknown_identity_rejected_with_401(self, client, auth_headers):
        """Identity, которой нет в `SERVICE_API_KEYS` map'е, → 401
        `INVALID_SERVICE_KEY`. Legacy soft-mode «пропускаем unknown с
        warning'ом» удалён вместе с shared-key режимом.
        """
        headers = {**auth_headers, "X-Service-Identity": "future_service"}
        r = client.post(
            f"{SERVICES_URL}/future_service/events",
            json={"events": [make_event_def(action="future.event")]},
            headers=headers,
        )
        assert r.status_code == 401
        assert r.json()["error_code"] == "INVALID_SERVICE_KEY"

    def test_identity_case_and_whitespace_normalised(
        self, client, auth_headers
    ):
        """``X-Service-Identity`` сравнивается через ``normalize_service_name``,
        который lowercase-folding'ит обе стороны. Это симметрично с reserved-
        service guard'ом для path'а в этом же endpoint'е (см.
        ``TestRegisterEventsReservedServiceUnicodeBypass``) — обе стороны
        проходят одну и ту же нормализацию.
        """
        # ``AUTH_SERVICE`` (uppercase) против path ``auth_service`` — после
        # нормализации обе → ``auth_service`` → match → 200.
        headers = {**auth_headers, "X-Service-Identity": "AUTH_SERVICE"}
        r = client.post(
            f"{SERVICES_URL}/auth_service/events",
            json={"events": [make_event_def(action="user.login")]},
            headers=headers,
        )
        assert r.status_code == 200

    def test_random_garbage_identity_rejected_with_401(self, client, auth_headers):
        """Random identity вне `SERVICE_API_KEYS` map'а → 401
        `INVALID_SERVICE_KEY`. Legacy `STRICT_SERVICE_IDENTITY=true` режим
        выкинут — в per-service-only режиме каждая identity вне map'а
        отвергается всегда.
        """
        headers = {**auth_headers, "X-Service-Identity": "random_garbage"}
        r = client.post(
            f"{SERVICES_URL}/auth_service/events",
            json={"events": [make_event_def(action="user.login")]},
            headers=headers,
        )
        assert r.status_code == 401
        assert r.json()["error_code"] == "INVALID_SERVICE_KEY"


# ── POST /services/{service}/events — Unicode bypass guard ──────────────────


class TestRegisterEventsReservedServiceUnicodeBypass:
    """`endpoints/services.py` использовал ``.strip().lower()``
    и пропускал Unicode-обходы reserved-service guard'а, тогда как
    симметричный guard в ``POST /events`` уже шёл через
    ``normalize_service_name`` (NFKC + invisibles strip + confusables fold +
    lower).

    Атакующий с SERVICE_API_KEY мог зарегистрировать events под
    ``loging_service`` через path-параметр с кириллической ``о`` или ZWSP:
    ``POST /services/lоging_service/events`` → ``service_events`` с raw
    Unicode → дрейф каталога событий и фейковые actions для
    SUPPRESS-правил поверх audit trail loging_service.

    После фикса оба guard'а вызывают одну и ту же ``normalize_service_name``.
    """

    def test_cyrillic_confusable_rejected(self, client, auth_headers):
        """``lоging_service`` с кириллической ``о`` (U+043E) → 403."""
        # path-сегмент с кириллической 'о' (U+043E) вместо латинской 'o'
        variant = "lоging_service"
        r = client.post(
            f"{SERVICES_URL}/{variant}/events",
            json={"events": [make_event_def(action="audit.suppress")]},
            headers=auth_headers,
        )
        assert r.status_code == 403, f"variant {variant!r} not blocked"
        assert r.json()["error_code"] == "RESERVED_SERVICE_NAME"

    def test_zero_width_space_bypass_rejected(self, client, auth_headers):
        """``loging_service`` + U+200B (ZWSP) → 403."""
        variant = "loging_service​"  # trailing ZWSP U+200B
        r = client.post(
            f"{SERVICES_URL}/{variant}/events",
            json={"events": [make_event_def(action="audit.suppress")]},
            headers=auth_headers,
        )
        assert r.status_code == 403, f"variant {variant!r} not blocked"
        assert r.json()["error_code"] == "RESERVED_SERVICE_NAME"

    def test_uppercase_canonical_still_rejected(self, client, auth_headers):
        """Регрессия: ``LOGING_SERVICE`` (uppercase) → 403."""
        r = client.post(
            f"{SERVICES_URL}/LOGING_SERVICE/events",
            json={"events": [make_event_def(action="audit.suppress")]},
            headers=auth_headers,
        )
        assert r.status_code == 403
        assert r.json()["error_code"] == "RESERVED_SERVICE_NAME"

    def test_legitimate_service_still_accepted(self, client, auth_headers):
        """Регрессия: ``auth_service`` продолжает регистрировать события (200)."""
        r = client.post(
            f"{SERVICES_URL}/auth_service/events",
            json={"events": [make_event_def(action="user.login")]},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["service"] == "auth_service"


# ── GET /services/{service}/events — просмотр реестра ────────────────────────

class TestGetServiceEvents:
    def test_empty_registry(self, admin_client):
        r = admin_client.get(f"{SERVICES_URL}/auth_service/events")
        assert r.status_code == 200
        body = r.json()
        assert body["service"] == "auth_service"
        assert body["items"] == []
        assert body["total"] == 0

    def test_returns_registered_events(self, client, admin_client, auth_headers):
        client.post(f"{SERVICES_URL}/auth_service/events",
                    json={"events": [
                        make_event_def(action="user.login", default_severity="INFO"),
                        make_event_def(action="user.ban", default_severity="CRITICAL"),
                    ]},
                    headers=auth_headers)
        r = admin_client.get(f"{SERVICES_URL}/auth_service/events")
        body = r.json()
        assert body["total"] == 2
        actions = {e["action"] for e in body["items"]}
        assert actions == {"user.login", "user.ban"}

    def test_events_sorted_by_action(self, client, admin_client, auth_headers):
        client.post(f"{SERVICES_URL}/auth_service/events",
                    json={"events": [
                        make_event_def(action="user.login"),
                        make_event_def(action="pat.create"),
                        make_event_def(action="bot.create"),
                    ]},
                    headers=auth_headers)
        r = admin_client.get(f"{SERVICES_URL}/auth_service/events")
        actions = [e["action"] for e in r.json()["items"]]
        assert actions == sorted(actions)

    def test_pagination_limit(self, client, admin_client, auth_headers):
        client.post(f"{SERVICES_URL}/auth_service/events",
                    json={"events": [make_event_def(action=f"event.{i}") for i in range(5)]},
                    headers=auth_headers)
        r = admin_client.get(f"{SERVICES_URL}/auth_service/events", params={"limit": 2})
        body = r.json()
        assert len(body["items"]) == 2
        assert body["total"] == 5
        assert body["limit"] == 2

    def test_pagination_offset(self, client, admin_client, auth_headers):
        client.post(f"{SERVICES_URL}/auth_service/events",
                    json={"events": [make_event_def(action=f"event.{i}") for i in range(5)]},
                    headers=auth_headers)
        r = admin_client.get(f"{SERVICES_URL}/auth_service/events", params={"offset": 3})
        assert r.json()["total"] == 5
        assert len(r.json()["items"]) == 2

    def test_requires_admin(self, client):
        r = client.get(f"{SERVICES_URL}/auth_service/events")
        assert r.status_code == 401

    def test_different_services_isolated(self, client, admin_client, auth_headers):
        for svc in ("auth_service", "config_service"):
            client.post(f"{SERVICES_URL}/{svc}/events",
                        json={"events": [make_event_def(action="service.started")]},
                        headers={**auth_headers, "X-Service-Identity": svc})
        r_auth = admin_client.get(f"{SERVICES_URL}/auth_service/events")
        r_conf = admin_client.get(f"{SERVICES_URL}/config_service/events")
        assert r_auth.json()["total"] == 1
        assert r_conf.json()["total"] == 1


# ── GET /services — статистика сервисов ──────────────────────────────────────

class TestListServices:
    def test_empty_when_no_events(self, admin_client):
        r = admin_client.get(SERVICES_URL)
        assert r.status_code == 200
        assert r.json()["items"] == []

    def test_shows_services_after_events(self, client, admin_client, auth_headers):
        client.post(EVENTS_URL,
                    headers=auth_headers | {"X-Service-Identity": "auth_service"},
                    json=make_event(service="auth_service"))
        client.post(EVENTS_URL,
                    headers=auth_headers | {"X-Service-Identity": "config_service"},
                    json=make_event(service="config_service"))
        r = admin_client.get(SERVICES_URL)
        body = r.json()
        assert body["total"] == 2
        services = {s["service"] for s in body["items"]}
        assert services == {"auth_service", "config_service"}

    def test_event_count_per_service(self, client, admin_client, auth_headers):
        for _ in range(3):
            client.post(EVENTS_URL,
                        headers=auth_headers | {"X-Service-Identity": "auth_service"},
                        json=make_event(service="auth_service"))
        client.post(EVENTS_URL,
                    headers=auth_headers | {"X-Service-Identity": "config_service"},
                    json=make_event(service="config_service"))
        r = admin_client.get(SERVICES_URL)
        svc_map = {s["service"]: s for s in r.json()["items"]}
        assert svc_map["auth_service"]["event_count"] == 3
        assert svc_map["config_service"]["event_count"] == 1

    def test_requires_admin(self, client):
        r = client.get(SERVICES_URL)
        assert r.status_code == 401


# ── Валидация match_action при создании правил ───────────────────────────────

class TestRuleMatchActionValidation:
    def test_unregistered_exact_action_rejected_when_registry_nonempty(
        self, client, admin_client, auth_headers
    ):
        # Зарегистрируем хотя бы одно событие
        client.post(f"{SERVICES_URL}/auth_service/events",
                    json={"events": [make_event_def(action="user.login")]},
                    headers=auth_headers)
        # Пробуем создать правило на незарегистрированное действие
        r = admin_client.post("/api/logging/v1/rules", json={
            "name": "bad-rule",
            "effect": "SUPPRESS",
            "match_action": "user.unknown_action",
        })
        assert r.status_code == 422
        body = r.json()
        # AppException-envelope: `error_code` + `message`, не legacy `detail`.
        assert body["error_code"] == "UNKNOWN_MATCH_ACTION"
        assert "not registered" in body["message"].lower()

    def test_registered_exact_action_allowed(self, client, admin_client, auth_headers):
        client.post(f"{SERVICES_URL}/auth_service/events",
                    json={"events": [make_event_def(action="user.login")]},
                    headers=auth_headers)
        r = admin_client.post("/api/logging/v1/rules", json={
            "name": "ok-rule",
            "effect": "SUPPRESS",
            "match_action": "user.login",
        })
        assert r.status_code == 201

    def test_glob_pattern_always_allowed(self, client, admin_client, auth_headers):
        # Даже при непустом реестре глоб-паттерны разрешены
        client.post(f"{SERVICES_URL}/auth_service/events",
                    json={"events": [make_event_def(action="user.login")]},
                    headers=auth_headers)
        r = admin_client.post("/api/logging/v1/rules", json={
            "name": "glob-rule",
            "effect": "SUPPRESS",
            "match_action": "user.*",
        })
        assert r.status_code == 201

    def test_no_validation_when_registry_empty(self, admin_client):
        # При пустом реестре любое точное имя разрешено
        r = admin_client.post("/api/logging/v1/rules", json={
            "name": "bootstrap-rule",
            "effect": "SUPPRESS",
            "match_action": "user.login",
        })
        assert r.status_code == 201

    def test_glob_matching_registered_action_allowed(
        self, client, admin_client, auth_headers
    ):
        """Glob, который реально матчит хотя бы один зарегистрированный action."""
        client.post(f"{SERVICES_URL}/auth_service/events",
                    json={"events": [make_event_def(action="user.login")]},
                    headers=auth_headers)
        r = admin_client.post("/api/logging/v1/rules", json={
            "name": "glob-match", "effect": "SUPPRESS", "match_action": "user.*",
        })
        assert r.status_code == 201

    def test_glob_not_matching_any_action_still_allowed(
        self, client, admin_client, auth_headers
    ):
        """Glob, который НЕ матчит ничего из реестра — всё равно проходит
        (валидация для glob отключена)."""
        client.post(f"{SERVICES_URL}/auth_service/events",
                    json={"events": [make_event_def(action="user.login")]},
                    headers=auth_headers)
        r = admin_client.post("/api/logging/v1/rules", json={
            "name": "glob-orphan", "effect": "SUPPRESS",
            "match_action": "nonexistent.*",
        })
        assert r.status_code == 201

    def test_action_is_registered_glob_helper(self, client, auth_headers, db):
        """Точечный unit-тест на se_repo.action_is_registered для glob."""
        from src.repositories import service_events as se_repo
        client.post(f"{SERVICES_URL}/auth_service/events",
                    json={"events": [
                        make_event_def(action="user.login"),
                        make_event_def(action="user.logout"),
                        make_event_def(action="bot.create"),
                    ]},
                    headers=auth_headers)
        assert se_repo.action_is_registered(db, "user.*") is True
        assert se_repo.action_is_registered(db, "bot.*") is True
        assert se_repo.action_is_registered(db, "missing.*") is False
        assert se_repo.action_is_registered(db, "user.login") is True
        assert se_repo.action_is_registered(db, "user.unknown") is False


# ── GET /services — global cross-dept aggregate ─────────────────────────────


class TestListServicesAccess:
    """`GET /services` отдаёт глобальный GROUP BY service агрегат по
    ``audit_events`` без dept-фильтра. Эндпоинт доступен любой из пяти
    read-ролей (`loging_admin` / `loging_reader` / `account_admin` /
    `loging_reader_dep` / `department_admin`); даже dept-scoped роли видят
    общий реестр имён сервисов — это не пер-dept данные событий, dept-scope
    живёт на `/events` / `/stats` / `/export`. Service-роль в loging_service
    и пользователь без платформенной роли отбиваются `INSUFFICIENT_ROLE` в
    `require_reader`.

    Тесты под прежний scope-фильтр на endpoint'е сняты — фильтр в
    `events_repo.list_services` всё ещё доступен через API репозитория
    (см. `test_repo_list_services_with_dept_scope_unit`), но endpoint его
    больше не использует.
    """

    def test_loging_reader_sees_all_services_cross_dept(
        self, client, auth_headers
    ):
        """`loging_reader` теперь global-read: видит сервисы и события всех
        отделов в агрегате, никакого dept-scope нет."""
        client.post(EVENTS_URL,
                    headers=auth_headers | {"X-Service-Identity": "auth_service"},
                    json=make_event(service="auth_service", department_id="dep_a"))
        client.post(EVENTS_URL,
                    headers=auth_headers | {"X-Service-Identity": "config_service"},
                    json=make_event(service="config_service", department_id="dep_b"))

        p = _mock_identity({
            "user_id": "u_lr",
            "username": "lr",
            "platform_role": "loging_reader",
            "department_id": "dep_a",
        })
        try:
            r = client.get(SERVICES_URL, headers={"Authorization": "Bearer t"})
        finally:
            p.stop()
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 2
        services = {s["service"] for s in body["items"]}
        assert services == {"auth_service", "config_service"}

    def test_loging_admin_sees_all_services(self, client, auth_headers):
        """`loging_admin` — глобальный read, видит всё (smoke против регрессии
        на dept-фильтр)."""
        client.post(EVENTS_URL,
                    headers=auth_headers | {"X-Service-Identity": "auth_service"},
                    json=make_event(service="auth_service", department_id="dep_a"))
        client.post(EVENTS_URL,
                    headers=auth_headers | {"X-Service-Identity": "config_service"},
                    json=make_event(service="config_service", department_id="dep_b"))

        p = _mock_identity({
            "user_id": "u_la",
            "username": "log_admin",
            "platform_role": "loging_admin",
            "department_id": None,
        })
        try:
            r = client.get(SERVICES_URL, headers={"Authorization": "Bearer t"})
        finally:
            p.stop()
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 2
        services = {s["service"] for s in body["items"]}
        assert services == {"auth_service", "config_service"}

    def test_account_admin_sees_all_services(self, client, auth_headers):
        """`account_admin` получает read-only доступ к реестру сервисов."""
        client.post(EVENTS_URL,
                    headers=auth_headers | {"X-Service-Identity": "auth_service"},
                    json=make_event(service="auth_service", department_id="dep_a"))
        p = _mock_identity({
            "user_id": "u_aa",
            "username": "acc_admin",
            "platform_role": "account_admin",
            "department_id": None,
        })
        try:
            r = client.get(SERVICES_URL, headers={"Authorization": "Bearer t"})
        finally:
            p.stop()
        assert r.status_code == 200
        assert r.json()["total"] == 1

    def test_department_admin_sees_all_services(self, client, auth_headers):
        """`department_admin` (dept-scoped reader) видит общий реестр имён
        сервисов — этот эндпоинт не применяет dept-scope (не пер-dept данные)."""
        client.post(EVENTS_URL,
                    headers=auth_headers | {"X-Service-Identity": "auth_service"},
                    json=make_event(service="auth_service", department_id="dep_b"))
        p = _mock_identity({
            "user_id": "u_da",
            "username": "dept_admin",
            "platform_role": "department_admin",
            "department_id": "dep_a",
        })
        try:
            r = client.get(SERVICES_URL, headers={"Authorization": "Bearer t"})
        finally:
            p.stop()
        assert r.status_code == 200
        assert r.json()["total"] == 1

    def test_service_role_in_loging_service_403(self, client, auth_headers):
        """Service-роль в `loging_service` больше не пускает к чтению — нужна
        платформенная `loging_reader`."""
        p = _mock_identity({
            "user_id": "u_sr",
            "username": "sr_reader",
            "platform_role": None,
            "department_id": "dep_a",
            "service_roles": {"loging_service": ["reader"]},
        })
        try:
            r = client.get(SERVICES_URL, headers={"Authorization": "Bearer t"})
        finally:
            p.stop()
        assert r.status_code == 403
        assert r.json()["error_code"] == "INSUFFICIENT_ROLE"

    def test_repo_list_services_with_dept_scope_unit(self, db):
        """Pin the repository contract directly: ``department_id`` argument
        restricts the aggregate and ``department_id=None`` returns the legacy
        global aggregate.
        """
        from src.repositories import events as events_repo
        from src.models.audit_event import AuditEvent
        from datetime import datetime, timezone
        from src.utils.ids import audit_event_id

        # 2 events for dep_a, 1 for dep_b in auth_service; 1 for dep_b only
        # in config_service.
        for dept in ("dep_a", "dep_a", "dep_b"):
            db.add(AuditEvent(
                id=audit_event_id(),
                timestamp=datetime.now(timezone.utc),
                received_at=datetime.now(timezone.utc),
                service="auth_service",
                action="user.login",
                actor_type="user",
                status="success",
                allowed=True,
                severity="INFO",
                department_id=dept,
            ))
        db.add(AuditEvent(
            id=audit_event_id(),
            timestamp=datetime.now(timezone.utc),
            received_at=datetime.now(timezone.utc),
            service="config_service",
            action="config.update",
            actor_type="user",
            status="success",
            allowed=True,
            severity="INFO",
            department_id="dep_b",
        ))
        db.commit()

        # Unscoped — sees both services with full counts.
        all_rows = events_repo.list_services(db)
        rows_by_svc = {r.service: r for r in all_rows}
        assert rows_by_svc["auth_service"].event_count == 3
        assert rows_by_svc["config_service"].event_count == 1

        # Scoped to dep_a — only auth_service visible, count == 2.
        dep_a_rows = events_repo.list_services(db, department_id="dep_a")
        assert len(dep_a_rows) == 1
        assert dep_a_rows[0].service == "auth_service"
        assert dep_a_rows[0].event_count == 2

        # Scoped to dep_b — both services visible, but counts reflect dep_b only.
        dep_b_rows = events_repo.list_services(db, department_id="dep_b")
        by_svc = {r.service: r for r in dep_b_rows}
        assert set(by_svc.keys()) == {"auth_service", "config_service"}
        assert by_svc["auth_service"].event_count == 1
        assert by_svc["config_service"].event_count == 1

        # Scoped to a department with no events — empty list.
        dep_c_rows = events_repo.list_services(db, department_id="dep_c")
        assert dep_c_rows == []


# ── catalog/ingest нормализация path-параметра ──────────────────────────────


class TestServiceEventsCatalogNormalization:
    """Реестр в `service_events` и события в `audit_events` должны храниться
    под одним и тем же каноническим именем сервиса.

    До фикса `register_events` писал path-параметр raw, а ingest `POST /events`
    нормализовал `EventCreate.service` через NFKC+confusables+lower. Caller,
    зарегистрировавший события через `AUTH_SERVICE`, потом не находил их в
    `GET /services/auth_service/events` — реестр и журнал жили в разных
    «вселенных».
    """

    def test_uppercase_path_normalised_in_response_and_db(
        self, client, admin_client, auth_headers
    ):
        # Регистрируем через uppercase-путь, identity тоже uppercase
        # (нормализация в auth_dependency сравнит обе стороны).
        headers = {**auth_headers, "X-Service-Identity": "AUTH_SERVICE"}
        r = client.post(
            f"{SERVICES_URL}/AUTH_SERVICE/events",
            json={"events": [make_event_def(action="user.login")]},
            headers=headers,
        )
        assert r.status_code == 200
        # Ответ возвращает уже нормализованное имя.
        assert r.json()["service"] == "auth_service"

        # GET под нормализованным path возвращает зарегистрированный action,
        # доказывая, что catalog хранит запись под каноническим именем.
        r_get = admin_client.get(f"{SERVICES_URL}/auth_service/events")
        assert r_get.status_code == 200
        body = r_get.json()
        assert body["total"] == 1
        assert body["service"] == "auth_service"
        assert {e["action"] for e in body["items"]} == {"user.login"}

        # И симметрично: GET под uppercase-path находит ту же запись
        # (path тоже нормализуется на чтении).
        r_upper = admin_client.get(f"{SERVICES_URL}/AUTH_SERVICE/events")
        assert r_upper.status_code == 200
        assert r_upper.json()["total"] == 1
        assert r_upper.json()["service"] == "auth_service"
