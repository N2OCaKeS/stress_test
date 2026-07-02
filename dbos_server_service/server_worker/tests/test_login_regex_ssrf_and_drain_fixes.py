"""Regression: login-regex, SSRF-guard и drain/recovery фиксы worker'а.

Покрывает:

* `_LOGIN_RE`/`_GROUP_RE`/`_PATH_RE.fullmatch` — `.match` с anchors `^...$`
  пропускал trailing `\\n`. `"root\\n"` теперь отбивается до chpasswd.
* `_is_blocked_ip` теперь включает `is_unspecified` (`0.0.0.0` / `::`).
* `_recover_due_scheduled_retries_once` при unknown task_kind ставит
  `scheduled_retry_at = now + max(60s, backoff)` — иначе recovery каждую
  минуту молотил MAX_PER_TICK на ту же row до выкатки missing-handler'а.
* `_drain_running_tasks` re-locks row через `SELECT ... FOR UPDATE` и не
  трогает её, если фактический статус остался QUEUED (race-окно с
  `register_running_task` до commit'а mark_running).
* `audit_outbox_publisher._publish_one` через ContextVar держит латч
  «row уже отправлена» — после успешного 2xx record_success-exception не
  приводит к повторному HTTP-roundtrip'у на следующем проходе.
* `_filter_result_for_audit` sentinel-ветки `no_whitelist`/`result_not_dict`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.models import AuditOutbox
from src.repositories import task as task_repo


pytestmark = pytest.mark.asyncio


def _new_id(prefix: str = "tsk_") -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


# ── ssh.py login regex .fullmatch ────────────────────────────────────


class TestLoginRegexFullmatch:
    """`.match` + якоря `^...$` принимали trailing `\\n`. fullmatch — не принимает."""

    def test_login_re_rejects_trailing_newline(self):
        from src.clients.ssh import _LOGIN_RE

        # Контракт: regex покрывает строку целиком, включая отсутствие \n.
        assert _LOGIN_RE.fullmatch("root") is not None
        assert _LOGIN_RE.fullmatch("root\n") is None
        assert _LOGIN_RE.fullmatch("root\nrm") is None

    def test_group_re_rejects_trailing_newline(self):
        from src.clients.ssh import _GROUP_RE

        assert _GROUP_RE.fullmatch("sudo") is not None
        assert _GROUP_RE.fullmatch("sudo\n") is None

    def test_path_re_rejects_trailing_newline(self):
        from src.clients.ssh import _PATH_RE

        assert _PATH_RE.fullmatch("/bin/bash") is not None
        assert _PATH_RE.fullmatch("/bin/bash\n") is None

    async def test_user_exists_rejects_newline_login(self):
        from src.clients.ssh import SshClient, SshError

        ssh = SshClient(host="10.0.0.8", username="dbos", password="pwd")

        with pytest.raises(SshError) as exc:
            await ssh.user_exists("root\n")
        assert exc.value.error_code == "SSH_INVALID_LOGIN"

    async def test_validate_login_rejects_newline(self):
        from src.clients.ssh import SshClient, SshError

        ssh = SshClient(host="10.0.0.8", username="dbos", password="pwd")

        with pytest.raises(SshError) as exc:
            ssh._validate_login("root\n")
        assert exc.value.error_code == "SSH_INVALID_LOGIN"

    def test_safe_path_rejects_newline(self):
        from src.clients.ssh import SshClient, SshError

        ssh = SshClient(host="10.0.0.8", username="dbos", password="pwd")
        with pytest.raises(SshError) as exc:
            ssh._safe_path("/bin/bash\n", field="shell")
        assert exc.value.error_code == "SSH_INVALID_ARG"

    def test_resolve_groups_rejects_newline(self):
        from src.clients.ssh import SshClient, SshError

        ssh = SshClient(host="10.0.0.8", username="dbos", password="pwd")
        with pytest.raises(SshError) as exc:
            ssh._resolve_groups(["sudo\n"], has_sudo=False)
        assert exc.value.error_code == "SSH_INVALID_ARG"


# ── SSRF guard 0.0.0.0 / :: ───────────────────────────────────────────


@pytest.mark.enforce_bmc_ssrf_guard
class TestSsrfGuardUnspecified:
    """`0.0.0.0`/`::` теперь отбиваются (kernel роутит их в loopback)."""

    async def test_ipv4_unspecified_blocked(self):
        from src.clients import BmcEndpointBlockedError, ensure_bmc_host_allowed

        with pytest.raises(BmcEndpointBlockedError) as exc:
            await ensure_bmc_host_allowed("0.0.0.0")
        assert exc.value.details["reason"] == "unspecified"

    async def test_ipv6_unspecified_blocked(self):
        from src.clients import BmcEndpointBlockedError, ensure_bmc_host_allowed

        with pytest.raises(BmcEndpointBlockedError) as exc:
            await ensure_bmc_host_allowed("::")
        assert exc.value.details["reason"] == "unspecified"

    async def test_hostname_resolving_to_unspecified_blocked(self, monkeypatch):
        import socket as _socket

        from src.clients import BmcEndpointBlockedError, ensure_bmc_host_allowed

        def fake_getaddrinfo(host, *args, **kwargs):
            return [(_socket.AF_INET, _socket.SOCK_STREAM, 0, "", ("0.0.0.0", 0))]

        monkeypatch.setattr("src.clients.socket.getaddrinfo", fake_getaddrinfo)
        with pytest.raises(BmcEndpointBlockedError) as exc:
            await ensure_bmc_host_allowed("bmc.example.test")
        assert exc.value.details["reason"] == "unspecified"


# ── unknown task_kind floor backoff ──────────────────────────────────


class TestUnknownTaskKindDefersClaim:
    async def test_unknown_task_kind_defers_at_least_60s(self, monkeypatch):
        """Раньше release_claimed_retry без аргументов ставил scheduled_retry_at=now() —
        следующий тик той же минутой снова брал ту же row. Теперь
        scheduled_retry_at = now + max(60s, backoff)."""
        from src.main import _recover_due_scheduled_retries_once, broker

        now = datetime.now(timezone.utc)
        tid = _new_id()
        async with AsyncSessionLocal() as session:
            await task_repo.create(session, {
                "id": tid,
                "task_kind": "vanished.kind",
                "target_server_id": "srv_vanished",
                "payload": {},
                "status": TaskStatus.QUEUED,
                "attempt": 1,
                "max_attempts": 3,
                "scheduled_retry_at": now - timedelta(seconds=5),
            })
            await session.commit()

        monkeypatch.setattr(broker, "find_task", lambda kind: None)
        before = datetime.now(timezone.utc)
        await _recover_due_scheduled_retries_once()

        async with AsyncSessionLocal() as session:
            row = await task_repo.get_by_id(session, tid)
            assert row is not None
            assert row.status == TaskStatus.QUEUED
            assert row.scheduled_retry_at is not None
            # Минимум 60 секунд в будущем от момента before — окно claim'а
            # за прошедшие миллисекунды учли с запасом.
            delta = (row.scheduled_retry_at - before).total_seconds()
            assert delta >= 55, (
                f"unknown task_kind: scheduled_retry_at сдвинут только на {delta}s, "
                "должен быть ≥60s"
            )


# ── _drain_running_tasks не трогает QUEUED row ───────────────────────


class TestDrainSkipsQueuedMidRace:
    async def test_drain_leaves_queued_row_untouched(self, monkeypatch):
        """Если pre_drain_status == QUEUED, drain не должен перезатирать
        attempt/worker_id/scheduled_retry_at — _runner управляет lifecycle сам.
        """
        import src.main as main_mod
        from src.tasks._runner_state import RUNNING_TASKS
        from taskiq import TaskiqState

        tid = _new_id()
        original_retry_at = datetime.now(timezone.utc) + timedelta(seconds=300)
        async with AsyncSessionLocal() as session:
            await task_repo.create(session, {
                "id": tid,
                "task_kind": "power.on",
                "target_server_id": "srv_drain_race",
                "payload": {"server_id": "srv_drain_race"},
                "status": TaskStatus.QUEUED,
                "attempt": 2,
                "max_attempts": 3,
                "worker_id": "worker_A",
                "scheduled_retry_at": original_retry_at,
                "started_at": datetime.now(timezone.utc),
                "created_by": "usr_test",
                "request_id": "req_test",
            })
            await session.commit()

        RUNNING_TASKS.add(tid)

        fake_settings = MagicMock(wraps=main_mod._settings)
        fake_settings.worker_shutdown_timeout_seconds = 0.01
        monkeypatch.setattr(main_mod, "_settings", fake_settings)

        try:
            await main_mod._drain_running_tasks(TaskiqState())
        finally:
            RUNNING_TASKS.discard(tid)

        # Поля row не перезатёрты.
        async with AsyncSessionLocal() as session:
            row = await task_repo.get_by_id(session, tid)
            assert row is not None
            assert row.status == TaskStatus.QUEUED
            assert row.attempt == 2, (
                f"drain переписал attempt {row.attempt} (ожидали 2)"
            )
            assert row.worker_id == "worker_A", (
                f"drain переписал worker_id {row.worker_id} (ожидали worker_A)"
            )
            assert row.scheduled_retry_at is not None
            # Допускаем мелкое расхождение в микросекундах при сериализации
            # через postgres timestamp(6).
            diff = abs((row.scheduled_retry_at - original_retry_at).total_seconds())
            assert diff < 1, (
                f"drain сдвинул scheduled_retry_at: {row.scheduled_retry_at} "
                f"vs {original_retry_at}"
            )

        # Audit о shutdown'е всё равно должен быть с pre_drain_status=queued.
        async with AsyncSessionLocal() as session:
            from sqlalchemy import select as _select

            rows = (await session.execute(
                _select(AuditOutbox).where(AuditOutbox.task_id == tid)
            )).scalars().all()
            shutdown_rows = [
                r for r in rows
                if (r.payload or {}).get("action") == "task.worker_shutdown"
            ]
            assert len(shutdown_rows) == 1, (
                f"ожидали 1 audit task.worker_shutdown, получили {len(shutdown_rows)}"
            )
            assert shutdown_rows[0].payload["details"]["pre_drain_status"] == TaskStatus.QUEUED


# ── record_success exception не вызывает re-publish ─────────────────


class TestRecordSuccessDuplicationGuard:
    """Защёлка `_published_row_ids`: после успешного emit + flush повторный
    `_publish_one` на ту же row не делает HTTP-roundtrip."""

    async def test_second_call_does_not_re_emit(self, monkeypatch):
        from src.services import audit_outbox_publisher, audit_client, audit_publisher_breaker

        # Один outbox-row в БД, ещё не опубликован.
        async with AsyncSessionLocal() as session:
            row = AuditOutbox(
                task_id=None,
                payload={"action": "test.dup_guard"},
            )
            session.add(row)
            await session.commit()
            row_id = row.id

        emit_calls: list[str] = []

        async def fake_emit(action, **kwargs):
            emit_calls.append(action)

        async def fake_check():
            return None

        async def fake_record_success():
            return None

        monkeypatch.setattr(audit_client, "emit", fake_emit)
        monkeypatch.setattr(audit_publisher_breaker, "check", fake_check)
        monkeypatch.setattr(
            audit_publisher_breaker, "record_success", fake_record_success,
        )

        # Прогон 1: row отправляется, published_at проставляется.
        async with AsyncSessionLocal() as session:
            from sqlalchemy import select as _select
            fresh = (await session.execute(
                _select(AuditOutbox).where(AuditOutbox.id == row_id)
            )).scalar_one()
            res1 = await audit_outbox_publisher._publish_one(session, fresh)
            await session.commit()

        assert res1.was_published is True
        assert len(emit_calls) == 1

        # Симулируем «rollback внешней транзакции потерял published_at» —
        # вручную сбросим колонку, чтобы _publish_one снова увидел row как
        # unpublished. Защёлка `_published_row_ids` в текущем contextvar
        # должна отбить повторный emit.
        async with AsyncSessionLocal() as session:
            fresh = (await session.execute(
                _select(AuditOutbox).where(AuditOutbox.id == row_id)
            )).scalar_one()
            fresh.published_at = None
            await session.commit()

        async with AsyncSessionLocal() as session:
            fresh = (await session.execute(
                _select(AuditOutbox).where(AuditOutbox.id == row_id)
            )).scalar_one()
            res2 = await audit_outbox_publisher._publish_one(session, fresh)
            await session.commit()

        # emit повторно НЕ вызывался; row помечена published.
        assert len(emit_calls) == 1, (
            f"emit вызывался повторно: {emit_calls}"
        )
        assert res2.was_published is False  # уже не was_published — защёлка
        assert res2.closed is True

        async with AsyncSessionLocal() as session:
            fresh = (await session.execute(
                _select(AuditOutbox).where(AuditOutbox.id == row_id)
            )).scalar_one()
            assert fresh.published_at is not None


# ── dispatch_outbox idempotency cap + _filter_result_for_audit sentinels ──


class TestFilterResultForAuditSentinels:
    """Sentinel-ветки (`no_whitelist`, `result_not_dict`, `None`)."""

    def test_none_returns_none(self):
        from src.tasks._runner import _filter_result_for_audit

        assert _filter_result_for_audit(None, {"x"}) is None
        assert _filter_result_for_audit(None, None) is None

    def test_missing_whitelist_returns_no_whitelist_sentinel(self):
        from src.tasks._runner import _filter_result_for_audit

        out = _filter_result_for_audit({"any": "data"}, None)
        assert isinstance(out, dict)
        assert out == {
            "emitted": False,
            "reason": "no_whitelist",
            "result_type": "dict",
        }

    def test_non_dict_result_returns_result_not_dict_sentinel(self):
        from src.tasks._runner import _filter_result_for_audit

        out = _filter_result_for_audit("scalar-str", {"power_state"})
        assert out == {
            "emitted": False,
            "reason": "result_not_dict",
            "result_type": "str",
        }

        out2 = _filter_result_for_audit(["a"], {"x"})
        assert out2["reason"] == "result_not_dict"
        assert out2["result_type"] == "list"

    def test_whitelist_drops_non_whitelisted_keys_silently(self):
        from src.tasks._runner import _filter_result_for_audit

        out = _filter_result_for_audit(
            {"power_state": "on", "secret": "p@ss"}, {"power_state"},
        )
        assert out == {"power_state": "on"}
        # `secret` ушёл без следа — нет placeholder'а.
        assert "secret" not in out
