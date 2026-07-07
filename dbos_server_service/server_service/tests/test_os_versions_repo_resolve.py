"""Интеграционные тесты авто-резолва repo-URL для OS-версий.

Проверяют, что создание версии с `build_version` и отдельный эндпоинт
`resolve-repositories` проставляют `repositories` из индекса релизов. Сеть
замокана на уровне `_fetch_index_from_network` — реального похода в
releases.devos.astralinux.ru нет.
"""

from __future__ import annotations

import pytest

from src.services import os_version_repo_resolver as resolver
from tests._helpers import assert_error, auth_hdr as _hdr

BASE = "/api/server/v1/os-versions"
RELEASES = "https://releases.devos.astralinux.ru"


def _index() -> dict:
    return {
        "releases": {
            "1.7": {
                "1.7.5": {
                    "1.7.5.6": {
                        "files": [
                            {"mount_point": "frozen/1.7/1.7.5/1.7.5.6/base-repository"},
                            {"mount_point": "frozen/1.7/1.7.5/1.7.5.6/installation"},
                        ],
                    },
                },
            },
            "1.8": {
                "1.8.1": {
                    "1.8.1.6": {
                        "files": [
                            {"mount_point": "1.8/1.8.1/1.8.1.6/main-repository"},
                            {"mount_point": "1.8/1.8.1/1.8.1.6/installation-di"},
                        ],
                    },
                },
            },
        },
    }


@pytest.fixture(autouse=True)
def _mock_releases_index(monkeypatch):
    resolver.clear_index_cache()

    async def _fake_fetch() -> dict:
        return _index()

    monkeypatch.setattr(resolver, "_fetch_index_from_network", _fake_fetch)
    yield
    resolver.clear_index_cache()


class TestCreateWithBuildVersion:
    async def test_create_resolves_repositories(self, client, admin_role_token_a):
        resp = await client.post(
            BASE,
            headers=_hdr(admin_role_token_a),
            json={"name": "astra-1.7.5.6", "build_version": "1.7.5.6"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["repositories"] == [
            f"deb {RELEASES}/frozen/1.7/1.7.5/1.7.5.6/base-repository "
            "1.7_x86-64 main contrib non-free"
        ]

    async def test_explicit_repositories_win_over_build_version(
        self, client, admin_role_token_a,
    ):
        """Если repositories переданы явно — build_version резолв не затирает их."""
        repos = ["https://repo.example.org/manual"]
        resp = await client.post(
            BASE,
            headers=_hdr(admin_role_token_a),
            json={
                "name": "astra-manual",
                "build_version": "1.7.5.6",
                "repositories": repos,
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["repositories"] == repos

    async def test_create_without_build_version_leaves_empty(
        self, client, admin_role_token_a,
    ):
        resp = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json={"name": "astra-plain"},
        )
        assert resp.status_code == 201
        assert resp.json()["repositories"] == []

    async def test_unknown_build_version_returns_404(self, client, admin_role_token_a):
        resp = await client.post(
            BASE,
            headers=_hdr(admin_role_token_a),
            json={"name": "astra-ghost", "build_version": "1.7.9.99"},
        )
        assert_error(resp, 404, "OS_RELEASE_NOT_FOUND")

    async def test_malformed_build_version_returns_422(self, client, admin_role_token_a):
        resp = await client.post(
            BASE,
            headers=_hdr(admin_role_token_a),
            json={"name": "astra-bad", "build_version": "1.7"},
        )
        assert_error(resp, 422, "VALIDATION_ERROR")


class TestResolveEndpoint:
    async def _make(self, client, token, name="astra-resolve"):
        created = await client.post(
            BASE, headers=_hdr(token), json={"name": name},
        )
        assert created.status_code == 201, created.text
        return created.json()["id"]

    async def test_resolve_sets_repositories(self, client, admin_role_token_a):
        ov_id = await self._make(client, admin_role_token_a)
        resp = await client.post(
            f"{BASE}/{ov_id}/resolve-repositories",
            headers=_hdr(admin_role_token_a),
            json={"build_version": "1.8.1.6"},
        )
        assert resp.status_code == 200, resp.text
        # 1.8 — installation-di выкинут.
        assert resp.json()["repositories"] == [
            f"deb {RELEASES}/1.8/1.8.1/1.8.1.6/main-repository "
            "1.8_x86-64 main contrib non-free"
        ]

    async def test_resolve_overwrites_previous(self, client, admin_role_token_a):
        ov_id = await self._make(client, admin_role_token_a, name="astra-overwrite")
        await client.patch(
            f"{BASE}/{ov_id}",
            headers=_hdr(admin_role_token_a),
            json={"repositories": ["https://repo.example.org/old"]},
        )
        resp = await client.post(
            f"{BASE}/{ov_id}/resolve-repositories",
            headers=_hdr(admin_role_token_a),
            json={"build_version": "1.7.5.6"},
        )
        assert resp.status_code == 200
        assert resp.json()["repositories"] == [
            f"deb {RELEASES}/frozen/1.7/1.7.5/1.7.5.6/base-repository "
            "1.7_x86-64 main contrib non-free"
        ]

    async def test_reader_cannot_resolve(
        self, client, reader_token_a, admin_role_token_a,
    ):
        ov_id = await self._make(client, admin_role_token_a, name="astra-ro-resolve")
        resp = await client.post(
            f"{BASE}/{ov_id}/resolve-repositories",
            headers=_hdr(reader_token_a),
            json={"build_version": "1.7.5.6"},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_no_token_cannot_resolve(self, client, admin_role_token_a):
        ov_id = await self._make(client, admin_role_token_a, name="astra-noauth-resolve")
        resp = await client.post(
            f"{BASE}/{ov_id}/resolve-repositories",
            json={"build_version": "1.7.5.6"},
        )
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_resolve_nonexistent_version_returns_404(
        self, client, admin_role_token_a,
    ):
        resp = await client.post(
            f"{BASE}/osv_ghost/resolve-repositories",
            headers=_hdr(admin_role_token_a),
            json={"build_version": "1.7.5.6"},
        )
        assert_error(resp, 404, "OS_VERSION_NOT_FOUND")

    async def test_resolve_unknown_build_returns_404(self, client, admin_role_token_a):
        ov_id = await self._make(client, admin_role_token_a, name="astra-unknown-build")
        resp = await client.post(
            f"{BASE}/{ov_id}/resolve-repositories",
            headers=_hdr(admin_role_token_a),
            json={"build_version": "1.7.9.99"},
        )
        assert_error(resp, 404, "OS_RELEASE_NOT_FOUND")
