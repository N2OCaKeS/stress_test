"""Startup-сигнал при выключенном BMC TLS-verify (`REDFISH_VERIFY_TLS=false`).

Probe-cascade эмитит `bmc.tls_downgrade` только на фактическом переходе на
менее защищённый канал. Когда verify выключен с самого старта, cascade
отвечает на первом шаге и ничего не пишет — `_warn_on_redfish_verify_disabled`
закрывает разрыв: WARNING в любом окружении + audit-событие
`bmc.tls_verify_disabled` в production/staging.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from src.db.session import AsyncSessionLocal
from src.models import AuditOutbox

pytestmark = pytest.mark.asyncio


async def _count_verify_disabled_rows() -> int:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(AuditOutbox))).scalars().all()
    return sum(
        1 for r in rows if r.payload.get("action") == "bmc.tls_verify_disabled"
    )


class TestRedfishVerifyDisabledStartupHook:
    async def test_warning_logged_when_verify_disabled(self, monkeypatch, caplog):
        import src.main as main_mod
        from taskiq import TaskiqState

        fake_settings = MagicMock()
        fake_settings.redfish_verify_tls = False
        fake_settings.app_env = "local"
        monkeypatch.setattr(main_mod, "_settings", fake_settings)

        with caplog.at_level(logging.WARNING):
            await main_mod._warn_on_redfish_verify_disabled(TaskiqState())

        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "REDFISH_VERIFY_TLS is disabled" in m and "app_env=local" in m
            for m in warnings
        )

    async def test_silent_when_verify_enabled(self, monkeypatch, caplog):
        import src.main as main_mod
        from taskiq import TaskiqState

        fake_settings = MagicMock()
        fake_settings.redfish_verify_tls = True
        fake_settings.app_env = "production"
        monkeypatch.setattr(main_mod, "_settings", fake_settings)

        before = await _count_verify_disabled_rows()
        with caplog.at_level(logging.WARNING):
            await main_mod._warn_on_redfish_verify_disabled(TaskiqState())

        assert not any(
            "REDFISH_VERIFY_TLS is disabled" in r.getMessage()
            for r in caplog.records
        )
        # verify включён — ни WARNING, ни audit-row.
        assert await _count_verify_disabled_rows() == before

    @pytest.mark.parametrize("env", ["production", "staging"])
    async def test_audit_emitted_in_prod_like_envs(self, monkeypatch, env):
        import src.main as main_mod
        from taskiq import TaskiqState

        fake_settings = MagicMock()
        fake_settings.redfish_verify_tls = False
        fake_settings.app_env = env
        monkeypatch.setattr(main_mod, "_settings", fake_settings)

        before = await _count_verify_disabled_rows()
        await main_mod._warn_on_redfish_verify_disabled(TaskiqState())
        after = await _count_verify_disabled_rows()
        assert after == before + 1

        async with AsyncSessionLocal() as session:
            rows = (await session.execute(select(AuditOutbox))).scalars().all()
        matching = [
            r for r in rows
            if r.payload.get("action") == "bmc.tls_verify_disabled"
            and r.payload.get("details", {}).get("app_env") == env
        ]
        assert matching, f"audit-row для env={env} не записан"
        payload = matching[-1].payload
        assert payload["severity"] == "WARNING"
        assert payload["status"] == "success"
        assert payload["actor_type"] == "service"
        assert payload["target_type"] == "ipmi_controller"
        assert payload["details"]["redfish_verify_tls"] is False

    @pytest.mark.parametrize("env", ["local", "dev", "test"])
    async def test_no_audit_in_non_prod(self, monkeypatch, env):
        import src.main as main_mod
        from taskiq import TaskiqState

        fake_settings = MagicMock()
        fake_settings.redfish_verify_tls = False
        fake_settings.app_env = env
        monkeypatch.setattr(main_mod, "_settings", fake_settings)

        before = await _count_verify_disabled_rows()
        await main_mod._warn_on_redfish_verify_disabled(TaskiqState())
        # WARNING пишется, но audit-событие в non-prod не эмитим.
        assert await _count_verify_disabled_rows() == before

    async def test_audit_failure_does_not_raise(self, monkeypatch):
        """БД/outbox недоступны на старте — хук логирует и не падает."""
        import src.main as main_mod
        from taskiq import TaskiqState

        fake_settings = MagicMock()
        fake_settings.redfish_verify_tls = False
        fake_settings.app_env = "production"
        monkeypatch.setattr(main_mod, "_settings", fake_settings)

        def boom_session(*a, **kw):
            raise RuntimeError("db down")

        monkeypatch.setattr(main_mod, "AsyncSessionLocal", boom_session)

        # Не должно бросить наружу.
        await main_mod._warn_on_redfish_verify_disabled(TaskiqState())
