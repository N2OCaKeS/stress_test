"""Тесты конкретных task handlers (power/inventory/passwords).

HTTP-клиенты к server_service и iDRAC/SSH замочены — фокусируемся на:
* правильно ли handler составляет audit_action / target_type;
* передаёт ли credentials и server_id в правильные mock'и;
* как реагирует на ошибки фетча (mark_failed + audit failure);
* `_generate_password` даёт уникальные строки.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import update

from src.core.constants import TaskStatus
from src.core.exceptions import CredentialFetchError
from src.db.session import AsyncSessionLocal
from src.models import Task
from src.tasks import inventory, passwords, power


# ── helpers ─────────────────────────────────────────────────────────────────
#
# Для тестов, которые проверяют terminal FAILED после одной ошибки, нужно
# установить `max_attempts=1` — иначе runner делает mark_pending_for_retry
# и shedule re-kick. `_patch_no_retry` — заглушка для `_schedule_retry`,
# чтобы фоновые asyncio-task'и не утекали между тестами.


async def _make_task_max1(make_task, **kw) -> str:
    """make_task → UPDATE max_attempts=1 → terminal FAILED после одной ошибки."""
    tid = await make_task(**kw)
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Task).where(Task.id == tid).values(max_attempts=1)
        )
        await session.commit()
    return tid


def _patch_no_retry(monkeypatch) -> None:
    """Заглушка `_schedule_retry` на async-noop. Защита от leak'ов
    background-task'ов в pytest-asyncio."""
    from src.tasks import _runner

    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr(_runner, "_schedule_retry", noop)


# ── power.* ──────────────────────────────────────────────────────────────────


class _FakeRedfishClient:
    """In-memory stand-in для RedfishClient — calls track'аются."""

    def __init__(self, *, power_state: str = "On", raise_on: str | None = None):
        self._state = power_state
        self._raise_on = raise_on
        self.actions: list[str] = []
        self.aclose_called = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        await self.aclose()

    async def aclose(self):
        self.aclose_called = True

    async def get_power_state(self):
        if self._raise_on == "get_power_state":
            from src.clients.redfish import RedfishError
            raise RedfishError(500, "boom")
        return self._state

    async def power_action(self, action: str):
        self.actions.append(action)
        if self._raise_on == "power_action":
            from src.clients.redfish import RedfishError
            raise RedfishError(409, "conflict")
        # эмулируем эффект action на state
        if action in ("On",):
            self._state = "On"
        elif action in ("ForceOff", "GracefulShutdown"):
            self._state = "Off"
        elif action in ("GracefulRestart", "ForceRestart", "PowerCycle"):
            self._state = "On"


def _patch_power_redfish(monkeypatch, fake_client: _FakeRedfishClient):
    """Подменяем `_get_bmc` в power.py на async-фабрику, возвращающую fake.

    Точка подмены — async helper, потому что `clients.get_bmc_client`
    делает probe + fallback на ipmitool.
    """
    async def _factory(creds, *, prefer="redfish"):
        return fake_client
    monkeypatch.setattr("src.tasks.power._get_bmc", _factory)


class TestPowerOn:
    async def test_power_on_audit_action(self, make_task, fetch_task, captured_audit, monkeypatch):
        tid = await make_task(task_kind="power.on", target_server_id="srv_1")

        async def fake_fetch(server_id, target_department_id=None):
            return {"endpoint_url": "https://bmc", "username": "u", "password": "p"}

        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        fake = _FakeRedfishClient(power_state="Off")
        _patch_power_redfish(monkeypatch, fake)

        await power.power_on.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result == {"power_state": "on"}
        assert fake.actions == ["On"]
        assert fake.aclose_called
        assert captured_audit[0]["action"] == "server.power_on"
        assert captured_audit[0]["target_type"] == "server"

    async def test_power_off_audit_action(self, make_task, fetch_task, captured_audit, monkeypatch):
        tid = await make_task(task_kind="power.off", target_server_id="srv_1")
        async def fake_fetch(server_id, target_department_id=None):
            return {"endpoint_url": "https://bmc", "username": "u", "password": "p"}
        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        fake = _FakeRedfishClient(power_state="On")
        _patch_power_redfish(monkeypatch, fake)
        await power.power_off.original_func(tid)
        assert captured_audit[0]["action"] == "server.power_off"
        assert fake.actions == ["ForceOff"]

    async def test_power_off_ignores_payload_hints(self, make_task, fetch_task, captured_audit, monkeypatch):
        """Старые/неаккуратные клиенты могут прислать `graceful=true` в payload.
        Worker такие подсказки игнорирует — power.off всегда hard ForceOff.
        """
        tid = await make_task(
            task_kind="power.off",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "graceful": True},
        )
        async def fake_fetch(server_id, target_department_id=None):
            return {"endpoint_url": "https://bmc", "username": "u", "password": "p"}
        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        fake = _FakeRedfishClient(power_state="On")
        _patch_power_redfish(monkeypatch, fake)
        await power.power_off.original_func(tid)
        assert fake.actions == ["ForceOff"]

    async def test_power_reboot_audit_action(self, make_task, fetch_task, captured_audit, monkeypatch):
        tid = await make_task(task_kind="power.reboot", target_server_id="srv_1")
        async def fake_fetch(server_id, target_department_id=None):
            return {"endpoint_url": "https://bmc", "username": "u", "password": "p"}
        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        fake = _FakeRedfishClient(power_state="On")
        _patch_power_redfish(monkeypatch, fake)
        await power.power_reboot.original_func(tid)
        assert captured_audit[0]["action"] == "server.power_reboot"
        assert fake.actions == ["ForceRestart"]

    async def test_power_reboot_ignores_force(self, make_task, fetch_task, captured_audit, monkeypatch):
        tid = await make_task(
            task_kind="power.reboot",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "force": False},
        )
        async def fake_fetch(server_id, target_department_id=None):
            return {"endpoint_url": "https://bmc", "username": "u", "password": "p"}
        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        fake = _FakeRedfishClient(power_state="On")
        _patch_power_redfish(monkeypatch, fake)
        await power.power_reboot.original_func(tid)
        assert fake.actions == ["ForceRestart"]

    async def test_power_status_audit_action(self, make_task, fetch_task, captured_audit, monkeypatch):
        tid = await make_task(task_kind="power.status", target_server_id="srv_1")
        async def fake_fetch(server_id, target_department_id=None):
            return {"endpoint_url": "https://bmc", "username": "u", "password": "p"}
        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        fake = _FakeRedfishClient(power_state="On")
        _patch_power_redfish(monkeypatch, fake)
        await power.power_status.original_func(tid)
        assert captured_audit[0]["action"] == "server.power_status"
        t = await fetch_task(tid)
        # PoweringOn → powering_on (snake-case); plain On → on.
        assert t.result["power_state"] == "on"

    async def test_power_status_normalises_camelcase(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(task_kind="power.status", target_server_id="srv_1")
        async def fake_fetch(server_id, target_department_id=None):
            return {"endpoint_url": "https://bmc", "username": "u", "password": "p"}
        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        fake = _FakeRedfishClient(power_state="PoweringOn")
        _patch_power_redfish(monkeypatch, fake)
        await power.power_status.original_func(tid)
        t = await fetch_task(tid)
        assert t.result["power_state"] == "powering_on"

    async def test_credentials_fetch_failure_marks_failed(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        # Для terminal FAILED ставим max_attempts=1.
        tid = await _make_task_max1(make_task, task_kind="power.on", target_server_id="srv_x")
        _patch_no_retry(monkeypatch)

        async def boom(server_id, target_department_id=None):
            raise CredentialFetchError(
                error_code="IPMI_CREDENTIALS_UNAVAILABLE",
                message="404",
            )

        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials", boom,
        )
        await power.power_on.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert "CredentialFetchError" in t.last_error
        assert captured_audit[0]["status"] == "failure"

    async def test_bmc_unreachable_marks_failed(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        from src.clients.redfish import RedfishError

        tid = await _make_task_max1(make_task, task_kind="power.on", target_server_id="srv_x")
        _patch_no_retry(monkeypatch)

        async def fake_fetch(server_id, target_department_id=None):
            return {"endpoint_url": "https://bmc", "username": "u", "password": "p"}
        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )

        class _DeadClient:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def power_action(self, _): raise RedfishError(None, "transport boom")
            async def get_power_state(self): raise RedfishError(None, "transport boom")

        async def _dead_factory(creds, *, prefer="redfish"):
            return _DeadClient()
        monkeypatch.setattr("src.tasks.power._get_bmc", _dead_factory)
        await power.power_on.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert "BMC_UNREACHABLE" in t.last_error

    async def test_missing_server_id_keyerror_failure(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await _make_task_max1(make_task, task_kind="power.on", payload={"other": 1})
        _patch_no_retry(monkeypatch)

        async def fake_fetch(server_id, target_department_id=None): return {}
        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        _patch_power_redfish(monkeypatch, _FakeRedfishClient())

        await power.power_on.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert "KeyError" in t.last_error


# ── inventory.sync ───────────────────────────────────────────────────────────

class TestInventorySync:
    async def test_default_ssh_login_root_when_no_account(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(task_kind="inventory.sync", target_server_id="srv_1",
                              payload={"server_id": "srv_1"})

        captured_creds = []
        async def fake_collect(creds, server_id):
            captured_creds.append(creds)
            return {"os_version_name": "Astra"}

        async def fake_submit(server_id, facts, target_department_id=None):
            return {"accepted_at": "now"}

        monkeypatch.setattr("src.tasks.inventory.ssh_client.collect_inventory", fake_collect)
        monkeypatch.setattr(
            "src.tasks.inventory.server_service_client.submit_inventory_facts", fake_submit,
        )

        await inventory.inventory_sync.original_func(tid)
        # без account_id используется default login='root'; apply_session_hints
        # выставляет is_managed=False явно (контракт «всегда задаём флаг»).
        assert captured_creds == [{"login": "root", "is_managed": False}]
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["facts"]["os_version_name"] == "Astra"
        assert t.result["submit_status"] == "submitted"
        assert captured_audit[0]["action"] == "server.inventory_sync"

    async def test_account_id_fetches_credentials_from_server_service(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(
            task_kind="inventory.sync", target_server_id="srv_1",
            payload={"server_id": "srv_1", "account_id": "acc_2"},
        )

        fetch_calls = []
        async def fake_fetch(server_id, account_id, target_department_id=None):
            fetch_calls.append((server_id, account_id))
            return {"login": "ops", "password": "p"}

        captured_creds = []
        async def fake_collect(creds, server_id):
            captured_creds.append(creds)
            return {"os_version_name": "Astra"}

        async def fake_submit(server_id, facts, target_department_id=None):
            return {"accepted_at": "now"}

        monkeypatch.setattr(
            "src.tasks.inventory.server_service_client.fetch_account_password", fake_fetch,
        )
        monkeypatch.setattr("src.tasks.inventory.ssh_client.collect_inventory", fake_collect)
        monkeypatch.setattr(
            "src.tasks.inventory.server_service_client.submit_inventory_facts", fake_submit,
        )

        await inventory.inventory_sync.original_func(tid)
        assert fetch_calls == [("srv_1", "acc_2")]
        assert captured_creds[0]["login"] == "ops"

    async def test_custom_ssh_login_passed_through(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(
            task_kind="inventory.sync", target_server_id="srv_1",
            payload={"server_id": "srv_1", "ssh_login": "custom_user"},
        )
        captured_creds = []
        async def fake_collect(creds, server_id):
            captured_creds.append(creds)
            return {}
        async def fake_submit(server_id, facts, target_department_id=None):
            return {"accepted_at": "now"}
        monkeypatch.setattr("src.tasks.inventory.ssh_client.collect_inventory", fake_collect)
        monkeypatch.setattr(
            "src.tasks.inventory.server_service_client.submit_inventory_facts", fake_submit,
        )
        await inventory.inventory_sync.original_func(tid)
        # apply_session_hints всегда добавляет is_managed=False, если payload
        # не несёт is_managed=True.
        assert captured_creds == [{"login": "custom_user", "is_managed": False}]


# ── passwords.account_rotate_password ────────────────────────────────────────

class TestAccountRotate:
    async def test_full_flow_fetch_ssh_submit(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(
            task_kind="account.rotate_password",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "account_id": "acc_1"},
        )

        async def fake_fetch(server_id, account_id, target_department_id=None):
            return {"login": "ops", "password": "old"}

        ssh_calls = []
        async def fake_ssh_set(creds, server_id, login, new_pwd):
            ssh_calls.append((creds, server_id, login, new_pwd))
            return {}

        submit_calls = []
        async def fake_submit(server_id, account_id, new_password, target_department_id=None):
            submit_calls.append((server_id, account_id, new_password))
            return {"rotated_at": "2026-05-14T12:00:00Z"}

        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_account_password", fake_fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.ssh_client.set_account_password", fake_ssh_set,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_password", fake_submit,
        )

        await passwords.account_rotate_password.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["server_id"] == "srv_1"
        assert t.result["account_id"] == "acc_1"
        assert t.result["rotated_at"] == "2026-05-14T12:00:00Z"

        # SSH получил тот же новый пароль, что был отправлен submit
        assert len(ssh_calls) == 1
        new_pwd_via_ssh = ssh_calls[0][3]
        # policy: 20 chars, всё ascii — точное равенство, не >=.
        assert len(new_pwd_via_ssh) == 20
        assert submit_calls == [("srv_1", "acc_1", new_pwd_via_ssh)]

        # audit-event
        assert captured_audit[0]["action"] == "server_account.password_rotate"
        assert captured_audit[0]["target_type"] == "server_account"

    async def test_plaintext_password_not_in_audit_details(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(
            task_kind="account.rotate_password",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "account_id": "acc_1"},
        )
        captured_new = {}

        async def fake_fetch(server_id, account_id, target_department_id=None):
            return {"login": "ops", "password": "old"}

        async def fake_ssh_set(creds, server_id, login, new_pwd):
            captured_new["pwd"] = new_pwd
            return {}

        async def fake_submit(server_id, account_id, new_password, target_department_id=None):
            return {"rotated_at": "2026-05-14T12:00:00Z"}

        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_account_password", fake_fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.ssh_client.set_account_password", fake_ssh_set,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_password", fake_submit,
        )

        await passwords.account_rotate_password.original_func(tid)
        # Сгенерированный пароль НЕ должен утечь в audit details.
        new_pwd = captured_new["pwd"]
        audit_serialised = str(captured_audit)
        assert new_pwd not in audit_serialised


class _FakeRedfishForRotate:
    """Stand-in для RedfishClient в IPMI-rotate тестах."""

    def __init__(
        self,
        *,
        raise_on_rotate: bool = False,
        raise_on_verify: bool = False,
        power_state: str = "On",
    ):
        self._raise = raise_on_rotate
        self._raise_verify = raise_on_verify
        self._power_state = power_state
        self.rotate_calls: list[tuple[int, str]] = []
        self.get_power_state_calls: int = 0
        self.aclose_calls: int = 0

    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass

    async def rotate_user_password(self, user_id: int, new_password: str):
        if self._raise:
            from src.clients.redfish import RedfishError
            raise RedfishError(500, "patch failed")
        self.rotate_calls.append((user_id, new_password))

    async def get_power_state(self) -> str:
        # Используется verify-шагом ротации (`dispatch_get_power_state`).
        self.get_power_state_calls += 1
        if self._raise_verify:
            from src.clients.redfish import RedfishError
            raise RedfishError(401, "auth failed")
        return self._power_state

    async def aclose(self) -> None:
        self.aclose_calls += 1


class TestIpmiRotate:
    """Verify-then-submit ordering: BMC apply → verify (новый пароль) →
    submit ciphertext в server_service с `verified_at`.

    Если BMC apply упал — submit НЕ зовётся (storage остался со старым).
    Если verify упал — submit НЕ зовётся, task FAILED `BMC_VERIFY_AFTER_ROTATE_FAILED`
    (BMC мог принять или нет, но мы не подтвердили — лучше не коммитить
    в storage потенциально невалидный пароль).
    Если submit упал — задача FAILED (BMC уже на новом пароле, оператор
    пересинхронизирует вручную или через retry).
    """

    async def test_full_flow_bmc_then_verify_then_submit(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(
            task_kind="ipmi.rotate_password",
            target_server_id="srv_1",
            payload={"server_id": "srv_1"},
        )

        async def fake_fetch(server_id, target_department_id=None):
            return {
                "controller_id": "ipm_1",
                "kind": "idrac",
                "endpoint_url": "https://bmc.test",
                "username": "root",
                "password": "old",
            }

        submit_calls: list[dict] = []
        async def fake_submit(controller_id, new_password, rotated_at, target_department_id=None, verified_at=None):
            submit_calls.append({
                "controller_id": controller_id,
                "new_password": new_password,
                "rotated_at": rotated_at,
                "verified_at": verified_at,
            })
            return {"rotated_at": "2026-05-21T10:00:00Z"}

        # Один и тот же fake — apply и verify дёргают `get_power_state`
        # на новом клиенте, но `_get_bmc` мы мокаем, так что обе фазы
        # получают этот же объект.
        fake_rf = _FakeRedfishForRotate()
        bmc_creds_seen: list[str] = []

        async def _bmc_factory(creds, *, prefer="redfish"):
            bmc_creds_seen.append(creds["password"])
            return fake_rf

        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_ipmi_password",
            fake_submit,
        )
        monkeypatch.setattr("src.tasks.passwords._get_bmc", _bmc_factory)

        await passwords.ipmi_rotate_password.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        # BMC apply действительно был вызван.
        assert len(fake_rf.rotate_calls) == 1
        new_password = fake_rf.rotate_calls[0][1]
        assert fake_rf.rotate_calls[0][0] == 2  # default user_id=2
        # Verify был сделан с НОВЫМ паролем (вторая фабрика BMC получила
        # его в creds).
        assert bmc_creds_seen == ["old", new_password]
        assert fake_rf.get_power_state_calls == 1
        # Submit был вызван один раз, с `verified_at` и тем же паролем.
        assert len(submit_calls) == 1
        assert submit_calls[0]["controller_id"] == "ipm_1"
        assert submit_calls[0]["new_password"] == new_password
        assert submit_calls[0]["rotated_at"]
        assert submit_calls[0]["verified_at"]
        assert t.result["server_id"] == "srv_1"
        # IP/endpoint BMC наружу не уходит — в result только controller_id.
        assert t.result["controller_id"] == "ipm_1"
        assert "ipmi_host" not in t.result
        assert "endpoint_url" not in t.result
        assert t.result["user_id"] == 2
        assert t.result["controller_rotated"] is True
        assert t.result["password_rotated_at"] == "2026-05-21T10:00:00Z"
        assert captured_audit[0]["action"] == "ipmi_controller.password_rotate"
        assert captured_audit[0]["status"] == "success"

    async def test_bmc_apply_failure_skips_submit(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """BMC apply упал — submit_rotated_ipmi_password НЕ вызывается."""
        tid = await _make_task_max1(
            make_task,
            task_kind="ipmi.rotate_password",
            target_server_id="srv_1",
            payload={"server_id": "srv_1"},
        )
        _patch_no_retry(monkeypatch)

        async def fake_fetch(server_id, target_department_id=None):
            return {
                "controller_id": "ipm_1",
                "endpoint_url": "https://bmc", "username": "u", "password": "p",
            }

        submit_calls: list = []
        async def fake_submit(controller_id, new_password, rotated_at, target_department_id=None, verified_at=None):
            submit_calls.append(controller_id)
            return {"rotated_at": "x"}

        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_ipmi_password",
            fake_submit,
        )
        async def _bmc_raising(creds, *, prefer="redfish"):
            return _FakeRedfishForRotate(raise_on_rotate=True)
        monkeypatch.setattr("src.tasks.passwords._get_bmc", _bmc_raising)

        await passwords.ipmi_rotate_password.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert submit_calls == [], "submit must not be called if BMC apply failed"
        assert "BMC_ERROR" in t.last_error
        assert captured_audit[0]["status"] == "failure"

    async def test_verify_failure_skips_submit(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """BMC apply прошёл, но verify под новым паролем упал —
        submit НЕ вызывается, task FAILED с `BMC_VERIFY_AFTER_ROTATE_FAILED`."""
        tid = await _make_task_max1(
            make_task,
            task_kind="ipmi.rotate_password",
            target_server_id="srv_1",
            payload={"server_id": "srv_1"},
        )
        _patch_no_retry(monkeypatch)

        async def fake_fetch(server_id, target_department_id=None):
            return {
                "controller_id": "ipm_1",
                "endpoint_url": "https://bmc", "username": "u", "password": "p",
            }

        submit_calls: list = []
        async def fake_submit(controller_id, new_password, rotated_at, target_department_id=None, verified_at=None):
            submit_calls.append(controller_id)
            return {"rotated_at": "x"}

        # `_get_bmc` зовётся: apply, verify (1st), verify-retry (2nd, после
        # asyncio.sleep(1) для NTP-drift tolerance window). Оба verify-shot'а
        # моделируем как фейлящие — оба попытки → final failure.
        bmc_seq: list[_FakeRedfishForRotate] = [
            _FakeRedfishForRotate(),  # apply
            _FakeRedfishForRotate(raise_on_verify=True),  # verify attempt 1
            _FakeRedfishForRotate(raise_on_verify=True),  # verify attempt 2 (retry)
        ]
        idx = {"n": 0}
        async def _bmc_factory(creds, *, prefer="redfish"):
            cli = bmc_seq[idx["n"]]
            idx["n"] += 1
            return cli

        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_ipmi_password",
            fake_submit,
        )
        monkeypatch.setattr("src.tasks.passwords._get_bmc", _bmc_factory)

        await passwords.ipmi_rotate_password.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert submit_calls == [], "submit must not be called if verify failed"
        assert "BMC_VERIFY_AFTER_ROTATE_FAILED" in t.last_error
        assert captured_audit[0]["status"] == "failure"
        # Apply прошёл (на первом клиенте), оба verify-shot'а запустились.
        assert len(bmc_seq[0].rotate_calls) == 1
        assert bmc_seq[1].get_power_state_calls == 1
        assert bmc_seq[2].get_power_state_calls == 1

    async def test_submit_failure_after_verify_marks_failed(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """BMC apply + verify прошли, submit упал — task FAILED."""
        tid = await _make_task_max1(
            make_task,
            task_kind="ipmi.rotate_password",
            target_server_id="srv_1",
            payload={"server_id": "srv_1"},
        )
        _patch_no_retry(monkeypatch)

        async def fake_fetch(server_id, target_department_id=None):
            return {
                "controller_id": "ipm_1",
                "endpoint_url": "https://bmc", "username": "u", "password": "p",
            }

        async def boom_submit(controller_id, new_password, rotated_at, target_department_id=None, verified_at=None):
            raise CredentialFetchError(
                error_code="IPMI_ROTATE_REJECTED", message="404",
            )

        fake_rf = _FakeRedfishForRotate()
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_ipmi_password",
            boom_submit,
        )
        async def _bmc_factory(creds, *, prefer="redfish"):
            return fake_rf
        monkeypatch.setattr("src.tasks.passwords._get_bmc", _bmc_factory)

        await passwords.ipmi_rotate_password.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        # BMC apply и verify прошли.
        assert len(fake_rf.rotate_calls) == 1
        assert fake_rf.get_power_state_calls == 1
        assert "CredentialFetchError" in t.last_error
        assert captured_audit[0]["status"] == "failure"

    async def test_user_id_override_from_payload(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(
            task_kind="ipmi.rotate_password",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "user_id": 3},
        )
        async def fake_fetch(server_id, target_department_id=None):
            return {
                "controller_id": "ipm_1",
                "endpoint_url": "https://bmc", "username": "u", "password": "p",
            }
        async def fake_submit(controller_id, new_password, rotated_at, target_department_id=None, verified_at=None):
            return {"rotated_at": "now"}

        fake_rf = _FakeRedfishForRotate()
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_ipmi_password",
            fake_submit,
        )
        async def _bmc_factory(creds, *, prefer="redfish"):
            return fake_rf
        monkeypatch.setattr("src.tasks.passwords._get_bmc", _bmc_factory)

        await passwords.ipmi_rotate_password.original_func(tid)
        # user_id из payload (3) — не default 2
        assert fake_rf.rotate_calls[0][0] == 3

    async def test_plaintext_password_not_in_audit(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(
            task_kind="ipmi.rotate_password",
            target_server_id="srv_1",
            payload={"server_id": "srv_1"},
        )
        async def fake_fetch(server_id, target_department_id=None):
            return {
                "controller_id": "ipm_1",
                "endpoint_url": "https://bmc", "username": "u", "password": "p",
            }

        captured_pwd = {}
        async def fake_submit(controller_id, new_password, rotated_at, target_department_id=None, verified_at=None):
            captured_pwd["pwd"] = new_password
            return {"rotated_at": "x"}

        fake_rf = _FakeRedfishForRotate()
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_ipmi_password",
            fake_submit,
        )
        async def _bmc_factory(creds, *, prefer="redfish"):
            return fake_rf
        monkeypatch.setattr("src.tasks.passwords._get_bmc", _bmc_factory)
        await passwords.ipmi_rotate_password.original_func(tid)
        # plaintext не утёк в audit details
        assert captured_pwd["pwd"] not in str(captured_audit)


class TestGeneratePassword:
    def test_generates_unique_strings(self):
        pws = {passwords._generate_password() for _ in range(200)}
        # Коллизии астрономически маловероятны для 20-symbol ascii(94) пула.
        assert len(pws) == 200

    def test_length_matches_policy(self):
        for _ in range(50):
            assert len(passwords._generate_password()) == 20

    @pytest.mark.parametrize("_n", range(1000))
    def test_policy_classes_present(self, _n):
        """Sample N=1000: каждый пароль удовлетворяет policy.

        Регекс с lookahead-ами — по одному классу:
        lowercase, uppercase, digit, `[!@#$%^&*]`.
        """
        pwd = passwords._generate_password()
        assert re.search(r"[a-z]", pwd), f"no lowercase in {pwd!r}"
        assert re.search(r"[A-Z]", pwd), f"no uppercase in {pwd!r}"
        assert re.search(r"\d", pwd), f"no digit in {pwd!r}"
        assert re.search(r"[!@#$%^&*]", pwd), f"no punct in {pwd!r}"
        # И никаких символов вне разрешённого alphabet'а.
        assert re.fullmatch(r"[A-Za-z0-9!@#$%^&*]{20}", pwd), (
            f"password {pwd!r} contains chars outside the allowed alphabet"
        )


