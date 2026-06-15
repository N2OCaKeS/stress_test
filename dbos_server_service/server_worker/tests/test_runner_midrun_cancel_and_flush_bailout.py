"""Worker coverage — граничные сценарии второго уровня.

Покрываемые области:
  * scrub finally — `account_provision._impl` finally-scrub запускается даже
    при RuntimeError на happy-path (impl upало после useradd/submit).
  * breaker half_open probe-gate — второй check() в half_open отбивается
    probe-ключом (anti-thundering-herd: один пробный запрос на cycle).
  * flush bail-out — пустая очередь после первого bail-out не вызывает
    дополнительных SELECT'ов; breaker_skipped при пустом batch.
  * rotated_at after BMC apply — rotated_at не фиксируется если verify упал.
  * cancel timestamp from task.cancelled_at — midrun-пути (success и failure)
    используют task.cancelled_at как audit timestamp, когда поле задано.
  * deleted_midrun audit — failure-path original_action и success-path phase
    корректно заполнены; failure deleted + retry подавляется.
  * install_authorized_key home guard — home="/" отбивается тем же guard'ом
    что и home="" (выход rc=1 из bash).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, update

from src.clients.ssh import SshClient, SshError
from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.models import AuditOutbox, Task
from src.repositories import task as task_repo
from src.services import audit_outbox_publisher, audit_publisher_breaker
from src.tasks._runner import run_task
from tests._ssh_mock_helpers import make_conn, run_result
from tests.unit._breaker_test_helpers import (
    FakeRedis,
    frozen_clock_fixture,
    install_fake_redis,
)


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_redis_apb(monkeypatch: pytest.MonkeyPatch) -> type[FakeRedis]:
    return install_fake_redis(monkeypatch, audit_publisher_breaker)


@pytest.fixture
def frozen_clock_apb(monkeypatch: pytest.MonkeyPatch) -> dict:
    return frozen_clock_fixture(monkeypatch, audit_publisher_breaker)


def _patch_no_retry(monkeypatch) -> None:
    from src.tasks import _runner
    async def noop(*args, **kwargs):
        pass
    monkeypatch.setattr(_runner, "_schedule_retry", noop)


async def _cancel_with_cancelled_at(
    task_id: str,
    *,
    cancelled_at: datetime,
    cancelled_by: str | None = "usr_op",
    cancel_reason: str | None = "test reason",
) -> None:
    values: dict = {
        "status": TaskStatus.CANCELLED,
        "cancelled_at": cancelled_at,
    }
    if cancelled_by is not None:
        values["cancelled_by"] = cancelled_by
    if cancel_reason is not None:
        values["cancel_reason"] = cancel_reason
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Task).where(Task.id == task_id).values(**values)
        )
        await session.commit()


# ── scrub finally ─────────────────────────────────────────────────────────────


# Класс TestScrubFinallyRunsOnHappyPathFailure (payload-plaintext scrub)
# удалён: payload больше не содержит plaintext-полей после фикса
# server_service. Защиту от inline-плэйнтекста закрывает запрет на
# `password_plaintext`/`ssh_private_key_plaintext` в payload на уровне
# server_service.


# ── breaker half_open probe-gate ─────────────────────────────────────────────


class TestBreakerHalfOpenSecondCheck:
    """Второй check() в half_open отбивается probe-ключом (anti-thundering-herd).

    Раньше check() в half_open пропускал всех подряд — все реплики разом
    видели «половинку открыто» и кидали залп в едва ожившие BMC/loging.
    Теперь open→half_open захватывается через SETNX probe-ключа; второй
    check (любая другая реплика) видит probe в Redis и получает
    `CircuitBreakerOpenError` до результата пробного запроса первого.
    """

    async def test_second_check_in_half_open_is_rejected(
        self, fake_redis_apb, frozen_clock_apb,
    ):
        """Первый check проходит как пробный, второй отбивается probe-ключом."""
        for _ in range(audit_publisher_breaker.DEFAULT_FAILURE_THRESHOLD):
            await audit_publisher_breaker.record_failure()

        frozen_clock_apb["now"] += audit_publisher_breaker.DEFAULT_COOLDOWN_SECONDS + 1

        # Первый check() — winner SETNX, state переходит в half_open.
        await audit_publisher_breaker.check()
        _, state_key, _ = audit_publisher_breaker._keys()
        assert FakeRedis._store.get(state_key) == "half_open"

        # Второй check — probe-ключ занят, отбивается.
        with pytest.raises(audit_publisher_breaker.CircuitBreakerOpenError):
            await audit_publisher_breaker.check()
        # State не меняется — probe ещё в полёте.
        assert FakeRedis._store.get(state_key) == "half_open", (
            "проигравший check не должен трогать state"
        )

    async def test_half_open_reopens_on_record_failure(
        self, fake_redis_apb, frozen_clock_apb,
    ):
        """half_open → open при record_failure (пробный запрос провалился)."""
        for _ in range(audit_publisher_breaker.DEFAULT_FAILURE_THRESHOLD):
            await audit_publisher_breaker.record_failure()

        frozen_clock_apb["now"] += audit_publisher_breaker.DEFAULT_COOLDOWN_SECONDS + 1
        await audit_publisher_breaker.check()  # → half_open (winner)

        _, state_key, _ = audit_publisher_breaker._keys()
        assert FakeRedis._store.get(state_key) == "half_open"

        # Запрос прошёл, но провалился → record_failure → open.
        await audit_publisher_breaker.record_failure()
        assert FakeRedis._store.get(state_key) == "open"

    async def test_half_open_closes_after_record_success(
        self, fake_redis_apb, frozen_clock_apb,
    ):
        """half_open + record_success → closed (все ключи удалены)."""
        for _ in range(audit_publisher_breaker.DEFAULT_FAILURE_THRESHOLD):
            await audit_publisher_breaker.record_failure()

        frozen_clock_apb["now"] += audit_publisher_breaker.DEFAULT_COOLDOWN_SECONDS + 1
        await audit_publisher_breaker.check()  # → half_open

        failures_key, state_key, open_until_key = audit_publisher_breaker._keys()
        assert FakeRedis._store.get(state_key) == "half_open"

        await audit_publisher_breaker.record_success()
        # После success все ключи должны быть удалены (closed state = no keys).
        assert state_key not in FakeRedis._store
        assert failures_key not in FakeRedis._store
        assert open_until_key not in FakeRedis._store


# ── flush bail-out ────────────────────────────────────────────────────────────


class TestFlushBailOutEdgeCases:
    """Дополнительные грани bail-out: пустая очередь и нет лишних SELECT'ов."""

    async def _insert_rows(self, n: int) -> None:
        from sqlalchemy import select
        async with AsyncSessionLocal() as session:
            for i in range(n):
                row = AuditOutbox(
                    task_id=f"tsk_bail_{i}",
                    payload={"action": "server.power_on", "target_id": f"srv_{i}"},
                )
                session.add(row)
            await session.commit()

    async def test_empty_queue_after_bail_out_exits_cleanly(
        self, fake_redis_apb, frozen_clock_apb, monkeypatch,
    ):
        """Open breaker + пустая очередь → flush возвращает 0, SELECT один раз."""
        for _ in range(audit_publisher_breaker.DEFAULT_FAILURE_THRESHOLD):
            await audit_publisher_breaker.record_failure()

        select_calls = {"n": 0}
        original_select = audit_outbox_publisher._select_unpublished_excluding

        def counted_select(limit, exclude_ids):
            select_calls["n"] += 1
            return original_select(limit, exclude_ids)

        monkeypatch.setattr(
            audit_outbox_publisher,
            "_select_unpublished_excluding",
            counted_select,
        )

        published = await audit_outbox_publisher.flush_outbox()
        assert published == 0
        # Один SELECT — пустая очередь или первый bail-out, оба дают break.
        assert select_calls["n"] <= 1

    async def test_breaker_skipped_row_not_added_to_failed_ids(
        self, fake_redis_apb, frozen_clock_apb, monkeypatch,
    ):
        """breaker_skipped row не попадает в failed_ids и не блокирует attempts."""
        await self._insert_rows(2)

        for _ in range(audit_publisher_breaker.DEFAULT_FAILURE_THRESHOLD):
            await audit_publisher_breaker.record_failure()

        async def no_emit(action, **kw):
            raise AssertionError("emit must not be called under open breaker")

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit", no_emit
        )

        published = await audit_outbox_publisher.flush_outbox()
        assert published == 0

        # Row'ы не должны иметь incremented attempts — breaker-skip не попытка.
        from sqlalchemy import select
        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(AuditOutbox).order_by(AuditOutbox.id.asc())
                )
            ).scalars().all()
        assert all(row.attempts == 0 for row in rows), (
            "breaker-skip не считается попыткой, attempts должен быть 0"
        )

    async def test_limit_exhausted_without_bail_out(
        self, monkeypatch,
    ):
        """Когда breaker closed и строк меньше limit — loop завершается корректно.

        Проверяем сценарий «очередь ровно из одной строки», чтобы убедиться,
        что second SELECT возвращает None и цикл выходит без bail-out.
        """
        await self._insert_rows(1)

        async def fake_emit(action, **kw) -> None:
            pass

        async def pass_check() -> None:
            pass

        async def noop_record(*a, **kw) -> None:
            pass

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit", fake_emit,
        )
        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_publisher_breaker.check",
            pass_check,
        )
        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_publisher_breaker.record_success",
            noop_record,
        )
        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_publisher_breaker.record_failure",
            noop_record,
        )

        published = await audit_outbox_publisher.flush_outbox()
        assert published == 1, "одна строка должна быть опубликована"


# ── cancel timestamp from task.cancelled_at midrun paths ─────────────────────


class TestCancelTimestampMidrunPaths:
    """cancelled_at override применяется в audit.timestamp для midrun-путей.

    Существующий тест в test_worker_fixes_bundle.py покрыл fast-path.
    Здесь — success-midrun и failure-midrun ветки _runner'а.
    """

    async def test_success_midrun_cancel_uses_cancelled_at_as_timestamp(
        self, make_task, captured_audit,
    ):
        """impl возвращается успешно, cancel прошёл во время выполнения.
        audit.timestamp = task.cancelled_at, не now().
        """
        tid = await make_task(task_kind="power.on", target_server_id="srv_cm1")

        cancel_ts = datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc)

        async def impl(_payload: dict) -> dict:
            await _cancel_with_cancelled_at(
                tid,
                cancelled_at=cancel_ts,
                cancelled_by="usr_cm1",
                cancel_reason="success-midrun-ts-check",
            )
            return {"power_state": "on"}

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
            audit_safe_fields={"power_state"},
        )

        assert len(captured_audit) == 1
        ev = captured_audit[0]
        assert ev["status"] == "failure"
        assert ev["details"]["reason"] == "cancelled_midrun"
        # Ключевая проверка: timestamp override совпадает с cancelled_at.
        assert ev.get("timestamp") == cancel_ts.isoformat(), (
            f"ожидался timestamp={cancel_ts.isoformat()}, got={ev.get('timestamp')}"
        )

    async def test_failure_midrun_cancel_uses_cancelled_at_as_timestamp(
        self, make_task, captured_audit,
    ):
        """impl бросает на terminal-attempt, cancel прошёл во время выполнения.
        audit.timestamp = task.cancelled_at из refreshed row.
        """
        tid = await make_task(task_kind="power.on", target_server_id="srv_cm2")
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Task).where(Task.id == tid).values(max_attempts=1)
            )
            await session.commit()

        cancel_ts = datetime(2026, 4, 1, 8, 30, 0, tzinfo=timezone.utc)

        async def impl(_payload: dict) -> dict:
            await _cancel_with_cancelled_at(
                tid,
                cancelled_at=cancel_ts,
                cancelled_by="usr_cm2",
                cancel_reason="failure-midrun-ts-check",
            )
            raise RuntimeError("impl failed")

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
        )

        assert len(captured_audit) == 1
        ev = captured_audit[0]
        assert ev["status"] == "failure"
        assert ev["details"]["reason"] == "cancelled_midrun"
        assert ev.get("timestamp") == cancel_ts.isoformat(), (
            f"ожидался timestamp={cancel_ts.isoformat()}, got={ev.get('timestamp')}"
        )

    async def test_success_midrun_cancel_without_cancelled_at_uses_worker_clock(
        self, make_task, captured_audit,
    ):
        """Если `cancelled_at` не задан — в audit идёт worker_clock_now,
        timestamp всё равно выставляется (детерминированный поведение
        контракт: midrun-cancel без явного cancel-timestamp получает
        timestamp из worker'а)."""
        tid = await make_task(task_kind="power.on", target_server_id="srv_cm3")

        async def impl(_payload: dict) -> dict:
            # Отменяем без явного cancelled_at (поле остаётся NULL).
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(Task)
                    .where(Task.id == tid)
                    .values(status=TaskStatus.CANCELLED)
                )
                await session.commit()
            return {"power_state": "on"}

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
        )

        assert len(captured_audit) == 1
        ev = captured_audit[0]
        assert ev["details"]["reason"] == "cancelled_midrun"
        # Timestamp всё равно выставлен — но из worker_clock_now, не из
        # `cancelled_at` (т.к. cancelled_at был NULL).
        assert ev.get("timestamp") is not None, (
            "timestamp должен выставляться даже без cancelled_at"
        )

    async def test_failure_midrun_retry_path_cancel_uses_cancelled_at(
        self, make_task, captured_audit,
    ):
        """impl бросает, попытки ещё есть (retry-path), cancel прошёл.
        Timestamp override применяется на retry-path тоже.
        """
        from src.tasks import _runner
        async def noop_retry(*a, **kw):
            pass
        _orig = _runner._schedule_retry
        _runner._schedule_retry = noop_retry  # type: ignore[assignment]
        try:
            tid = await make_task(task_kind="power.on", target_server_id="srv_cm4")
            cancel_ts = datetime(2026, 5, 10, 14, 0, 0, tzinfo=timezone.utc)

            async def impl(_payload: dict) -> dict:
                await _cancel_with_cancelled_at(
                    tid,
                    cancelled_at=cancel_ts,
                    cancelled_by="usr_cm4",
                    cancel_reason="retry-path-ts",
                )
                raise RuntimeError("transient error")

            await run_task(
                tid,
                audit_action="server.power_on",
                audit_target_type="server",
                impl=impl,
            )
        finally:
            _runner._schedule_retry = _orig  # type: ignore[assignment]

        assert len(captured_audit) == 1
        ev = captured_audit[0]
        assert ev["details"]["reason"] == "cancelled_midrun"
        assert ev.get("timestamp") == cancel_ts.isoformat()


# ── deleted_midrun audit — доп. поля ─────────────────────────────────────────


class TestDeletedMidrunAuditFields:
    """Дополнительные проверки поля original_action и phase в deleted_midrun.

    Основные deleted_midrun тесты уже в test_worker_fixes_bundle.py.
    Здесь фиксируем детали, которые там не проверены.
    """

    async def test_failure_deleted_midrun_original_action_matches(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """failure deleted_midrun: original_action = переданный audit_action."""
        tid = await make_task(task_kind="power.on", target_server_id="srv_dm1")

        async def impl(_payload: dict) -> dict:
            async with AsyncSessionLocal() as session:
                await session.execute(delete(Task).where(Task.id == tid))
                await session.commit()
            raise RuntimeError("impl died and row deleted")

        _patch_no_retry(monkeypatch)

        await run_task(
            tid,
            audit_action="server.power_off",
            audit_target_type="server",
            impl=impl,
        )

        assert fetch_task  # fixture присутствует
        row = await fetch_task(tid)
        assert row is None

        assert len(captured_audit) == 1
        ev = captured_audit[0]
        assert ev["action"] == "task.deleted_midrun"
        assert ev["details"]["original_action"] == "server.power_off", (
            "original_action должен совпадать с переданным audit_action"
        )
        assert "phase" not in ev["details"], (
            "failure-path deleted_midrun не имеет поля phase"
        )

    async def test_success_deleted_midrun_phase_is_success(
        self, make_task, fetch_task, captured_audit,
    ):
        """success deleted_midrun: details.phase = 'success'."""
        tid = await make_task(task_kind="power.on", target_server_id="srv_dm2")

        async def impl(_payload: dict) -> dict:
            async with AsyncSessionLocal() as session:
                await session.execute(delete(Task).where(Task.id == tid))
                await session.commit()
            return {"power_state": "on"}

        await run_task(
            tid,
            audit_action="ipmi_controller.power_on",
            audit_target_type="ipmi_controller",
            impl=impl,
        )

        row = await fetch_task(tid)
        assert row is None

        assert len(captured_audit) == 1
        ev = captured_audit[0]
        assert ev["action"] == "task.deleted_midrun"
        assert ev["details"]["phase"] == "success"
        assert ev["details"]["original_action"] == "ipmi_controller.power_on"

    async def test_failure_deleted_midrun_error_in_details(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """failure deleted_midrun: error-поле содержит сообщение исключения."""
        tid = await make_task(task_kind="power.on", target_server_id="srv_dm3")

        async def impl(_payload: dict) -> dict:
            async with AsyncSessionLocal() as session:
                await session.execute(delete(Task).where(Task.id == tid))
                await session.commit()
            raise ValueError("unique-error-marker-xyz")

        _patch_no_retry(monkeypatch)

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
        )

        assert len(captured_audit) == 1
        ev = captured_audit[0]
        assert ev["action"] == "task.deleted_midrun"
        error_field = ev["details"].get("error", "")
        assert "unique-error-marker-xyz" in error_field, (
            f"error-поле должно содержать сообщение исключения; got={error_field!r}"
        )


# ── install_authorized_key home guard ─────────────────────────────────────────


class TestInstallAuthorizedKeyHomeGuardEdge:
    """Граничные сценарии home-guard в _install_authorized_key.

    Базовый тест (home="") уже в test_worker_fixes_bundle.py.
    Здесь: home="/", нулевой rc и коректная пропаганда ошибки.
    """

    def _make_client(self, run_results):
        ssh = SshClient(host="10.0.0.5", username="dbos", password="pwd")
        ssh._conn = make_conn(run_results)
        return ssh

    async def test_root_home_guard_propagates_error(self):
        """bash exit 1 при home="/" → SshError с rc=1."""
        ssh = self._make_client([
            run_result("", "user has home=/", 1),
        ])
        with pytest.raises(SshError) as exc:
            await ssh._install_authorized_key(
                target_user="dbos",
                public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKey test@host",
                truncate=False,
                error_code="SSH_AUTHORIZED_KEYS_FAILED",
            )
        assert exc.value.error_code == "SSH_AUTHORIZED_KEYS_FAILED"
        assert exc.value.returncode == 1

    async def test_bash_cmd_contains_both_home_guard_conditions(self):
        """Bash-команда содержит case-guard с пустым home и root-home.

        Guard переехал с `[ -z ... ] || [ ... = / ]` на `case "$home" in
        ""|"/"|...) ...` — список расширен системными псевдо-аккаунтами
        (`/dev`, `/var/empty` и т.п.), см. `_FORBIDDEN_HOMES`.
        """
        ssh = self._make_client([run_result("", "guard triggered", 1)])
        with pytest.raises(SshError):
            await ssh._install_authorized_key(
                target_user="dbos",
                public_key="ssh-rsa AAAAB3NzaC1yc2EAAAADAQABTEST comment@host",
                truncate=True,
                error_code="SSH_PREPARE_FAILED",
            )
        cmd = ssh._conn.run.await_args.args[0]
        assert "case " in cmd, "guard переехал на case-выражение"
        assert '""' in cmd, "пустой home в шаблонах case должен присутствовать"
        assert '"/"' in cmd, "root-home в шаблонах case должен присутствовать"

    async def test_error_code_propagated_correctly(self):
        """error_code из аргумента передаётся в SshError.error_code."""
        ssh = self._make_client([run_result("", "err", 1)])
        with pytest.raises(SshError) as exc:
            await ssh._install_authorized_key(
                target_user="dbos",
                public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKey comment@host",
                truncate=True,
                error_code="SSH_PREPARE_FAILED",
            )
        assert exc.value.error_code == "SSH_PREPARE_FAILED"

    async def test_successful_run_does_not_raise(self):
        """rc=0 → нет SshError, выход чистый."""
        ssh = self._make_client([run_result("", "", 0)])
        await ssh._install_authorized_key(
            target_user="dbos",
            public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKey comment@host",
            truncate=False,
            error_code="SSH_AUTHORIZED_KEYS_FAILED",
        )
        ssh._conn.run.assert_awaited_once()

    async def test_stdin_contains_key_line_for_both_truncate_modes(self):
        """stdin содержит ключ + LF независимо от truncate."""
        key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIStdinCheck test@host"

        for truncate in (True, False):
            ssh = self._make_client([run_result("", "", 0)])
            await ssh._install_authorized_key(
                target_user="dbos",
                public_key=key,
                truncate=truncate,
                error_code="SSH_AUTHORIZED_KEYS_FAILED",
            )
            stdin = ssh._conn.run.await_args.kwargs.get("input", "")
            assert key in stdin, f"key должен быть в stdin (truncate={truncate})"
            # key+LF должен присутствовать в хвосте stdin.
            assert (key + "\n") in stdin


# ── rotated_at after BMC apply — verify-fail path ────────────────────────────


class _BmcVerifyFail:
    """BMC: apply проходит, verify (get_power_state) падает.

    Возвращается из _bmc_factory на каждый вызов — один и тот же экземпляр
    используется и для rotate, и для verify (passwords.py вызывает _get_bmc дважды).
    """

    def __init__(self):
        self._rotate_done = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def rotate_user_password(self, user_id: int, new_password: str) -> None:
        self._rotate_done = True

    async def get_power_state(self) -> str:
        from src.clients.redfish import RedfishError
        raise RedfishError(401, "unauthorized after rotate")

    async def aclose(self) -> None:
        pass


class TestRotatedAtVerifyFail:
    """rotated_at не фиксируется при провале verify-шага.

    Основной тест happy-path и apply-fail уже в test_worker_fixes_bundle.py.
    Здесь добавляем: verify (get_power_state) упал — submit не вызывается,
    задача помечается FAILED.
    """

    async def test_verify_fail_does_not_call_submit(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """verify упал → submit не вызывается → task FAILED."""
        from src.tasks import passwords

        tid = await make_task(
            task_kind="ipmi.rotate_password",
            target_server_id="srv_vf1",
            payload={"server_id": "srv_vf1"},
        )
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Task).where(Task.id == tid).values(max_attempts=1)
            )
            await session.commit()

        async def fake_fetch(server_id, target_department_id=None):
            return {
                "controller_id": "ipm_vf1",
                "kind": "idrac",
                "endpoint_url": "https://bmc.verify.fail",
                "username": "root",
                "password": "old",
            }

        submit_calls: list = []

        async def fake_submit(*args, **kwargs):
            submit_calls.append(args)
            return {"rotated_at": "should-not-reach"}

        bmc_instance = _BmcVerifyFail()

        async def _bmc_factory(creds, *, prefer="redfish"):
            return bmc_instance

        async def noop_breaker_check(host) -> None:
            pass

        async def noop_breaker_record(host) -> None:
            pass

        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_ipmi_credentials",
            fake_fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_ipmi_password",
            fake_submit,
        )
        monkeypatch.setattr("src.tasks.passwords._get_bmc", _bmc_factory)
        monkeypatch.setattr("src.tasks.passwords._breaker.check", noop_breaker_check)
        monkeypatch.setattr(
            "src.tasks.passwords._breaker.record_failure", noop_breaker_record,
        )
        monkeypatch.setattr(
            "src.tasks.passwords._breaker.record_success", noop_breaker_record,
        )

        await passwords.ipmi_rotate_password.original_func(tid)

        t = await fetch_task(tid)
        assert t is not None
        assert t.status == TaskStatus.FAILED, (
            f"verify fail → task должна быть FAILED; got={t.status}"
        )
        assert submit_calls == [], "submit не должен вызываться при fail verify"
        assert bmc_instance._rotate_done, "rotate_user_password должен был вызваться"

    async def test_verify_fail_audit_action_is_rotate(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """verify упал → audit action = ipmi_controller.password_rotate, status=failure."""
        from src.tasks import passwords

        tid = await make_task(
            task_kind="ipmi.rotate_password",
            target_server_id="srv_vf2",
            payload={"server_id": "srv_vf2"},
        )
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Task).where(Task.id == tid).values(max_attempts=1)
            )
            await session.commit()

        async def fake_fetch(server_id, target_department_id=None):
            return {
                "controller_id": "ipm_vf2",
                "kind": "idrac",
                "endpoint_url": "https://bmc.vf2.test",
                "username": "root",
                "password": "old",
            }

        bmc_instance = _BmcVerifyFail()

        async def _bmc_factory(creds, *, prefer="redfish"):
            return bmc_instance

        async def fake_submit(*a, **kw):
            return {"rotated_at": "unused"}

        async def noop_breaker_check(host) -> None:
            pass

        async def noop_breaker_record(host) -> None:
            pass

        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_ipmi_credentials",
            fake_fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_ipmi_password",
            fake_submit,
        )
        monkeypatch.setattr("src.tasks.passwords._get_bmc", _bmc_factory)
        monkeypatch.setattr("src.tasks.passwords._breaker.check", noop_breaker_check)
        monkeypatch.setattr(
            "src.tasks.passwords._breaker.record_failure", noop_breaker_record,
        )
        monkeypatch.setattr(
            "src.tasks.passwords._breaker.record_success", noop_breaker_record,
        )

        await passwords.ipmi_rotate_password.original_func(tid)

        assert len(captured_audit) == 1
        ev = captured_audit[0]
        assert ev["action"] == "ipmi_controller.password_rotate"
        assert ev["status"] == "failure"
