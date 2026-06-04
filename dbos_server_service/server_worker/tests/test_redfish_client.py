"""Unit-тесты `src/clients/redfish.py`.

HTTP-слой замокан через `httpx.MockTransport` — никакого реального
BMC. Покрытие:

  * happy-path для каждого high-level метода;
  * 4xx / 5xx ответы → `RedfishError` с корректным `status_code` и
    `redfish_error_code` (из `error.@Message.ExtendedInfo`);
  * 401/403 (auth) → отдельный status_code-branch;
  * transport-error (httpx.ConnectError/TimeoutException) → `RedfishError`
    с `status_code=None`;
  * malformed JSON body → `RedfishError(non-JSON)`;
  * `_normalize_host` для `https://`, bare hostname, trailing slash;
  * `_sanitize_url` убирает userinfo;
  * `_extract_redfish_error` парсит ExtendedInfo / fallback на top-level.
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.clients.redfish import (
    RedfishClient,
    RedfishError,
    _extract_redfish_error,
    _sanitize_url,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_client(handler) -> RedfishClient:
    """Собрать RedfishClient с подменённым MockTransport.

    `handler(request)` — обычный httpx callback `(request) -> Response`.
    """
    transport = httpx.MockTransport(handler)
    client = RedfishClient(
        host="https://bmc.test",
        username="root",
        password="Calvin",
    )
    # Заменяем underlying httpx-инстанс на свой с MockTransport. Это нужно
    # потому что transport нельзя передать в конструктор public API
    # RedfishClient — он намеренно скрыт.
    client._client = httpx.AsyncClient(
        base_url="https://bmc.test",
        auth=("root", "Calvin"),
        timeout=5.0,
        transport=transport,
    )
    return client


def _ok(status: int = 200, body: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json=body or {})


# ── _normalize_host ──────────────────────────────────────────────────────────


class TestNormalizeHost:
    def test_full_https_url_unchanged(self):
        c = RedfishClient("https://bmc.example.com", "u", "p")
        assert c._base_url == "https://bmc.example.com"

    def test_bare_hostname_gets_https(self):
        c = RedfishClient("bmc.example.com", "u", "p")
        assert c._base_url == "https://bmc.example.com"

    def test_trailing_slash_stripped(self):
        c = RedfishClient("https://bmc.example.com/", "u", "p")
        assert c._base_url == "https://bmc.example.com"

    def test_http_scheme_preserved(self):
        c = RedfishClient("http://localhost:8000", "u", "p")
        assert c._base_url == "http://localhost:8000"


# ── _sanitize_url ────────────────────────────────────────────────────────────


class TestSanitizeUrl:
    def test_no_userinfo_unchanged(self):
        assert _sanitize_url("https://bmc/redfish/v1") == "https://bmc/redfish/v1"

    def test_user_and_password_masked(self):
        out = _sanitize_url("https://root:Calvin@bmc/redfish/v1")
        assert "Calvin" not in out
        assert "<USER>:<PASSWORD>@bmc" in out

    def test_only_user_still_masked(self):
        out = _sanitize_url("https://root@bmc/redfish/v1")
        assert "<USER>:<PASSWORD>@bmc" in out
        assert "root" not in out


# ── _extract_redfish_error ───────────────────────────────────────────────────


class TestExtractRedfishError:
    def test_full_extended_info(self):
        body = json.dumps({
            "error": {
                "code": "Base.1.0.PropertyValueNotInList",
                "message": "top-level message",
                "@Message.ExtendedInfo": [
                    {"MessageId": "X", "Message": "deep message"},
                ],
            }
        })
        code, msg = _extract_redfish_error(body)
        assert code == "Base.1.0.PropertyValueNotInList"
        assert msg == "deep message"

    def test_fallback_to_top_message(self):
        body = json.dumps({"error": {"code": "X", "message": "fallback"}})
        code, msg = _extract_redfish_error(body)
        assert code == "X"
        assert msg == "fallback"

    def test_non_json(self):
        assert _extract_redfish_error("not json") == (None, None)

    def test_empty_string(self):
        assert _extract_redfish_error("") == (None, None)

    def test_non_object_root(self):
        assert _extract_redfish_error("[]") == (None, None)


# ── RedfishError formatting ──────────────────────────────────────────────────


class TestRedfishErrorStr:
    def test_includes_status_and_code(self):
        e = RedfishError(500, "boom", "Base.1.0.GeneralError")
        s = str(e)
        assert "boom" in s
        assert "status=500" in s
        assert "Base.1.0.GeneralError" in s

    def test_no_status_when_none(self):
        e = RedfishError(None, "transport down")
        s = str(e)
        assert "transport down" in s
        assert "status=" not in s


# ── get_power_state ──────────────────────────────────────────────────────────


class TestGetPowerState:
    async def test_returns_power_state(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/redfish/v1/Systems/1"
            return _ok(200, {"PowerState": "On"})

        async with _make_client(handler) as c:
            assert await c.get_power_state() == "On"

    async def test_404_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _ok(404, {"error": {"code": "X", "message": "no sys"}})

        async with _make_client(handler) as c:
            with pytest.raises(RedfishError) as exc:
                await c.get_power_state()
            assert exc.value.status_code == 404

    async def test_401_auth_fail(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _ok(401, {"error": {"code": "AuthFail"}})

        async with _make_client(handler) as c:
            with pytest.raises(RedfishError) as exc:
                await c.get_power_state()
            assert exc.value.status_code == 401

    async def test_500_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _ok(500, {"error": {"code": "Internal"}})

        async with _make_client(handler) as c:
            with pytest.raises(RedfishError) as exc:
                await c.get_power_state()
            assert exc.value.status_code == 500
            assert exc.value.redfish_error_code == "Internal"

    async def test_transport_error_status_none(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("conn refused")

        async with _make_client(handler) as c:
            with pytest.raises(RedfishError) as exc:
                await c.get_power_state()
            assert exc.value.status_code is None

    async def test_malformed_json_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not-json")

        async with _make_client(handler) as c:
            with pytest.raises(RedfishError) as exc:
                await c.get_power_state()
            assert "non-JSON" in exc.value.message

    async def test_missing_power_state_field(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _ok(200, {"NotPowerState": "On"})

        async with _make_client(handler) as c:
            with pytest.raises(RedfishError) as exc:
                await c.get_power_state()
            assert "PowerState" in exc.value.message

    async def test_empty_body_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _ok(200, {})

        async with _make_client(handler) as c:
            with pytest.raises(RedfishError):
                await c.get_power_state()


# ── power_action ─────────────────────────────────────────────────────────────


class TestPowerAction:
    async def test_post_reset_with_on(self):
        captured = {}
        def handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            captured["body"] = json.loads(request.content)
            return _ok(204)

        async with _make_client(handler) as c:
            await c.power_action("On")
        assert captured["path"].endswith("/Actions/ComputerSystem.Reset")
        assert captured["body"] == {"ResetType": "On"}

    async def test_force_off(self):
        captured = {}
        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return _ok(204)

        async with _make_client(handler) as c:
            await c.power_action("ForceOff")
        assert captured["body"]["ResetType"] == "ForceOff"

    async def test_409_extended_info_propagates(self):
        body = {
            "error": {
                "code": "Base.1.0.GeneralError",
                "@Message.ExtendedInfo": [
                    {"MessageId": "Base.1.0.PropertyValueNotInList",
                     "Message": "System already On"}
                ],
            }
        }
        def handler(request: httpx.Request) -> httpx.Response:
            return _ok(409, body)

        async with _make_client(handler) as c:
            with pytest.raises(RedfishError) as exc:
                await c.power_action("On")
            assert exc.value.status_code == 409
            assert exc.value.redfish_error_code == "Base.1.0.GeneralError"
            assert "already On" in exc.value.message

    async def test_202_async_accepted(self):
        """BMC может вернуть 202 (long-running task) — это OK."""
        def handler(request: httpx.Request) -> httpx.Response:
            return _ok(202, {"@odata.id": "/redfish/v1/TaskService/Tasks/1"})

        async with _make_client(handler) as c:
            await c.power_action("ForceRestart")  # не падает

    async def test_timeout_status_none(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow bmc")

        async with _make_client(handler) as c:
            with pytest.raises(RedfishError) as exc:
                await c.power_action("On")
            assert exc.value.status_code is None


# ── rotate_user_password ─────────────────────────────────────────────────────


class TestRotateUserPassword:
    async def test_patch_accounts(self):
        captured = {}
        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["path"] = request.url.path
            captured["body"] = json.loads(request.content)
            return _ok(200)

        async with _make_client(handler) as c:
            await c.rotate_user_password(2, "NewPass!23")
        assert captured["method"] == "PATCH"
        assert captured["path"] == "/redfish/v1/Managers/iDRAC.Embedded.1/Accounts/2"
        assert captured["body"] == {"Password": "NewPass!23"}

    async def test_401_raises_auth(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _ok(401, {"error": {"code": "AuthFail"}})

        async with _make_client(handler) as c:
            with pytest.raises(RedfishError) as exc:
                await c.rotate_user_password(2, "x")
            assert exc.value.status_code == 401

    async def test_204_no_content_ok(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _ok(204)

        async with _make_client(handler) as c:
            await c.rotate_user_password(2, "x")  # не падает


# ── lifecycle: aclose ──────────────────────────────────────────────────────


class TestLifecycle:
    async def test_aclose_idempotent(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _ok(200, {"PowerState": "Off"})

        c = _make_client(handler)
        await c.aclose()
        # повторный aclose не падает
        await c.aclose()

    async def test_context_manager_calls_aclose(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _ok(200, {"PowerState": "Off"})

        async with _make_client(handler) as c:
            assert await c.get_power_state() == "Off"
        # client закрыт; повторное использование даёт явный RedfishError
        with pytest.raises(RedfishError, match="closed"):
            await c.get_power_state()
