"""Тесты GET /servers/{id}/acs-snapshots — живой список снимков ACS сервера.

Покрывает:
* happy path — ACS отдаёт снимки нескольких серверов вперемешку, фильтр по
  префиксу `{hostname}-` этого сервера + вычленение `version_name`,
  сортировка по имени;
* нет снимков этого сервера (только чужие) → пустой список, не ошибка;
* ACS выключен платформенно → 503 ACS_DISABLED;
* отдел без opt-in → 403 ACS_DEPARTMENT_NOT_ENABLED;
* нет права `acs_snapshot` → 403 PERMISSION_DENIED;
* чужой департамент → 404;
* ACS-клиент кидает ServiceUnavailableError (timeout/unreachable) →
  пробрасывается как есть, с failure-audit.
"""

from __future__ import annotations

import pytest

from tests._helpers import assert_error, auth_hdr as _hdr

BASE = "/api/server/v1/servers"


async def _enable_acs(db, *, department_id: str = "dep_a", enabled: bool = True) -> None:
    from src.models.acs_department_access import AcsDepartmentAccess
    from src.models.acs_settings import SINGLETON_ID, AcsSettings
    from src.services import secrets_service
    from src.utils.ids import acs_department_access_id

    row = await db.get(AcsSettings, SINGLETON_ID)
    if row is None:
        row = AcsSettings(
            id=SINGLETON_ID, enabled=enabled, acs_url="http://acs.example.com",
            acs_password_encrypted=secrets_service.encrypt(
                "acs-plaintext-secret", aad=secrets_service.aad_for_acs_password(SINGLETON_ID),
            ),
        )
        db.add(row)
    else:
        row.enabled = enabled
    db.add(AcsDepartmentAccess(
        id=acs_department_access_id(), department_id=department_id, is_enabled=True,
    ))
    await db.flush()


@pytest.fixture
def captured_emits(monkeypatch):
    from tests._helpers import make_emit_capture

    return make_emit_capture(monkeypatch)


class TestAcsSnapshotList:
    async def test_filters_by_hostname_prefix_and_sorts(
        self, client, admin_role_token_a, make_server, db, monkeypatch,
    ):
        srv = await make_server(department_id="dep_a", hostname="lowserver1")
        await _enable_acs(db)

        async def fake_list_snapshots(base_url, password):
            return [
                "othertest-Astra 1.8",
                f"{srv.hostname}-Astra 1.8 zzz",
                f"{srv.hostname}-Astra 1.7 aaa",
                "unrelated-name",
            ]

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.acs_client.list_snapshots",
            fake_list_snapshots,
        )

        resp = await client.get(
            f"{BASE}/{srv.id}/acs-snapshots", headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        names = [item["name"] for item in body["snapshots"]]
        assert names == [
            f"{srv.hostname}-Astra 1.7 aaa",
            f"{srv.hostname}-Astra 1.8 zzz",
        ]
        version_names = {item["name"]: item["version_name"] for item in body["snapshots"]}
        assert version_names[f"{srv.hostname}-Astra 1.7 aaa"] == "Astra 1.7 aaa"
        assert version_names[f"{srv.hostname}-Astra 1.8 zzz"] == "Astra 1.8 zzz"
        # Некомпактный хвост нормализация не трогает.
        assert body["snapshots"][0]["normalized_version"] == "Astra 1.7 aaa"
        assert body["hostname"] == srv.hostname

    async def test_no_matching_snapshots_returns_empty(
        self, client, admin_role_token_a, make_server, db, monkeypatch,
    ):
        srv = await make_server(department_id="dep_a", hostname="lonelyserver")
        await _enable_acs(db)

        async def fake_list_snapshots(base_url, password):
            return ["othertest-Astra 1.8"]

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.acs_client.list_snapshots",
            fake_list_snapshots,
        )

        resp = await client.get(
            f"{BASE}/{srv.id}/acs-snapshots", headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["snapshots"] == []

    async def test_acs_disabled_503(
        self, client, admin_role_token_a, make_server, db,
    ):
        srv = await make_server(department_id="dep_a")
        await _enable_acs(db, enabled=False)

        resp = await client.get(
            f"{BASE}/{srv.id}/acs-snapshots", headers=_hdr(admin_role_token_a),
        )
        assert_error(resp, 503, "ACS_DISABLED")

    async def test_department_not_enabled_403(
        self, client, admin_role_token_a, make_server, db,
    ):
        from src.models.acs_settings import SINGLETON_ID, AcsSettings
        from src.services import secrets_service

        srv = await make_server(department_id="dep_a")
        db.add(AcsSettings(
            id=SINGLETON_ID, enabled=True, acs_url="http://acs.example.com",
            acs_password_encrypted=secrets_service.encrypt(
                "pwd", aad=secrets_service.aad_for_acs_password(SINGLETON_ID),
            ),
        ))
        await db.flush()
        # AcsDepartmentAccess для dep_a намеренно не создаём.

        resp = await client.get(
            f"{BASE}/{srv.id}/acs-snapshots", headers=_hdr(admin_role_token_a),
        )
        assert_error(resp, 403, "ACS_DEPARTMENT_NOT_ENABLED")

    async def test_no_permission_403(
        self, client, reader_token_a, make_server, db,
    ):
        srv = await make_server(department_id="dep_a")
        await _enable_acs(db)

        resp = await client.get(
            f"{BASE}/{srv.id}/acs-snapshots", headers=_hdr(reader_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_cross_department_404(
        self, client, admin_role_token_a, make_server, db,
    ):
        srv = await make_server(department_id="dep_b")
        await _enable_acs(db, department_id="dep_b")

        resp = await client.get(
            f"{BASE}/{srv.id}/acs-snapshots", headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 404, resp.text

    async def test_acs_unreachable_propagates_503(
        self, client, admin_role_token_a, make_server, db, monkeypatch, captured_emits,
    ):
        from src.core.exceptions import ServiceUnavailableError

        srv = await make_server(department_id="dep_a")
        await _enable_acs(db)

        async def boom(base_url, password):
            raise ServiceUnavailableError(
                error_code="ACS_UNREACHABLE", message="connection refused",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.acs_client.list_snapshots", boom,
        )

        resp = await client.get(
            f"{BASE}/{srv.id}/acs-snapshots", headers=_hdr(admin_role_token_a),
        )
        assert_error(resp, 503, "ACS_UNREACHABLE")
        reasons = [e.get("details", {}).get("reason") for e in captured_emits]
        assert "acs_unreachable" in reasons
