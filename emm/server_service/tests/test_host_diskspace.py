"""GET /host/diskspace — локальная заполняемость диска на хосте server_service.

Не про managed test-серверы (`ServerDisk`/`servers`) — про хост, где крутится
сам процесс. См. `src/api/v1/endpoints/host_disk.py`.
"""

from __future__ import annotations

from tests._helpers import assert_error, auth_hdr as _hdr

from src.api.v1.endpoints import host_disk as host_disk_module

URL = "/api/server/v1/host/diskspace"


class TestAuth:
    async def test_anonymous_returns_401(self, client):
        resp = await client.get(URL)
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_authenticated_returns_200(self, client, admin_role_token_a):
        resp = await client.get(URL, headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200


class TestDiskUsage:
    async def test_existing_path_reports_available(self, client, admin_role_token_a, monkeypatch):
        # "/tmp" гарантированно существует в тестовом контейнере — happy path.
        monkeypatch.setattr(
            host_disk_module,
            "WATCHED_HOST_DISK_PATHS",
            [("/tmp", "/tmp")],
        )
        resp = await client.get(URL, headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200
        body = resp.json()
        assert body["paths"] == [
            {
                "path": "/tmp",
                "total_gb": body["paths"][0]["total_gb"],
                "used_gb": body["paths"][0]["used_gb"],
                "used_percent": body["paths"][0]["used_percent"],
                "available": True,
                "error": None,
            }
        ]
        entry = body["paths"][0]
        assert entry["available"] is True
        assert entry["error"] is None
        assert entry["total_gb"] > 0
        assert 0 <= entry["used_gb"] <= entry["total_gb"]
        assert 0 <= entry["used_percent"] <= 100

    async def test_missing_path_reports_not_mounted(self, client, admin_role_token_a, monkeypatch):
        monkeypatch.setattr(
            host_disk_module,
            "WATCHED_HOST_DISK_PATHS",
            [("/srv/ftp", "/definitely/does/not/exist/on/this/box")],
        )
        resp = await client.get(URL, headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200
        body = resp.json()
        assert body["paths"] == [
            {
                "path": "/srv/ftp",
                "total_gb": None,
                "used_gb": None,
                "used_percent": None,
                "available": False,
                "error": "not_mounted",
            }
        ]

    async def test_permission_denied_is_degraded_not_500(self, client, admin_role_token_a, monkeypatch):
        def _boom(_path):
            raise PermissionError("denied")

        monkeypatch.setattr(host_disk_module.shutil, "disk_usage", _boom)
        resp = await client.get(URL, headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200
        body = resp.json()
        assert all(p["available"] is False and p["error"] == "permission_denied" for p in body["paths"])

    async def test_default_watched_paths_match_legacy_semantics(self):
        """Регресс-guard: набор путей 1:1 с legacy `server_diskspace_used()`."""
        labels = [label for label, _stat_path in host_disk_module.WATCHED_HOST_DISK_PATHS]
        assert labels == ["/", "/srv/ftp", "/home/partimag"]
