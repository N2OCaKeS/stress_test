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
            "server.prepare", "server.prepare_for_test", "server.stand_setup",
            "server.rotate_management_creds",
            "server.astra_update", "server.install_node_exporter",
            "installed_packages.list", "installed_packages.install",
            "installed_packages.remove", "installed_packages.update",
            "management_user_sync",
            "vms_hub.prepare", "vms_hub.teardown", "vm.create", "vm.power",
            "vm.status", "vm.delete", "vm.set_autostart", "vm.console_prep",
            "vm.prepare", "vm.install_node_exporter", "vm.set_network",
            "vm.update", "vm.disk_attach", "vm.disk_delete", "vm.disk_resize",
            "vm.snapshot_create", "vm.snapshot_delete", "vm.snapshot_revert",
            "vm.prepare_for_test", "vm.stand_setup",
            "vm.astra_update", "vm.allta_update", "vm.passwd",
            "vm.list_packages",
            "vm.install_packages", "vm.remove_packages", "vm.update_packages",
            "vm.inventory_sync", "vm.users_inventory",
            "vm.account_provision", "vm.account_update_on_host",
            "vm.account_deprovision",
            "box.download",
            "acs.snapshot_create", "acs.snapshot_restore",
        }

    def test_dot_namespaced(self):
        # `management_user_sync` — единственный legacy-лейбл без точки (исторически
        # underscore-namespaced); остальные kind'ы держат `<object>.<verb>`.
        for tk in TaskKind:
            if tk is TaskKind.MANAGEMENT_USER_SYNC:
                continue
            assert "." in tk.value

    def test_is_str_enum(self):
        assert isinstance(TaskKind.POWER_ON, str)
        assert TaskKind.POWER_ON == "power.on"

    @pytest.mark.parametrize("kind", list(TaskKind))
    def test_string_equality(self, kind):
        assert kind == kind.value


# Внутренние / maintenance-таски брокера, которые НЕ диспатчатся как hardware/
# server-операции и сознательно не входят в `TaskKind`: scheduler-периодика,
# outbox-движок, heartbeat'ы. `TaskKind` — каталог именно server-dispatch'абельных
# kind'ов, не всех зарегистрированных тасков.
_NON_DISPATCH_BROKER_TASKS = frozenset({
    "auto_inventory.sweep",
    "power.sweep",
    "audit_outbox.cleanup_published_old",
    "dispatch_outbox.cleanup_old",
    "dispatch_outbox.poll",
    "internal.outbox_re_attempt",
    "system.heartbeat",
    "tasks.cleanup_completed_old",
    "tasks.recover_scheduled_retries",
    "tasks.sweep_orphaned",
    "vms.reconcile_failed_creates",
    "vms.status_sweep",
    "worker.cleanup_stale_heartbeats",
    "worker.heartbeat",
})


class TestTaskKindBrokerDrift:
    """Статическая детекция дрейфа между `TaskKind` и broker-декораторами.

    Декораторы `@broker.task("...")` держат литералы (wiring на enum требует
    общего sdk-модуля), поэтому несовпадение enum'а и реальных тасков иначе
    ловится только в рантайме при dispatch'е unknown kind'а. Эти тесты делают
    рассинхрон видимым на этапе тестов.
    """

    def _broker_task_names(self) -> set[str]:
        from src.main import broker
        return set(broker.get_all_tasks().keys())

    def test_every_task_kind_has_broker_handler(self):
        """Каждое значение `TaskKind` зарегистрировано как broker-таск.

        Ловит переименование/удаление декоратора при живом enum-члене
        (dispatch такого kind'а упёрся бы в `broker.find_task → None`).
        """
        names = self._broker_task_names()
        missing = {tk.value for tk in TaskKind} - names
        assert not missing, f"TaskKind без broker-handler'а: {sorted(missing)}"

    def test_every_dispatch_broker_task_is_in_task_kind(self):
        """Каждый dispatch'абельный broker-таск (не maintenance) есть в `TaskKind`.

        Ловит добавление нового `@broker.task` без обновления каталога —
        новый kind, который server_service сможет задиспатчить, но enum о нём
        не знает.
        """
        names = self._broker_task_names()
        dispatch_names = names - _NON_DISPATCH_BROKER_TASKS
        kinds = {tk.value for tk in TaskKind}
        undeclared = dispatch_names - kinds
        assert not undeclared, (
            "broker-таски без TaskKind-члена: "
            f"{sorted(undeclared)} (добавь в TaskKind или в "
            "_NON_DISPATCH_BROKER_TASKS, если это maintenance)"
        )


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
