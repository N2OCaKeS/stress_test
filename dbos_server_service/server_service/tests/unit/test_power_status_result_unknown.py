"""Контракт чтения результата `power.status` с reachability-fallback'ом.

Воркерский `power.status` теперь умеет вернуть `power_state="unknown"` (BMC не
ответил и сетевая проба сервера не прошла) либо `power_state="on"` с пометкой
`source` (`bmc` / `ping` / `ssh`), откуда взято состояние. server_service сам
это поле не пишет в `servers.power_state` — результат живёт в `task.result` и
отдаётся оператору через `GET /tasks/{task_id}`. Тесты фиксируют, что путь
чтения task'и:

  * принимает `power_state="unknown"` без падения (поле свободной формы);
  * не теряет `source` ни в detail (полный result), ни в листинге
    (`_summarize_result` оставляет скалярные значения как есть).

Если кто-то позже ужесточит `TaskRead.result` до enum'а power_state или
вырежет «лишние» ключи из summary — эти тесты должны явно сломаться.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.schemas.task import TaskRead
from src.services.tasks import _summarize_result, _to_task_read


def _power_status_row(result: dict) -> dict:
    """Минимальный БД-row power.status-таски (имена колонок dev_server_worker)."""
    return {
        "id": "tsk_power_status_1",
        "task_kind": "power.status",
        "status": "succeeded",
        "target_server_id": "srv_1",
        "target_resource_id": None,
        "created_by": "usr_1",
        "enqueued_at": datetime(2026, 6, 29, 10, 0, tzinfo=timezone.utc),
        "started_at": datetime(2026, 6, 29, 10, 0, 1, tzinfo=timezone.utc),
        "completed_at": datetime(2026, 6, 29, 10, 0, 2, tzinfo=timezone.utc),
        "attempt": 0,
        "priority": 0,
        "last_error": None,
        "result": result,
    }


class TestTaskReadAcceptsPowerStatusOutcomes:
    """TaskRead не отвергает unknown/source и не роняет карточку power.status."""

    @pytest.mark.parametrize("source", ["bmc", "ping", "ssh"])
    def test_unknown_with_source_validates(self, source):
        tr = TaskRead(
            id="tsk_x",
            kind="power.status",
            status="succeeded",
            created_at=datetime(2026, 6, 29, 10, 0, tzinfo=timezone.utc),
            retry_count=0,
            result={"power_state": "unknown", "source": source},
        )
        assert tr.result["power_state"] == "unknown"
        assert tr.result["source"] == source

    def test_on_via_reachability_validates(self):
        tr = TaskRead(
            id="tsk_y",
            kind="power.status",
            status="succeeded",
            created_at=datetime(2026, 6, 29, 10, 0, tzinfo=timezone.utc),
            retry_count=0,
            result={"power_state": "on", "source": "ssh"},
        )
        assert tr.result == {"power_state": "on", "source": "ssh"}


class TestToTaskReadPreservesSource:
    """`_to_task_read` отдаёт result целиком в detail и со скалярами в summary."""

    def test_detail_keeps_full_result(self):
        row = _power_status_row({"power_state": "unknown", "source": "bmc"})
        tr = _to_task_read(row, {}, {}, {}, summarize=False)
        assert tr.result == {"power_state": "unknown", "source": "bmc"}

    def test_summary_keeps_scalar_source_and_state(self):
        row = _power_status_row({"power_state": "on", "source": "ping"})
        tr = _to_task_read(row, {}, {}, {}, summarize=True)
        # power_state и source — скаляры, summary их не режет.
        assert tr.result["power_state"] == "on"
        assert tr.result["source"] == "ping"


class TestSummarizeResultPowerStatus:
    """`_summarize_result` не теряет источник состояния питания."""

    def test_unknown_source_survive_summary(self):
        summarized = _summarize_result({"power_state": "unknown", "source": "ssh"})
        assert summarized == {"power_state": "unknown", "source": "ssh"}
