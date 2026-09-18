"""Тесты `/api/testing/v1/test-stands` — тестовые стенды.

Чтение доступно любой роли СВОЕГО отдела, аноним → 401, чужой отдел → 403
(см. `test_department_isolation.py`). Запись — под матрицей прав
(сид-миграция даёт её системной роли `admin`), тот же паттерн, что и у
`test_definitions`.

Создание и карточка одного стенда идут живым pass-through вызовом к
server_service (`server_client.get_server`) — транспорт подменяется
MockTransport'ом, тем же приёмом, что и у резолверов choices
(`test_choices_resolvers.py`).
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from src.services import server_client
from tests.conftest import auth_hdr as _hdr

BASE = "/api/testing/v1/test-stands"


class _StubSettings:
    """Минимальный Settings для server_client — только то, что он читает."""

    server_service_url = "http://server-service"
    server_service_api_key = "dbos_bot_test"
    server_request_timeout_seconds = 2.0


@pytest.fixture
def mock_server_service(monkeypatch):
    """Подменяет транспорт исходящих вызовов в server_service.

    Возвращает функцию, которой тест задаёт свой handler(request) -> Response.
    """
    monkeypatch.setattr(server_client, "get_settings", lambda: _StubSettings())

    def _install(handler):
        def _build(timeout: float) -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.MockTransport(handler))

        monkeypatch.setattr(server_client, "build_client", _build)

    return _install


def _server_found(department_id: str = "dep_a", **extra):
    """Handler: server_service находит сервер и отдаёт его карточку."""

    def handler(request: httpx.Request) -> httpx.Response:
        server_id = request.url.path.rsplit("/", 1)[-1]
        body = {
            "id": server_id,
            "hostname": f"host-{server_id}",
            "department_id": department_id,
        }
        body.update(extra)
        return httpx.Response(200, json=body)

    return handler


def _server_not_found(request: httpx.Request) -> httpx.Response:
    return httpx.Response(404, json={})


def _server_unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def _payload(**overrides) -> dict:
    base = {"server_id": f"srv_{uuid.uuid4().hex[:8]}"}
    base.update(overrides)
    return base


async def _create_stand(
    client, token, mock_server_service, *, department_id: str = "dep_a", **payload_overrides,
) -> httpx.Response:
    mock_server_service(_server_found(department_id=department_id))
    return await client.post(BASE, headers=_hdr(token), json=_payload(**payload_overrides))


# ── Чтение ──────────────────────────────────────────────────────────────────

class TestReadAccess:
    async def test_anonymous_gets_401(self, client):
        resp = await client.get(BASE)
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "ACCESS_TOKEN_MISSING"

    async def test_user_without_roles_can_list(self, client, no_role_token):
        resp = await client.get(BASE, headers=_hdr(no_role_token))
        assert resp.status_code == 200

    async def test_get_by_id_enriches_with_server(
        self, client, admin_token, no_role_token, mock_server_service,
    ):
        created = await _create_stand(client, admin_token, mock_server_service)
        assert created.status_code == 201, created.text
        stand_id = created.json()["id"]

        mock_server_service(_server_found(department_id="dep_a", hostname="stand-host"))
        resp = await client.get(f"{BASE}/{stand_id}", headers=_hdr(no_role_token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == stand_id
        assert body["server"]["hostname"] == "stand-host"
        assert body["server_unavailable"] is False

    async def test_get_by_id_survives_unreachable_server_service(
        self, client, admin_token, no_role_token, mock_server_service,
    ):
        created = await _create_stand(client, admin_token, mock_server_service)
        assert created.status_code == 201
        stand_id = created.json()["id"]

        mock_server_service(_server_unreachable)
        resp = await client.get(f"{BASE}/{stand_id}", headers=_hdr(no_role_token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["server"] is None
        assert body["server_unavailable"] is True

    async def test_get_by_id_survives_deleted_upstream_server(
        self, client, admin_token, no_role_token, mock_server_service,
    ):
        created = await _create_stand(client, admin_token, mock_server_service)
        assert created.status_code == 201
        stand_id = created.json()["id"]

        mock_server_service(_server_not_found)
        resp = await client.get(f"{BASE}/{stand_id}", headers=_hdr(no_role_token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["server"] is None
        assert body["server_unavailable"] is True

    async def test_unknown_id_404(self, client, no_role_token):
        resp = await client.get(f"{BASE}/stand_nope", headers=_hdr(no_role_token))
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "TEST_STAND_NOT_FOUND"


# ── POST ────────────────────────────────────────────────────────────────────

class TestCreate:
    async def test_admin_creates_and_resolves_department_from_server(
        self, client, admin_token, mock_server_service,
    ):
        resp = await _create_stand(client, admin_token, mock_server_service, department_id="dep_live")
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["id"].startswith("stand_")
        assert body["department_id"] == "dep_live"
        assert body["queue_enabled"] is True
        assert body["is_active"] is True
        assert body["created_by"]

    async def test_spoofed_department_id_is_ignored(self, client, admin_token, mock_server_service):
        """`department_id` не входит в схему создания — прилетевшее значение отбрасывается."""
        mock_server_service(_server_found(department_id="dep_real"))
        resp = await client.post(
            BASE, headers=_hdr(admin_token),
            json={**_payload(), "department_id": "dep_spoofed"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["department_id"] == "dep_real"

    async def test_explicit_flags_persisted(self, client, admin_token, mock_server_service):
        resp = await _create_stand(
            client, admin_token, mock_server_service, queue_enabled=False, is_active=False,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["queue_enabled"] is False
        assert body["is_active"] is False

    async def test_legacy_token_persisted_and_unique(self, client, admin_token, mock_server_service):
        token = f"stand{uuid.uuid4().hex[:4]}"
        first = await _create_stand(client, admin_token, mock_server_service, legacy_token=token)
        assert first.status_code == 201, first.text
        assert first.json()["legacy_token"] == token

        second = await _create_stand(client, admin_token, mock_server_service, legacy_token=token)
        assert second.status_code == 409, second.text
        assert second.json()["error_code"] == "TEST_STAND_DUPLICATE"

    async def test_blank_legacy_token_becomes_null(self, client, admin_token, mock_server_service):
        """Пустая строка из формы — «имени нет»; иначе второй безымянный стенд
        упрётся в UNIQUE (NULL с NULL не конфликтует, а '' с '' — да)."""
        first = await _create_stand(client, admin_token, mock_server_service, legacy_token="")
        second = await _create_stand(client, admin_token, mock_server_service, legacy_token="  ")
        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        assert first.json()["legacy_token"] is None
        assert second.json()["legacy_token"] is None

    async def test_user_without_role_gets_403(self, client, no_role_token):
        resp = await client.post(BASE, headers=_hdr(no_role_token), json=_payload())
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "PERMISSION_DENIED"

    async def test_guest_cannot_create(self, client, guest_token):
        resp = await client.post(BASE, headers=_hdr(guest_token), json=_payload())
        assert resp.status_code == 403

    async def test_duplicate_server_id_409(self, client, admin_token, mock_server_service):
        payload = _payload()
        mock_server_service(_server_found())
        first = await client.post(BASE, headers=_hdr(admin_token), json=payload)
        assert first.status_code == 201, first.text

        mock_server_service(_server_found())
        second = await client.post(BASE, headers=_hdr(admin_token), json=payload)
        assert second.status_code == 409
        assert second.json()["error_code"] == "TEST_STAND_DUPLICATE"

    async def test_unknown_server_gives_404(self, client, admin_token, mock_server_service):
        mock_server_service(_server_not_found)
        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload())
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "SERVER_NOT_FOUND"

    async def test_unreachable_server_service_gives_503(self, client, admin_token, mock_server_service):
        mock_server_service(_server_unreachable)
        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload())
        assert resp.status_code == 503
        assert resp.json()["error_code"] == "SERVER_SERVICE_UNREACHABLE"

    async def test_missing_server_id_rejected(self, client, admin_token):
        resp = await client.post(BASE, headers=_hdr(admin_token), json={})
        assert resp.status_code == 422


# ── Список + фильтры ─────────────────────────────────────────────────────────

class TestListFilters:
    async def test_listing_is_scoped_to_own_department(
        self, client, admin_token, no_role_token, mock_server_service,
    ):
        """`department_id` стенда NOT NULL — чужой отдел не виден вообще."""
        own = await _create_stand(client, admin_token, mock_server_service, department_id="dep_a")
        assert own.status_code == 201
        other = await _create_stand(
            client, admin_token, mock_server_service, department_id="dep_other",
        )
        assert other.status_code == 201

        resp = await client.get(BASE, params={"limit": 500}, headers=_hdr(no_role_token))
        assert resp.status_code == 200
        ids = {item["id"] for item in resp.json()["items"]}
        assert own.json()["id"] in ids
        assert other.json()["id"] not in ids

    async def test_explicit_foreign_department_filter_rejected(self, client, no_role_token):
        resp = await client.get(
            BASE, params={"department_id": "dep_other"}, headers=_hdr(no_role_token),
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "DEPARTMENT_ISOLATION"

    async def test_filter_by_is_active(self, client, admin_token, no_role_token, mock_server_service):
        active = await _create_stand(client, admin_token, mock_server_service, is_active=True)
        assert active.status_code == 201
        inactive = await _create_stand(client, admin_token, mock_server_service, is_active=False)
        assert inactive.status_code == 201

        resp = await client.get(BASE, params={"is_active": False, "limit": 500}, headers=_hdr(no_role_token))
        assert resp.status_code == 200
        ids = {item["id"] for item in resp.json()["items"]}
        assert inactive.json()["id"] in ids
        assert active.json()["id"] not in ids

    async def test_filter_by_queue_enabled(self, client, admin_token, no_role_token, mock_server_service):
        enabled = await _create_stand(client, admin_token, mock_server_service, queue_enabled=True)
        assert enabled.status_code == 201
        disabled = await _create_stand(client, admin_token, mock_server_service, queue_enabled=False)
        assert disabled.status_code == 201

        resp = await client.get(BASE, params={"queue_enabled": False, "limit": 500}, headers=_hdr(no_role_token))
        assert resp.status_code == 200
        ids = {item["id"] for item in resp.json()["items"]}
        assert disabled.json()["id"] in ids
        assert enabled.json()["id"] not in ids

    async def test_list_does_not_call_server_service(self, client, admin_token, no_role_token, mock_server_service):
        """Список отдаёт только хранимые поля — без live-обогащения (без N+1)."""
        created = await _create_stand(client, admin_token, mock_server_service)
        assert created.status_code == 201

        def _boom(request: httpx.Request) -> httpx.Response:
            raise AssertionError("list не должен звать server_service")

        mock_server_service(_boom)
        resp = await client.get(BASE, params={"limit": 500}, headers=_hdr(no_role_token))
        assert resp.status_code == 200
        ids = {item["id"] for item in resp.json()["items"]}
        assert created.json()["id"] in ids
        item = next(i for i in resp.json()["items"] if i["id"] == created.json()["id"])
        assert item["server"] is None
        assert item["server_unavailable"] is False


# ── PATCH ───────────────────────────────────────────────────────────────────

class TestUpdate:
    async def _create(self, client, admin_token, mock_server_service) -> str:
        resp = await _create_stand(client, admin_token, mock_server_service)
        assert resp.status_code == 201
        return resp.json()["id"]

    async def test_admin_toggles_flags(self, client, admin_token, mock_server_service):
        stand_id = await self._create(client, admin_token, mock_server_service)
        resp = await client.patch(
            f"{BASE}/{stand_id}", headers=_hdr(admin_token),
            json={"queue_enabled": False, "is_active": False},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["queue_enabled"] is False
        assert body["is_active"] is False

    async def test_admin_sets_legacy_token(self, client, admin_token, mock_server_service):
        stand_id = await self._create(client, admin_token, mock_server_service)
        token = f"stand{uuid.uuid4().hex[:4]}"
        resp = await client.patch(
            f"{BASE}/{stand_id}", headers=_hdr(admin_token), json={"legacy_token": token},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["legacy_token"] == token

    async def test_duplicate_legacy_token_on_update_is_409(
        self, client, admin_token, mock_server_service,
    ):
        token = f"stand{uuid.uuid4().hex[:4]}"
        taken = await _create_stand(client, admin_token, mock_server_service, legacy_token=token)
        assert taken.status_code == 201, taken.text
        stand_id = await self._create(client, admin_token, mock_server_service)

        resp = await client.patch(
            f"{BASE}/{stand_id}", headers=_hdr(admin_token), json={"legacy_token": token},
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "TEST_STAND_DUPLICATE"

    async def test_server_id_field_is_ignored(self, client, admin_token, mock_server_service):
        stand_id = await self._create(client, admin_token, mock_server_service)
        original = (await client.get(
            f"{BASE}/{stand_id}", headers=_hdr(admin_token),
        )).json()["server_id"]

        resp = await client.patch(
            f"{BASE}/{stand_id}", headers=_hdr(admin_token),
            json={"server_id": "srv_spoofed", "is_active": False},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["server_id"] == original
        assert body["is_active"] is False

    async def test_no_role_gets_403(self, client, no_role_token, admin_token, mock_server_service):
        stand_id = await self._create(client, admin_token, mock_server_service)
        resp = await client.patch(
            f"{BASE}/{stand_id}", headers=_hdr(no_role_token), json={"is_active": False},
        )
        assert resp.status_code == 403

    async def test_empty_body_is_noop(self, client, admin_token, mock_server_service):
        stand_id = await self._create(client, admin_token, mock_server_service)
        resp = await client.patch(f"{BASE}/{stand_id}", headers=_hdr(admin_token), json={})
        assert resp.status_code == 200
        assert resp.json()["queue_enabled"] is True
        assert resp.json()["is_active"] is True

    async def test_unknown_id_404(self, client, admin_token):
        resp = await client.patch(
            f"{BASE}/stand_nope", headers=_hdr(admin_token), json={"is_active": False},
        )
        assert resp.status_code == 404


# ── DELETE ──────────────────────────────────────────────────────────────────

class TestDelete:
    async def _create(self, client, admin_token, mock_server_service) -> str:
        resp = await _create_stand(client, admin_token, mock_server_service)
        assert resp.status_code == 201
        return resp.json()["id"]

    async def test_admin_deletes(self, client, admin_token, no_role_token, mock_server_service):
        stand_id = await self._create(client, admin_token, mock_server_service)
        resp = await client.delete(f"{BASE}/{stand_id}", headers=_hdr(admin_token))
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        gone = await client.get(f"{BASE}/{stand_id}", headers=_hdr(no_role_token))
        assert gone.status_code == 404

    async def test_no_role_gets_403(self, client, no_role_token, admin_token, mock_server_service):
        stand_id = await self._create(client, admin_token, mock_server_service)
        resp = await client.delete(f"{BASE}/{stand_id}", headers=_hdr(no_role_token))
        assert resp.status_code == 403

    async def test_unknown_id_404(self, client, admin_token):
        resp = await client.delete(f"{BASE}/stand_nope", headers=_hdr(admin_token))
        assert resp.status_code == 404


# ── Креды учётки исполнения теста: view vs reveal ───────────────────────────

class TestTestCredentialsAudit:
    """Просмотр карточки и раскрытие секрета пишут РАЗНЫЕ audit-события.

    Зеркалит `server_service.prepare_for_test.reveal_test_credentials`:
    `viewed` (метаданные, WARNING) против `revealed` (пароль и приватный
    ключ уехали вызывающему, CRITICAL). Одним событием на оба случая SIEM
    не отличал бы просмотр карточки от выноса секрета.
    """

    @staticmethod
    def _credentials_handler(reveal: bool):
        """Handler server_service'а: карточка сервера + его test-credentials."""
        server_handler = _server_found()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/test-credentials"):
                body = {
                    "exists": True,
                    "username": "test_runner",
                    "ssh_public_key": "ssh-ed25519 AAAA",
                    "rotated_at": "2026-09-01T10:00:00+00:00",
                }
                if reveal:
                    body["password_b64"] = "cHdk"
                    body["ssh_private_key_b64"] = "cGVt"
                return httpx.Response(200, json=body)
            return server_handler(request)

        return handler

    @pytest.fixture
    def captured_audit(self, monkeypatch):
        """Перехватывает `audit_service.emit` — собирает (action, status)."""
        from src.services import audit_service

        events: list[tuple[str, str]] = []

        def _capture(action, actor_id=None, **kwargs):
            events.append((action, kwargs.get("status", "success")))

        monkeypatch.setattr(audit_service, "emit", _capture)
        return events

    async def test_view_without_reveal_stays_viewed(
        self, client, admin_token, mock_server_service, captured_audit,
    ):
        created = await _create_stand(client, admin_token, mock_server_service)
        assert created.status_code == 201, created.text
        stand_id = created.json()["id"]
        captured_audit.clear()

        mock_server_service(self._credentials_handler(False))
        resp = await client.get(
            f"{BASE}/{stand_id}/test-credentials", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["password_b64"] is None

        assert ("test_stand.test_credentials_viewed", "success") in captured_audit
        assert all(a != "test_stand.test_credentials_revealed" for a, _s in captured_audit)

    async def test_reveal_emits_separate_action(
        self, client, admin_token, mock_server_service, captured_audit,
    ):
        created = await _create_stand(client, admin_token, mock_server_service)
        assert created.status_code == 201, created.text
        stand_id = created.json()["id"]
        captured_audit.clear()

        mock_server_service(self._credentials_handler(True))
        resp = await client.get(
            f"{BASE}/{stand_id}/test-credentials?reveal=true", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["password_b64"] == "cHdk"

        assert ("test_stand.test_credentials_revealed", "success") in captured_audit
        assert all(a != "test_stand.test_credentials_viewed" for a, _s in captured_audit)

    async def test_denied_reveal_uses_revealed_action(
        self, client, admin_token, no_role_token, mock_server_service, captured_audit,
    ):
        created = await _create_stand(client, admin_token, mock_server_service)
        assert created.status_code == 201, created.text
        stand_id = created.json()["id"]
        captured_audit.clear()

        resp = await client.get(
            f"{BASE}/{stand_id}/test-credentials?reveal=true", headers=_hdr(no_role_token),
        )
        assert resp.status_code == 403
        assert ("test_stand.test_credentials_revealed", "denied") in captured_audit

    async def test_severity_split_in_catalog(self):
        from src.services.audit_events import SERVICE_EVENTS, default_severity

        assert default_severity("test_stand.test_credentials_viewed", "success") == "WARNING"
        assert default_severity("test_stand.test_credentials_revealed", "success") == "CRITICAL"
        # denied — попытка, секрет не раскрыт: WARNING в обоих случаях.
        assert default_severity("test_stand.test_credentials_revealed", "denied") == "WARNING"
        catalog = {e["action"]: e["default_severity"] for e in SERVICE_EVENTS}
        assert catalog["test_stand.test_credentials_revealed"] == "CRITICAL"
        assert catalog["test_stand.test_credentials_viewed"] == "WARNING"
