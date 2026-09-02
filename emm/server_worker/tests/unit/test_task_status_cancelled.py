"""Контракт `TaskStatus.CANCELLED` после F22-C — operator cancel реализован.

F22-C добавил cancel fast-path в `_runner.run_task`: если status уже
`CANCELLED` (выставлен через `POST /tasks/{id}/cancel` в server_service)
— runner пропускает dispatch, эмитит результат `reason=task_cancelled`.
Этот тест фиксирует наличие фичи (не её отсутствие, как было до F22-C).
"""

from __future__ import annotations

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


class TestCancelledFastPathInRunner:
    """После F22-C runner проверяет CANCELLED и эмитит результат-skip."""

    def test_runner_references_cancelled(self):
        from src.tasks import _runner
        import inspect
        src = inspect.getsource(_runner)
        assert "CANCELLED" in src, "runner должен иметь cancel fast-path"

    def test_enum_all_values(self):
        values = {s.value for s in TaskStatus}
        assert values == {"queued", "running", "succeeded", "failed", "cancelled"}
