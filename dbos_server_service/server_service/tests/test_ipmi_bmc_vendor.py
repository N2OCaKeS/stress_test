"""Тесты CRUD `ipmi_controllers` с разными `bmc_vendor`.

Покрытие:

* POST / — каждый из 3 вендоров (idrac/ilo/ipmi_generic) принимается,
  default fallback на `ipmi_generic` при отсутствии поля.
* GET / — response содержит `bmc_vendor`.
* PATCH / — смена vendor (idrac → ilo), без vendor поле сохраняется.
* Невалидный vendor (`hpe_xyz`) — 422 на схеме.
* Internal endpoint `/internal/.../ipmi/credentials` — отдаёт `bmc_vendor`
  worker'у (нужен для подбора Redfish Manager-id).
"""

from __future__ import annotations

import pytest

BASE = "/api/server/v1/servers"
INTERNAL_BASE = "/api/server/v1/internal/servers"


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestCreateWithVendor:
    async def test_idrac_vendor_accepted(self, client, admin_token, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(admin_token),
            json={
                "kind": "idrac",
                "bmc_vendor": "idrac",
                "endpoint_url": "https://idrac.example.com",
                "username": "root",
                "password": "Calvin",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["bmc_vendor"] == "idrac"

    async def test_ilo_vendor_accepted(self, client, admin_token, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(admin_token),
            json={
                "kind": "ilo",
                "bmc_vendor": "ilo",
                "endpoint_url": "https://ilo.example.com",
                "username": "admin",
                "password": "p",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["bmc_vendor"] == "ilo"

    async def test_ipmi_generic_vendor_accepted(
        self, client, admin_token, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(admin_token),
            json={
                "kind": "redfish",
                "bmc_vendor": "ipmi_generic",
                "endpoint_url": "https://bmc.example.com",
                "username": "u",
                "password": "p",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["bmc_vendor"] == "ipmi_generic"

    async def test_vendor_omitted_defaults_to_ipmi_generic(
        self, client, admin_token, make_server,
    ):
        """Поле bmc_vendor опционально на create — default `ipmi_generic`."""
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(admin_token),
            json={
                "kind": "ipmi",
                "endpoint_url": "https://bmc.example.com",
                "username": "u",
                "password": "p",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["bmc_vendor"] == "ipmi_generic"

    async def test_invalid_vendor_returns_422(
        self, client, admin_token, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(admin_token),
            json={
                "kind": "idrac",
                "bmc_vendor": "hpe_xyz",
                "endpoint_url": "https://x",
                "username": "u",
                "password": "p",
            },
        )
        assert resp.status_code == 422


class TestGetVendor:
    async def test_get_returns_vendor(
        self, client, admin_token, make_server, make_ipmi, db,
    ):
        """GET /ipmi отдаёт сохранённый bmc_vendor."""
        from src.models import IpmiController
        from sqlalchemy import select

        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id, kind="ilo")
        # Записываем vendor напрямую — make_ipmi не принимает его параметром
        ctrl.bmc_vendor = "ilo"
        await db.flush()

        resp = await client.get(f"{BASE}/{srv.id}/ipmi", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["bmc_vendor"] == "ilo"

        # Sanity: убедимся, что в БД действительно сохранено
        row = (
            await db.execute(
                select(IpmiController).where(IpmiController.server_id == srv.id)
            )
        ).scalar_one()
        assert row.bmc_vendor == "ilo"


class TestUpdateVendor:
    async def test_patch_changes_vendor(
        self, client, admin_token, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id, kind="idrac")
        resp = await client.patch(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(admin_token),
            json={"bmc_vendor": "ilo"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["bmc_vendor"] == "ilo"

    async def test_patch_without_vendor_keeps_value(
        self, client, admin_token, make_server, make_ipmi, db,
    ):
        from src.models import IpmiController
        from sqlalchemy import select

        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id)
        ctrl.bmc_vendor = "ilo"
        await db.flush()
        await client.patch(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(admin_token),
            json={"username": "new_login"},
        )
        row = (
            await db.execute(
                select(IpmiController).where(IpmiController.server_id == srv.id)
            )
        ).scalar_one()
        assert row.bmc_vendor == "ilo"
        assert row.username == "new_login"

    async def test_patch_invalid_vendor_returns_422(
        self, client, admin_token, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.patch(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(admin_token),
            json={"bmc_vendor": "wrong_vendor"},
        )
        assert resp.status_code == 422


@pytest.mark.usefixtures("soft_dept_mode")
class TestInternalCredentialsVendor:
    async def test_internal_credentials_include_vendor(
        self, client, worker_pat_token, make_server, make_ipmi, db,
    ):
        """worker'у нужен bmc_vendor — internal endpoint должен возвращать его."""
        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id, kind="ilo")
        ctrl.bmc_vendor = "ilo"
        await db.flush()

        resp = await client.get(
            f"{INTERNAL_BASE}/{srv.id}/ipmi/credentials",
            headers=_hdr(worker_pat_token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["bmc_vendor"] == "ilo"
        assert "password" in body

    async def test_internal_credentials_default_vendor(
        self, client, worker_pat_token, make_server, make_ipmi,
    ):
        """Default fixture make_ipmi не задаёт vendor — fallback `ipmi_generic`."""
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.get(
            f"{INTERNAL_BASE}/{srv.id}/ipmi/credentials",
            headers=_hdr(worker_pat_token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["bmc_vendor"] == "ipmi_generic"
