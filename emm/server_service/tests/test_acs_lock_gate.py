"""Тесты единого гейта `ensure_not_acs_locked` на worker-dispatch эндпоинтах.

Пока `busy_state=acs` (сервер занят снимком/восстановлением через ACS),
управлять сервером — дёргать worker — вправе только department_admin отдела
сервера или носитель service-роли `admin` в server_service (см.
`services/reservation.py::is_server_admin`). Остальным — 409
`SERVER_ACS_BUSY`. Read/tab-switch и правки, не дёргающие worker (карточка
сервера, привязка учётки), гейтом не тронуты — это отдельное сознательное
решение, покрыто регрессионными тестами ниже.

Изолированный unit-тест самой `ensure_not_acs_locked` (admin проходит,
остальные — 409) уже есть в `test_acs_snapshots_dispatch.py::TestEnsureNotAcsLockedUnit`
— здесь не дублируется, только HTTP-уровень fan-out по затронутым точкам.

Покрытие (полная матрица stranger/dept_admin/service_admin — на трёх
представительных точках из разных файлов; остальные — представительный
stranger-blocked, т.к. используют тот же вызов гейта):

* ipmi.py `_dispatch_power` (power/on) — полная матрица + free-регрессия.
* server_account.py `_ensure_no_linked_server_reserved` (rotate_password) —
  полная матрица.
* worker_dispatch.py `install_node_exporter_dispatch` — полная матрица.
* worker_dispatch.py: account provision/update_on_host, astra-update,
  management-credentials rotate, single prepare, power/status — stranger-
  blocked (тот же паттерн вызова, что и выше).
* services/vm.py `prepare_vms_hub` (`POST /servers/{id}/prepare-vms-hub`) —
  stranger-blocked (кастомный грант, т.к. `vms_hub_prepare` по умолчанию есть
  только у `admin`).
* installed_packages.py bulk-action — per-server `acs_busy` статус, не валит
  батч; admin получает `ok`.
* Регрессия: PATCH /servers/{id} (чистая DB-мутация, worker не дёргает) и
  GET /servers/{id} не гейтятся ACS-локом.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from src.core.constants import BusyState
from tests._helpers import assert_error, auth_hdr as _hdr, make_dispatch_capture

SRV = "/api/server/v1/servers"
ACC = "/api/server/v1/server-accounts"


# ── Токены ────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def dept_admin_token_a(make_token, dept_a) -> str:
    """Platform `department_admin` dep_a с базовой ролью `operator` (не `admin`).

    Роль `operator` тут только чтобы пройти обычный RBAC (без неё
    `require_resource_action` отбивает 403 ещё до ACS-гейта — платформенная
    `department_admin` сама по себе матрицу действий не наполняет). Проход
    через сам ACS-гейт при этом обеспечивает именно платформенная роль
    (первая ветка `is_server_admin`), а не `operator` — изолирует её от
    `admin_role_token_a` (вторая ветка — service-роль `admin`).
    """
    return make_token(
        platform_role="department_admin", department_id=dept_a,
        service_roles={"server_service": ["operator"]},
    )


@pytest_asyncio.fixture
async def dept_admin_token_b(make_token, dept_b) -> str:
    """Department_admin ЧУЖОГО отдела (dep_b) — не должен проходить гейт dep_a-сервера."""
    return make_token(
        platform_role="department_admin", department_id=dept_b,
        service_roles={"server_service": ["operator"]},
    )


# ── Fixtures: серверы, занятые ACS ────────────────────────────────────────────


@pytest_asyncio.fixture
async def make_acs_server(make_server, db):
    """Сервер dep_a с `busy_state=acs` (снимок/восстановление в процессе)."""

    async def _factory(*, with_ipmi: bool = False, managed: bool = False, department_id: str = "dep_a"):
        srv = await make_server(department_id=department_id, with_ipmi=with_ipmi)
        if managed:
            srv.is_managed = True
            srv.management_user = "dbos"
        srv.busy_state = BusyState.ACS
        srv.busy_note = "ACS_CREATE_osv_test"
        await db.flush()
        return srv

    return _factory


@pytest.fixture
def captured_dispatch(monkeypatch):
    """`worker_client.dispatch_task[_with_hit]` замочен во всех известных call-site'ах."""
    return make_dispatch_capture(
        monkeypatch,
        "src.api.v1.endpoints._dispatch.worker_client.dispatch_task",
        "src.api.v1.endpoints._dispatch.worker_client.dispatch_task_with_hit",
    )


# ── 1. IPMI power/on — полная матрица + free-регрессия ───────────────────────


class TestPowerAcsGate:
    async def test_stranger_blocked(
        self, client, operator_token_a, make_acs_server, captured_dispatch,
    ):
        srv = await make_acs_server(with_ipmi=True)
        resp = await client.post(
            f"{SRV}/{srv.id}/ipmi/power/on", headers=_hdr(operator_token_a),
        )
        body = assert_error(resp, 409, "SERVER_ACS_BUSY")
        assert body["details"]["busy_note"] == "ACS_CREATE_osv_test"
        assert captured_dispatch == []

    async def test_dept_admin_of_same_dept_passes(
        self, client, dept_admin_token_a, make_acs_server, captured_dispatch,
    ):
        srv = await make_acs_server(with_ipmi=True)
        resp = await client.post(
            f"{SRV}/{srv.id}/ipmi/power/on", headers=_hdr(dept_admin_token_a),
        )
        assert resp.status_code == 202, resp.text

    async def test_service_admin_role_passes(
        self, client, admin_role_token_a, make_acs_server, captured_dispatch,
    ):
        srv = await make_acs_server(with_ipmi=True)
        resp = await client.post(
            f"{SRV}/{srv.id}/ipmi/power/on", headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 202, resp.text

    async def test_dept_admin_of_other_dept_blocked(
        self, client, dept_admin_token_b, make_acs_server, captured_dispatch,
    ):
        """Department_admin чужого отдела — не проходит: acs-лок не смотрит на визибилити,
        но `is_server_admin` требует совпадения department_id."""
        srv = await make_acs_server(with_ipmi=True, department_id="dep_a")
        resp = await client.post(
            f"{SRV}/{srv.id}/ipmi/power/on", headers=_hdr(dept_admin_token_b),
        )
        # Cross-dept viewer видит 404 раньше, чем дошло бы до ACS-гейта —
        # для dep_b-админа сервер dep_a невидим в принципе.
        assert resp.status_code in (403, 404)

    async def test_free_server_not_gated(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        """Регрессия: свободный сервер acs-гейт не трогает."""
        srv = await make_server(department_id="dep_a", with_ipmi=True)
        resp = await client.post(
            f"{SRV}/{srv.id}/ipmi/power/on", headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text


# ── 2. server_account fan-out (_ensure_no_linked_server_reserved) ────────────


class TestAccountFanoutAcsGate:
    async def test_stranger_rotate_password_blocked(
        self, client, operator_token_a, make_acs_server, make_account,
    ):
        srv = await make_acs_server()
        acc = await make_account(server_id=srv.id, login="deploy")
        resp = await client.post(
            f"{ACC}/{acc.id}/rotate_password", headers=_hdr(operator_token_a),
        )
        assert_error(resp, 409, "SERVER_ACS_BUSY")

    async def test_dept_admin_rotate_password_allowed(
        self, client, dept_admin_token_a, make_acs_server, make_account,
    ):
        srv = await make_acs_server()
        acc = await make_account(server_id=srv.id, login="deploy")
        resp = await client.post(
            f"{ACC}/{acc.id}/rotate_password", headers=_hdr(dept_admin_token_a),
        )
        assert resp.status_code == 200, resp.text

    async def test_service_admin_rotate_password_allowed(
        self, client, admin_role_token_a, make_acs_server, make_account,
    ):
        srv = await make_acs_server()
        acc = await make_account(server_id=srv.id, login="deploy")
        resp = await client.post(
            f"{ACC}/{acc.id}/rotate_password", headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200, resp.text

    async def test_free_server_account_not_gated(
        self, client, operator_token_a, make_server, make_account,
    ):
        """Регрессия: аккаунт на свободном сервере ротируется без гейта."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="deploy")
        resp = await client.post(
            f"{ACC}/{acc.id}/rotate_password", headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 200, resp.text


# ── 3. install-node-exporter — полная матрица ────────────────────────────────


class TestInstallNodeExporterAcsGate:
    async def test_stranger_blocked(
        self, client, operator_token_a, make_acs_server, captured_dispatch,
    ):
        srv = await make_acs_server(managed=True)
        resp = await client.post(
            f"{SRV}/{srv.id}/install-node-exporter", headers=_hdr(operator_token_a),
        )
        assert_error(resp, 409, "SERVER_ACS_BUSY")
        assert captured_dispatch == []

    async def test_dept_admin_passes(
        self, client, dept_admin_token_a, make_acs_server, captured_dispatch,
    ):
        srv = await make_acs_server(managed=True)
        resp = await client.post(
            f"{SRV}/{srv.id}/install-node-exporter", headers=_hdr(dept_admin_token_a),
        )
        assert resp.status_code == 202, resp.text

    async def test_service_admin_passes(
        self, client, admin_role_token_a, make_acs_server, captured_dispatch,
    ):
        srv = await make_acs_server(managed=True)
        resp = await client.post(
            f"{SRV}/{srv.id}/install-node-exporter", headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 202, resp.text


# ── 4. Остальные worker-dispatch точки — представительный stranger-blocked ──


class TestRemainingDispatchPointsBlocked:
    async def test_account_provision_blocked(
        self, client, operator_token_a, make_acs_server, make_account, captured_dispatch,
    ):
        srv = await make_acs_server()
        acc = await make_account(server_id=srv.id, login="deploy")
        resp = await client.post(
            f"{ACC}/{acc.id}/provision?server_id={srv.id}",
            headers=_hdr(operator_token_a),
        )
        assert_error(resp, 409, "SERVER_ACS_BUSY")
        assert captured_dispatch == []

    async def test_account_update_on_host_blocked(
        self, client, operator_token_a, make_acs_server, make_account, captured_dispatch,
    ):
        srv = await make_acs_server()
        acc = await make_account(server_id=srv.id, login="deploy")
        resp = await client.post(
            f"{ACC}/{acc.id}/update_on_host?server_id={srv.id}",
            headers=_hdr(operator_token_a),
        )
        assert_error(resp, 409, "SERVER_ACS_BUSY")
        assert captured_dispatch == []

    # `account_deprovision_dispatch` отдельным тестом не покрыт: он зовёт тот
    # же `_dispatch_account_on_host` и тот же гейт (`ensure_not_reserved_for`
    # + `ensure_not_acs_locked` рядом), что и update_on_host выше — разница
    # только в task_kind/audit_action, не в авторизации или гейте.

    async def test_astra_update_blocked(
        self, client, operator_token_a, make_acs_server, captured_dispatch,
    ):
        srv = await make_acs_server()
        resp = await client.post(
            f"{SRV}/{srv.id}/astra-update",
            headers=_hdr(operator_token_a),
            json={"os_version_id": "osv_does_not_matter"},
        )
        assert_error(resp, 409, "SERVER_ACS_BUSY")
        assert captured_dispatch == []

    async def test_management_creds_rotate_blocked(
        self, client, operator_token_a, make_acs_server, captured_dispatch,
    ):
        srv = await make_acs_server()
        resp = await client.post(
            f"{SRV}/{srv.id}/management-credentials/rotate",
            headers=_hdr(operator_token_a),
        )
        assert_error(resp, 409, "SERVER_ACS_BUSY")
        assert captured_dispatch == []

    async def test_prepare_blocked(
        self, client, operator_token_a, make_acs_server, captured_dispatch,
    ):
        from tests._helpers import b64

        srv = await make_acs_server()
        resp = await client.post(
            f"{SRV}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={"username_b64": b64("bootadmin"), "password_b64": b64("Boot1234!StrongPwd")},
        )
        assert_error(resp, 409, "SERVER_ACS_BUSY")
        assert captured_dispatch == []

    async def test_power_status_blocked(
        self, client, operator_token_a, make_acs_server, captured_dispatch,
    ):
        srv = await make_acs_server(with_ipmi=True)
        resp = await client.post(
            f"{SRV}/{srv.id}/power/status", headers=_hdr(operator_token_a),
        )
        assert_error(resp, 409, "SERVER_ACS_BUSY")
        assert captured_dispatch == []


# ── 4.5. vms_hub prepare (services/vm.py) — вне исходной разведки, добавлено ──
# отдельно: явно названо в требовании владельца ("vms_hub prepare"), но своего
# гейта по брони раньше не имело вовсе (ни ACS, ни обычного busy) — тип-wide
# `vms_hub_prepare` по умолчанию есть только у `admin`, поэтому stranger'у
# нужен точечный грант, как в `stranger_can_delete` из test_server_reservation_gate.py.


@pytest_asyncio.fixture
async def hub_prepare_grant_role_token(make_token, db, dept_a) -> str:
    from src.models import EntityPermission
    from src.utils.ids import _new_id

    db.add(EntityPermission(
        id=_new_id("ep_"),
        entity_type="vm",
        role="hub_preparer",
        action="vms_hub_prepare",
        department_id=dept_a,
    ))
    await db.flush()
    return make_token(
        department_id=dept_a,
        service_roles={"server_service": ["hub_preparer"]},
    )


class TestVmsHubPrepareAcsGate:
    async def test_stranger_blocked(
        self, client, hub_prepare_grant_role_token, make_acs_server, captured_dispatch,
    ):
        srv = await make_acs_server(managed=True)
        resp = await client.post(
            f"{SRV}/{srv.id}/prepare-vms-hub", headers=_hdr(hub_prepare_grant_role_token),
        )
        assert_error(resp, 409, "SERVER_ACS_BUSY")
        assert captured_dispatch == []


# ── 5. installed_packages bulk-action — статус acs_busy, не валит батч ───────


class TestInstalledPackagesBulkAcsGate:
    URL = f"{SRV}/packages/bulk-action"

    @staticmethod
    def _by_id(results: list[dict]) -> dict[str, dict]:
        return {r["server_id"]: r for r in results}

    async def test_stranger_gets_acs_busy_status_batch_continues(
        self, client, operator_token_a, make_acs_server, make_server, captured_dispatch, db,
    ):
        acs_srv = await make_acs_server(managed=True)
        free_srv = await make_server(department_id="dep_a")
        free_srv.is_managed = True
        free_srv.management_user = "dbos"
        await db.flush()

        resp = await client.post(
            self.URL,
            headers=_hdr(operator_token_a),
            json={
                "server_ids": [acs_srv.id, free_srv.id],
                "action": "install",
                "packages": ["sl"],
            },
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        results = self._by_id(body["results"])
        assert results[acs_srv.id]["status"] == "acs_busy"
        assert results[free_srv.id]["status"] == "ok"
        assert body["dispatched"] == 1

    async def test_admin_gets_ok_status(
        self, client, dept_admin_token_a, make_acs_server, captured_dispatch,
    ):
        srv = await make_acs_server(managed=True)
        resp = await client.post(
            self.URL,
            headers=_hdr(dept_admin_token_a),
            json={"server_ids": [srv.id], "action": "install", "packages": ["sl"]},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert self._by_id(body["results"])[srv.id]["status"] == "ok"
        assert body["dispatched"] == 1


# ── 6. Регрессия: операции без worker-dispatch ACS-гейтом не тронуты ────────


class TestNonDispatchOperationsNotGated:
    async def test_update_server_card_not_gated(
        self, client, operator_token_a, make_acs_server,
    ):
        """PATCH карточки сервера — чистая DB-мутация, воркер не дёргает.
        Сознательно НЕ гейтится ACS-локом (см. отчёт волны)."""
        srv = await make_acs_server()
        resp = await client.patch(
            f"{SRV}/{srv.id}",
            headers=_hdr(operator_token_a),
            json={"display_name": "renamed-during-acs"},
        )
        assert resp.status_code == 200, resp.text

    async def test_read_server_not_gated(
        self, client, operator_token_a, make_acs_server,
    ):
        srv = await make_acs_server()
        resp = await client.get(f"{SRV}/{srv.id}", headers=_hdr(operator_token_a))
        assert resp.status_code == 200
        assert resp.json()["busy_state"] == "acs"
