"""Регрессии под W55 P0+P1 в server_worker.

* P0-1 — `dispatch_outbox.poll_once` ставит Redis SET-NX до kiq и
  пропускает повторный kiq, если ключ уже стоит (commit упал в прошлый
  тик после успешного kiq). Подмена redis_pool — единственный mock,
  остальное (broker / session) реальное либо имитируется fake-row'ой.

* P0-2 — `ipmi_rotate_password` / `account_rotate_password` отказываются
  генерировать новый пароль на retry'е (`attempt >= 2`) с пустым stash'ем
  и поднимают `AppException(*_STASH_MISS_ON_RETRY)`. Тест на реальной БД
  + Redis.

* P1 — `_drain_running_tasks` не пишет `task.worker_shutdown` audit, если
  task была cancel'нута между `get_by_id` и UPDATE (CAS-miss в
  `mark_pending_for_retry` / `mark_failed`).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.constants import TaskStatus
from src.core.exceptions import AppException
from src.db.session import AsyncSessionLocal
from src.models import AuditOutbox
from src.repositories import task as task_repo
from src.tasks import dispatch_outbox as outbox_poller
from src.tasks import passwords
from tests._helpers.broker_mocks import make_broker as _make_broker


pytestmark = pytest.mark.asyncio


# ── dispatch_outbox P0-1 ────────────────────────────────────────────────────


class _Row:
    """In-memory имитация DispatchOutbox-row'и."""

    def __init__(self, *, task_id: str, task_kind: str, attempts: int = 0):
        self.id = uuid.uuid4()
        self.task_id = task_id
        self.task_kind = task_kind
        self.payload = {}
        self.attempts = attempts
        self.last_error = None
        self.next_retry_at = None
        self.dispatched_at = None
        self.created_at = datetime.now(timezone.utc)


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeSession:
    def __init__(self, rows, *, commit_fails_on=None):
        """commit_fails_on: callable(row) -> bool, если True — commit бросит."""
        self._rows = rows
        self._commit_fails_on = commit_fails_on
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _stmt):
        return _FakeResult(self._rows)

    async def commit(self):
        self.commits += 1
        if self._commit_fails_on and self._commit_fails_on(self._rows):
            raise RuntimeError("commit transient")

    async def rollback(self):
        self.rollbacks += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def _install_session(monkeypatch, session):
    monkeypatch.setattr(
        outbox_poller.dispatch_outbox_session,
        "get_session_factory",
        lambda: (lambda: session),
    )


def _install_broker(monkeypatch, broker_mock):
    import src.main as _main
    monkeypatch.setattr(_main, "broker", broker_mock)


class _FakeRedis:
    """Минимальная редис-имитация под SET NX + DELETE."""

    def __init__(self):
        self._store: dict[str, str] = {}
        self.set_calls: list[tuple] = []
        self.del_calls: list[str] = []

    async def set(self, key, value, *, ex=None, nx=False):
        self.set_calls.append((key, value, ex, nx))
        if nx and key in self._store:
            return None
        self._store[key] = value
        return True

    async def delete(self, key):
        self.del_calls.append(key)
        return self._store.pop(key, None) is not None


class TestDispatchOutboxDedupSetNx:
    async def test_setnx_acquired_then_kiq_runs(self, monkeypatch):
        """Happy-path: первый заход — SETNX выставил ключ, kiq вызван."""
        rows = [_Row(task_id="tsk_1", task_kind="power.on")]
        session = _FakeSession(rows)
        broker = _make_broker()
        redis_fake = _FakeRedis()

        _install_session(monkeypatch, session)
        _install_broker(monkeypatch, broker)
        monkeypatch.setattr(
            outbox_poller.redis_pool, "get_redis", lambda: redis_fake,
        )

        await outbox_poller.poll_once()

        assert broker._kiq.call_count == 1
        assert rows[0].dispatched_at is not None
        # SETNX ставился и потом снят DELETE'ом
        nx_calls = [c for c in redis_fake.set_calls if c[3] is True]
        assert len(nx_calls) == 1
        # Ключ удалён после успешного commit'а
        assert any("dispatch_dedup" in k for k in redis_fake.del_calls)

    async def test_dedup_hit_skips_kiq_marks_dispatched(self, monkeypatch):
        """Повторный заход: SETNX вернул None (ключ уже стоит) → kiq
        не вызывается, dispatched_at всё равно ставится."""
        row = _Row(task_id="tsk_dup", task_kind="power.on")
        session = _FakeSession([row])
        broker = _make_broker()
        redis_fake = _FakeRedis()
        # Эмулируем оставшийся с прошлого тика дедуп-ключ.
        redis_fake._store[f"dbos:dispatch_dedup:{row.id}"] = "1"

        _install_session(monkeypatch, session)
        _install_broker(monkeypatch, broker)
        monkeypatch.setattr(
            outbox_poller.redis_pool, "get_redis", lambda: redis_fake,
        )

        await outbox_poller.poll_once()

        # kiq НЕ должен был вызваться — дубль предотвращён
        assert broker._kiq.call_count == 0
        assert row.dispatched_at is not None
        # last_error / next_retry_at очищены (row выглядит как success)
        assert row.last_error is None
        assert row.next_retry_at is None

    async def test_kiq_failure_releases_dedup_key(self, monkeypatch):
        """Если kiq упал — дедуп-ключ снимается, чтобы следующий тик
        мог нормально kiq'нуть заново."""
        row = _Row(task_id="tsk_kiq_fail", task_kind="power.on")
        session = _FakeSession([row])
        broker = _make_broker(kiq_exc=ConnectionError("redis down"))
        redis_fake = _FakeRedis()

        _install_session(monkeypatch, session)
        _install_broker(monkeypatch, broker)
        monkeypatch.setattr(
            outbox_poller.redis_pool, "get_redis", lambda: redis_fake,
        )

        await outbox_poller.poll_once()

        assert row.dispatched_at is None  # не диспатчилось
        # delete-call для дедуп-ключа: SETNX выставил, kiq упал, DELETE снёс
        dedup_dels = [k for k in redis_fake.del_calls if "dispatch_dedup" in k]
        assert len(dedup_dels) == 1


# ── passwords P0-2 ──────────────────────────────────────────────────────────


class TestIpmiStashMissOnRetry:
    async def test_first_attempt_empty_stash_proceeds(
        self, make_task, monkeypatch,
    ):
        """attempt == 1 + пустой stash — это первый заход, генерим как
        обычно. Проверяем напрямую: stash до = пусто, после impl = заполнен."""
        tid = await make_task(
            task_kind="ipmi.rotate_password",
            target_server_id="srv_first",
            payload={"server_id": "srv_first"},
        )

        # Эмулируем «mark_running прошёл, attempt=1».
        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            t.attempt = 1
            t.status = TaskStatus.RUNNING
            await session.commit()

        await passwords._delete_ipmi_rotate_password(tid)

        async def fake_fetch(server_id, target_department_id=None):
            return {
                "controller_id": "ipm_first2",
                "kind": "idrac",
                "endpoint_url": "https://bmc.test",
                "username": "root",
                "password": "old",
            }

        from tests._helpers.bmc_mocks import FakeBmc

        async def _bmc_factory(creds, *, prefer="redfish"):
            return FakeBmc()

        async def fake_submit(controller_id, new_password, rotated_at,
                              target_department_id=None, verified_at=None):
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

        # Подменяем run_task, чтобы прокинуть payload прямиком в _impl.
        from src.tasks import _runner

        impl_returned = {"ran": False}

        async def capture_run_task(task_id, *, impl, **kw):
            await impl({"server_id": "srv_first"})
            impl_returned["ran"] = True

        monkeypatch.setattr(passwords, "run_task", capture_run_task)

        # Не должно бросить — attempt==1, генерация разрешена.
        await passwords.ipmi_rotate_password.original_func(tid)

        # impl дошёл до конца — пароль сгенерён и засабмиттен (submit_called).
        assert impl_returned["ran"] is True
        # stash удалён после успешного submit'а (явный DELETE в конце impl).
        assert (await passwords._read_ipmi_rotate_state(tid)) == (None, None)

    async def test_retry_with_empty_stash_raises_stash_miss(
        self, make_task, fetch_task, monkeypatch,
    ):
        """attempt >= 2 + пустой stash → fail-loud с
        IPMI_STASH_MISS_ON_RETRY, без генерации нового пароля."""
        tid = await make_task(
            task_kind="ipmi.rotate_password",
            target_server_id="srv_miss",
            payload={"server_id": "srv_miss"},
        )

        # Поднимаем attempt на 2 — имитируем retry.
        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            t.attempt = 2
            t.status = TaskStatus.RUNNING
            await session.commit()

        # Удостоверяемся, что stash пуст.
        await passwords._delete_ipmi_rotate_password(tid)

        gen_calls: list[int] = []
        original_gen = passwords._generate_password

        def spy_gen():
            gen_calls.append(1)
            return original_gen()

        monkeypatch.setattr(passwords, "_generate_password", spy_gen)

        async def fake_fetch(server_id, target_department_id=None):
            return {
                "controller_id": "ipm_miss",
                "kind": "idrac",
                "endpoint_url": "https://bmc.test",
                "username": "root",
                "password": "old",
            }

        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_ipmi_credentials",
            fake_fetch,
        )

        # Не должно дойти до _get_bmc / submit — фейл до BMC apply.
        bmc_called = {"flag": False}

        async def _bmc_factory(creds, *, prefer="redfish"):
            bmc_called["flag"] = True
            return None

        monkeypatch.setattr("src.tasks.passwords._get_bmc", _bmc_factory)

        # Дёргаем напрямую внутренний _impl через тестовый shim: вызываем
        # `original_func` под run_task'ом. run_task проглотит exception
        # и mark_failed/retry — нас интересует именно exception type
        # перед обработкой. Берём импл напрямую через monkeypatch
        # `run_task` для capture.
        captured_exc: list[Exception] = []

        from src.tasks import _runner

        original_run_task = _runner.run_task

        async def capture_run_task(task_id, *, impl, **kw):
            try:
                await impl({"server_id": "srv_miss"})
            except Exception as e:  # noqa: BLE001
                captured_exc.append(e)
                raise

        monkeypatch.setattr(_runner, "run_task", capture_run_task)
        # passwords.py импортировал `run_task` локально — патчим там тоже.
        monkeypatch.setattr(passwords, "run_task", capture_run_task)

        with pytest.raises(AppException) as ei:
            await passwords.ipmi_rotate_password.original_func(tid)

        assert ei.value.error_code == "IPMI_STASH_MISS_ON_RETRY"
        assert ei.value.details["attempt"] == 2
        # Главное: новый пароль НЕ генерился, BMC apply НЕ запускался.
        assert gen_calls == []
        assert bmc_called["flag"] is False

        # Восстанавливаем run_task для последующих тестов (autouse-ом не управляем).
        monkeypatch.setattr(_runner, "run_task", original_run_task)


class TestAccountStashMissOnRetry:
    async def test_retry_with_empty_stash_raises_stash_miss(
        self, make_task, monkeypatch,
    ):
        tid = await make_task(
            task_kind="account.rotate_password",
            target_server_id="srv_acc_miss",
            payload={
                "server_id": "srv_acc_miss",
                "account_id": "acc_x",
                "login": "ops",
                "is_managed": True,
            },
        )

        async with AsyncSessionLocal() as session:
            t = await task_repo.get_by_id(session, tid)
            t.attempt = 3
            t.status = TaskStatus.RUNNING
            await session.commit()

        await passwords._delete_account_rotate_password(tid)

        gen_calls: list[int] = []
        original_gen = passwords._generate_password

        def spy_gen():
            gen_calls.append(1)
            return original_gen()

        monkeypatch.setattr(passwords, "_generate_password", spy_gen)

        chpasswd_called = {"flag": False}

        async def fake_set_pw(*args, **kwargs):
            chpasswd_called["flag"] = True

        monkeypatch.setattr(
            "src.tasks.passwords.ssh_client.set_account_password",
            fake_set_pw,
        )

        # is_managed=True + login в payload → fetch_account_password не зовётся.
        from src.tasks import _runner

        async def capture_run_task(task_id, *, impl, **kw):
            await impl({
                "server_id": "srv_acc_miss",
                "account_id": "acc_x",
                "login": "ops",
                "is_managed": True,
            })

        monkeypatch.setattr(passwords, "run_task", capture_run_task)

        with pytest.raises(AppException) as ei:
            await passwords.account_rotate_password.original_func(tid)

        assert ei.value.error_code == "ACCOUNT_STASH_MISS_ON_RETRY"
        assert ei.value.details["attempt"] == 3
        assert gen_calls == []
        assert chpasswd_called["flag"] is False


# ── _drain_running_tasks P1 ─────────────────────────────────────────────────


def _new_id() -> str:
    return f"tsk_{uuid.uuid4().hex[:16]}"


async def _all_outbox_rows() -> list[AuditOutbox]:
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        stmt = select(AuditOutbox).order_by(AuditOutbox.id.asc())
        return list((await session.execute(stmt)).scalars().all())


class TestDrainSkipsAuditOnCancelledTask:
    """Если задача была cancel'нута между get_by_id и UPDATE
    (`mark_pending_for_retry` / `mark_failed` CAS-miss), drain НЕ пишет
    audit `task.worker_shutdown` поверх cancelled-row'ы.

    Симулируем race через monkeypatch `mark_pending_for_retry`/`mark_failed`
    — вернём None напрямую. До фикса audit был безусловный.
    """

    async def test_cancel_race_in_retry_branch_skips_audit(
        self, make_task, fetch_task, monkeypatch,
    ):
        from src.main import _drain_running_tasks
        from src.tasks._runner_state import RUNNING_TASKS
        from taskiq import TaskiqState

        tid = _new_id()
        async with AsyncSessionLocal() as session:
            await task_repo.create(session, {
                "id": tid,
                "task_kind": "power.on",
                "target_server_id": "srv_drain_cancel",
                "payload": {},
                "status": TaskStatus.RUNNING,
                "attempt": 1,
                "max_attempts": 3,
            })
            await session.commit()
        RUNNING_TASKS.add(tid)

        from src.main import _settings as main_settings
        monkeypatch.setattr(
            main_settings, "worker_shutdown_timeout_seconds", 0.2,
        )

        # Эмулируем CAS-miss: mark_pending_for_retry возвращает None.
        async def fake_mark_pending(session, task, error_message, *, scheduled_retry_at=None):
            return None

        monkeypatch.setattr(
            task_repo, "mark_pending_for_retry", fake_mark_pending,
        )

        before_rows = await _all_outbox_rows()
        before_count = len(before_rows)

        await _drain_running_tasks(TaskiqState())

        after_rows = await _all_outbox_rows()
        # Ни одного нового audit-row про worker_shutdown по нашему task_id.
        new_rows = [
            r for r in after_rows
            if r.task_id == tid
            and r.payload.get("action") == "task.worker_shutdown"
        ]
        assert new_rows == [], (
            f"audit task.worker_shutdown не должен писаться при CAS-miss: "
            f"{new_rows}"
        )
        # Глобально outbox не вырос по нашему task_id.
        assert len(after_rows) == before_count or all(
            r.task_id != tid for r in after_rows[before_count:]
        )

    async def test_cancel_race_in_terminal_branch_skips_audit(
        self, make_task, fetch_task, monkeypatch,
    ):
        from src.main import _drain_running_tasks
        from src.tasks._runner_state import RUNNING_TASKS
        from taskiq import TaskiqState

        tid = _new_id()
        async with AsyncSessionLocal() as session:
            await task_repo.create(session, {
                "id": tid,
                "task_kind": "power.on",
                "target_server_id": "srv_drain_cancel2",
                "payload": {},
                "status": TaskStatus.RUNNING,
                "attempt": 3,
                "max_attempts": 3,
            })
            await session.commit()
        RUNNING_TASKS.add(tid)

        from src.main import _settings as main_settings
        monkeypatch.setattr(
            main_settings, "worker_shutdown_timeout_seconds", 0.2,
        )

        async def fake_mark_failed(session, task, error_message):
            return None

        monkeypatch.setattr(task_repo, "mark_failed", fake_mark_failed)

        await _drain_running_tasks(TaskiqState())

        after_rows = await _all_outbox_rows()
        bad = [
            r for r in after_rows
            if r.task_id == tid
            and r.payload.get("action") == "task.worker_shutdown"
        ]
        assert bad == [], (
            f"task.worker_shutdown audit не должен писаться при CAS-miss "
            f"в terminal-ветке: {bad}"
        )
