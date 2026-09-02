"""Жёсткий regression-якорь по `_SYSTEM_TASK_KINDS`.

Существующий `test_ipmi_rotate_gone_and_permission_canon.test_system_task_kinds_set_contents`
проверяет содержимое через `in` — пропускает кейс «случайно дописали в set
пользовательский task_kind» или «убрали один из системных». Тут — точное
равенство по содержимому + тип `frozenset`, чтобы любая правка set'а явно
ломала тест и шла через ревью вместе с обновлением `tests.cancel`-логики.

Источник истины по самим именам — autostart task'и server_worker'а
(`server_worker/src/main.py`, decorator `@broker.task("...", schedule=[...])`).
"""
from __future__ import annotations

from src.api.v1.endpoints.tasks import _SYSTEM_TASK_KINDS


_EXPECTED: frozenset[str] = frozenset({
    "system.heartbeat",
    "worker.heartbeat",
    "tasks.sweep_orphaned",
    "tasks.recover_scheduled_retries",
    "tasks.cleanup_completed_old",
    "worker.cleanup_stale_heartbeats",
    "audit_outbox.cleanup_published_old",
    "secrets.reencrypt_lazy",
})


class TestSystemTaskKindsRegression:
    """Любая правка содержимого `_SYSTEM_TASK_KINDS` должна ломать этот тест."""

    def test_exact_set_equality(self):
        assert _SYSTEM_TASK_KINDS == _EXPECTED, (
            "_SYSTEM_TASK_KINDS изменился — обнови _EXPECTED здесь и проверь, "
            "что endpoints/tasks.py::cancel_task всё ещё корректно режет "
            "не-системные kinds. См. server_worker/src/main.py для актуального "
            "списка scheduler-registered task'ов."
        )

    def test_is_frozenset(self):
        # Изменяемый set ломает кэш в FastAPI-router'е (race на конкуррентной
        # модификации) и нарушает «безопасный лукап без копии».
        assert isinstance(_SYSTEM_TASK_KINDS, frozenset)

    def test_no_user_task_kinds_leak_in(self):
        # Защита от обратной ошибки: пользовательские task_kind'ы (power.*,
        # account.*, inventory.*, ipmi.*) не должны попадать сюда — иначе
        # dept-admin сможет отменять чужие операции через системный whitelist.
        leaks = {
            kind for kind in _SYSTEM_TASK_KINDS
            if kind.startswith(("power.", "account.", "ipmi.", "inventory.",
                                "installed_packages.", "users.", "server."))
        }
        assert not leaks, f"user-facing task kinds leaked into _SYSTEM_TASK_KINDS: {leaks}"
