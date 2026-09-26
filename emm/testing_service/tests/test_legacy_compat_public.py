"""Публичный compat `/rest/api/*` по легаси-путям, доступ по подсетям.

IP источника задаётся транспортом (`ASGITransport(client=(ip, port))`).
server_service (каталог версий, connection-info стендов) подменяется
монки-патчем `server_client`, как в `test_legacy_compat.py`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.core.exceptions import NotFoundError
from src.db.session import AsyncSessionLocal
from src.models import TestStand as StandRow
from src.repositories import department_integration_settings as dis_repo
from src.repositories import global_variable as variable_repo
from src.services import audit_service, client_ip, server_client
from src.services import legacy_compat as svc
from src.utils.ids import department_integration_settings_id
from tests.conftest import auth_hdr as _hdr

PUBLIC = "/rest/api"
SETTINGS = "/api/testing/v1/legacy-compat"
LEGACY_RELEASES = os.path.join(os.path.dirname(__file__), "..", "..", "allta_app_full", "releases.json")

STAND_IP = "10.177.103.204"      # stand3 легаси (allta_image_conf.py:37)
UNKNOWN_IP = "10.177.103.77"     # в подсети стендов, но стенда нет
FOREIGN_IP = "192.168.50.10"     # вне разрешённых подсетей


def _client_from(ip: str) -> AsyncClient:
    from src.main import app

    return AsyncClient(transport=ASGITransport(app=app, client=(ip, 40000)), base_url="http://test")


@pytest_asyncio.fixture
async def stand_client():
    async with _client_from(STAND_IP) as ac:
        yield ac


@pytest_asyncio.fixture
async def unknown_client():
    async with _client_from(UNKNOWN_IP) as ac:
        yield ac


@pytest_asyncio.fixture
async def foreign_client():
    async with _client_from(FOREIGN_IP) as ac:
        yield ac


@pytest.fixture
def legacy_releases() -> dict[str, list[str]]:
    with open(LEGACY_RELEASES, encoding="utf-8") as fh:
        releases = json.load(fh)
    return {name: releases[name] for name in ("1.7.5.16", "1.8.1.6", "1.8.1.UU.1.6") if name in releases}


@pytest.fixture
def mock_os_versions(monkeypatch, legacy_releases):
    async def _fake_list_os_versions():
        items = [
            {"id": f"osv_{i}", "name": name, "repositories": lines}
            for i, (name, lines) in enumerate(reversed(legacy_releases.items()))
        ]
        items.append({"id": "osv_empty", "name": "1.8.9.1", "repositories": []})
        return items

    monkeypatch.setattr(server_client, "list_os_versions", _fake_list_os_versions)


@pytest.fixture
def stand_hosts(monkeypatch):
    """server_id → IP для `get_connection_info`; незнакомый id — 404."""
    hosts: dict[str, str] = {}

    async def _fake_connection_info(server_id: str) -> dict:
        if server_id not in hosts:
            raise NotFoundError(error_code="SERVER_NOT_FOUND", message="nope")
        return {"server_id": server_id, "host": hosts[server_id]}

    monkeypatch.setattr(server_client, "get_connection_info", _fake_connection_info)
    return hosts


@pytest.fixture
def audit_events(monkeypatch):
    events: list[dict] = []
    original = audit_service.emit

    def _capture(action, *args, **kwargs):
        events.append({"action": action, **kwargs})
        return original(action, *args, **kwargs)

    monkeypatch.setattr(audit_service, "emit", _capture)
    return events


async def _add_stand(stand_hosts: dict[str, str], department_id: str, ip: str) -> str:
    server_id = f"srv_{uuid.uuid4().hex[:12]}"
    stand_id = f"stand_{uuid.uuid4().hex}"
    async with AsyncSessionLocal() as db:
        db.add(StandRow(id=stand_id, server_id=server_id, department_id=department_id, created_by="usr_test"))
        await db.commit()
    stand_hosts[server_id] = ip
    return stand_id


async def _integration(department_id: str, **fields) -> None:
    async with AsyncSessionLocal() as db:
        await dis_repo.create(db, {"id": department_integration_settings_id(), "department_id": department_id, **fields})
        await db.commit()


async def _set_default_department(department_id: str | None) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("UPDATE legacy_compat_settings SET default_department_id = :d"), {"d": department_id},
        )
        await db.commit()


# ── доступ по подсетям ───────────────────────────────────────────────────────


class TestSourceNetworks:
    async def test_repo_path_from_allowed_network_is_legacy_json(
        self, stand_client, mock_os_versions, legacy_releases,
    ):
        resp = await stand_client.get(f"{PUBLIC}/get-repo-path")
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-disposition"] == 'attachment; filename="releases.json"'
        assert resp.headers["content-type"].startswith("application/json")
        # Тот же JSON, что легаси `releases.json` (версии без репозиториев не отдаются).
        assert resp.json() == legacy_releases
        assert list(resp.json()) == sorted(legacy_releases)

    async def test_jq_on_response_gives_sources_list_lines(self, stand_client, mock_os_versions, legacy_releases):
        """`jq ".\\"1.8.1.6\\"[]"` — так `prepare.sh` вытаскивает строки sources.list."""
        resp = await stand_client.get(f"{PUBLIC}/get-repo-path")
        assert resp.status_code == 200
        if shutil.which("jq") is None:
            lines = resp.json()["1.8.1.6"]
        else:
            out = subprocess.run(
                ["jq", "-r", '."1.8.1.6"[]'], input=resp.content, capture_output=True, check=True,
            )
            lines = out.stdout.decode().splitlines()
        assert lines == legacy_releases["1.8.1.6"]
        assert all(line.startswith("deb https://releases.devos.astralinux.ru/") for line in lines)

    async def test_repo_path_as_json_same_body_without_attachment(self, unknown_client, mock_os_versions, legacy_releases):
        resp = await unknown_client.get(f"{PUBLIC}/get-repo-path-as-json")
        assert resp.status_code == 200
        assert "content-disposition" not in resp.headers
        assert json.loads(resp.text) == legacy_releases

    async def test_foreign_network_is_forbidden(self, foreign_client, mock_os_versions):
        for path in ("get-repo-path", "get-repo-path-as-json", "get-confluence-url", "get-box-config", "whatever"):
            resp = await foreign_client.get(f"{PUBLIC}/{path}")
            assert resp.status_code == 403, path
            assert resp.json()["error_code"] == "LEGACY_COMPAT_SOURCE_FORBIDDEN"

    async def test_disabled_network_gives_no_access(self, stand_client, mock_os_versions):
        async with AsyncSessionLocal() as db:
            await db.execute(text("UPDATE compat_allowed_networks SET enabled = false"))
            await db.commit()
        resp = await stand_client.get(f"{PUBLIC}/get-repo-path")
        assert resp.status_code == 403

    async def test_network_added_in_ui_opens_access(self, foreign_client, client, admin_token, mock_os_versions):
        resp = await client.post(
            f"{SETTINGS}/networks", headers=_hdr(admin_token),
            json={"cidr": "192.168.50.0/24", "description": "лаборатория"},
        )
        assert resp.status_code == 201, resp.text
        assert (await foreign_client.get(f"{PUBLIC}/get-repo-path")).status_code == 200

    async def test_no_bearer_needed_and_bearer_ignored(self, stand_client, mock_os_versions):
        resp = await stand_client.get(f"{PUBLIC}/get-repo-path", headers={"Authorization": "Bearer junk"})
        assert resp.status_code == 200

    async def test_unknown_path_has_legacy_404(self, stand_client):
        resp = await stand_client.get(f"{PUBLIC}/get-times")
        assert resp.status_code == 404
        assert resp.json() == {"error": "Not found", "message": "Endpoint /rest/api/get-times not found"}


# ── URL интеграций: отдел по IP стенда ───────────────────────────────────────


class TestIntegrationUrls:
    async def test_stand_ip_gets_its_department_url_and_unknown_ip_gets_default(
        self, stand_client, unknown_client, stand_hosts, audit_events,
    ):
        dept_a, dept_default = f"dep_{uuid.uuid4().hex[:8]}", f"dep_{uuid.uuid4().hex[:8]}"
        await _integration(dept_a, confluence_base_url="https://life-a.example/", jira_base_url="https://jira-a.example")
        await _integration(dept_default, confluence_base_url="https://life.astralinux.ru", jira_base_url="https://jira.astralinux.ru")
        stand_id = await _add_stand(stand_hosts, dept_a, STAND_IP)
        await _add_stand(stand_hosts, dept_default, "10.177.103.201")
        await _set_default_department(dept_default)

        resp = await stand_client.get(f"{PUBLIC}/get-confluence-url")
        assert resp.status_code == 200, resp.text
        # Легаси-формат: голое имя без схемы и `/` — потребители сами пишут `https://{...}`.
        assert resp.text == "life-a.example"
        assert resp.headers["content-type"].startswith("text/html")
        assert (await stand_client.get(f"{PUBLIC}/get-jira-url")).text == "jira-a.example"

        resp = await unknown_client.get(f"{PUBLIC}/get-confluence-url")
        assert resp.status_code == 200
        assert resp.text == "life.astralinux.ru"

        resolved = [e for e in audit_events if e["action"] == "legacy_compat.department_resolved"]
        assert [(e["department_id"], e["details"]["reason"]) for e in resolved] == [
            (dept_a, "stand"), (dept_a, "stand"), (dept_default, "default"),
        ]
        assert resolved[0]["details"]["stand_ids"] == [stand_id]
        assert resolved[0]["details"]["source_ip"] == STAND_IP
        assert resolved[0]["actor_type"] == "anonymous"

    async def test_unknown_ip_without_default_department_is_404(self, unknown_client, stand_hosts, audit_events):
        resp = await unknown_client.get(f"{PUBLIC}/get-jira-url")
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "LEGACY_COMPAT_DEPARTMENT_UNKNOWN"
        failure = [e for e in audit_events if e["action"] == "legacy_compat.department_resolved"]
        assert failure and failure[0]["status"] == "failure"
        assert failure[0]["details"]["reason"] == "no_default"

    async def test_stands_of_two_departments_on_one_ip_use_default(self, stand_client, stand_hosts):
        dept_a, dept_b, dept_default = (f"dep_{uuid.uuid4().hex[:8]}" for _ in range(3))
        await _integration(dept_default, jira_base_url="https://jira.default.example")
        await _add_stand(stand_hosts, dept_a, STAND_IP)
        await _add_stand(stand_hosts, dept_b, STAND_IP)
        await _set_default_department(dept_default)
        resp = await stand_client.get(f"{PUBLIC}/get-jira-url")
        assert resp.status_code == 200
        assert resp.text == "jira.default.example"

    async def test_department_without_url_is_404_not_fallback(self, stand_client, stand_hosts):
        dept_a, dept_default = f"dep_{uuid.uuid4().hex[:8]}", f"dep_{uuid.uuid4().hex[:8]}"
        await _integration(dept_default, jira_base_url="https://jira.default.example")
        await _add_stand(stand_hosts, dept_a, STAND_IP)
        await _set_default_department(dept_default)
        resp = await stand_client.get(f"{PUBLIC}/get-jira-url")
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "LEGACY_COMPAT_URL_NOT_CONFIGURED"
        assert resp.json()["details"]["department_id"] == dept_a

    async def test_unreachable_stand_host_is_skipped(self, unknown_client, stand_hosts):
        dept_default = f"dep_{uuid.uuid4().hex[:8]}"
        await _integration(dept_default, jira_base_url="https://jira.default.example")
        async with AsyncSessionLocal() as db:  # стенд без connection-info (404 у server_service)
            db.add(StandRow(id=f"stand_{uuid.uuid4().hex}", server_id="srv_gone", department_id="dep_x", created_by="usr_test"))
            await db.commit()
        await _set_default_department(dept_default)
        resp = await unknown_client.get(f"{PUBLIC}/get-jira-url")
        assert resp.status_code == 200
        assert resp.text == "jira.default.example"

    def test_legacy_host(self):
        assert svc.legacy_host("https://jira.astralinux.ru") == "jira.astralinux.ru"
        assert svc.legacy_host("https://life.astralinux.ru/") == "life.astralinux.ru"
        assert svc.legacy_host("http://host:8090/confluence/") == "host:8090/confluence"
        assert svc.legacy_host("jira.astralinux.ru") == "jira.astralinux.ru"


# ── статичные справочники и переменные адресов ───────────────────────────────


class TestStaticRoutes:
    async def test_static_dictionaries(self, stand_client):
        resp = await stand_client.get(f"{PUBLIC}/get-testname-columns")
        assert resp.status_code == 200 and resp.json()["postgresql benchmark"] == "PostgreSQL"
        assert "BT-51261" in (await stand_client.get(f"{PUBLIC}/known-bugs")).json()["PostgreSQL"]
        assert "BT-61530" in (await stand_client.get(f"{PUBLIC}/annotations")).json()["UnixBench"]
        resp = await stand_client.get(f"{PUBLIC}/get-stand")
        assert resp.headers["content-disposition"] == 'attachment; filename="stand.html"'
        assert "10.177.103.205" in resp.text

    async def test_box_config_uses_ftp_url_variable(self, stand_client):
        resp = await stand_client.get(f"{PUBLIC}/get-box-config")
        assert resp.status_code == 200
        assert resp.headers["content-disposition"] == 'attachment; filename="box-config.json"'
        first = resp.json()["vagrant_box"][0]["1.8.0.s"]
        # Значение переменной по умолчанию = легаси-адрес.
        assert first[1] == "ftp://10.177.103.10/boxes/box/smolensk-vanilla-gui-1.8.0-gmg15.0.0-virtualbox.box"

        async with AsyncSessionLocal() as db:
            variable = await variable_repo.get_by_code(db, "FTP_URL")
            original = dict(variable.source_ref)
            variable.source_ref = {"value": "ftp://ftp.qa.example/"}
            await db.commit()
        try:
            first = (await stand_client.get(f"{PUBLIC}/get-box-config")).json()["vagrant_box"][0]["1.8.0.s"]
            assert first == [
                "smolensk-vanilla-gui/1.8.0.15",
                "ftp://ftp.qa.example/boxes/box/smolensk-vanilla-gui-1.8.0-gmg15.0.0-virtualbox.box",
            ]
        finally:
            async with AsyncSessionLocal() as db:
                variable = await variable_repo.get_by_code(db, "FTP_URL")
                variable.source_ref = original
                await db.commit()

    async def test_service_address_variables_seeded(self):
        async with AsyncSessionLocal() as db:
            values = {
                code: (await variable_repo.get_by_code(db, code)).source_ref["value"]
                for code in ("INFOCOLLECTOR_URL", "FTP_URL", "DEVPI_URL", "DOCKER_REGISTRY")
            }
        assert values == {
            "INFOCOLLECTOR_URL": "http://10.177.103.10:18181",
            "FTP_URL": "ftp://10.177.103.10",
            "DEVPI_URL": "http://10.177.103.10:3141/root/release",
            "DOCKER_REGISTRY": "allta.devos.astralinux.ru:21503",
        }

    async def test_available_kernels(self, stand_client, monkeypatch):
        async def _find(name):
            return {"id": "osv_1", "name": name}

        async def _kernels(os_version_id):
            return ["6.1.90-1-generic", "5.15.0-111-generic"]

        monkeypatch.setattr(server_client, "find_os_version_by_name", _find)
        monkeypatch.setattr(server_client, "resolve_os_kernels", _kernels)
        resp = await stand_client.post(f"{PUBLIC}/available-kernels-from-1.8.1.6")
        assert resp.status_code == 200
        assert resp.json() == ["6.1.90-1-generic", "5.15.0-111-generic"]


# ── IP источника за прокси ───────────────────────────────────────────────────


class _Req:
    def __init__(self, host: str, headers: dict[str, str] | None = None):
        self.client = type("C", (), {"host": host})()
        self.headers = headers or {}


class TestSourceIp:
    def test_direct_address_without_trusted_proxies(self):
        req = _Req("10.177.103.5", {"X-Forwarded-For": "10.177.103.201"})
        assert client_ip.source_ip(req, []) == "10.177.103.5"

    def test_rightmost_untrusted_behind_trusted_proxy(self):
        # Клиент подделал левый элемент, traefik (10.42.x) дописал настоящий адрес справа.
        req = _Req("10.42.0.7", {"X-Forwarded-For": "10.177.103.201, 192.168.50.10"})
        assert client_ip.source_ip(req, ["10.42.0.0/16"]) == "192.168.50.10"

    def test_chain_of_trusted_proxies(self):
        req = _Req("10.42.0.7", {"X-Forwarded-For": "10.177.103.204, 10.42.0.9"})
        assert client_ip.source_ip(req, ["10.42.0.0/16"]) == "10.177.103.204"

    def test_ipv4_mapped_ipv6(self):
        assert client_ip.source_ip(_Req("::ffff:10.177.103.204"), []) == "10.177.103.204"

    async def test_spoofed_forwarded_for_does_not_open_access(self, foreign_client, mock_os_versions):
        resp = await foreign_client.get(f"{PUBLIC}/get-repo-path", headers={"X-Forwarded-For": STAND_IP})
        assert resp.status_code == 403


# ── настройки compat (UI) ────────────────────────────────────────────────────


class TestCompatSettingsApi:
    async def test_seed_network_listed(self, client, admin_token):
        resp = await client.get(f"{SETTINGS}/networks", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        cidrs = [item["cidr"] for item in resp.json()["items"]]
        assert "10.177.103.0/24" in cidrs

    async def test_network_crud_and_validation(self, client, admin_token, audit_events):
        resp = await client.post(f"{SETTINGS}/networks", headers=_hdr(admin_token), json={"cidr": "10.1.2.3"})
        assert resp.status_code == 201
        created = resp.json()
        assert created["cidr"] == "10.1.2.3/32"

        resp = await client.post(f"{SETTINGS}/networks", headers=_hdr(admin_token), json={"cidr": "10.9.9.5/24"})
        assert resp.status_code == 422
        assert "10.9.9.0/24" in resp.text

        resp = await client.post(f"{SETTINGS}/networks", headers=_hdr(admin_token), json={"cidr": "10.1.2.3/32"})
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "COMPAT_NETWORK_DUPLICATE"

        resp = await client.patch(
            f"{SETTINGS}/networks/{created['id']}", headers=_hdr(admin_token),
            json={"enabled": False, "description": "временно"},
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False and resp.json()["description"] == "временно"

        resp = await client.delete(f"{SETTINGS}/networks/{created['id']}", headers=_hdr(admin_token))
        assert resp.status_code == 200
        resp = await client.delete(f"{SETTINGS}/networks/{created['id']}", headers=_hdr(admin_token))
        assert resp.status_code == 404
        actions = [(e["action"], e.get("status")) for e in audit_events if e["action"].startswith("compat_network.")]
        assert ("compat_network.create", "success") in actions
        assert ("compat_network.update", "success") in actions
        assert ("compat_network.delete", "success") in actions

    async def test_guest_cannot_read_or_write(self, client, guest_token):
        assert (await client.get(f"{SETTINGS}/networks", headers=_hdr(guest_token))).status_code == 403
        resp = await client.post(f"{SETTINGS}/networks", headers=_hdr(guest_token), json={"cidr": "10.0.0.0/8"})
        assert resp.status_code == 403
        resp = await client.put(f"{SETTINGS}/settings", headers=_hdr(guest_token), json={"default_department_id": "dep_x"})
        assert resp.status_code == 403

    async def test_department_admin_cannot_open_networks(self, client, make_token, dept_a):
        token = make_token(department_id=dept_a, platform_role="department_admin")
        resp = await client.post(f"{SETTINGS}/networks", headers=_hdr(token), json={"cidr": "10.0.0.0/8"})
        assert resp.status_code == 403

    async def test_default_department_roundtrip(self, client, admin_token):
        resp = await client.get(f"{SETTINGS}/settings", headers=_hdr(admin_token))
        assert resp.status_code == 200 and resp.json()["default_department_id"] is None
        resp = await client.put(f"{SETTINGS}/settings", headers=_hdr(admin_token), json={"default_department_id": "dep_main"})
        assert resp.status_code == 200 and resp.json()["default_department_id"] == "dep_main"
        resp = await client.put(f"{SETTINGS}/settings", headers=_hdr(admin_token), json={"default_department_id": ""})
        assert resp.json()["default_department_id"] is None

    async def test_resolve_explains_choice(self, client, admin_token, stand_hosts):
        dept_a = f"dep_{uuid.uuid4().hex[:8]}"
        stand_id = await _add_stand(stand_hosts, dept_a, STAND_IP)
        await _set_default_department("dep_default")

        resp = await client.get(f"{SETTINGS}/resolve", params={"ip": STAND_IP}, headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "ip": STAND_IP, "allowed": True, "network_id": "cnet_stands_legacy", "cidr": "10.177.103.0/24",
            "department_id": dept_a, "reason": "stand", "stand_ids": [stand_id],
        }
        resp = await client.get(f"{SETTINGS}/resolve", params={"ip": FOREIGN_IP}, headers=_hdr(admin_token))
        body = resp.json()
        assert body["allowed"] is False and body["department_id"] == "dep_default" and body["reason"] == "default"
        resp = await client.get(f"{SETTINGS}/resolve", params={"ip": "not-an-ip"}, headers=_hdr(admin_token))
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "LEGACY_COMPAT_IP_INVALID"


class TestHttpsGuard:
    async def test_public_compat_passes_https_guard(self):
        from fastapi import FastAPI

        from src.middleware.https_guard import HTTPSRequiredMiddleware

        app = FastAPI()

        @app.get("/rest/api/get-jira-url")
        async def _legacy():
            return "ok"

        @app.get("/api/testing/v1/other")
        async def _other():
            return "ok"

        app.add_middleware(HTTPSRequiredMiddleware, app_env="production")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            assert (await ac.get("/rest/api/get-jira-url")).status_code == 200
            assert (await ac.get("/api/testing/v1/other")).status_code == 403
