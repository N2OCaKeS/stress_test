"""Тесты worker-таски `server.prepare_for_test`.

Покрытие:

* happy path на оба значения `mode` — провижн тестового пользователя, смена
  ядра, смена режима безопасности Astra (`astra-modeswitch` + для Смоленска
  МРД/МКЦ), ребут, success-callback без предупреждений;
* discrepancy до смены — `astra-modeswitch get` вернул не тот уровень, что
  запрошен: `set` всё равно применяется, но success-callback уносит
  `error` как non-fatal warning;
* провал на новых шагах: grub menuentry не найден (`kernel_change`),
  `astra-modeswitch set` не подтвердился повторным `get` (`mode_switch`) —
  в обоих случаях `failed_step` в callback'е и task FAILED.

Все SSH-вызовы идут через `_FakeSshClient` — ordered script, без реального
asyncssh/сети. Redis-stash подменяется совместимым по формату fake-клиентом,
чтобы `_read_test_creds` реально прогонял envelope-decrypt.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import update

from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.models import Task
from src.services.redis_stash_crypto import (
    aad_for_redis_stash,
    encrypt_stash,
    stash_id_from_key,
)
from src.tasks import prepare_for_test as pft_task

CREDS_KEY = "dbos:dispatch_creds:pft_test_1"


async def _set_single_attempt(tid: str) -> None:
    """max_attempts=1 → провал в _impl сразу даёт FAILED, без retry-петли."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Task).where(Task.id == tid).values(max_attempts=1)
        )
        await session.commit()


def _test_creds_stash(**overrides) -> dict:
    payload = {
        "test_username": "u",
        "test_password": "Str0ng-Pass-24chars-AB",
        "test_ssh_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc test",
    }
    payload.update(overrides)
    return payload


class _FakeRedisClient:
    """`.get`/`.delete` над одним ключом — envelope реально шифруется/дешифруется."""

    def __init__(self, key: str, stash: dict):
        self._key = key
        self._envelope = encrypt_stash(
            json.dumps(stash), aad=aad_for_redis_stash(stash_id_from_key(key)),
        )
        self.deleted: list[str] = []

    async def get(self, key: str):
        if key != self._key:
            return None
        return self._envelope.encode("utf-8")

    async def delete(self, key: str) -> None:
        self.deleted.append(key)

    async def eval(self, *_args, **_kwargs):
        # `redis_pool.get_redis()` — тот же singleton, которым пользуется
        # audit_publisher_breaker; на failure-путях таска он тоже дёргает
        # eval (circuit-check перед публикацией outbox-строки). Отдаём
        # нейтральный "closed" — не тестируем breaker здесь, важно не дать
        # AttributeError перебить настоящий SshError таска.
        return ("closed", "0")

    async def aclose(self) -> None:
        return None


class _FakeSshClient:
    """Ordered script: каждый `.run()` потребляет следующий (pattern, response).

    `pattern` сверяется подстрокой с командой — падает `AssertionError`
    при рассинхроне, а не молча отдаёт неверный ответ.
    """

    def __init__(self, script: list[tuple[str, tuple[int, str, str]]]):
        self._script = list(script)
        self.commands: list[str] = []
        self.create_user_calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def create_user(
        self, login, *, new_password=None, public_key=None,
        has_sudo=False, force_replace=False, **_kwargs,
    ) -> None:
        self.create_user_calls.append({
            "login": login,
            "new_password": new_password,
            "public_key": public_key,
            "has_sudo": has_sudo,
            "force_replace": force_replace,
        })

    async def run(self, command: str, *, sudo: bool = False, stdin_payload=None):
        self.commands.append(command)
        if not self._script:
            raise AssertionError(f"unexpected ssh.run call, no script left: {command!r}")
        pattern, response = self._script.pop(0)
        assert pattern in command, (
            f"expected next command to match {pattern!r}, got {command!r}"
        )
        return response


_KERNEL_STEPS = [
    ("dpkg -s linux-image", (0, "", "")),
    ("dpkg -s linux-headers", (0, "", "")),
    ("dpkg -s linux-astra-modules", (0, "", "")),
    ("grep menuentry_id", (0, "gnulinux-5.10.0-advanced-uuid-1", "")),
    ("GRUB_DEFAULT=", (0, "", "")),
    ("update-grub", (0, "", "")),
]


def _payload(*, mode: str = "orel", server_id: str = "srv_pft_1") -> dict:
    return {
        "server_id": server_id,
        "prepare_request_id": "prep_test_1",
        "target_department_id": "dep_a",
        "host": "10.0.0.9",
        "ssh_port": 22,
        "is_managed": True,
        "management_user": "dbos",
        "kernel": "5.10.0",
        "mode": mode,
        "test_creds_key": CREDS_KEY,
    }


@pytest.fixture(autouse=True)
def stub_collaborators(monkeypatch):
    """No-op management creds fetch + stub reachability/verify — обе уже
    покрыты отдельными тестами `acs_snapshots`, здесь не переисследуем их."""

    async def _attach(credentials, server_id):  # noqa: ARG001
        credentials["management_private_key"] = "unused-in-fake"
        return credentials

    monkeypatch.setattr(pft_task.ssh_client, "attach_management_creds", _attach)

    async def _await_ok(*_args, **_kwargs) -> bool:
        return True

    async def _verify_ok(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(pft_task, "_await_reachability", _await_ok)
    monkeypatch.setattr(pft_task, "verify_system_running", _verify_ok)


@pytest.fixture
def captured_submit(monkeypatch):
    calls: list[dict] = []

    async def _submit(server_id, prepare_request_id, succeeded, target_department_id=None,
                       *, failed_step=None, error_message=None):
        calls.append({
            "server_id": server_id,
            "prepare_request_id": prepare_request_id,
            "succeeded": succeeded,
            "target_department_id": target_department_id,
            "failed_step": failed_step,
            "error_message": error_message,
        })
        return {"ok": True}

    monkeypatch.setattr(
        pft_task.server_service_client, "submit_prepare_for_test_result", _submit,
    )
    return calls


def _patch_ssh(monkeypatch, fake: _FakeSshClient) -> None:
    monkeypatch.setattr(pft_task.ssh_client, "build_session", lambda creds, sid: fake)


def _patch_redis(monkeypatch, fake: _FakeRedisClient) -> None:
    monkeypatch.setattr(pft_task.redis_pool, "get_redis", lambda: fake)


class TestHappyPath:
    async def test_orel_already_orel_skips_set(
        self, monkeypatch, make_task, fetch_task, captured_submit,
    ):
        """Бокс уже на 0 (orel) — `set` не вызывается вовсе."""
        fake = _FakeSshClient([
            *_KERNEL_STEPS,
            ("astra-modeswitch get", (0, "0", "")),
            ("reboot", (0, "", "")),
        ])
        _patch_ssh(monkeypatch, fake)
        _patch_redis(monkeypatch, _FakeRedisClient(CREDS_KEY, _test_creds_stash()))

        tid = await make_task(
            task_kind="server.prepare_for_test",
            target_server_id="srv_pft_1",
            payload=_payload(mode="orel"),
        )
        await pft_task.server_prepare_for_test.original_func(tid)

        task = await fetch_task(tid)
        assert task.status == TaskStatus.SUCCEEDED
        assert fake.create_user_calls[0]["login"] == "u"
        assert fake.create_user_calls[0]["force_replace"] is True
        assert len(captured_submit) == 1
        assert captured_submit[0]["succeeded"] is True
        assert captured_submit[0]["error_message"] is None
        # ровно один `get`, ни одного `set`/mac-control/mic-control
        assert sum("astra-modeswitch" in c for c in fake.commands) == 1

    async def test_smolensk_switches_and_enables_controls(
        self, monkeypatch, make_task, fetch_task, captured_submit,
    ):
        """orel → smolensk: set 2 + mac-control + mic-control, потом ребут."""
        fake = _FakeSshClient([
            *_KERNEL_STEPS,
            ("astra-modeswitch get", (0, "0", "")),
            ("astra-modeswitch set 2", (0, "", "")),
            ("astra-mac-control enable", (0, "", "")),
            ("astra-mic-control enable", (0, "", "")),
            ("astra-modeswitch get", (0, "2", "")),
            ("reboot", (0, "", "")),
        ])
        _patch_ssh(monkeypatch, fake)
        _patch_redis(monkeypatch, _FakeRedisClient(CREDS_KEY, _test_creds_stash()))

        tid = await make_task(
            task_kind="server.prepare_for_test",
            target_server_id="srv_pft_2",
            payload=_payload(mode="smolensk", server_id="srv_pft_2"),
        )
        await pft_task.server_prepare_for_test.original_func(tid)

        task = await fetch_task(tid)
        assert task.status == TaskStatus.SUCCEEDED
        assert captured_submit[0]["succeeded"] is True
        # before(orel) != smolensk → non-fatal warning уходит в error_message
        assert captured_submit[0]["error_message"] is not None
        assert "orel" in captured_submit[0]["error_message"]
        assert "smolensk" in captured_submit[0]["error_message"]

    async def test_smolensk_already_smolensk_skips_set_but_reenables_controls(
        self, monkeypatch, make_task, fetch_task, captured_submit,
    ):
        """Бокс уже на 2 — `set` не вызывается, но mac/mic-control всё равно
        прогоняются (это отдельные флаги, `get` их состояние не видит)."""
        fake = _FakeSshClient([
            *_KERNEL_STEPS,
            ("astra-modeswitch get", (0, "2", "")),
            ("astra-mac-control enable", (0, "", "")),
            ("astra-mic-control enable", (0, "", "")),
            ("reboot", (0, "", "")),
        ])
        _patch_ssh(monkeypatch, fake)
        _patch_redis(monkeypatch, _FakeRedisClient(CREDS_KEY, _test_creds_stash()))

        tid = await make_task(
            task_kind="server.prepare_for_test",
            target_server_id="srv_pft_3",
            payload=_payload(mode="smolensk", server_id="srv_pft_3"),
        )
        await pft_task.server_prepare_for_test.original_func(tid)

        task = await fetch_task(tid)
        assert task.status == TaskStatus.SUCCEEDED
        assert captured_submit[0]["error_message"] is None
        assert sum("astra-modeswitch set" in c for c in fake.commands) == 0


class TestFailures:
    async def test_kernel_menuentry_not_found(
        self, monkeypatch, make_task, fetch_task, captured_submit,
    ):
        fake = _FakeSshClient([
            ("dpkg -s linux-image", (0, "", "")),
            ("dpkg -s linux-headers", (0, "", "")),
            ("dpkg -s linux-astra-modules", (0, "", "")),
            ("grep menuentry_id", (0, "", "")),  # пусто — ядра нет в grub.cfg
        ])
        _patch_ssh(monkeypatch, fake)
        _patch_redis(monkeypatch, _FakeRedisClient(CREDS_KEY, _test_creds_stash()))

        tid = await make_task(
            task_kind="server.prepare_for_test",
            target_server_id="srv_pft_4",
            payload=_payload(mode="orel", server_id="srv_pft_4"),
        )
        await _set_single_attempt(tid)
        await pft_task.server_prepare_for_test.original_func(tid)

        task = await fetch_task(tid)
        assert task.status == TaskStatus.FAILED
        assert task.last_error and "PREPARE_FOR_TEST_KERNEL_NOT_IN_GRUB" in task.last_error
        assert len(captured_submit) == 1
        assert captured_submit[0]["succeeded"] is False
        assert captured_submit[0]["failed_step"] == "kernel_change"

    async def test_mode_switch_not_confirmed_after_set(
        self, monkeypatch, make_task, fetch_task, captured_submit,
    ):
        """`set 2` возвращает rc=0, но повторный `get` всё ещё видит 0 —
        команда не применилась, `failed_step=mode_switch`, до ребута не идём."""
        fake = _FakeSshClient([
            *_KERNEL_STEPS,
            ("astra-modeswitch get", (0, "0", "")),
            ("astra-modeswitch set 2", (0, "", "")),
            ("astra-mac-control enable", (0, "", "")),
            ("astra-mic-control enable", (0, "", "")),
            ("astra-modeswitch get", (0, "0", "")),  # не применилось
        ])
        _patch_ssh(monkeypatch, fake)
        _patch_redis(monkeypatch, _FakeRedisClient(CREDS_KEY, _test_creds_stash()))

        tid = await make_task(
            task_kind="server.prepare_for_test",
            target_server_id="srv_pft_5",
            payload=_payload(mode="smolensk", server_id="srv_pft_5"),
        )
        await _set_single_attempt(tid)
        await pft_task.server_prepare_for_test.original_func(tid)

        task = await fetch_task(tid)
        assert task.status == TaskStatus.FAILED
        assert task.last_error and "PREPARE_FOR_TEST_MODE_SWITCH_FAILED" in task.last_error
        assert captured_submit[0]["succeeded"] is False
        assert captured_submit[0]["failed_step"] == "mode_switch"
        # reboot не должен был вызываться — исключение прервало пайплайн раньше
        assert "reboot" not in fake.commands

    async def test_mode_switch_set_command_fails(
        self, monkeypatch, make_task, fetch_task, captured_submit,
    ):
        fake = _FakeSshClient([
            *_KERNEL_STEPS,
            ("astra-modeswitch get", (0, "0", "")),
            ("astra-modeswitch set 2", (1, "", "permission denied")),
        ])
        _patch_ssh(monkeypatch, fake)
        _patch_redis(monkeypatch, _FakeRedisClient(CREDS_KEY, _test_creds_stash()))

        tid = await make_task(
            task_kind="server.prepare_for_test",
            target_server_id="srv_pft_6",
            payload=_payload(mode="smolensk", server_id="srv_pft_6"),
        )
        await _set_single_attempt(tid)
        await pft_task.server_prepare_for_test.original_func(tid)

        task = await fetch_task(tid)
        assert task.status == TaskStatus.FAILED
        assert task.last_error and "PREPARE_FOR_TEST_MODE_SWITCH_FAILED" in task.last_error
        assert captured_submit[0]["failed_step"] == "mode_switch"
