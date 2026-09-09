"""Тесты резолверов `choices_source`.

`static:` разбирается на лету из строки; `dynamic:` идёт в источник живым
запросом. server_service поднимать не нужно — httpx подменяется MockTransport'ом
через `server_client.build_client`.
"""

from __future__ import annotations

import httpx
import pytest

from src.core.exceptions import BadRequestError, DomainValidationError
from src.services import choices, server_client
from tests.conftest import auth_hdr as _hdr

BASE = "/api/testing/v1/global-variables"


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


async def _variable_id(client, token, code: str) -> str:
    resp = await client.get(f"{BASE}/by-code/{code}", headers=_hdr(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


# ── static: ─────────────────────────────────────────────────────────────────

class TestStaticResolver:
    async def test_array_of_strings(self):
        items = await choices.resolve('static:["orel","smolensk"]')
        assert items == [
            {"value": "orel", "label": "orel"},
            {"value": "smolensk", "label": "smolensk"},
        ]

    async def test_array_of_objects_keeps_labels(self):
        items = await choices.resolve(
            'static:[{"value":"orel","label":"Орёл"},{"value":"smolensk","label":"Смоленск"}]'
        )
        assert items == [
            {"value": "orel", "label": "Орёл"},
            {"value": "smolensk", "label": "Смоленск"},
        ]

    async def test_empty_array(self):
        assert await choices.resolve("static:[]") == []

    async def test_broken_json_rejected(self):
        with pytest.raises(DomainValidationError) as exc:
            await choices.resolve('static:["orel",')
        assert exc.value.error_code == "CHOICES_SOURCE_INVALID"

    async def test_non_array_json_rejected(self):
        with pytest.raises(DomainValidationError):
            await choices.resolve('static:{"orel": 1}')

    async def test_object_without_value_rejected(self):
        with pytest.raises(DomainValidationError):
            await choices.resolve('static:[{"label":"Орёл"}]')

    async def test_unknown_prefix_rejected(self):
        with pytest.raises(DomainValidationError):
            await choices.resolve('["orel"]')

    async def test_mode_variable_resolves_through_endpoint(self, client, no_role_token):
        variable_id = await _variable_id(client, no_role_token, "MODE")
        resp = await client.get(f"{BASE}/{variable_id}/choices", headers=_hdr(no_role_token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [item["value"] for item in body["items"]] == ["orel", "smolensk"]
        assert body["choices_source"] == 'static:["orel","smolensk"]'


# ── dynamic:os_versions ─────────────────────────────────────────────────────

class TestOsVersionsResolver:
    async def test_calls_server_service_and_maps_items(
        self, client, no_role_token, mock_server_service,
    ):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("Authorization")
            seen["identity"] = request.headers.get("X-Service-Identity")
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"id": "osv_1", "name": "astra-1.7.5.6"},
                        {"id": "osv_2", "name": "astra-1.8.2.0"},
                    ],
                    "total": 2, "limit": 500, "offset": 0,
                },
            )

        mock_server_service(handler)
        variable_id = await _variable_id(client, no_role_token, "RC")
        resp = await client.get(f"{BASE}/{variable_id}/choices", headers=_hdr(no_role_token))

        assert resp.status_code == 200, resp.text
        assert resp.json()["items"] == [
            {"value": "osv_1", "label": "astra-1.7.5.6"},
            {"value": "osv_2", "label": "astra-1.8.2.0"},
        ]
        assert "/api/server/v1/os-versions" in seen["url"]
        assert seen["auth"] == "Bearer dbos_bot_test"
        assert seen["identity"] == "testing_service"

    async def test_empty_catalog_gives_empty_list(
        self, client, no_role_token, mock_server_service,
    ):
        mock_server_service(lambda request: httpx.Response(200, json={"items": []}))
        variable_id = await _variable_id(client, no_role_token, "RC")
        resp = await client.get(f"{BASE}/{variable_id}/choices", headers=_hdr(no_role_token))
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    async def test_unreachable_server_service_gives_503(
        self, client, no_role_token, mock_server_service,
    ):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        mock_server_service(handler)
        variable_id = await _variable_id(client, no_role_token, "RC")
        resp = await client.get(f"{BASE}/{variable_id}/choices", headers=_hdr(no_role_token))
        assert resp.status_code == 503
        assert resp.json()["error_code"] == "SERVER_SERVICE_UNREACHABLE"

    async def test_server_service_5xx_gives_503(
        self, client, no_role_token, mock_server_service,
    ):
        mock_server_service(lambda request: httpx.Response(500, json={}))
        variable_id = await _variable_id(client, no_role_token, "RC")
        resp = await client.get(f"{BASE}/{variable_id}/choices", headers=_hdr(no_role_token))
        assert resp.status_code == 503
        assert resp.json()["error_code"] == "SERVER_SERVICE_ERROR"

    async def test_unconfigured_channel_gives_503(self, client, no_role_token, monkeypatch):
        class _Empty:
            server_service_url = ""
            server_service_api_key = ""
            server_request_timeout_seconds = 2.0

        monkeypatch.setattr(server_client, "get_settings", lambda: _Empty())
        variable_id = await _variable_id(client, no_role_token, "RC")
        resp = await client.get(f"{BASE}/{variable_id}/choices", headers=_hdr(no_role_token))
        assert resp.status_code == 503
        assert resp.json()["error_code"] == "SERVER_SERVICE_NOT_CONFIGURED"


# ── dynamic:kernels ─────────────────────────────────────────────────────────

class TestKernelsResolver:
    async def test_requires_os_version_id(self, client, no_role_token):
        variable_id = await _variable_id(client, no_role_token, "KERNEL")
        resp = await client.get(f"{BASE}/{variable_id}/choices", headers=_hdr(no_role_token))
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "CHOICES_PARAM_REQUIRED"

    async def test_returns_kernels_of_requested_version(
        self, client, no_role_token, mock_server_service,
    ):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            return httpx.Response(
                200,
                json={"id": "osv_1", "name": "astra-1.7", "kernels": ["5.15.0", "6.1.0"]},
            )

        mock_server_service(handler)
        variable_id = await _variable_id(client, no_role_token, "KERNEL")
        resp = await client.get(
            f"{BASE}/{variable_id}/choices",
            params={"os_version_id": "osv_1"},
            headers=_hdr(no_role_token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["items"] == [
            {"value": "5.15.0", "label": "5.15.0"},
            {"value": "6.1.0", "label": "6.1.0"},
        ]
        assert seen["path"] == "/api/server/v1/os-versions/osv_1"

    async def test_unknown_os_version_gives_404(
        self, client, no_role_token, mock_server_service,
    ):
        mock_server_service(lambda request: httpx.Response(404, json={}))
        variable_id = await _variable_id(client, no_role_token, "KERNEL")
        resp = await client.get(
            f"{BASE}/{variable_id}/choices",
            params={"os_version_id": "osv_missing"},
            headers=_hdr(no_role_token),
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "SERVER_SERVICE_OBJECT_NOT_FOUND"

    async def test_missing_param_raised_by_resolver_directly(self):
        with pytest.raises(BadRequestError) as exc:
            await choices.resolve_kernels({})
        assert exc.value.error_code == "CHOICES_PARAM_REQUIRED"


# ── Реестр резолверов ───────────────────────────────────────────────────────

class TestResolverRegistry:
    def test_known_resolvers(self):
        assert set(choices.RESOLVERS) == {"os_versions", "kernels"}

    def test_is_supported(self):
        assert choices.is_supported('static:["a"]')
        assert choices.is_supported("dynamic:os_versions")
        assert not choices.is_supported("dynamic:department_credential")
        assert not choices.is_supported("os_versions")

    async def test_unknown_dynamic_name_rejected_at_resolve(self):
        with pytest.raises(DomainValidationError) as exc:
            await choices.resolve("dynamic:nope")
        assert exc.value.error_code == "CHOICES_RESOLVER_UNKNOWN"

    async def test_variable_without_choices_source(self, client, no_role_token):
        variable_id = await _variable_id(client, no_role_token, "TESTENV")
        resp = await client.get(f"{BASE}/{variable_id}/choices", headers=_hdr(no_role_token))
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "CHOICES_SOURCE_NOT_SET"

    async def test_choices_of_unknown_variable_404(self, client, no_role_token):
        resp = await client.get(f"{BASE}/gvar_nope/choices", headers=_hdr(no_role_token))
        assert resp.status_code == 404

    async def test_anonymous_cannot_resolve(self, client):
        resp = await client.get(f"{BASE}/gvar_mode/choices")
        assert resp.status_code == 401
