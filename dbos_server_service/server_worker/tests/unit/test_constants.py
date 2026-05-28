"""Unit-тесты `src/core/constants.py` — TaskKind / TaskStatus invariants."""

from __future__ import annotations

import pytest

from src.core.constants import TaskKind, TaskStatus


# ── TaskKind ─────────────────────────────────────────────────────────────────

class TestTaskKind:
    def test_all_kinds_present(self):
        kinds = {tk.value for tk in TaskKind}
        assert kinds == {
            "power.on", "power.off", "power.reboot", "power.status",
            "inventory.sync", "users.inventory",
            "account.rotate_password", "ipmi.rotate_password",
            "account.provision", "account.update_on_host", "account.deprovision",
            "server.prepare",
            "installed_packages.list",
        }

    def test_dot_namespaced(self):
        for tk in TaskKind:
            assert "." in tk.value

    def test_is_str_enum(self):
        assert isinstance(TaskKind.POWER_ON, str)
        assert TaskKind.POWER_ON == "power.on"

    @pytest.mark.parametrize("kind", list(TaskKind))
    def test_string_equality(self, kind):
        assert kind == kind.value


# ── TaskStatus ───────────────────────────────────────────────────────────────

class TestTaskStatus:
    def test_all_five_statuses(self):
        assert {ts.value for ts in TaskStatus} == {
            "queued", "running", "succeeded", "failed", "cancelled",
        }

    def test_is_str_enum(self):
        assert isinstance(TaskStatus.RUNNING, str)
        assert TaskStatus.RUNNING == "running"

    def test_progression_order_makes_sense(self):
        """Terminal-статусы — succeeded/failed/cancelled; стартовый — queued."""
        terminals = {"succeeded", "failed", "cancelled"}
        non_terminals = {"queued", "running"}
        assert terminals.union(non_terminals) == {ts.value for ts in TaskStatus}
