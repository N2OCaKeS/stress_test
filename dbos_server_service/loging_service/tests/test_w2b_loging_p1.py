"""P1 для loging_service: per-user rate-limit key, severity-from-catalog,
расширенные фильтры `/events`.

Покрывает:

* `reader_rate_limit_key` — JWT identity → `usr:{sub}`, без identity → IP.
* `_resolve_default_severity` с catalog'ом — severity из БД, когда (action,
  status) нет в hardcoded dict; catalog invalidation hook.
* `GET /events` — новые query-параметры `actor_id`, `target_id`, `status`,
  `request_id`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tests.conftest import make_event


# ── 1. reader_rate_limit_key ─────────────────────────────────────────────────


class TestReaderRateLimitKey:
    """Per-user bucket'ы на read-канале.

    За k8s ingress все reader'ы приходят с одного IP; общий per-IP bucket
    позволил бы одному JWT выжать бюджет других. `reader_rate_limit_key`
    отдаёт `usr:{sub}` когда identity verified, иначе IP.
    """

    def _fake_request(self, *, identity: dict | None, client_host: str = "10.0.0.5",
                      path: str = "/api/logging/v1/events"):
        """Минимальный stub Request: только то, что трогает key_func."""
        state = SimpleNamespace()
        if identity is not None:
            state.auth_identity = identity
        url = SimpleNamespace(path=path)
        client = SimpleNamespace(host=client_host)
        # slowapi.get_remote_address ходит в `request.client.host`; путь
        # через X-Forwarded-For игнорируем — за ingress оба варианта
        # дают одно и то же.
        return SimpleNamespace(state=state, url=url, client=client, headers={})

    def test_jwt_identity_keys_by_sub(self):
        from src.core.limiter import reader_rate_limit_key

        req = self._fake_request(identity={"sub": "usr_alice", "username": "alice"})
        assert reader_rate_limit_key(req) == "usr:usr_alice"

    def test_falls_back_to_user_id_when_sub_absent(self):
        """introspect может вернуть только `user_id` без `sub` на legacy-стенде."""
        from src.core.limiter import reader_rate_limit_key

        req = self._fake_request(identity={"user_id": "usr_bob"})
        assert reader_rate_limit_key(req) == "usr:usr_bob"

    def test_no_identity_falls_back_to_ip(self):
        """Auth-fail / pre-auth — bucket по IP, чтобы анонимный flood не
        обходил лимит."""
        from src.core.limiter import reader_rate_limit_key

        req = self._fake_request(identity=None, client_host="192.168.7.42")
        assert reader_rate_limit_key(req) == "ip:192.168.7.42"

    def test_identity_without_sub_or_user_id_falls_back_to_ip(self):
        """Empty identity dict (introspect не вернул subject) → IP fallback."""
        from src.core.limiter import reader_rate_limit_key

        req = self._fake_request(identity={"platform_role": "loging_reader"},
                                  client_host="10.0.0.99")
        assert reader_rate_limit_key(req) == "ip:10.0.0.99"

    def test_different_users_get_independent_keys(self):
        """Ключи для двух разных JWT не сливаются — bucket'ы независимы."""
        from src.core.limiter import reader_rate_limit_key

        k1 = reader_rate_limit_key(self._fake_request(identity={"sub": "usr_x"}))
        k2 = reader_rate_limit_key(self._fake_request(identity={"sub": "usr_y"}))
        assert k1 != k2

    def test_exempt_path_returns_unique_key(self):
        """Health/token endpoint'ы получают uuid-ключ (не копятся в общем bucket'е).
        Для read-flow они через этот key_func не идут, но идемпотентность важна.
        """
        from src.core.limiter import reader_rate_limit_key

        req = self._fake_request(
            identity={"sub": "usr_test"},
            path="/api/logging/v1/health",
        )
        key = reader_rate_limit_key(req)
        assert key.startswith("exempt:"), key


# ── 2. _resolve_default_severity from DB catalog ─────────────────────────────


class TestSeverityFromCatalog:
    """`_DEFAULT_SEVERITY` hardcoded ≠ полный каталог сервисов.

    Каждый сервис при `register_events()` объявляет `default_severity` —
    эта severity должна подхватываться `_resolve_default_severity`, иначе
    ~60% действий получают heuristic INFO/WARNING вместо обещанного.
    """

    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        """Catalog-кеш TTL=30s, между тестами форсим сброс."""
        from src.services.rule_service import invalidate_catalog_severity_cache
        invalidate_catalog_severity_cache()
        yield
        invalidate_catalog_severity_cache()

    def test_hardcoded_takes_precedence_over_catalog(self, db):
        """Если (action, status) в hardcoded dict — catalog игнорируется.

        Hardcoded source — это статус-зависимая таблица; catalog только
        per-action. Hardcoded точнее, поэтому первичный.
        """
        from src.repositories import service_events as se_repo
        from src.services.rule_service import _resolve_default_severity

        # `user.login`/success есть в hardcoded → "INFO".
        se_repo.upsert_events(
            db, "auth_service",
            [{"action": "user.login", "description": "x",
              "default_severity": "CRITICAL"}],
        )
        assert _resolve_default_severity("user.login", "success", db) == "INFO"

    def test_catalog_used_when_hardcoded_missing(self, db):
        """Action не в hardcoded → catalog → "CRITICAL"."""
        from src.repositories import service_events as se_repo
        from src.services.rule_service import _resolve_default_severity

        se_repo.upsert_events(
            db, "custom_service",
            [{"action": "custom.weird_action", "description": "x",
              "default_severity": "CRITICAL"}],
        )
        assert _resolve_default_severity("custom.weird_action", "success", db) == "CRITICAL"

    def test_heuristic_fallback_when_neither_present(self, db):
        """Action нет ни в hardcoded, ни в catalog → heuristic."""
        from src.services.rule_service import _resolve_default_severity

        assert _resolve_default_severity("totally.unknown", "failure", db) == "WARNING"
        assert _resolve_default_severity("totally.unknown", "success", db) == "INFO"

    def test_catalog_null_severity_falls_through_to_heuristic(self, db):
        """Action в catalog'е без `default_severity` (NULL) → heuristic."""
        from src.repositories import service_events as se_repo
        from src.services.rule_service import _resolve_default_severity

        se_repo.upsert_events(
            db, "custom_service",
            [{"action": "custom.no_sev", "description": "x",
              "default_severity": None}],
        )
        assert _resolve_default_severity("custom.no_sev", "denied", db) == "WARNING"

    def test_no_db_session_skips_catalog_lookup(self):
        """Caller без db — поведение совпадает со старой 2-arg сигнатурой."""
        from src.services.rule_service import _resolve_default_severity

        # Heuristic, потому что catalog не доступен.
        assert _resolve_default_severity("unknown.thing", "failure") == "WARNING"
        # Hardcoded работает и без db.
        assert _resolve_default_severity("user.login", "success") == "INFO"

    def test_register_events_invalidates_catalog_cache(self, client, auth_headers, db):
        """Hook на upsert: после `POST /services/{}/events` свежий severity
        виден без TTL-задержки."""
        from src.services.rule_service import _resolve_default_severity

        # 1. lookup без записи — heuristic.
        assert _resolve_default_severity("custom.fresh", "success", db) == "INFO"

        # 2. Регистрируем через API.
        resp = client.post(
            "/api/logging/v1/services/auth_service/events",
            json={"events": [{
                "action": "custom.fresh",
                "description": "fresh",
                "default_severity": "ERROR",
            }]},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text

        # 3. Сразу же — catalog severity, без ожидания TTL.
        assert _resolve_default_severity("custom.fresh", "success", db) == "ERROR"

    def test_apply_rules_uses_catalog_severity(self, db):
        """`apply_rules` сам передаёт db → catalog подхватывается на ingest."""
        from src.repositories import service_events as se_repo
        from src.schemas.events import EventCreate
        from src.services import rule_service

        se_repo.upsert_events(
            db, "custom_service",
            [{"action": "custom.from_catalog", "description": "x",
              "default_severity": "ERROR"}],
        )

        payload = EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="custom_service",
            action="custom.from_catalog",
            actor_id="usr_x",
            actor_type="user",
            status="success",
            allowed=True,
            severity=None,  # резолвится из catalog'а
        )
        result = rule_service.apply_rules(db, payload)
        assert result is not None
        assert result.severity == "ERROR"


# ── 3. GET /events — расширенные фильтры ─────────────────────────────────────


def _ingest(client, auth_headers, **kwargs):
    payload = make_event(**kwargs)
    headers = auth_headers | {"X-Service-Identity": payload["service"]}
    resp = client.post("/api/logging/v1/events", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


class TestEventsExtendedFilters:
    """`GET /events` принимает actor_id / target_id / status / request_id.

    Поля давно в схеме `EventCreate`, колонки давно в БД, но query
    выставлял только service/severity/action/time. Теперь — паритет
    с тем, что лежит в audit_events.
    """

    def test_filter_by_actor_id(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers, actor_id="usr_alice")
        _ingest(client, auth_headers, actor_id="usr_alice")
        _ingest(client, auth_headers, actor_id="usr_bob")

        body = admin_client.get(
            "/api/logging/v1/events",
            params={"actor_id": "usr_alice", "include_total": "true"},
        ).json()
        assert body["total"] == 2
        assert all(i["actor_id"] == "usr_alice" for i in body["items"])

    def test_filter_by_target_id(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers, target_id="srv_001", target_type="server")
        _ingest(client, auth_headers, target_id="srv_002", target_type="server")
        _ingest(client, auth_headers, target_id="srv_001", target_type="server")

        body = admin_client.get(
            "/api/logging/v1/events",
            params={"target_id": "srv_001", "include_total": "true"},
        ).json()
        assert body["total"] == 2
        assert all(i["target_id"] == "srv_001" for i in body["items"])

    def test_filter_by_status(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers, status="success", allowed=True)
        _ingest(client, auth_headers, status="failure", allowed=False,
                action="user.login")
        _ingest(client, auth_headers, status="denied", allowed=False,
                action="http.access_denied")

        body = admin_client.get(
            "/api/logging/v1/events",
            params={"status": "failure", "include_total": "true"},
        ).json()
        assert body["total"] == 1
        assert body["items"][0]["status"] == "failure"

    def test_filter_by_status_rejects_unknown_value(self, admin_client):
        """`status` ограничен Literal — невалидное значение даёт 422."""
        resp = admin_client.get(
            "/api/logging/v1/events",
            params={"status": "not_a_status"},
        )
        assert resp.status_code == 422

    def test_filter_by_request_id(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers, request_id="req-aaa")
        _ingest(client, auth_headers, request_id="req-bbb")
        _ingest(client, auth_headers, request_id="req-aaa")

        body = admin_client.get(
            "/api/logging/v1/events",
            params={"request_id": "req-aaa", "include_total": "true"},
        ).json()
        assert body["total"] == 2
        assert all(i["request_id"] == "req-aaa" for i in body["items"])

    def test_combined_filters_are_anded(self, client, admin_client, auth_headers):
        """`status` AND `actor_id` AND `service` — все фильтры в конъюнкции."""
        _ingest(client, auth_headers, service="auth_service",
                actor_id="usr_alice", status="failure", allowed=False)
        _ingest(client, auth_headers, service="auth_service",
                actor_id="usr_alice", status="success")
        _ingest(client, auth_headers, service="auth_service",
                actor_id="usr_bob", status="failure", allowed=False)

        body = admin_client.get(
            "/api/logging/v1/events",
            params={
                "service": "auth_service",
                "actor_id": "usr_alice",
                "status": "failure",
                "include_total": "true",
            },
        ).json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["actor_id"] == "usr_alice"
        assert item["status"] == "failure"

    def test_no_filter_returns_all(self, client, admin_client, auth_headers):
        """Без новых фильтров поведение не изменилось."""
        _ingest(client, auth_headers, actor_id="usr_a")
        _ingest(client, auth_headers, actor_id="usr_b")

        body = admin_client.get(
            "/api/logging/v1/events", params={"include_total": "true"},
        ).json()
        assert body["total"] == 2

    def test_unknown_actor_returns_empty(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers, actor_id="usr_a")
        body = admin_client.get(
            "/api/logging/v1/events",
            params={"actor_id": "usr_nonexistent", "include_total": "true"},
        ).json()
        assert body["total"] == 0
