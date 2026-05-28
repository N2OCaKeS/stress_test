"""Тест: secrets.reencrypt_lazy использует outbox, а не прямой audit_client.emit.

W4-fix: задача перешла с direct emit на transactional outbox через
`task_repo.enqueue_audit + commit + flush_outbox`. Это гарантирует, что при
недоступности loging_service audit-запись не теряется.

Проверяем: после выполнения задачи в таблице `audit_outbox` появляется
соответствующая строка (независимо от состояния emit).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from src.db.session import AsyncSessionLocal
from src.models import AuditOutbox
from src.services import audit_outbox_publisher


async def _all_outbox_rows() -> list[AuditOutbox]:
    async with AsyncSessionLocal() as session:
        stmt = select(AuditOutbox).order_by(AuditOutbox.id.asc())
        return list((await session.execute(stmt)).scalars().all())


async def _invoke_task() -> None:
    from src.main import secrets_reencrypt_lazy
    fn = getattr(secrets_reencrypt_lazy, "original_func", secrets_reencrypt_lazy)
    await fn()


@pytest.fixture(autouse=True)
def _clear_running_tasks():
    from src.tasks._runner_state import RUNNING_TASKS
    RUNNING_TASKS.clear()
    yield
    RUNNING_TASKS.clear()


@pytest.fixture(autouse=True)
def _sync_settings_singleton():
    from src import main as worker_main
    from src.core import config as cfg
    cfg.get_settings.cache_clear()
    fresh = cfg.get_settings()
    original = worker_main._settings
    worker_main._settings = fresh
    yield
    worker_main._settings = original
    cfg.get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_breaker():
    audit_outbox_publisher._reset_breaker_state()
    yield
    audit_outbox_publisher._reset_breaker_state()


class TestSecretsReencryptUsesOutbox:
    async def test_skipped_tick_writes_outbox_row(
        self, monkeypatch,
    ):
        """При skip (active tasks) — audit row пишется в outbox."""
        from src.main import _settings
        from src.services import server_service_client
        from src.tasks._runner_state import RUNNING_TASKS

        monkeypatch.setattr(_settings, "secrets_reencrypt_enabled", True)
        RUNNING_TASKS.add("tsk_fake_001")

        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status",
            AsyncMock(return_value={"remaining": 5}),
        )

        # emit глушим, чтобы проверить именно запись в outbox, а не доставку.
        async def failing_emit(action, **kw):
            raise RuntimeError("loging down")

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            failing_emit,
        )

        await _invoke_task()

        rows = await _all_outbox_rows()
        assert len(rows) >= 1
        tick_rows = [r for r in rows if r.payload.get("action") == "secrets.reencrypt_tick"]
        assert len(tick_rows) == 1
        # skipped=True в details.
        assert tick_rows[0].payload["details"]["skipped"] is True
        # Row остался unpublished (emit пал), но записан — это ключевой инвариант.
        assert tick_rows[0].published_at is None

    async def test_happy_path_audit_row_written_to_outbox(
        self, monkeypatch,
    ):
        """После успешного batch — outbox-row с reencrypt_tick создан."""
        from src.main import _settings
        from src.services import server_service_client

        monkeypatch.setattr(_settings, "secrets_reencrypt_enabled", True)
        monkeypatch.setattr(_settings, "secrets_reencrypt_batch_size", 10)

        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status",
            AsyncMock(return_value={
                "remaining": 50,
                "total": 100,
                "active_version": 2,
                "by_version": {"1": 50},
            }),
        )
        monkeypatch.setattr(
            server_service_client, "trigger_secrets_reencrypt_batch",
            AsyncMock(return_value={"processed": 10, "errors": 0}),
        )

        # Нейтральный emit (ничего не делает).
        async def noop_emit(action, **kw):
            pass

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            noop_emit,
        )

        await _invoke_task()

        rows = await _all_outbox_rows()
        tick_rows = [r for r in rows if r.payload.get("action") == "secrets.reencrypt_tick"]
        assert len(tick_rows) == 1
        details = tick_rows[0].payload["details"]
        assert details["processed"] == 10
        assert details["remaining_before"] == 50
        assert details["active_version"] == 2

    async def test_direct_emit_not_called_secrets_task(
        self, monkeypatch,
    ):
        """secrets.reencrypt_lazy НЕ должен напрямую вызывать audit_client.emit.

        Все audit-события идут через outbox (enqueue_audit + flush).
        """
        from src.main import _settings
        from src.services import server_service_client

        monkeypatch.setattr(_settings, "secrets_reencrypt_enabled", True)
        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status",
            AsyncMock(return_value={
                "remaining": 0, "total": 5, "active_version": 2, "by_version": {"2": 5},
            }),
        )

        # Если бы код вызвал emit напрямую (не через outbox flush),
        # этот assert сломал бы тест.
        direct_calls = []

        # Монки на сам audit_client.emit модуля main (там нет прямого импорта).
        # Монки только для flush-пути (через publisher), а не для secrets модуля напрямую.
        async def record_emit(action, **kw):
            direct_calls.append(action)

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            record_emit,
        )
        # Убеждаемся, что secrets.reencrypt_lazy не вызывает audit_client.emit напрямую.
        # Фильтруем строки кода (без docstring и комментариев).
        import src.main as main_mod
        import inspect
        src_lines = inspect.getsource(main_mod.secrets_reencrypt_lazy).splitlines()
        code_lines = [
            ln for ln in src_lines
            if ln.strip() and not ln.strip().startswith(("#", '"""', "'''"))
        ]
        code_text = "\n".join(code_lines)
        assert "audit_client.emit(" not in code_text, (
            "secrets.reencrypt_lazy must not call audit_client.emit directly — use outbox"
        )

        await _invoke_task()
