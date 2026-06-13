"""Тесты one-shot guard'а против двойного reboot'а при durable-retry.

`power.reboot` деструктивен в отличие от on/off: повторный reset на сервер,
который ещё грузит ОС после первого reboot'а, может повредить FS. Guard
ставит маркер в Redis сразу после того, как BMC принял reset; retry, поднявший
`_impl` заново после фейла на get_power_state/commit, видит маркер и не выдаёт
второй reset.

Покрываем:

1. reboot уже issued (маркер в Redis есть) → retry НЕ шлёт второй reset,
   идёт сразу к verify и завершается success'ом.
2. reboot НЕ issued, но fail случился на verify ПОСЛЕ apply → маркер
   выставлен (значит следующий retry пропустит reset).
3. fail на САМОМ reset (apply) → маркер НЕ выставлен → retry имеет право
   повторить reset.
4. happy-path: первый reboot проходит, маркер ставится и снимается после
   успеха.
5. roundtrip самих helper'ов маркера.
"""

from __future__ import annotations

import pytest

from src.core.constants import TaskStatus
from src.tasks import power


pytestmark = pytest.mark.asyncio


def _creds():
    return {
        "endpoint_url": "https://bmc.test",
        "username": "root",
        "password": "p",
        "controller_id": "ipm_rb",
    }


class _RebootBmc:
    """`RedfishClient`-stand-in для reboot-сценариев.

    `power_action` собирает выданные reset'ы (по ним считаем, сколько раз
    реально ребутнули). `get_power_state` опционально падает заданное число
    раз — эмулирует transient-fail после успешного apply.
    """

    def __init__(self, *, get_state_fails=0, power_action_fails=0):
        self.power_actions: list[str] = []
        self.get_state_calls = 0
        self._get_state_fails = get_state_fails
        self._power_action_fails = power_action_fails
        self._power_action_calls = 0

    async def power_action(self, action: str) -> None:
        self._power_action_calls += 1
        if self._power_action_calls <= self._power_action_fails:
            from src.clients.redfish import RedfishError
            raise RedfishError(None, "bmc down", None)
        self.power_actions.append(action)

    async def get_power_state(self) -> str:
        self.get_state_calls += 1
        if self.get_state_calls <= self._get_state_fails:
            from src.clients.redfish import RedfishError
            raise RedfishError(None, "transient after reset", None)
        return "On"

    async def aclose(self) -> None:
        return None


def _patch_bmc(monkeypatch, bmc):
    async def fake_fetch(server_id, target_department_id=None):
        return _creds()

    async def _factory(creds, *, prefer="redfish"):
        return bmc

    monkeypatch.setattr(
        "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
    )
    monkeypatch.setattr("src.tasks.power._get_bmc", _factory)


class TestRebootMarkerHelpers:
    async def test_marker_roundtrip(self):
        tid = "tsk_reboot_marker_roundtrip"
        await power._delete_reboot_issued(tid)
        assert await power._read_reboot_issued(tid) is False

        await power._store_reboot_issued(tid)
        assert await power._read_reboot_issued(tid) is True

        await power._delete_reboot_issued(tid)
        assert await power._read_reboot_issued(tid) is False


class TestRebootGuardSkipsDoubleIssue:
    async def test_retry_with_marker_does_not_reissue_reset(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Маркер уже стоит (как после успешного apply в прошлой попытке) —
        retry НЕ должен слать второй reset, но обязан довести verify до
        success'а."""
        tid = await make_task(
            task_kind="power.reboot",
            target_server_id="srv_rb",
            payload={"server_id": "srv_rb"},
        )
        # Эмулируем «попытка 1 уже выдала reset на BMC».
        await power._store_reboot_issued(tid)

        bmc = _RebootBmc()
        _patch_bmc(monkeypatch, bmc)

        await power.power_reboot.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result == {"power_state": "on", "rebooted": True}
        assert bmc.power_actions == [], (
            "при наличии маркера reset не должен выдаваться повторно"
        )
        assert bmc.get_state_calls == 1, "verify всё равно должен сходить за состоянием"
        # После успеха маркер снят.
        assert await power._read_reboot_issued(tid) is False


class TestRebootMarkerSetAfterApply:
    async def test_marker_persists_when_verify_fails(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """reset прошёл, get_power_state упал → задача FAILED, но маркер
        должен пережить фейл, чтобы retry не ребутнул второй раз."""
        from tests.test_task_handlers import _make_task_max1, _patch_no_retry

        tid = await _make_task_max1(
            make_task, task_kind="power.reboot", target_server_id="srv_v",
        )
        _patch_no_retry(monkeypatch)

        bmc = _RebootBmc(get_state_fails=99)  # verify падает всегда
        _patch_bmc(monkeypatch, bmc)

        await power.power_reboot.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        # reset был выдан ровно один раз.
        assert bmc.power_actions == ["GracefulRestart"]
        # Маркер выставлен — следующий retry пропустит reset.
        assert await power._read_reboot_issued(tid) is True, (
            "после успешного apply маркер должен жить, иначе retry ребутнет снова"
        )

        # cleanup, чтобы не протёк в соседний тест с тем же task_id.
        await power._delete_reboot_issued(tid)


class TestRebootApplyFailLeavesNoMarker:
    async def test_marker_absent_when_reset_itself_fails(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Сам reset (apply) упал — маркер НЕ должен выставляться, retry
        обязан повторить reset."""
        from tests.test_task_handlers import _make_task_max1, _patch_no_retry

        tid = await _make_task_max1(
            make_task, task_kind="power.reboot", target_server_id="srv_a",
        )
        _patch_no_retry(monkeypatch)

        bmc = _RebootBmc(power_action_fails=99)  # reset падает всегда
        _patch_bmc(monkeypatch, bmc)

        await power.power_reboot.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert "BMC_UNREACHABLE" in t.last_error
        assert bmc.power_actions == [], "reset не дошёл до BMC"
        assert await power._read_reboot_issued(tid) is False, (
            "при фейле самого reset'а маркер ставить нельзя — retry должен "
            "повторить reset"
        )

    async def test_retry_after_apply_fail_reissues_reset(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Полный цикл: попытка 1 падает на apply (маркера нет), попытка 2
        стартует с того же task_id и обязана снова выдать reset."""
        tid = await make_task(
            task_kind="power.reboot",
            target_server_id="srv_reissue",
            payload={"server_id": "srv_reissue"},
        )
        await power._delete_reboot_issued(tid)

        from src.tasks import _runner

        async def noop(*a, **kw):
            pass

        monkeypatch.setattr(_runner, "_schedule_retry", noop)

        # Первая попытка: reset падает.
        bmc1 = _RebootBmc(power_action_fails=99)
        _patch_bmc(monkeypatch, bmc1)
        await power.power_reboot.original_func(tid)
        assert await power._read_reboot_issued(tid) is False

        # Вернём task в QUEUED, чтобы прогнать второй заход.
        from sqlalchemy import update
        from src.db.session import AsyncSessionLocal
        from src.models import Task

        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Task)
                .where(Task.id == tid)
                .values(status=TaskStatus.QUEUED, attempt=0, max_attempts=3)
            )
            await session.commit()

        # Вторая попытка: reset теперь проходит.
        bmc2 = _RebootBmc()
        _patch_bmc(monkeypatch, bmc2)
        await power.power_reboot.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert bmc2.power_actions == ["GracefulRestart"], (
            "после фейла apply retry обязан повторить reset"
        )
        assert await power._read_reboot_issued(tid) is False


class TestRebootHappyPath:
    async def test_first_reboot_issues_once_and_clears_marker(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Чистый happy-path: маркера нет → reset выдан один раз → verify ок
        → маркер снят, success."""
        tid = await make_task(
            task_kind="power.reboot",
            target_server_id="srv_happy",
            payload={"server_id": "srv_happy"},
        )
        await power._delete_reboot_issued(tid)

        bmc = _RebootBmc()
        _patch_bmc(monkeypatch, bmc)

        await power.power_reboot.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result == {"power_state": "on", "rebooted": True}
        assert bmc.power_actions == ["GracefulRestart"]
        assert await power._read_reboot_issued(tid) is False

    async def test_force_reboot_uses_force_restart(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """`force=true` в payload → ForceRestart, guard работает так же."""
        tid = await make_task(
            task_kind="power.reboot",
            target_server_id="srv_force",
            payload={"server_id": "srv_force", "force": True},
        )
        await power._delete_reboot_issued(tid)

        bmc = _RebootBmc()
        _patch_bmc(monkeypatch, bmc)

        await power.power_reboot.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert bmc.power_actions == ["ForceRestart"]
