"""Тесты легаси-совместимых read-only маршрутов (`/legacy-compat/...`, §12).

Продюсеры server_service (`list_os_versions`/`find_os_version_by_name`/
`resolve_os_kernels`) и внешний git (`astra_qa_stand_client`) подменяются
монки-патчем на уровне вызываемых функций — сам по себе транспорт до этих
систем уже покрыт своими тестами (`test_choices_resolvers.py`,
`server_service/tests/unit/test_os_version_repo_resolver.py`).
"""

from __future__ import annotations

import pytest

from src.core.exceptions import NotFoundError, ServiceUnavailableError
from src.db.session import AsyncSessionLocal
from src.repositories import department_integration_settings as dis_repo
from src.services import astra_qa_stand_client, legacy_compat, server_client
from src.utils.ids import department_integration_settings_id
from tests.conftest import auth_hdr as _hdr

BASE = "/api/testing/v1/legacy-compat"


async def _seed_integration_settings(department_id: str, **overrides) -> None:
    async with AsyncSessionLocal() as db:
        data = {
            "id": department_integration_settings_id(),
            "department_id": department_id,
        }
        data.update(overrides)
        await dis_repo.create(db, data)
        await db.commit()


# ── get-repo-path-as-json / get-repo-path ──────────────────────────────────

class TestRepoPath:
    async def test_returns_resolved_repositories_sorted(self, client, no_role_token, monkeypatch):
        async def _fake_list_os_versions():
            return [
                {"id": "osv_2", "name": "1.8.6.38", "repositories": ["deb .../1.8.6 main"]},
                {"id": "osv_1", "name": "1.8.5.46", "repositories": ["deb .../1.8.5 main"]},
                {"id": "osv_3", "name": "1.8.4.10", "repositories": []},
            ]

        monkeypatch.setattr(server_client, "list_os_versions", _fake_list_os_versions)
        resp = await client.get(f"{BASE}/get-repo-path-as-json", headers=_hdr(no_role_token))
        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "1.8.5.46": ["deb .../1.8.5 main"],
            "1.8.6.38": ["deb .../1.8.6 main"],
        }

    async def test_version_without_repositories_is_omitted(self, client, no_role_token, monkeypatch):
        async def _fake_list_os_versions():
            return [{"id": "osv_1", "name": "1.8.4.10", "repositories": []}]

        monkeypatch.setattr(server_client, "list_os_versions", _fake_list_os_versions)
        resp = await client.get(f"{BASE}/get-repo-path-as-json", headers=_hdr(no_role_token))
        assert resp.status_code == 200
        assert resp.json() == {}

    async def test_repo_path_is_attachment(self, client, no_role_token, monkeypatch):
        async def _fake_list_os_versions():
            return [{"id": "osv_1", "name": "1.8.5.46", "repositories": ["deb .../1.8.5 main"]}]

        monkeypatch.setattr(server_client, "list_os_versions", _fake_list_os_versions)
        resp = await client.get(f"{BASE}/get-repo-path", headers=_hdr(no_role_token))
        assert resp.status_code == 200
        assert resp.headers["content-disposition"] == 'attachment; filename="releases.json"'
        assert resp.json() == {"1.8.5.46": ["deb .../1.8.5 main"]}

    async def test_anonymous_rejected(self, client):
        resp = await client.get(f"{BASE}/get-repo-path-as-json")
        assert resp.status_code == 401


# ── get-astra-config ────────────────────────────────────────────────────────

class TestAstraConfig:
    async def test_returns_live_payload(self, client, no_role_token, monkeypatch):
        async def _fake_get_astra_config():
            return {"astra-version": {"vm-machines": ["sudcm", "clear"]}}

        monkeypatch.setattr(astra_qa_stand_client, "get_astra_config", _fake_get_astra_config)
        resp = await client.get(f"{BASE}/get-astra-config", headers=_hdr(no_role_token))
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"astra-version": {"vm-machines": ["sudcm", "clear"]}}

    async def test_unreachable_repo_gives_503(self, client, no_role_token, monkeypatch):
        async def _boom():
            raise ServiceUnavailableError(
                error_code="ASTRA_QA_STAND_UNAVAILABLE", message="unreachable",
            )

        monkeypatch.setattr(astra_qa_stand_client, "get_astra_config", _boom)
        resp = await client.get(f"{BASE}/get-astra-config", headers=_hdr(no_role_token))
        assert resp.status_code == 503
        assert resp.json()["error_code"] == "ASTRA_QA_STAND_UNAVAILABLE"


# ── get-box-config / get-testname-columns / known-bugs / annotations ──────

class TestStaticData:
    async def test_box_config(self, client, no_role_token):
        resp = await client.get(f"{BASE}/get-box-config", headers=_hdr(no_role_token))
        assert resp.status_code == 200
        boxes = resp.json()["vagrant_box"]
        assert {"1.8.0.s": ["smolensk-vanilla-gui/1.8.0.15", "ftp://10.177.103.10/boxes/box/smolensk-vanilla-gui-1.8.0-gmg15.0.0-virtualbox.box"]} in boxes

    async def test_testname_columns(self, client, no_role_token):
        resp = await client.get(f"{BASE}/get-testname-columns", headers=_hdr(no_role_token))
        assert resp.status_code == 200
        assert resp.json()["postgresql benchmark"] == "PostgreSQL"

    async def test_known_bugs(self, client, no_role_token):
        resp = await client.get(f"{BASE}/known-bugs", headers=_hdr(no_role_token))
        assert resp.status_code == 200
        assert resp.json()["PostgreSQL"]["BT-51261"] == "https://jira.astralinux.ru/browse/BT-51261"

    async def test_annotations(self, client, no_role_token):
        resp = await client.get(f"{BASE}/annotations", headers=_hdr(no_role_token))
        assert resp.status_code == 200
        assert "BT-61530" in resp.json()["UnixBench"]

    async def test_get_stand_returns_html(self, client, no_role_token):
        resp = await client.get(f"{BASE}/get-stand", headers=_hdr(no_role_token))
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        assert "10.177.103.205" in resp.text

    async def test_static_routes_reject_anonymous(self, client):
        for path in ("get-box-config", "get-testname-columns", "known-bugs", "annotations", "get-stand"):
            resp = await client.get(f"{BASE}/{path}")
            assert resp.status_code == 401, path


# ── get-jira-url / get-confluence-url ──────────────────────────────────────

class TestIntegrationUrls:
    async def test_jira_url_of_own_department(self, client, make_token):
        dept = "dep_legacy_a"
        await _seed_integration_settings(dept, jira_base_url="https://jira.example/dep-a")
        token = make_token(department_id=dept)
        resp = await client.get(
            f"{BASE}/get-jira-url", params={"department_id": dept}, headers=_hdr(token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"department_id": dept, "url": "https://jira.example/dep-a"}

    async def test_confluence_url_not_configured_is_null(self, client, make_token):
        dept = "dep_legacy_b"
        token = make_token(department_id=dept)
        resp = await client.get(
            f"{BASE}/get-confluence-url", params={"department_id": dept}, headers=_hdr(token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"department_id": dept, "url": None}

    async def test_cross_department_is_forbidden(self, client, make_token):
        dept = "dep_legacy_c"
        await _seed_integration_settings(dept, confluence_base_url="https://life.example")
        token = make_token(department_id="dep_legacy_other")
        resp = await client.get(
            f"{BASE}/get-confluence-url", params={"department_id": dept}, headers=_hdr(token),
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "DEPARTMENT_ISOLATION"


# ── available-kernels-from-<rc> ─────────────────────────────────────────────

class TestAvailableKernels:
    async def test_returns_kernels_of_matched_version(self, client, no_role_token, monkeypatch):
        async def _fake_find(name):
            assert name == "1.8.5.46"
            return {"id": "osv_1", "name": "1.8.5.46"}

        async def _fake_resolve(os_version_id):
            assert os_version_id == "osv_1"
            return ["5.15.0", "6.1.0"]

        monkeypatch.setattr(server_client, "find_os_version_by_name", _fake_find)
        monkeypatch.setattr(server_client, "resolve_os_kernels", _fake_resolve)
        resp = await client.post(f"{BASE}/available-kernels-from-1.8.5.46", headers=_hdr(no_role_token))
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"rc": "1.8.5.46", "kernels": ["5.15.0", "6.1.0"]}

    async def test_unknown_rc_gives_404(self, client, no_role_token, monkeypatch):
        async def _fake_find(name):
            return None

        monkeypatch.setattr(server_client, "find_os_version_by_name", _fake_find)
        resp = await client.post(f"{BASE}/available-kernels-from-9.9.9.9", headers=_hdr(no_role_token))
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "LEGACY_RC_NOT_FOUND"

    async def test_anonymous_rejected(self, client):
        resp = await client.post(f"{BASE}/available-kernels-from-1.8.5.46")
        assert resp.status_code == 401


# ── service-level: find_os_version_by_name ─────────────────────────────────

class TestFindOsVersionByName:
    async def test_matches_by_name(self, monkeypatch):
        async def _fake_list_os_versions():
            return [{"id": "osv_1", "name": "1.8.5.46"}, {"id": "osv_2", "name": "1.8.6.38"}]

        monkeypatch.setattr(server_client, "list_os_versions", _fake_list_os_versions)
        result = await server_client.find_os_version_by_name("1.8.6.38")
        assert result == {"id": "osv_2", "name": "1.8.6.38"}

    async def test_no_match_returns_none(self, monkeypatch):
        async def _fake_list_os_versions():
            return [{"id": "osv_1", "name": "1.8.5.46"}]

        monkeypatch.setattr(server_client, "list_os_versions", _fake_list_os_versions)
        result = await server_client.find_os_version_by_name("9.9.9.9")
        assert result is None


# ── service-level: available_kernels_from_rc raises on missing rc ─────────

class TestLegacyCompatServiceEdgeCases:
    async def test_available_kernels_raises_not_found(self, monkeypatch):
        async def _fake_find(name):
            return None

        monkeypatch.setattr(server_client, "find_os_version_by_name", _fake_find)
        with pytest.raises(NotFoundError) as exc:
            await legacy_compat.available_kernels_from_rc("9.9.9.9")
        assert exc.value.error_code == "LEGACY_RC_NOT_FOUND"
