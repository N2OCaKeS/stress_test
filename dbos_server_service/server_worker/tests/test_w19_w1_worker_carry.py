"""W19-W1 — closeouts оставшихся P3/P4 carry для server_worker.

Покрывает:
  * `_safe_flush_outbox` пробрасывает `asyncio.CancelledError`;
  * `AUDIT_EVENTS.md` перечисляет handler-actions и worker-lifecycle
    события (`task.worker_shutdown`, `task.worker_orphaned`,
    `secrets.reencrypt_tick`, `audit.outbox_reattempt_manual`,
    `installed_packages.list`);
  * docstring `ipmi_rotate_password` фиксирует, что `rotated_at`
    берётся из worker-clock и о ±60s окне `verify_in_future`.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from src.services import audit_outbox_publisher
from src.tasks._runner import _safe_flush_outbox


_WORKER_ROOT = Path(__file__).resolve().parents[1]
_AUDIT_EVENTS_MD = _WORKER_ROOT / "AUDIT_EVENTS.md"


class TestSafeFlushOutboxCancelledPassThrough:
    """`_safe_flush_outbox` пропускает `CancelledError` наружу."""

    async def test_cancelled_error_propagates(self, monkeypatch):
        """Если publisher словил cancel — _safe_flush_outbox его пробрасывает."""

        async def cancelled_flush() -> None:
            raise asyncio.CancelledError()

        monkeypatch.setattr(audit_outbox_publisher, "flush_outbox", cancelled_flush)

        with pytest.raises(asyncio.CancelledError):
            await _safe_flush_outbox()

    async def test_regular_exception_swallowed(self, monkeypatch):
        """RuntimeError (и любой Exception) по-прежнему глотается."""
        calls: list[int] = []

        async def boom_flush() -> None:
            calls.append(1)
            raise RuntimeError("publisher down")

        monkeypatch.setattr(audit_outbox_publisher, "flush_outbox", boom_flush)

        # Не должно бросить наружу.
        await _safe_flush_outbox()
        assert calls == [1]

    async def test_external_cancel_via_task_cancel(self, monkeypatch):
        """Если родительский Task отменяют пока publisher держит await —
        cancel должен дойти до event-loop'а через `_safe_flush_outbox`.
        """
        publisher_started = asyncio.Event()

        async def slow_flush() -> None:
            publisher_started.set()
            await asyncio.sleep(10)  # явно отдаём loop, ждём cancel снаружи

        monkeypatch.setattr(audit_outbox_publisher, "flush_outbox", slow_flush)

        task = asyncio.create_task(_safe_flush_outbox())
        await publisher_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.skipif(
    not _AUDIT_EVENTS_MD.exists(),
    reason="AUDIT_EVENTS.md not mounted in test container (CI-only check)",
)
class TestAuditEventsDocCoverage:
    """`AUDIT_EVENTS.md` перечисляет все worker-уровневые action'ы.

    Source-of-truth — литералы `"action": "..."` в `src/main.py`,
    `src/cli/outbox.py` и `audit_action="..."` в `src/tasks/*.py`.
    Drift между кодом и доками — частая причина непонятных аудитов;
    тест ловит drift на CI.
    """

    @pytest.mark.parametrize("action", [
        # Lifecycle worker'а (main.py)
        "task.worker_shutdown",
        "task.worker_orphaned",
        "secrets.reencrypt_tick",
        # CLI (cli/outbox.py)
        "audit.outbox_reattempt_manual",
        # Handler-уровень (tasks/*.py)
        "server.power_on",
        "server.power_off",
        "server.power_reboot",
        "server.power_status",
        "server.inventory_sync",
        "server.prepare",
        "installed_packages.list",
        "server_account.provision",
        "server_account.update_on_host",
        "server_account.deprovision",
        "server_account.users_inventory",
        "server_account.password_rotate",
        "ipmi_controller.password_rotate",
    ])
    def test_action_documented(self, action):
        text = _AUDIT_EVENTS_MD.read_text(encoding="utf-8")
        assert action in text, (
            f"action {action!r} эмитится в коде, но не упомянут в "
            f"AUDIT_EVENTS.md. Допиши таблицу в worker'е."
        )

    def test_runner_meta_reasons_documented(self):
        """Все `details.reason` из `_runner.py` упомянуты в доках."""
        text = _AUDIT_EVENTS_MD.read_text(encoding="utf-8")
        for reason in (
            "task_not_found",
            "duplicate_dispatch",
            "task_cancelled",
            "cancelled_midrun",
            "task_deleted_midrun",
        ):
            assert reason in text, f"reason={reason!r} не в AUDIT_EVENTS.md"


class TestRotatedAtDocsNTPNote:
    """В docstring/комментарии `ipmi_rotate_password` явная пометка про
    NTP-skew и server-clock как источник истины.
    """

    def test_ntp_skew_note_present(self):
        passwords_src = (_WORKER_ROOT / "src" / "tasks" / "passwords.py").read_text(
            encoding="utf-8"
        )
        # Ищем оба ключевых маркера: worker-clock как источник rotated_at
        # и упоминание verify_in_future окна.
        assert "worker-clock" in passwords_src or "worker_clock" in passwords_src, (
            "пометка про worker-clock как источник rotated_at не найдена"
        )
        assert "verify_in_future" in passwords_src, (
            "ссылка на verify_in_future ±60s окно не найдена"
        )


class TestBmcHostIPv6Verified:
    """Re-verify W14 carry: `extract_bmc_host` парсит IPv6 в `[...]`."""

    @pytest.mark.parametrize("endpoint,expected", [
        ("https://[2001:db8::1]:443", "[2001:db8::1]:443"),
        ("https://[::1]", "[::1]"),
        ("[fe80::1]:623", "[fe80::1]:623"),
        ("https://user:pass@[2001:db8::1]:8443", "[2001:db8::1]:8443"),
    ])
    def test_extract_ipv6_host(self, endpoint, expected):
        from src.tasks._bmc_helpers import extract_bmc_host
        assert extract_bmc_host(endpoint) == expected


class TestRunnerSafeFlushDocstring:
    """`_safe_flush_outbox` docstring явно говорит про CancelledError-pass."""

    def test_docstring_mentions_cancelled(self):
        runner_src = (_WORKER_ROOT / "src" / "tasks" / "_runner.py").read_text(
            encoding="utf-8"
        )
        # Должна быть либо строка про CancelledError-passthrough в docstring,
        # либо явный `except asyncio.CancelledError: raise` блок.
        has_except = re.search(
            r"except\s+asyncio\.CancelledError\s*:\s*\n\s*(?:#[^\n]*\n\s*)*raise\b",
            runner_src,
        )
        assert has_except is not None, (
            "в `_safe_flush_outbox` нет `except asyncio.CancelledError: raise` — "
            "shutdown / SIGTERM не пройдёт через publisher.flush_outbox"
        )
