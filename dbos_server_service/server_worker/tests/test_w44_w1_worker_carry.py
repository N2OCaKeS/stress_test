"""Доборка тестов после W44-аудита `server_worker/tests/`.

Покрывает gap'ы, обнаруженные аудитом:

  * `http_pool.aclose_all` — `CancelledError` на одном aclose не должен
    оставлять остальные слоты не закрытыми;
  * `passwords.ipmi_rotate_password` exception tuple `(RedfishError,
    IpmitoolError, ValueError, RuntimeError)` — проверяем, что
    `ValueError`/`RuntimeError` ловятся и классифицируются;
  * `dispatch_outbox.poll_once` — провал kiq на row #2 из 3 не должен
    откатывать commit row #1, а row #3 должен дойти отдельным commit'ом;
  * `audit_outbox` SKIP LOCKED per-row visibility — publisher A держит
    lock на row #1, publisher B SELECT skipping видит row #2.
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.asyncio


# ── http_pool: CancelledError-resilient drain ────────────────────────────────


class TestHttpPoolAcloseCancelled:
    """`aclose_all` ловит CancelledError на одном клиенте и продолжает.

    Без guard'а первый `aclose()` бросал бы наружу и не давал бы закрыть
    остальные пулы — FD-leak на shutdown'е.
    """

    async def test_cancelled_during_aclose_still_completes_drain(self, monkeypatch):
        from src.services import http_pool

        closed: list[str] = []

        class _CancelClient:
            label = "audit"

            def __init__(self, **kwargs):
                self.is_closed = False

            async def aclose(self):
                # Первый клиент имитирует CancelledError на закрытии.
                raise asyncio.CancelledError("aclose interrupted")

        class _NormalClient:
            label = "other"

            def __init__(self, **kwargs):
                self.is_closed = False

            async def aclose(self):
                closed.append(type(self).__name__)
                self.is_closed = True

        # Подменяем httpx.AsyncClient — первый get_audit_client отдаст
        # _CancelClient, второй get_server_service_client отдаст _NormalClient.
        # Конструируем по очереди через подмену.
        construct_idx = {"n": 0}

        def _ctor(**kwargs):
            construct_idx["n"] += 1
            if construct_idx["n"] == 1:
                return _CancelClient(**kwargs)
            return _NormalClient(**kwargs)

        monkeypatch.setattr("src.services.http_pool.httpx.AsyncClient", _ctor)

        class _S:
            audit_pool_max_connections = 20
            audit_pool_max_keepalive_connections = 10
            server_service_pool_max_connections = 20
            server_service_pool_max_keepalive_connections = 10
            http_request_timeout_seconds = 5.0
            audit_request_timeout_seconds = 5.0
            server_service_request_timeout_seconds = 15.0

        monkeypatch.setattr("src.services.http_pool.get_settings", lambda: _S())

        http_pool.reset_for_tests()
        try:
            http_pool.get_audit_client()
            http_pool.get_server_service_client()

            # aclose_all должен поймать CancelledError первого клиента,
            # закрыть второго, и потом переотправить CancelledError.
            with pytest.raises(asyncio.CancelledError):
                await http_pool.aclose_all()

            assert "_NormalClient" in closed, (
                "второй клиент должен закрыться даже после Cancelled на первом"
            )
        finally:
            http_pool.reset_for_tests()

    async def test_aclose_all_handles_already_closed_client(self, monkeypatch):
        """Если клиент уже закрыт (is_closed=True), aclose повторно не падает.

        Сейчас `aclose_all` зовёт `aclose()` безусловно — httpx.AsyncClient
        идемпотентен на повторных aclose. Тест гарантирует, что глобальный
        дренаж переживает «двойной aclose» — типичный случай при shutdown'е
        с конкурентным `reset_for_tests`.
        """
        from src.services import http_pool

        http_pool.reset_for_tests()
        try:
            c = http_pool.get_audit_client()
            await c.aclose()
            assert c.is_closed
            # Должно пройти без падения — drain просто закроет (idempotent).
            await http_pool.aclose_all()
        finally:
            http_pool.reset_for_tests()


# ── passwords: ValueError / RuntimeError в exception tuple ───────────────────


class _FakeBmcRaises:
    """BMC, у которого `rotate_user_password` бросает заданное исключение."""

    def __init__(self, exc: BaseException):
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def rotate_user_password(self, user_id: int, new_password: str) -> None:
        raise self._exc

    async def get_power_state(self) -> str:
        return "On"

    async def aclose(self) -> None:
        return None


class TestPasswordsApplyBmcExceptionMapping:
    """`ipmi_rotate_password` apply-шаг ловит `(RedfishError, IpmitoolError,
    ValueError, RuntimeError)` — проверяем, что ValueError и RuntimeError
    действительно классифицируются как BMC-ошибка (FAILED task, audit ушёл).
    """

    async def _run(self, exc: BaseException, make_task, fetch_task, captured_audit, monkeypatch):
        from src.core.constants import TaskStatus
        from src.tasks import passwords

        tid = await make_task(
            task_kind="ipmi.rotate_password",
            target_server_id="srv_w44_apply",
            payload={"server_id": "srv_w44_apply"},
        )

        async def fake_fetch(server_id, target_department_id=None):
            return {
                "controller_id": "ipm_w44",
                "kind": "idrac",
                "endpoint_url": "https://bmc.w44.test",
                "username": "root",
                "password": "old",
            }

        async def _bmc_factory(creds, *, prefer="redfish"):
            return _FakeBmcRaises(exc)

        async def fake_submit(*a, **kw):
            return None

        async def spy_check(host: str) -> None:
            return None

        async def spy_record_failure(host: str) -> None:
            return None

        async def spy_record_success(host: str) -> None:
            return None

        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_ipmi_credentials",
            fake_fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_ipmi_password",
            fake_submit,
        )
        monkeypatch.setattr("src.tasks.passwords._get_bmc", _bmc_factory)
        monkeypatch.setattr("src.tasks.passwords._breaker.check", spy_check)
        monkeypatch.setattr(
            "src.tasks.passwords._breaker.record_failure", spy_record_failure,
        )
        monkeypatch.setattr(
            "src.tasks.passwords._breaker.record_success", spy_record_success,
        )

        await passwords.ipmi_rotate_password.original_func(tid)

        t = await fetch_task(tid)
        # apply-шаг упал → task либо в QUEUED (запланирован retry),
        # либо FAILED (исчерпаны попытки или unhandled).
        assert t.status in (TaskStatus.QUEUED, TaskStatus.FAILED), (
            f"ожидали QUEUED/FAILED, получили {t.status}"
        )
        # last_error выставлен — exception обработан как BMC-ошибка, а не
        # пробросился наружу как unhandled (тогда last_error был бы None).
        assert t.last_error is not None and t.last_error != ""

    async def test_value_error_in_apply_is_classified(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """ValueError на rotate_user_password — apply-фейл, не unhandled."""
        await self._run(
            ValueError("payload validation: unsupported user_id range"),
            make_task, fetch_task, captured_audit, monkeypatch,
        )

    async def test_runtime_error_in_apply_is_classified(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """RuntimeError на rotate_user_password — apply-фейл, не unhandled."""
        await self._run(
            RuntimeError("BMC client invariant broken: pre-PATCH state mismatch"),
            make_task, fetch_task, captured_audit, monkeypatch,
        )


# ── dispatch_outbox: per-row commit isolation ────────────────────────────────


class TestDispatchOutboxPerRowRollbackIsolation:
    """Провал kiq на row #2 из 3 не должен отменять commit row #1, и row
    #3 должен пойти отдельным commit'ом.

    Защита от регрессии: «один rollback откатывает соседей». Test mirroring
    шаблона из `tests/unit/test_w30_dispatch_outbox_poller.py`.
    """

    async def test_middle_row_fail_does_not_rollback_neighbors(self, monkeypatch):
        from tests.unit.test_w30_dispatch_outbox_poller import (
            _Row,
            _FakeSession,
            _install_session,
            _install_broker,
        )
        from src.tasks import dispatch_outbox as outbox_poller
        from unittest.mock import AsyncMock, MagicMock

        rows = [
            _Row(task_id="tsk_ok1", task_kind="power.on"),
            _Row(task_id="tsk_fail", task_kind="power.on"),
            _Row(task_id="tsk_ok2", task_kind="power.on"),
        ]
        session = _FakeSession(rows)

        # broker.find_task возвращает task, у которого kiq на tsk_fail бросает.
        broker = MagicMock()
        call_log: list[str] = []

        async def _kiq(task_id):
            call_log.append(task_id)
            if task_id == "tsk_fail":
                raise RuntimeError("redis ConnectionError: simulated")

        kicker = MagicMock()
        kicker.kiq = _kiq
        task = MagicMock()
        task.kicker = MagicMock(return_value=kicker)
        broker.find_task = MagicMock(return_value=task)

        _install_session(monkeypatch, session)
        _install_broker(monkeypatch, broker)

        await outbox_poller.poll_once()

        # Все три row дошли до kiq.
        assert call_log == ["tsk_ok1", "tsk_fail", "tsk_ok2"]
        # Row #1 и #3 успешно dispatched.
        assert rows[0].dispatched_at is not None, "row #1 должна быть dispatched"
        assert rows[2].dispatched_at is not None, "row #3 должна быть dispatched"
        # Row #2 — attempt инкрементнут, dispatched_at не выставлен.
        assert rows[1].dispatched_at is None
        assert rows[1].attempts == 1
        assert rows[1].last_error is not None
        # Все 3 commit'а прошли — по одному на row.
        assert session.commit_count == 3, (
            f"ожидали 3 commit'а (по одному на row), получили {session.commit_count}"
        )


# ── audit_outbox: per-row lock-visibility ────────────────────────────────────


class TestAuditOutboxSkipLockedPerRow:
    """`SELECT ... FOR UPDATE SKIP LOCKED` отдаёт publisher'у B только те
    строки, которые publisher A ещё не залочил.

    Mirror existing `TestConcurrentPublishers` — отличие в том, что здесь
    мы вручную задерживаем emit publisher'а A, чтобы publisher B успел
    отдельным транзакционным окном SELECT'ить и взять оставшийся набор.
    """

    async def test_publisher_b_picks_up_rows_not_locked_by_a(
        self, make_task, monkeypatch,
    ):
        from collections import Counter
        from sqlalchemy import update as _upd

        from src.db.session import AsyncSessionLocal
        from src.models.audit_outbox import AuditOutbox
        from src.services import audit_outbox_publisher
        from src.tasks._runner import run_task

        # Засеваем 4 unpublished строки.
        async def failing_emit_seed(action, **kw):
            raise RuntimeError("seed: down")

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            failing_emit_seed,
        )

        async def ok_impl(_):
            return {"power_state": "on"}

        N = 4
        for _ in range(N):
            tid = await make_task(task_kind="power.on")
            await run_task(
                tid,
                audit_action="server.power_on",
                audit_target_type="server",
                impl=ok_impl,
                audit_safe_fields={"power_state"},
            )

        # Сбрасываем next_retry_at, чтобы оба publisher'а сразу увидели
        # очередь полностью.
        async with AsyncSessionLocal() as session:
            await session.execute(
                _upd(AuditOutbox).values(next_retry_at=None)
            )
            await session.commit()

        # Publisher A держит emit под Event'ом — после первого emit'а
        # «зависает», давая publisher'у B запустить SELECT параллельно.
        a_started = asyncio.Event()
        a_release = asyncio.Event()
        emit_counts: Counter = Counter()
        emit_lock = asyncio.Lock()
        emitted_by_a: list[str] = []
        emitted_by_b: list[str] = []
        current: dict[str, str] = {"who": "a"}

        async def a_emit(action, **kw):
            a_started.set()
            await a_release.wait()
            async with emit_lock:
                tid = kw.get("details", {}).get("task_id") or kw.get("target_id") or str(kw)
                emit_counts[tid] += 1
                emitted_by_a.append(tid)

        async def b_emit(action, **kw):
            async with emit_lock:
                tid = kw.get("details", {}).get("task_id") or kw.get("target_id") or str(kw)
                emit_counts[tid] += 1
                emitted_by_b.append(tid)

        async def dispatching_emit(action, **kw):
            if current["who"] == "a":
                await a_emit(action, **kw)
            else:
                await b_emit(action, **kw)

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            dispatching_emit,
        )

        # Запускаем publisher A с маленьким batch (2 row'а) — он залочит
        # эти строки SELECT'ом FOR UPDATE и «застрянет» на emit.
        async def run_a():
            current["who"] = "a"
            return await audit_outbox_publisher.flush_outbox(limit=2)

        a_task = asyncio.create_task(run_a())

        # Ждём, что publisher A добрался до emit (значит SELECT + UPDATE
        # уже прошли, locks держатся до commit'а — а commit ещё не было).
        await asyncio.wait_for(a_started.wait(), timeout=5.0)

        # Publisher B запускает свой проход — SELECT SKIP LOCKED должен
        # пропустить залоченные A row'и и взять оставшиеся.
        current["who"] = "b"
        published_b = await audit_outbox_publisher.flush_outbox(limit=N)

        # B должен забрать только не залоченные A row'и.
        assert published_b >= 1, (
            "publisher B должен взять хотя бы одну row, не залоченную A "
            "(SKIP LOCKED)"
        )
        assert published_b <= N - 1, (
            f"publisher B не должен видеть row'и A; published_b={published_b}"
        )

        # Освобождаем A и ждём завершения.
        a_release.set()
        published_a = await asyncio.wait_for(a_task, timeout=5.0)

        assert published_a + published_b == N
        # Ни одного дубликата.
        dups = {k: v for k, v in emit_counts.items() if v > 1}
        assert not dups, f"дубликаты при SKIP LOCKED: {dups}"
