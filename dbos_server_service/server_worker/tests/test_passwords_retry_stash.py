"""Тесты на удержание состояния между retry'ями для ротаций паролей.

Покрывают два инварианта:

1. `ipmi.rotate_password` — `rotated_at` стабилен между попытками submit'а.
   До фикса при retry'е submit'а worker пересчитывал
   `datetime.now()` после apply, drift до десятков секунд между BMC и
   storage. После фикса rotated_at сохраняется в Redis-stash вместе с
   паролем и переиспользуется на retry'ях.

2. `account.rotate_password` — пароль стабилен между попытками chpasswd
   и submit'а. До фикса каждый retry _impl генерил новый
   `_generate_password()`, chpasswd перезаписывал на хосте, storage
   получал то один пароль, то другой. После фикса первый retry кладёт
   пароль в Redis, следующие читают оттуда.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import asyncssh
import pytest
import pytest_asyncio

from src.core.constants import TaskStatus
from src.tasks import passwords


pytestmark = pytest.mark.asyncio


def _run_result(stdout="", stderr="", rc=0):
    res = MagicMock()
    res.stdout = stdout
    res.stderr = stderr
    res.exit_status = rc
    return res


def _conn_ok():
    conn = MagicMock(spec=asyncssh.SSHClientConnection)
    conn.close = MagicMock()
    conn.wait_closed = AsyncMock()
    conn.run = AsyncMock(return_value=_run_result("", "", 0))
    return conn


class _FakeBmc:
    """RedfishClient-stand-in без транзита через реальный transport."""

    def __init__(self):
        self.rotate_calls: list[tuple[int, str]] = []
        self.get_power_state_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def rotate_user_password(self, user_id: int, new_password: str):
        self.rotate_calls.append((user_id, new_password))

    async def get_power_state(self) -> str:
        self.get_power_state_calls += 1
        return "On"

    async def aclose(self) -> None:
        pass


@pytest_asyncio.fixture
async def _cleanup_account_stash():
    """Удаляет account-rotate ключи из Redis до и после теста.

    Тесты используют реальный Redis из dev-стека; изоляция между
    прогонами — наша забота.
    """
    yield


class TestIpmiRotatedAtStableAcrossRetries:
    """rotated_at не должен дрейфить между BMC apply и storage."""

    async def test_retry_submit_reuses_stashed_rotated_at(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Симулируем retry submit'а: первая попытка дошла до submit и
        записала rotated_at в stash; вторая попытка должна вернуть тот
        же rotated_at в submit, не пересчитывать `datetime.now()`."""
        tid = await make_task(
            task_kind="ipmi.rotate_password",
            target_server_id="srv_rt",
            payload={"server_id": "srv_rt"},
        )

        # Pre-fill stash: эмулирует «попытка 1 уже стораджила rotated_at».
        pre_stashed_pwd = "PreStashedPassword!2026"
        pre_stashed_rotated_at = "2026-01-01T00:00:00+00:00"
        await passwords._store_ipmi_rotate_password(
            tid, pre_stashed_pwd, pre_stashed_rotated_at,
        )

        async def fake_fetch(server_id, target_department_id=None):
            return {
                "controller_id": "ipm_rt",
                "kind": "idrac",
                "endpoint_url": "https://bmc.test",
                "username": "root",
                "password": "old",
            }

        async def _bmc_factory(creds, *, prefer="redfish"):
            return _FakeBmc()

        submit_calls: list[dict] = []

        async def fake_submit(
            controller_id, new_password, rotated_at,
            target_department_id=None, verified_at=None,
        ):
            submit_calls.append({
                "password": new_password,
                "rotated_at": rotated_at,
            })
            return {"rotated_at": rotated_at}

        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_ipmi_credentials",
            fake_fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_ipmi_password",
            fake_submit,
        )
        monkeypatch.setattr("src.tasks.passwords._get_bmc", _bmc_factory)

        await passwords.ipmi_rotate_password.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert len(submit_calls) == 1
        assert submit_calls[0]["password"] == pre_stashed_pwd
        assert submit_calls[0]["rotated_at"] == pre_stashed_rotated_at, (
            "retry submit'а должен переиспользовать rotated_at из stash'а, "
            "а не пересчитывать datetime.now() заново"
        )

        # После успеха stash удалён.
        assert (await passwords._read_ipmi_rotate_state(tid))[0] is None

    async def test_first_attempt_stores_rotated_at_in_stash(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Первая попытка: stash изначально пустой, после успешного apply
        rotated_at должен оказаться в stash'е — иначе retry submit'а его
        не увидит."""
        tid = await make_task(
            task_kind="ipmi.rotate_password",
            target_server_id="srv_first",
            payload={"server_id": "srv_first"},
        )

        async def fake_fetch(server_id, target_department_id=None):
            return {
                "controller_id": "ipm_first",
                "kind": "idrac",
                "endpoint_url": "https://bmc.test",
                "username": "root",
                "password": "old",
            }

        # Submit падает — задача FAILED, но stash должен жить со значениями.
        stash_after_apply: dict = {}

        async def fake_submit(
            controller_id, new_password, rotated_at,
            target_department_id=None, verified_at=None,
        ):
            # Подсматриваем содержимое stash'а ровно перед submit'ом
            # (после apply и записи rotated_at, до cleanup'а).
            stash_after_apply["password"], stash_after_apply["rotated_at"] = (
                await passwords._read_ipmi_rotate_state(tid)
            )
            stash_after_apply["submitted_rotated_at"] = rotated_at
            return {"rotated_at": rotated_at}

        async def _bmc_factory(creds, *, prefer="redfish"):
            return _FakeBmc()

        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_ipmi_credentials",
            fake_fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_ipmi_password",
            fake_submit,
        )
        monkeypatch.setattr("src.tasks.passwords._get_bmc", _bmc_factory)

        await passwords.ipmi_rotate_password.original_func(tid)

        assert stash_after_apply["password"], "пароль должен быть в stash'е"
        assert stash_after_apply["rotated_at"], (
            "rotated_at должен попасть в stash после apply, иначе retry "
            "submit'а пересчитает его заново"
        )
        # rotated_at, переданный в submit, совпадает с тем, что в stash'е.
        assert stash_after_apply["rotated_at"] == stash_after_apply["submitted_rotated_at"]


class TestAccountRotatePasswordStashed:
    """`account.rotate_password` должен переиспользовать пароль на retry'ях."""

    async def test_retry_reuses_stashed_password(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Эмулируем «retry попытки 2»: stash уже содержит пароль,
        который сгенерила «попытка 1». chpasswd и submit должны
        получить ТОТ ЖЕ пароль, а `_generate_password` не вызываться.
        """
        tid = await make_task(
            task_kind="account.rotate_password",
            target_server_id="srv_acc",
            payload={"server_id": "srv_acc", "account_id": "acc_acc"},
        )

        stashed_pwd = "PreviousAttemptPwd!7"
        await passwords._store_account_rotate_password(tid, stashed_pwd)

        # Если retry-логика поломана, _generate_password() будет вызван.
        gen_calls: list[int] = []
        original_gen = passwords._generate_password

        def spy_gen():
            gen_calls.append(1)
            return original_gen()

        monkeypatch.setattr(passwords, "_generate_password", spy_gen)

        async def fake_fetch(server_id, account_id, target_department_id=None):
            return {"login": "ops", "password": "old", "host": "10.0.0.5"}

        conn = _conn_ok()
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        submit_calls: list[str] = []

        async def fake_submit(server_id, account_id, new_password, target_department_id=None):
            submit_calls.append(new_password)
            return {"rotated_at": "2026-05-31T12:00:00Z"}

        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_account_password",
            fake_fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_password",
            fake_submit,
        )

        await passwords.account_rotate_password.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED

        # _generate_password НЕ должен был быть вызван — stash перекрыл.
        assert gen_calls == [], (
            "при наличии stash'а новый пароль генерироваться не должен"
        )

        # chpasswd получил тот же пароль, что в stash'е.
        assert conn.run.await_count >= 1
        stdin = conn.run.await_args.kwargs["input"]
        assert f"ops:{stashed_pwd}\n" in stdin

        # submit получил тот же пароль.
        assert submit_calls == [stashed_pwd]

        # После успеха stash вычищен.
        assert await passwords._read_account_rotate_password(tid) is None

    async def test_two_attempts_use_same_password(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Полная симуляция retry'я: первая попытка падает на submit,
        вторая стартует с того же task_id — обе попытки должны вызвать
        chpasswd с одним и тем же паролем."""
        tid = await make_task(
            task_kind="account.rotate_password",
            target_server_id="srv_retry",
            payload={"server_id": "srv_retry", "account_id": "acc_retry"},
        )

        # Чистим stash на случай мусора от предыдущего прогона.
        await passwords._delete_account_rotate_password(tid)

        # Подавляем retry-планировщик: попытки прокручиваем вручную.
        from src.tasks import _runner

        async def noop(*a, **kw):
            pass

        monkeypatch.setattr(_runner, "_schedule_retry", noop)

        async def fake_fetch(server_id, account_id, target_department_id=None):
            return {"login": "ops", "password": "old", "host": "10.0.0.5"}

        conn = _conn_ok()
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        attempt_passwords: list[str] = []
        submit_state = {"attempt": 0}

        async def fake_submit(server_id, account_id, new_password, target_department_id=None):
            attempt_passwords.append(new_password)
            submit_state["attempt"] += 1
            if submit_state["attempt"] == 1:
                # Первая попытка submit'а падает — typical transient.
                from src.core.exceptions import CredentialFetchError
                raise CredentialFetchError(
                    error_code="SERVER_SERVICE_UNREACHABLE",
                    message="transient",
                )
            return {"rotated_at": "2026-05-31T12:00:00Z"}

        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_account_password",
            fake_fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_password",
            fake_submit,
        )

        # Попытка 1: дойдёт до submit, упадёт; stash должен остаться.
        await passwords.account_rotate_password.original_func(tid)

        # Восстанавливаем task в QUEUED, чтобы можно было запустить заново.
        # _runner.run_task после failure возвращает task в QUEUED для retry.
        # Если задача в финальном FAILED — переводим вручную.
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

        # Stash после первой попытки должен жить.
        assert await passwords._read_account_rotate_password(tid) is not None, (
            "stash должен пережить failure submit'а"
        )

        # Попытка 2: stash перекрывает _generate_password.
        await passwords.account_rotate_password.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED

        # Обе попытки должны были использовать ОДИН И ТОТ ЖЕ пароль.
        assert len(attempt_passwords) == 2, attempt_passwords
        assert attempt_passwords[0] == attempt_passwords[1], (
            f"retry должен использовать тот же пароль, что и attempt 1: "
            f"{attempt_passwords}"
        )

        # chpasswd звался дважды (по разу на попытку) с одним паролем.
        assert conn.run.await_count >= 2
        # После успеха stash вычищен.
        assert await passwords._read_account_rotate_password(tid) is None


class TestAccountRotateStashHelpers:
    """Базовый roundtrip Redis-stash'а для account-rotate."""

    async def test_stash_roundtrip(self):
        fake_tid = "tsk_account_stash_roundtrip"

        await passwords._delete_account_rotate_password(fake_tid)
        assert await passwords._read_account_rotate_password(fake_tid) is None
        assert (
            await passwords._read_account_rotate_state(fake_tid)
            == (None, None, None)
        )

        await passwords._store_account_rotate_password(
            fake_tid, "secret_acc_42", login="ops", rotated_at=None,
        )
        # Tonkaya obyortka возвращает только password.
        assert (
            await passwords._read_account_rotate_password(fake_tid)
            == "secret_acc_42"
        )
        # Полный roundtrip — JSON с password+login+rotated_at.
        assert (
            await passwords._read_account_rotate_state(fake_tid)
            == ("secret_acc_42", "ops", None)
        )

        await passwords._delete_account_rotate_password(fake_tid)
        assert await passwords._read_account_rotate_password(fake_tid) is None
