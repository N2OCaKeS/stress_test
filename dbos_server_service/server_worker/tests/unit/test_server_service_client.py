"""Unit-тесты `src/services/server_service_client.py`.

* 200 → JSON; не-200 → типизированный CredentialFetchError;
* httpx.HTTPError → SERVER_SERVICE_UNREACHABLE;
* PAT (`worker_bot_token`) попадает в Authorization безусловно (пустой токен
  отсекает startup-валидатор Settings, тест проверяет happy-path с placeholder);
* `submit_rotated_password` НЕ логирует plaintext в exception.details.
"""

from __future__ import annotations

import httpx
import pytest

from src.core.exceptions import CredentialFetchError
from src.services import http_pool, server_service_client


@pytest.fixture(autouse=True)
def _reset_http_pool():
    """Сбрасываем закешированный pooled-клиент между тестами.

    Тесты подменяют `httpx.AsyncClient` через `monkeypatch.setattr` —
    закешированный реальный экземпляр пережил бы patch и продолжил
    ходить в сеть. Симметрично фикстуре в `test_audit_client.py`.
    """
    http_pool.reset_for_tests()
    yield
    http_pool.reset_for_tests()


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self): return self._payload


class _Client:
    def __init__(self, response, capture_kwargs=None):
        self._response = response
        self._capture = capture_kwargs

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass

    async def get(self, url, headers=None):
        if self._capture is not None:
            self._capture.update(url=url, headers=headers or {})
        return self._response

    async def post(self, url, headers=None, json=None):
        if self._capture is not None:
            self._capture.update(url=url, headers=headers or {}, json=json)
        return self._response


@pytest.fixture
def settings_stub(monkeypatch):
    class _S:
        server_service_url = "http://srv.test"
        worker_bot_token = "wbt-1"
        http_request_timeout_seconds = 5.0

    monkeypatch.setattr("src.services.server_service_client.get_settings", lambda: _S())
    return _S


# ── fetch_ipmi_credentials ───────────────────────────────────────────────────

class TestFetchIpmi:
    async def test_200_returns_payload(self, settings_stub, monkeypatch):
        cap = {}
        client = _Client(_Resp(200, {"kind": "idrac", "username": "u", "password": "p"}), cap)
        monkeypatch.setattr(httpx, "AsyncClient", client)

        got = await server_service_client.fetch_ipmi_credentials("srv_1")
        assert got["kind"] == "idrac"
        assert "Authorization" in cap["headers"]
        assert cap["headers"]["Authorization"] == "Bearer wbt-1"

    async def test_404_raises_unavailable(self, settings_stub, monkeypatch):
        monkeypatch.setattr(httpx, "AsyncClient", _Client(_Resp(404)))
        with pytest.raises(CredentialFetchError) as exc:
            await server_service_client.fetch_ipmi_credentials("srv_1")
        assert exc.value.error_code == "IPMI_CREDENTIALS_UNAVAILABLE"

    async def test_401_raises_unavailable(self, settings_stub, monkeypatch):
        monkeypatch.setattr(httpx, "AsyncClient", _Client(_Resp(401)))
        with pytest.raises(CredentialFetchError):
            await server_service_client.fetch_ipmi_credentials("srv_1")

    async def test_500_raises_unavailable(self, settings_stub, monkeypatch):
        monkeypatch.setattr(httpx, "AsyncClient", _Client(_Resp(500)))
        with pytest.raises(CredentialFetchError):
            await server_service_client.fetch_ipmi_credentials("srv_1")

    async def test_network_error_unreachable(self, settings_stub, monkeypatch):
        class _Boom:
            def __call__(self, *a, **kw): return self
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def get(self, *a, **kw):
                raise httpx.ConnectError("down")

        monkeypatch.setattr(httpx, "AsyncClient", _Boom())
        with pytest.raises(CredentialFetchError) as exc:
            await server_service_client.fetch_ipmi_credentials("srv_1")
        assert exc.value.error_code == "SERVER_SERVICE_UNREACHABLE"

    async def test_authorization_header_always_present(self, settings_stub, monkeypatch):
        """`_headers` всегда добавляет Bearer — пустой токен отсекает
        Settings-валидатор на старте (см. test_worker_bot_token_required.py).
        """
        cap = {}
        monkeypatch.setattr(httpx, "AsyncClient", _Client(_Resp(200, {}), cap))
        await server_service_client.fetch_ipmi_credentials("srv_1")
        assert cap["headers"]["Authorization"] == "Bearer wbt-1"

    async def test_target_department_id_forwarded_as_header(self, settings_stub, monkeypatch):
        """Worker forwards `target_department_id` arg as X-Target-Department-Id header."""
        cap = {}
        monkeypatch.setattr(httpx, "AsyncClient", _Client(_Resp(200, {"kind": "idrac"}), cap))
        await server_service_client.fetch_ipmi_credentials("srv_1", "dep_xyz")
        assert cap["headers"].get("X-Target-Department-Id") == "dep_xyz"

    async def test_no_target_department_id_omits_header(self, settings_stub, monkeypatch):
        cap = {}
        monkeypatch.setattr(httpx, "AsyncClient", _Client(_Resp(200, {"kind": "idrac"}), cap))
        await server_service_client.fetch_ipmi_credentials("srv_1")
        assert "X-Target-Department-Id" not in cap["headers"]


# ── fetch_account_password ───────────────────────────────────────────────────

class TestFetchAccount:
    async def test_404_includes_ids_in_details(self, settings_stub, monkeypatch):
        monkeypatch.setattr(httpx, "AsyncClient", _Client(_Resp(404)))
        with pytest.raises(CredentialFetchError) as exc:
            await server_service_client.fetch_account_password("srv_X", "acc_Y")
        assert exc.value.error_code == "ACCOUNT_PASSWORD_UNAVAILABLE"
        assert exc.value.details["server_id"] == "srv_X"
        assert exc.value.details["account_id"] == "acc_Y"
        assert exc.value.details["status_code"] == 404


# ── submit_rotated_password ──────────────────────────────────────────────────

class TestSubmitRotated:
    async def test_200_returns_payload(self, settings_stub, monkeypatch):
        cap = {}
        monkeypatch.setattr(httpx, "AsyncClient", _Client(_Resp(200, {"ok": True}), cap))
        await server_service_client.submit_rotated_password("srv_1", "acc_1", "secret-pwd")
        # пароль должен быть в JSON-body, но больше нигде
        assert cap["json"]["password"] == "secret-pwd"

    async def test_400_rejected(self, settings_stub, monkeypatch):
        monkeypatch.setattr(httpx, "AsyncClient", _Client(_Resp(400)))
        with pytest.raises(CredentialFetchError) as exc:
            await server_service_client.submit_rotated_password("s", "a", "secret-do-not-leak")
        assert exc.value.error_code == "PASSWORD_ROTATE_REJECTED"

    async def test_password_not_in_exception_details(self, settings_stub, monkeypatch):
        """Plaintext пароль не должен попасть в exception.details / message."""
        monkeypatch.setattr(httpx, "AsyncClient", _Client(_Resp(500)))
        try:
            await server_service_client.submit_rotated_password("s", "a", "highly-secret-value")
        except CredentialFetchError as exc:
            assert "highly-secret-value" not in exc.message
            assert "highly-secret-value" not in str(exc.details)

    async def test_password_not_in_exception_on_network_error(self, settings_stub, monkeypatch):
        class _Boom:
            def __call__(self, *a, **kw): return self
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def post(self, *a, **kw):
                raise httpx.ConnectError("down")

        monkeypatch.setattr(httpx, "AsyncClient", _Boom())
        try:
            await server_service_client.submit_rotated_password("s", "a", "totally-secret-pwd")
        except CredentialFetchError as exc:
            assert "totally-secret-pwd" not in exc.message + str(exc.details)


# ── submit_inventory_facts ───────────────────────────────────────────────────


class TestSubmitInventory:
    async def test_200_returns_payload(self, settings_stub, monkeypatch):
        cap = {}
        monkeypatch.setattr(
            httpx, "AsyncClient",
            _Client(_Resp(200, {"ok": True, "disks_upserted": 1}), cap),
        )
        flat_payload = {
            "hostname": "h1", "kernel": "5.15.0",
            "cpu_brand": "Intel", "cpu_model": "Xeon Silver 4314",
            "cpu_cores": 8, "cpu_threads": 16, "cpu_frequency_ghz": 2.4,
            "os_version": "Astra 1.7", "disks": [], "lspci": None,
        }
        out = await server_service_client.submit_inventory_facts("srv_1", flat_payload)
        assert out["ok"] is True
        # flat payload отправлен как есть, без обёртки {"facts": ...}.
        assert cap["json"] == flat_payload
        assert "facts" not in cap["json"]

    async def test_2xx_empty_body_returns_empty_dict(self, settings_stub, monkeypatch):
        class _RespNoBody:
            status_code = 204
            def json(self): raise ValueError("no body")
        monkeypatch.setattr(httpx, "AsyncClient", _Client(_RespNoBody()))
        out = await server_service_client.submit_inventory_facts("srv_1", {})
        assert out == {}

    async def test_404_rejected(self, settings_stub, monkeypatch):
        monkeypatch.setattr(httpx, "AsyncClient", _Client(_Resp(404)))
        with pytest.raises(CredentialFetchError) as exc:
            await server_service_client.submit_inventory_facts("srv_1", {})
        assert exc.value.error_code == "INVENTORY_SUBMIT_REJECTED"
        assert exc.value.details["server_id"] == "srv_1"
        assert exc.value.details["status_code"] == 404

    async def test_500_rejected(self, settings_stub, monkeypatch):
        monkeypatch.setattr(httpx, "AsyncClient", _Client(_Resp(500)))
        with pytest.raises(CredentialFetchError) as exc:
            await server_service_client.submit_inventory_facts("srv_1", {})
        assert exc.value.error_code == "INVENTORY_SUBMIT_REJECTED"

    async def test_network_error_unreachable(self, settings_stub, monkeypatch):
        class _Boom:
            def __call__(self, *a, **kw): return self
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def post(self, *a, **kw):
                raise httpx.ConnectError("down")
        monkeypatch.setattr(httpx, "AsyncClient", _Boom())
        with pytest.raises(CredentialFetchError) as exc:
            await server_service_client.submit_inventory_facts("srv_1", {})
        assert exc.value.error_code == "SERVER_SERVICE_UNREACHABLE"

    async def test_target_department_id_forwarded(self, settings_stub, monkeypatch):
        cap = {}
        monkeypatch.setattr(
            httpx, "AsyncClient", _Client(_Resp(200, {}), cap),
        )
        await server_service_client.submit_inventory_facts("srv_1", {}, "dep_42")
        assert cap["headers"].get("X-Target-Department-Id") == "dep_42"


# ── submit_rotated_ipmi_password ─────────────────────────────────────────────


class TestSubmitRotatedIpmi:
    async def test_url_uses_controller_id_not_server_id(self, settings_stub, monkeypatch):
        cap = {}
        monkeypatch.setattr(httpx, "AsyncClient", _Client(_Resp(200, {"ok": True}), cap))
        await server_service_client.submit_rotated_ipmi_password(
            "ipm_42", "secret-pwd", "2026-05-21T10:00:00Z",
            verified_at="2026-05-21T10:00:05Z",
        )
        assert "/ipmi-controllers/ipm_42/credentials_rotated" in cap["url"]
        assert cap["json"] == {
            "new_password": "secret-pwd",
            "rotated_at": "2026-05-21T10:00:00Z",
            "verified_at": "2026-05-21T10:00:05Z",
        }

    async def test_verified_at_included_when_passed(self, settings_stub, monkeypatch):
        cap = {}
        monkeypatch.setattr(httpx, "AsyncClient", _Client(_Resp(200, {"ok": True}), cap))
        await server_service_client.submit_rotated_ipmi_password(
            "ipm_42", "secret-pwd", "2026-05-21T10:00:00Z",
            verified_at="2026-05-21T10:00:05Z",
        )
        assert cap["json"] == {
            "new_password": "secret-pwd",
            "rotated_at": "2026-05-21T10:00:00Z",
            "verified_at": "2026-05-21T10:00:05Z",
        }

    async def test_404_rejected(self, settings_stub, monkeypatch):
        monkeypatch.setattr(httpx, "AsyncClient", _Client(_Resp(404)))
        with pytest.raises(CredentialFetchError) as exc:
            await server_service_client.submit_rotated_ipmi_password(
                "ipm_42", "p", "2026-05-21T10:00:00Z",
                verified_at="2026-05-21T10:00:05Z",
            )
        assert exc.value.error_code == "IPMI_ROTATE_REJECTED"
        assert exc.value.details["ipmi_controller_id"] == "ipm_42"

    async def test_password_not_in_exception(self, settings_stub, monkeypatch):
        monkeypatch.setattr(httpx, "AsyncClient", _Client(_Resp(500)))
        try:
            await server_service_client.submit_rotated_ipmi_password(
                "ipm_1", "highly-secret-bmc-pwd", "2026-05-21T10:00:00Z",
                verified_at="2026-05-21T10:00:05Z",
            )
        except CredentialFetchError as exc:
            assert "highly-secret-bmc-pwd" not in exc.message
            assert "highly-secret-bmc-pwd" not in str(exc.details)

    async def test_target_department_id_forwarded(self, settings_stub, monkeypatch):
        cap = {}
        monkeypatch.setattr(httpx, "AsyncClient", _Client(_Resp(200, {"ok": True}), cap))
        await server_service_client.submit_rotated_ipmi_password(
            "ipm_42", "p", "2026-05-21T10:00:00Z", "dep_42",
            verified_at="2026-05-21T10:00:05Z",
        )
        assert cap["headers"].get("X-Target-Department-Id") == "dep_42"


