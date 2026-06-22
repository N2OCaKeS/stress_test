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
    resolve_manager_id,
    resolve_system_id,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_client(handler, *, system_id: str | None = None) -> RedfishClient:
    """Собрать RedfishClient с подменённым MockTransport.

    `handler(request)` — обычный httpx callback `(request) -> Response`.
    `system_id` — переопределить System-id (по умолчанию конструкторный `1`);
    пустая строка триггерит discovery через `/redfish/v1/Systems`.
    """
    transport = httpx.MockTransport(handler)
    ctor_kwargs: dict = {
        "host": "https://bmc.test",
        "username": "root",
        "password": "Calvin",
    }
    if system_id is not None:
        ctor_kwargs["system_id"] = system_id
    client = RedfishClient(**ctor_kwargs)
    # Заменяем underlying httpx-инстанс на свой с MockTransport. Это нужно
    # потому что transport нельзя передать в конструктор public API
    # RedfishClient — он намеренно скрыт. Если когда-нибудь конструктор
    # начнёт принимать `transport=` kwarg — заменить эту подмену на
    # `RedfishClient(..., transport=transport)` и не лезть в private attr.
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


# ── resolve_manager_id / resolve_system_id ───────────────────────────────────


class TestResolveManagerId:
    def test_idrac(self):
        assert resolve_manager_id("idrac") == "iDRAC.Embedded.1"

    def test_ilo(self):
        assert resolve_manager_id("ilo") == "1"

    def test_unknown_kind_empty(self):
        assert resolve_manager_id("supermicro") == ""

    def test_none_empty(self):
        assert resolve_manager_id(None) == ""


class TestResolveSystemId:
    def test_idrac_embedded_path(self):
        # Dell держит ComputerSystem под `System.Embedded.1`, не под `1` —
        # хардкод `Systems/1` на iDRAC отдавал 404.
        assert resolve_system_id("idrac") == "System.Embedded.1"

    def test_ilo_numeric(self):
        assert resolve_system_id("ilo") == "1"

    def test_unknown_kind_empty(self):
        # Неизвестный kind → discovery через /Systems внутри клиента.
        assert resolve_system_id("supermicro") == ""

    def test_none_empty(self):
        assert resolve_system_id(None) == ""


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

    async def test_503_raises_with_status(self):
        """Service Unavailable: текущее поведение — RedfishError(status=503).

        Если worker когда-нибудь начнёт отдельно бэкоффить 503 (retryable),
        тест должен будет расшириться; пока — фиксируем shape ответа.
        """
        def handler(request: httpx.Request) -> httpx.Response:
            return _ok(503, {"error": {"code": "Base.1.0.ServiceUnavailable"}})

        async with _make_client(handler) as c:
            with pytest.raises(RedfishError) as exc:
                await c.get_power_state()
            assert exc.value.status_code == 503

    async def test_429_raises_with_status(self):
        """Rate-limited: BMC может вернуть 429 на shared-FIPS-host'е.

        Сейчас обрабатывается как обычная ошибка с status=429; future-work —
        читать Retry-After header и пробрасывать в backoff. Тест фиксирует
        текущий контракт, чтобы migration был осознанным.
        """
        def handler(request: httpx.Request) -> httpx.Response:
            return _ok(429, {"error": {"code": "Base.1.0.TooManyRequests"}})

        async with _make_client(handler) as c:
            with pytest.raises(RedfishError) as exc:
                await c.get_power_state()
            assert exc.value.status_code == 429


# ── system_id resolution / discovery ─────────────────────────────────────────


class TestSystemIdResolution:
    async def test_idrac_explicit_system_id_path(self):
        """iDRAC kind → клиент собран с system_id=`System.Embedded.1`,
        get_power_state бьёт ровно по этому пути (без discovery, без 404)."""
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/redfish/v1/Systems/System.Embedded.1"
            return _ok(200, {"PowerState": "On"})

        async with _make_client(handler, system_id="System.Embedded.1") as c:
            assert await c.get_power_state() == "On"

    async def test_discovery_when_system_id_empty(self):
        """Пустой system_id → discovery через /redfish/v1/Systems, берём
        first Member, дальше бьём по нему."""
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            if request.url.path == "/redfish/v1/Systems":
                return _ok(200, {
                    "Members": [{"@odata.id": "/redfish/v1/Systems/Self"}],
                })
            if request.url.path == "/redfish/v1/Systems/Self":
                return _ok(200, {"PowerState": "Off"})
            return _ok(404)

        async with _make_client(handler, system_id="") as c:
            assert await c.get_power_state() == "Off"
        assert "/redfish/v1/Systems" in seen
        assert "/redfish/v1/Systems/Self" in seen

    async def test_discovery_caches_system_id(self):
        """Повторный вызов не делает второй discovery round-trip —
        system_id кэшируется в self._system_id."""
        collection_hits = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal collection_hits
            if request.url.path == "/redfish/v1/Systems":
                collection_hits += 1
                return _ok(200, {
                    "Members": [{"@odata.id": "/redfish/v1/Systems/1"}],
                })
            return _ok(200, {"PowerState": "On"})

        async with _make_client(handler, system_id="") as c:
            await c.get_power_state()
            await c.get_power_state()
        assert collection_hits == 1

    async def test_discovery_empty_collection_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _ok(200, {"Members": []})

        async with _make_client(handler, system_id="") as c:
            with pytest.raises(RedfishError, match="Systems collection is empty"):
                await c.get_power_state()

    async def test_power_action_uses_resolved_system_id(self):
        """power_action тоже резолвит system_id — Reset идёт по discovered
        пути, не по хардкоду `Systems/1`."""
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/redfish/v1/Systems":
                return _ok(200, {
                    "Members": [{"@odata.id": "/redfish/v1/Systems/Self"}],
                })
            captured["path"] = request.url.path
            return _ok(204)

        async with _make_client(handler, system_id="") as c:
            await c.power_action("ForceOff")
        assert captured["path"] == (
            "/redfish/v1/Systems/Self/Actions/ComputerSystem.Reset"
        )


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
            if request.method == "GET" and request.url.path == "/redfish/v1/AccountService":
                return _ok(200, {"Accounts": {"@odata.id": "/redfish/v1/AccountService/Accounts"}})
            captured["method"] = request.method
            captured["path"] = request.url.path
            captured["body"] = json.loads(request.content)
            return _ok(200)

        async with _make_client(handler) as c:
            await c.rotate_user_password(2, "NewPass!23")
        assert captured["method"] == "PATCH"
        assert captured["path"] == "/redfish/v1/AccountService/Accounts/2"
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


class TestAccountsBaseResolution:
    """Путь к аккаунтам резолвится через AccountService (на iLO под Manager'ом
    коллекции нет — был 404), с fallback на legacy /Managers/{id}/Accounts."""

    async def test_resolves_from_account_service(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/redfish/v1/AccountService":
                return _ok(200, {"Accounts": {"@odata.id": "/redfish/v1/AccountService/Accounts"}})
            return _ok(404, {"error": {"code": "X"}})

        c = _make_client(handler)
        assert await c._resolve_accounts_base() == "/redfish/v1/AccountService/Accounts"

    async def test_fallback_to_manager_path_when_account_service_missing(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/redfish/v1/AccountService":
                return httpx.Response(404, json={"error": {"code": "NotFound"}})
            return _ok(200, {})

        c = _make_client(handler)
        # default manager_id у конструктора — iDRAC.Embedded.1
        assert await c._resolve_accounts_base() == "/redfish/v1/Managers/iDRAC.Embedded.1/Accounts"
