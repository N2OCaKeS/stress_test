"""Документационный тест: TaskStatus.CANCELLED существует, но не выставляется.

`TaskStatus.CANCELLED` — зарезервированный член перечисления для будущего
operator cancel из UI. Тест фиксирует:
1. Член существует и имеет ожидаемое строковое значение.
2. Ни один путь в `_runner.run_task` не выставляет этот статус.
3. Репозиторий задач не имеет функции mark_cancelled (пока).
"""

from __future__ import annotations

import pytest

from src.core.constants import TaskStatus


class TestCancelledMemberExists:
    def test_cancelled_member_in_enum(self):
        assert TaskStatus.CANCELLED == "cancelled"

    def test_cancelled_is_str(self):
        assert isinstance(TaskStatus.CANCELLED, str)

    def test_cancelled_distinct_from_other_statuses(self):
        for status in (
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
        ):
            assert TaskStatus.CANCELLED != status


class TestCancelledNotSetByRunner:
    def test_runner_does_not_import_cancelled(self):
        from src.tasks import _runner
        import inspect
        src = inspect.getsource(_runner)
        # Ни mark_cancelled, ни TaskStatus.CANCELLED в коде runner'а нет.
        assert "mark_cancelled" not in src
        assert "CANCELLED" not in src

    def test_task_repo_has_no_mark_cancelled(self):
        from src.repositories import task as task_repo
        assert not hasattr(task_repo, "mark_cancelled")

    def test_enum_all_values(self):
        values = {s.value for s in TaskStatus}
        assert values == {"queued", "running", "succeeded", "failed", "cancelled"}
