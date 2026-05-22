"""Интеграция: HTTP-запрос → middleware заполняет audit_context → emit подхватывает.

Запросы идут через AsyncClient + ASGITransport (in-process), audit_service.emit
перехватывается через monkeypatch httpx.post в audit_service.
"""

import logging
from unittest.mock import patch

import pytest

from src.services import audit_service


@pytest.fixture()
def capture_audit_payloads(monkeypatch):
    """Перехватывает payload из emit() и через async-путь (AsyncClient в
    in-process FastAPI лупе), и через sync-fallback (worker-поток без loop'а).

    Sync-fallback больше не делает blocking httpx.post — он пишет payload в
    `logger.info('audit_event_fallback ...')`. Поэтому подменяем и
    AsyncClient (для login/refresh — emit из async handler'а), и logger.info
    (для middleware `audit_access`, который зовёт emit через
    `asyncio.to_thread` без running loop).
    """
    from src.services import audit_service as _audit_service

    captured: list[dict] = []

    class _AsyncClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def post(self, url, json, headers):
            captured.append(json)
            class R:
                status_code = 201
            return R()

    monkeypatch.setattr("src.services.audit_service.httpx.AsyncClient", _AsyncClient)

    original_info = _audit_service.logger.info

    def fake_info(msg, *args, **kwargs):
        if isinstance(msg, str) and msg.startswith("audit_event_fallback") and args:
            payload = args[0]
            if isinstance(payload, dict):
                captured.append(payload)
                return
        original_info(msg, *args, **kwargs)

    monkeypatch.setattr(_audit_service.logger, "info", fake_info)
    monkeypatch.setattr("src.services.audit_service.get_settings", lambda: type(
        "S", (), {"logging_service_url": "http://test", "logging_service_api_key": "k"},
    )())
    return captured


# ── Login наполняет контекст и шлёт богатый событие ──────────────────────────


class TestLoginEnrichesAudit:
    async def test_successful_login_includes_username_and_dept(
        self, client, account_admin, capture_audit_payloads
    ):
        r = await client.post(
            "/api/auth/v1/login",
            json={"username": "t_admin", "password": "Admin1234!"},
            headers={"User-Agent": "pytest-client/1.0"},
        )
        assert r.status_code == 200

        # Среди опубликованных событий должен быть user.login success
        login_events = [p for p in capture_audit_payloads if p["action"] == "user.login"
                        and p["status"] == "success"]
        assert len(login_events) == 1
        ev = login_events[0]
        assert ev["actor_id"] == account_admin.id
        assert ev["username"] == "t_admin"
        # department у account_admin может быть None — главное чтобы поле было
        assert "department_id" in ev
        # ip/user_agent в details
        assert ev["details"].get("user_agent") == "pytest-client/1.0"

    async def test_failed_login_masks_password_if_leaked(
        self, client, account_admin, capture_audit_payloads
    ):
        """details для login failure не содержат пароля; даже если бы содержали — был бы маскирован."""
        await client.post(
            "/api/auth/v1/login",
            json={"username": "t_admin", "password": "wrong"},
        )
        login_failures = [p for p in capture_audit_payloads
                          if p["action"] == "user.login" and p["status"] == "failure"]
        assert len(login_failures) == 1
        details = login_failures[0]["details"]
        # пароля не должно быть в details
        for v in details.values():
            assert v != "wrong"


# ── Authenticated request наполняет контекст через JWT ───────────────────────


class TestAuthenticatedRequestContext:
    @pytest.fixture()
    def trust_loopback(self, monkeypatch):
        """ASGITransport ставит client.host=127.0.0.1; чтобы XFF поднимался
        в audit, тестовому процессу нужно доверять loopback'у. Middleware
        в `main.py` зовёт `extract_client_ip(request, settings.trusted_proxy_ips)`
        — подменяем helper на версию с loopback в allow-list."""
        from src import main as main_mod
        from src.services import audit_context as ac_mod

        original_helper = main_mod.extract_client_ip

        def _patched(request, _ips):
            return ac_mod.extract_client_ip(request, ["127.0.0.1"])

        monkeypatch.setattr(main_mod, "extract_client_ip", _patched)
        yield

    async def test_me_endpoint_carries_username_in_audit(
        self, client, account_admin, capture_audit_payloads, trust_loopback,
    ):
        login = await client.post("/api/auth/v1/login",
                                  json={"username": "t_admin",
                                        "password": "Admin1234!"})
        token = login.json()["access_token"]
        # Очищаем events от login
        capture_audit_payloads.clear()

        r = await client.get("/api/auth/v1/me",
                             headers={"Authorization": f"Bearer {token}",
                                      "User-Agent": "test-ua/2.0",
                                      "X-Forwarded-For": "203.0.113.42, 10.0.0.1"})
        assert r.status_code == 200

        me_events = [p for p in capture_audit_payloads if p["action"] == "user.me"]
        assert len(me_events) == 1
        ev = me_events[0]
        assert ev["actor_id"] == account_admin.id
        assert ev["username"] == "t_admin"
        assert ev["details"].get("ip") == "203.0.113.42"
        assert ev["details"].get("user_agent") == "test-ua/2.0"

    async def test_http_access_denied_event_for_401(
        self, client, capture_audit_payloads
    ):
        r = await client.get("/api/auth/v1/me")  # без Bearer
        assert r.status_code == 401

        # middleware audit_access должен сэмитить http.access_denied
        denied = [p for p in capture_audit_payloads
                  if p["action"] == "http.access_denied"]
        assert len(denied) == 1
        ev = denied[0]
        assert ev["status"] == "denied"
        assert ev["allowed"] is False
        assert ev["details"]["status_code"] == 401


# ── Details ВСЕГДА содержат контекст действия + пароли/токены маскируются ────


class TestDetailsAlwaysFilled:
    async def test_user_create_records_full_context_with_masked_password(
        self, client, admin_token, dept_a, capture_audit_payloads
    ):
        """user.create — details содержат username, department, role И замаскированный пароль."""
        r = await client.post(
            "/api/auth/v1/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "username": "audit_subject",
                "password": "S3cretP@ss!",
                "department_id": dept_a.id,
            },
        )
        assert r.status_code == 201

        creates = [p for p in capture_audit_payloads if p["action"] == "user.create"]
        assert len(creates) == 1
        details = creates[0]["details"]
        # WHAT админ задавал — есть в логе
        assert details["username"] == "audit_subject"
        assert details["department_id"] == dept_a.id
        # Пароль НЕ в открытом виде
        assert details["password"] == "<PASSWORD>"
        assert details["password"] != "S3cretP@ss!"

    async def test_pat_create_masks_token_in_details(
        self, client, admin_token, capture_audit_payloads
    ):
        """pat.create — details содержат имя PAT, allowed_services и замаскированный raw-токен."""
        r = await client.post(
            "/api/auth/v1/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "my-cli-token", "allowed_services": []},
        )
        assert r.status_code == 201

        creates = [p for p in capture_audit_payloads if p["action"] == "pat.create"]
        assert len(creates) == 1
        details = creates[0]["details"]
        assert details["name"] == "my-cli-token"
        # raw PAT-токен заменён на <TOKEN> (по эвристике dbos_pat_ или по ключу)
        assert details["token"] == "<TOKEN>"
        # token_prefix виден — это безопасный идентификатор для поиска
        assert details["token_prefix"].startswith("dbos_pat_")
