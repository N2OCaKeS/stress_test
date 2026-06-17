"""Тесты: require_reader — кто допущен к чтению audit'а.

К чтению audit'а допускаются пять платформенных ролей. Три cross-dept
(`loging_admin`, `loging_reader`, `account_admin`) видят журнал целиком;
`account_admin` ограничен чтением (правила/retention остаются за
`loging_admin`). Две dept-scoped (`loging_reader_dep`, `department_admin`)
читают аудит строго своего отдела — переданный `department_id`
перекрывается отделом из identity, отсутствие отдела → 403 (fail-closed).
Service-роли в `loging_service` и пользователи без платформенной роли
возвращают 403 INSUFFICIENT_ROLE.
"""

import httpx

from src.dependencies import auth as _auth_deps
from tests.conftest import make_event

EVENTS_URL = "/api/logging/v1/events"
RULES_URL = "/api/logging/v1/rules"
RETENTION_URL = "/api/logging/v1/retention"


class _IntrospectCtx:
    """Минимальный context-manager-совместимый объект — поддерживает старый
    `p.stop()`-интерфейс хелпера, под которым жили тесты файла."""

    def __init__(self, attr: str, pooled: httpx.AsyncClient, original):
        self._attr = attr
        self._pooled = pooled
        self._original = original

    def stop(self):
        setattr(_auth_deps, self._attr, self._original)
        import asyncio
        asyncio.run(self._pooled.aclose())


def _mock_identity(client, identity_payload):
    """Хелпер: мок introspect-ответа от auth_service.

    Принимает короткий dict с полями user_id/username/... и автоматически
    оборачивает его в формат IntrospectResponse (active=True, sub=...).
    Подменяет pooled `_introspect_client` AsyncClient'ом с MockTransport.
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
    return _IntrospectCtx("_introspect_client", pooled, original)


def _ingest(client, auth_headers, **overrides):
    return client.post(EVENTS_URL, headers=auth_headers, json=make_event(**overrides))


# ── allow: loging_admin / loging_reader — обе глобальные ──────────────────────


class TestAllowedReaders:
    def test_loging_admin_can_list_events(self, client, auth_headers):
        _ingest(client, auth_headers, department_id="dep_a")
        _ingest(client, auth_headers, department_id="dep_b")
        p = _mock_identity(client, {"user_id": "u1", "username": "la",
                                     "platform_role": "loging_admin", "department_id": None})
        try:
            r = client.get(
                EVENTS_URL,
                headers={"Authorization": "Bearer t"},
                params={"include_total": "true"},
            )
        finally:
            p.stop()
        assert r.status_code == 200
        # Видит события всех отделов.
        assert r.json()["total"] == 2

    def test_loging_reader_sees_all_events_cross_dept(self, client, auth_headers):
        """loging_reader теперь global-read: видит cross-dept."""
        _ingest(client, auth_headers, department_id="dep_a")
        _ingest(client, auth_headers, department_id="dep_a")
        _ingest(client, auth_headers, department_id="dep_b")
        p = _mock_identity(client, {"user_id": "u1", "username": "lr",
                                     "platform_role": "loging_reader", "department_id": None})
        try:
            r = client.get(
                EVENTS_URL,
                headers={"Authorization": "Bearer t"},
                params={"include_total": "true"},
            )
        finally:
            p.stop()
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 3

    def test_loging_reader_can_filter_by_arbitrary_department(self, client, auth_headers):
        """Фильтр `department_id` — обычный query, scope-enforcement'а нет."""
        _ingest(client, auth_headers, department_id="dep_a")
        _ingest(client, auth_headers, department_id="dep_b")
        p = _mock_identity(client, {"user_id": "u1", "username": "lr",
                                     "platform_role": "loging_reader", "department_id": "dep_a"})
        try:
            r = client.get(
                EVENTS_URL,
                headers={"Authorization": "Bearer t"},
                params={"department_id": "dep_b", "include_total": "true"},
            )
        finally:
            p.stop()
        assert r.status_code == 200
        body = r.json()
        # loging_reader с dept_id=dep_a свободно запрашивает dep_b — никакого
        # 403 DEPARTMENT_SCOPE_VIOLATION больше нет.
        assert body["total"] == 1
        assert body["items"][0]["department_id"] == "dep_b"


    def test_account_admin_sees_all_events_cross_dept(self, client, auth_headers):
        """account_admin получает read-only по всему журналу (cross-dept)."""
        _ingest(client, auth_headers, department_id="dep_a")
        _ingest(client, auth_headers, department_id="dep_b")
        p = _mock_identity(client, {"user_id": "u1", "username": "aa",
                                     "platform_role": "account_admin", "department_id": None})
        try:
            r = client.get(
                EVENTS_URL,
                headers={"Authorization": "Bearer t"},
                params={"include_total": "true"},
            )
        finally:
            p.stop()
        assert r.status_code == 200
        assert r.json()["total"] == 2


# ── loging_reader_dep — жёсткий dept-scope ────────────────────────────────────


class TestDeptScopedReader:
    def test_sees_only_own_department(self, client, auth_headers):
        """loging_reader_dep видит только события своего отдела."""
        _ingest(client, auth_headers, department_id="dep_a")
        _ingest(client, auth_headers, department_id="dep_a")
        _ingest(client, auth_headers, department_id="dep_b")
        p = _mock_identity(client, {"user_id": "u1", "username": "lrd",
                                     "platform_role": "loging_reader_dep",
                                     "department_id": "dep_a"})
        try:
            r = client.get(
                EVENTS_URL,
                headers={"Authorization": "Bearer t"},
                params={"include_total": "true"},
            )
        finally:
            p.stop()
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 2
        assert {item["department_id"] for item in body["items"]} == {"dep_a"}

    def test_cannot_read_other_department_via_query_override(self, client, auth_headers):
        """Передача чужого department_id перекрывается своим отделом — чужие
        события НЕ видны."""
        _ingest(client, auth_headers, department_id="dep_a")
        _ingest(client, auth_headers, department_id="dep_b")
        p = _mock_identity(client, {"user_id": "u1", "username": "lrd",
                                     "platform_role": "loging_reader_dep",
                                     "department_id": "dep_a"})
        try:
            r = client.get(
                EVENTS_URL,
                headers={"Authorization": "Bearer t"},
                params={"department_id": "dep_b", "include_total": "true"},
            )
        finally:
            p.stop()
        assert r.status_code == 200
        body = r.json()
        # Запросил dep_b, но scope форснул dep_a — видит только своё.
        assert body["total"] == 1
        assert body["items"][0]["department_id"] == "dep_a"

    def test_no_department_in_identity_returns_403(self, client, auth_headers):
        """Защитный fail-closed: loging_reader_dep без department_id → 403,
        а не доступ ко всему журналу."""
        _ingest(client, auth_headers, department_id="dep_a")
        p = _mock_identity(client, {"user_id": "u1", "username": "lrd",
                                     "platform_role": "loging_reader_dep",
                                     "department_id": None})
        try:
            r = client.get(EVENTS_URL, headers={"Authorization": "Bearer t"})
        finally:
            p.stop()
        assert r.status_code == 403
        assert r.json()["error_code"] == "INSUFFICIENT_ROLE"

    def test_rules_returns_403(self, client):
        """loging_reader_dep не управляет правилами → require_admin отбивает 403."""
        p = _mock_identity(client, {"user_id": "u1", "username": "lrd",
                                     "platform_role": "loging_reader_dep",
                                     "department_id": "dep_a"})
        try:
            r = client.get(RULES_URL, headers={"Authorization": "Bearer t"})
        finally:
            p.stop()
        assert r.status_code == 403
        assert r.json()["error_code"] == "INSUFFICIENT_ROLE"

    def test_retention_returns_403(self, client):
        """loging_reader_dep не управляет retention → require_admin отбивает 403."""
        p = _mock_identity(client, {"user_id": "u1", "username": "lrd",
                                     "platform_role": "loging_reader_dep",
                                     "department_id": "dep_a"})
        try:
            r = client.get(RETENTION_URL, headers={"Authorization": "Bearer t"})
        finally:
            p.stop()
        assert r.status_code == 403
        assert r.json()["error_code"] == "INSUFFICIENT_ROLE"


# ── department_admin — жёсткий dept-scope (как loging_reader_dep) ─────────────


class TestDeptAdminScopedReader:
    """department_admin читает аудит ТОЛЬКО своего отдела — тот же жёсткий
    dept-scope, что и у `loging_reader_dep`."""

    def test_sees_only_own_department(self, client, auth_headers):
        _ingest(client, auth_headers, department_id="dep_a")
        _ingest(client, auth_headers, department_id="dep_a")
        _ingest(client, auth_headers, department_id="dep_b")
        p = _mock_identity(client, {"user_id": "u1", "username": "da",
                                     "platform_role": "department_admin",
                                     "department_id": "dep_a"})
        try:
            r = client.get(
                EVENTS_URL,
                headers={"Authorization": "Bearer t"},
                params={"include_total": "true"},
            )
        finally:
            p.stop()
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 2
        assert {item["department_id"] for item in body["items"]} == {"dep_a"}

    def test_cannot_read_other_department_via_query_override(self, client, auth_headers):
        """Чужой `?department_id=` перекрывается своим отделом."""
        _ingest(client, auth_headers, department_id="dep_a")
        _ingest(client, auth_headers, department_id="dep_b")
        p = _mock_identity(client, {"user_id": "u1", "username": "da",
                                     "platform_role": "department_admin",
                                     "department_id": "dep_a"})
        try:
            r = client.get(
                EVENTS_URL,
                headers={"Authorization": "Bearer t"},
                params={"department_id": "dep_b", "include_total": "true"},
            )
        finally:
            p.stop()
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["department_id"] == "dep_a"

    def test_no_department_in_identity_returns_403(self, client, auth_headers):
        """fail-closed: department_admin без department_id → 403, а не весь журнал."""
        _ingest(client, auth_headers, department_id="dep_a")
        p = _mock_identity(client, {"user_id": "u1", "username": "da",
                                     "platform_role": "department_admin",
                                     "department_id": None})
        try:
            r = client.get(EVENTS_URL, headers={"Authorization": "Bearer t"})
        finally:
            p.stop()
        assert r.status_code == 403
        assert r.json()["error_code"] == "INSUFFICIENT_ROLE"

    def test_stats_scoped_to_own_department(self, client, auth_headers):
        _ingest(client, auth_headers, department_id="dep_a")
        _ingest(client, auth_headers, department_id="dep_b")
        p = _mock_identity(client, {"user_id": "u1", "username": "da",
                                     "platform_role": "department_admin",
                                     "department_id": "dep_a"})
        try:
            r = client.get(
                "/api/logging/v1/events/stats",
                headers={"Authorization": "Bearer t"},
                params={"department_id": "dep_b"},
            )
        finally:
            p.stop()
        assert r.status_code == 200
        # Запросил dep_b, scope форснул dep_a — считает только своё.
        assert r.json()["total"] == 1

    def test_export_scoped_to_own_department(self, client, auth_headers):
        _ingest(client, auth_headers, department_id="dep_a")
        _ingest(client, auth_headers, department_id="dep_b")
        p = _mock_identity(client, {"user_id": "u1", "username": "da",
                                     "platform_role": "department_admin",
                                     "department_id": "dep_a"})
        try:
            r = client.get(
                "/api/logging/v1/events/export",
                headers={"Authorization": "Bearer t"},
                params={"department_id": "dep_b"},
            )
        finally:
            p.stop()
        assert r.status_code == 200
        body = r.text.splitlines()
        # Header + ровно одна строка своего отдела (dep_b отфильтрован scope'ом).
        assert len(body) == 2
        assert "dep_a" in body[1]
        assert "dep_b" not in body[1]

    def test_rules_returns_403(self, client):
        """department_admin НЕ управляет правилами → require_admin отбивает 403."""
        p = _mock_identity(client, {"user_id": "u1", "username": "da",
                                     "platform_role": "department_admin",
                                     "department_id": "dep_a"})
        try:
            r = client.get(RULES_URL, headers={"Authorization": "Bearer t"})
        finally:
            p.stop()
        assert r.status_code == 403
        assert r.json()["error_code"] == "INSUFFICIENT_ROLE"

    def test_retention_returns_403(self, client):
        """department_admin НЕ управляет retention → require_admin отбивает 403."""
        p = _mock_identity(client, {"user_id": "u1", "username": "da",
                                     "platform_role": "department_admin",
                                     "department_id": "dep_a"})
        try:
            r = client.get(RETENTION_URL, headers={"Authorization": "Bearer t"})
        finally:
            p.stop()
        assert r.status_code == 403
        assert r.json()["error_code"] == "INSUFFICIENT_ROLE"


# ── deny: service-роли / без роли ─────────────────────────────────────────────


class TestDeniedReaders:
    def test_loging_service_role_reader_returns_403(self, client):
        """Service-роль `reader` в loging_service больше не пускается:
        чтение требует именно платформенной `loging_reader` / `loging_admin`."""
        p = _mock_identity(client, {
            "user_id": "u1", "username": "sr",
            "platform_role": None,
            "department_id": "dep_a",
            "service_roles": {"loging_service": ["reader"]},
        })
        try:
            r = client.get(EVENTS_URL, headers={"Authorization": "Bearer t"})
        finally:
            p.stop()
        assert r.status_code == 403
        assert r.json()["error_code"] == "INSUFFICIENT_ROLE"

    def test_user_without_any_role_returns_403(self, client):
        """platform_role=None, нет ролей в loging_service → 403 INSUFFICIENT_ROLE."""
        p = _mock_identity(client, {
            "user_id": "u1", "username": "nobody",
            "platform_role": None,
            "department_id": "dep_a",
            "service_roles": {"config_service": ["reader"]},
        })
        try:
            r = client.get(EVENTS_URL, headers={"Authorization": "Bearer t"})
        finally:
            p.stop()
        assert r.status_code == 403
        assert r.json()["error_code"] == "INSUFFICIENT_ROLE"
