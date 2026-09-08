"""Интеграционные тесты гейта брони сервера (задача #9).

Когда сервер забронирован (`busy_state` в `busy`/`testing`), деструктивные операции
(power on/off/reboot, мутации server_account, delete/update/os-sync сервера)
доступны только владельцу брони (`busy_user_id`) ИЛИ админу (platform
`department_admin` отдела сервера / service-роль `admin`). Остальным — 409
`SERVER_RESERVED`.

Покрытие:
* power: чужой → 409, владелец → проходит, админ → проходит;
* server_account: deprovision/rotate/ssh-key/delete чужим → 409, владельцем/
  админом → проходит;
* server: update/delete/os-sync чужим на занятом → 409;
* read (GET карточки, list) занятого сервера доступен любому с `view`;
* release занятого чужим оператором/админом — не гейтится (можно снять бронь);
* free-сервер гейт не трогает (регрессия — обычный happy path);
* сервисная бронь (`busy_state='testing'`, держатель — сервис): чужой и
  бывший владелец → 409, админ проходит, read не гейтится.

Реальный PostgreSQL через сервисный docker-compose.test.yml (см. conftest).
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from tests._helpers import assert_error, auth_hdr as _hdr

SRV = "/api/server/v1/servers"
ACC = "/api/server/v1/server-accounts"


# ── Фикстуры: владелец брони + чужой оператор того же отдела ──────────────────


@pytest_asyncio.fixture
def owner_user_id() -> str:
    """Стабильный user_id владельца брони — кладём его в busy_user_id."""
    return "usr_owner001"


@pytest_asyncio.fixture
async def owner_token(make_token, dept_a, owner_user_id) -> str:
    """Operator с фиксированным user_id — он же будет держателем брони."""
    return make_token(
        user_id=owner_user_id,
        department_id=dept_a,
        service_roles={"server_service": ["operator"]},
    )


@pytest_asyncio.fixture
async def stranger_token(make_token, dept_a) -> str:
    """Другой operator того же отдела — НЕ владелец брони и НЕ админ.

    Помимо `operator` несёт кастомную роль `gate_delete`. Сама по себе роль
    прав не даёт — `delete`-грант на неё навешивает фикстура
    `stranger_can_delete` точечно (per-dept). Так чужак проходит
    permission-чек на delete и упирается именно в гейт брони, а встроенный
    `operator` остаётся нетронутым.
    """
    return make_token(
        user_id="usr_stranger99",
        department_id=dept_a,
        service_roles={"server_service": ["operator", "gate_delete"]},
    )


@pytest_asyncio.fixture
async def stranger_can_delete(db, dept_a):
    """Выдать роли `gate_delete` право `delete` на server и server_account в dep_a.

    Permission-чек (403) идёт РАНЬШЕ гейта брони (409). Чтобы тест проверял
    именно бронь, у чужака должно быть legitimate-право delete — иначе он
    упрётся в 403 на permission и до reserve-гейта не дойдёт.
    """
    from src.models import EntityPermission
    from src.utils.ids import _new_id

    for entity_type in ("server", "server_account"):
        db.add(EntityPermission(
            id=_new_id("ep_"),
            entity_type=entity_type,
            role="gate_delete",
            action="delete",
            department_id=dept_a,
        ))
    await db.flush()


@pytest_asyncio.fixture
async def make_busy_server(make_server, db, owner_user_id):
    """Сервер dep_a, забронированный под `owner_user_id` (busy_state='busy')."""
    from datetime import datetime, timezone

    from src.core.constants import BusyState

    async def _factory(*, with_ipmi: bool = False):
        srv = await make_server(department_id="dep_a", with_ipmi=with_ipmi)
        srv.busy_state = BusyState.BUSY
        srv.busy_user_id = owner_user_id
        srv.busy_since = datetime.now(timezone.utc)
        srv.busy_note = "stress-run-42"
        await db.flush()
        return srv

    return _factory


@pytest_asyncio.fixture
async def make_testing_server(make_server, db):
    """Сервер dep_a под сервисной бронью исполнения теста.

    `busy_state='testing'`, держатель — сервис (`busy_actor_type='service'`,
    `busy_service_name='testing_service'`), человека-владельца нет вовсе.
    """
    from datetime import datetime, timezone

    from src.core.constants import BusyState

    async def _factory(*, with_ipmi: bool = False):
        srv = await make_server(department_id="dep_a", with_ipmi=with_ipmi)
        srv.busy_state = BusyState.TESTING
        srv.busy_actor_type = "service"
        srv.busy_service_name = "testing_service"
        srv.busy_since = datetime.now(timezone.utc)
        srv.busy_note = "smoke|1711rc42|6.6"
        await db.flush()
        return srv

    return _factory


# ── Power: гейт по брони ──────────────────────────────────────────────────────


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Заглушка `worker_client.dispatch*` в ipmi-endpoint'е.

    Реальный power-dispatch требует worker-БД + Redis, которых в тест-env нет
    (иначе 503 WORKER_DB_NOT_CONFIGURED). Тут нас интересует ИСХОД гейта брони
    (202 allowed / 409 reserved), а не инфраструктура dispatch'а — поэтому
    dispatch мочим в фиктивный task_id, как в `test_ipmi_endpoints.py`.
    """
    calls: list[dict] = []

    async def fake_dispatch(*, db=None, task_kind, target_server_id, payload,
                            created_by, request_id,
                            target_resource_id=None, idempotency_key=None,
                            priority=0,
                            return_hit=False):
        calls.append({"task_kind": task_kind, "target_server_id": target_server_id})
        new_id = f"tsk_{task_kind.replace('.', '_')}_fake_{len(calls)}"
        return (new_id, False) if return_hit else new_id

    async def fake_dispatch_with_hit(**kwargs):
        kwargs["return_hit"] = True
        return await fake_dispatch(**kwargs)

    monkeypatch.setattr(
        "src.api.v1.endpoints.ipmi.worker_client.dispatch_task", fake_dispatch,
    )
    monkeypatch.setattr(
        "src.api.v1.endpoints.ipmi.worker_client.dispatch_task_with_hit",
        fake_dispatch_with_hit,
    )
    return calls


class TestPowerReservationGate:
    async def test_stranger_power_on_blocked(
        self, client, stranger_token, make_busy_server,
    ):
        srv = await make_busy_server(with_ipmi=True)
        resp = await client.post(
            f"{SRV}/{srv.id}/ipmi/power/on", headers=_hdr(stranger_token),
        )
        body = assert_error(resp, 409, "SERVER_RESERVED")
        # В details — кто держит бронь, чтобы клиент мог показать «занято кем».
        assert body["details"]["busy_user_id"] == "usr_owner001"
        assert body["details"]["busy_note"] == "stress-run-42"

    async def test_owner_power_on_allowed(
        self, client, owner_token, make_busy_server, captured_dispatch,
    ):
        srv = await make_busy_server(with_ipmi=True)
        resp = await client.post(
            f"{SRV}/{srv.id}/ipmi/power/on", headers=_hdr(owner_token),
        )
        # Владелец проходит гейт — задача уходит воркеру (202).
        assert resp.status_code == 202, resp.text

    async def test_admin_power_off_allowed(
        self, client, admin_token, make_busy_server, captured_dispatch,
    ):
        srv = await make_busy_server(with_ipmi=True)
        resp = await client.post(
            f"{SRV}/{srv.id}/ipmi/power/off", headers=_hdr(admin_token),
        )
        assert resp.status_code == 202, resp.text

    async def test_free_server_power_not_gated(
        self, client, stranger_token, make_server, captured_dispatch,
    ):
        """Регрессия: свободный сервер гейт не трогает — обычный operator проходит."""
        srv = await make_server(department_id="dep_a", with_ipmi=True)
        resp = await client.post(
            f"{SRV}/{srv.id}/ipmi/power/reboot", headers=_hdr(stranger_token),
        )
        assert resp.status_code == 202, resp.text

    async def test_stranger_power_emits_reservation_denied(
        self, client, stranger_token, make_busy_server, captured_emits,
    ):
        srv = await make_busy_server(with_ipmi=True)
        await client.post(
            f"{SRV}/{srv.id}/ipmi/power/on", headers=_hdr(stranger_token),
        )
        denied = [
            e for e in captured_emits
            if e["action"] == "server.reservation_denied"
        ]
        assert len(denied) == 1
        assert denied[0]["status"] == "denied"
        assert denied[0]["allowed"] is False
        assert denied[0]["details"]["busy_user_id"] == "usr_owner001"
        assert denied[0]["details"]["blocked_action"] == "server.power_on"


# ── server_account: гейт по брони привязанного сервера ────────────────────────


class TestAccountReservationGate:
    async def test_stranger_rotate_password_blocked(
        self, client, stranger_token, make_busy_server, make_account,
    ):
        srv = await make_busy_server()
        acc = await make_account(server_id=srv.id, login="deploy")
        resp = await client.post(
            f"{ACC}/{acc.id}/rotate_password", headers=_hdr(stranger_token),
        )
        assert_error(resp, 409, "SERVER_RESERVED")

    async def test_owner_rotate_password_allowed(
        self, client, owner_token, make_busy_server, make_account,
    ):
        srv = await make_busy_server()
        acc = await make_account(server_id=srv.id, login="deploy")
        resp = await client.post(
            f"{ACC}/{acc.id}/rotate_password", headers=_hdr(owner_token),
        )
        assert resp.status_code == 200, resp.text

    async def test_admin_delete_account_allowed(
        self, client, admin_token, make_busy_server, make_account,
    ):
        srv = await make_busy_server()
        acc = await make_account(server_id=srv.id, login="deploy")
        resp = await client.delete(
            f"{ACC}/{acc.id}", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200, resp.text

    async def test_stranger_delete_account_blocked(
        self, client, stranger_token, stranger_can_delete,
        make_busy_server, make_account,
    ):
        srv = await make_busy_server()
        acc = await make_account(server_id=srv.id, login="deploy")
        resp = await client.delete(
            f"{ACC}/{acc.id}", headers=_hdr(stranger_token),
        )
        assert_error(resp, 409, "SERVER_RESERVED")

    async def test_stranger_set_ssh_key_blocked(
        self, client, stranger_token, make_busy_server, make_account,
    ):
        srv = await make_busy_server()
        acc = await make_account(server_id=srv.id, login="deploy")
        resp = await client.post(
            f"{ACC}/{acc.id}/ssh_key",
            headers=_hdr(stranger_token),
            json={"ssh_mode": "generate"},
        )
        assert_error(resp, 409, "SERVER_RESERVED")

    async def test_stranger_update_account_blocked(
        self, client, stranger_token, make_busy_server, make_account,
    ):
        srv = await make_busy_server()
        acc = await make_account(server_id=srv.id, login="deploy")
        resp = await client.patch(
            f"{ACC}/{acc.id}",
            headers=_hdr(stranger_token),
            json={"shell": "/bin/zsh"},
        )
        assert_error(resp, 409, "SERVER_RESERVED")

    async def test_free_server_account_rotate_not_gated(
        self, client, stranger_token, make_server, make_account,
    ):
        """Регрессия: аккаунт на свободном сервере ротируется без гейта."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="deploy")
        resp = await client.post(
            f"{ACC}/{acc.id}/rotate_password", headers=_hdr(stranger_token),
        )
        assert resp.status_code == 200, resp.text


# ── server CRUD/os-sync: гейт по брони ────────────────────────────────────────


class TestServerCrudReservationGate:
    async def test_stranger_update_server_blocked(
        self, client, stranger_token, make_busy_server,
    ):
        srv = await make_busy_server()
        resp = await client.patch(
            f"{SRV}/{srv.id}",
            headers=_hdr(stranger_token),
            json={"display_name": "renamed"},
        )
        assert_error(resp, 409, "SERVER_RESERVED")

    async def test_stranger_delete_server_blocked(
        self, client, stranger_token, stranger_can_delete, make_busy_server,
    ):
        srv = await make_busy_server()
        resp = await client.delete(f"{SRV}/{srv.id}", headers=_hdr(stranger_token))
        assert_error(resp, 409, "SERVER_RESERVED")

    async def test_stranger_os_sync_blocked(
        self, client, stranger_token, make_busy_server, db,
    ):
        from src.models import OsVersion
        from src.utils.ids import _new_id

        srv = await make_busy_server()
        osv = OsVersion(id=_new_id("osv_"), name="Astra 1.8 reserved-gate")
        db.add(osv)
        await db.flush()
        resp = await client.post(
            f"{SRV}/{srv.id}/os-sync",
            headers=_hdr(stranger_token),
            json={"os_version_id": osv.id},
        )
        assert_error(resp, 409, "SERVER_RESERVED")

    async def test_owner_update_server_allowed(
        self, client, owner_token, make_busy_server,
    ):
        srv = await make_busy_server()
        resp = await client.patch(
            f"{SRV}/{srv.id}",
            headers=_hdr(owner_token),
            json={"display_name": "owner-renamed"},
        )
        assert resp.status_code == 200, resp.text

    async def test_admin_delete_server_allowed(
        self, client, admin_token, make_busy_server,
    ):
        srv = await make_busy_server()
        resp = await client.delete(f"{SRV}/{srv.id}", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text


# ── Read и release не гейтятся ────────────────────────────────────────────────


class TestReadAndReleaseNotGated:
    async def test_stranger_can_read_reserved_server(
        self, client, stranger_token, make_busy_server,
    ):
        srv = await make_busy_server()
        resp = await client.get(f"{SRV}/{srv.id}", headers=_hdr(stranger_token))
        assert resp.status_code == 200
        assert resp.json()["busy_state"] == "busy"

    async def test_reader_can_list_accounts_of_reserved_server(
        self, client, reader_token_a, make_busy_server, make_account,
    ):
        srv = await make_busy_server()
        await make_account(server_id=srv.id, login="deploy")
        resp = await client.get(
            f"{ACC}?server_id={srv.id}", headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 1

    async def test_admin_can_release_reserved_server(
        self, client, admin_token, make_busy_server,
    ):
        """Админ снимает чужую бронь — release не гейтится."""
        srv = await make_busy_server()
        resp = await client.delete(f"{SRV}/{srv.id}/busy", headers=_hdr(admin_token))
        assert resp.status_code == 200
        assert resp.json()["busy_state"] == "free"

    async def test_stranger_with_release_role_can_release(
        self, client, owner_token, admin_role_token_a, make_busy_server,
    ):
        """Любая роль с busy_release снимает бронь — гейт release не трогает."""
        srv = await make_busy_server()
        resp = await client.delete(
            f"{SRV}/{srv.id}/busy", headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200
        assert resp.json()["busy_state"] == "free"


# ── Сервисная бронь (busy_state=testing) гейтит так же, как человеческая ──────


class TestServiceReservationGate:
    """Симметрия с человеческой бронью: держателя-человека нет, проходит админ.

    Сам держащий сервис поверх этой брони ходит своим internal-каналом
    (`/internal/servers/{id}/{release-for-service,service-status}`), а не
    пользовательскими эндпоинтами, поэтому в этот гейт он не упирается.
    """

    async def test_stranger_power_on_blocked(
        self, client, stranger_token, make_testing_server,
    ):
        srv = await make_testing_server(with_ipmi=True)
        resp = await client.post(
            f"{SRV}/{srv.id}/ipmi/power/on", headers=_hdr(stranger_token),
        )
        body = assert_error(resp, 409, "SERVER_RESERVED")
        # Держателя-человека нет — клиенту показываем имя сервиса.
        assert body["details"]["busy_user_id"] is None
        assert body["details"]["busy_service_name"] == "testing_service"
        assert body["details"]["busy_note"] == "smoke|1711rc42|6.6"

    async def test_operator_power_on_blocked_too(
        self, client, owner_token, make_testing_server,
    ):
        """Даже держатель прошлой человеческой брони не «владелец» этой."""
        srv = await make_testing_server(with_ipmi=True)
        resp = await client.post(
            f"{SRV}/{srv.id}/ipmi/power/on", headers=_hdr(owner_token),
        )
        assert_error(resp, 409, "SERVER_RESERVED")

    async def test_admin_power_off_allowed(
        self, client, admin_token, make_testing_server, captured_dispatch,
    ):
        srv = await make_testing_server(with_ipmi=True)
        resp = await client.post(
            f"{SRV}/{srv.id}/ipmi/power/off", headers=_hdr(admin_token),
        )
        assert resp.status_code == 202, resp.text

    async def test_stranger_delete_blocked(
        self, client, stranger_token, make_testing_server, stranger_can_delete,
    ):
        srv = await make_testing_server()
        resp = await client.delete(f"{SRV}/{srv.id}", headers=_hdr(stranger_token))
        assert_error(resp, 409, "SERVER_RESERVED")

    async def test_read_not_gated(
        self, client, stranger_token, make_testing_server,
    ):
        srv = await make_testing_server()
        resp = await client.get(f"{SRV}/{srv.id}", headers=_hdr(stranger_token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["busy_state"] == "testing"
        assert resp.json()["busy_service_name"] == "testing_service"


# ── Audit capture фикстура ────────────────────────────────────────────────────


@pytest.fixture
def captured_emits(monkeypatch):
    from tests._helpers import make_emit_capture

    return make_emit_capture(
        monkeypatch,
        "src.services.reservation.audit_service.emit",
        "src.services.server.audit_service.emit",
        "src.api.v1.endpoints.ipmi.audit_service.emit",
    )
