"""Unit-тест: race-fallback в `worker_client.cancel_task` отдаёт актуальный
`previous_status`, а не устаревший из первого SELECT FOR UPDATE.

Сценарий: SELECT FOR UPDATE видит row в `running`, UPDATE возвращает
rowcount=0 (worker успел finalize'нуть task в `succeeded` между нашими
двумя запросами). Code-path перечитывает фактический статус и должен
вернуть его caller'у — иначе и audit, и response покажут `previous_status:
running`, хотя реально row уже `succeeded`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest

from src.services import worker_client


class _FakeResult:
    """Имитирует sqlalchemy `Result` с `.first()` и `.rowcount`."""

    def __init__(self, row: tuple | None = None, rowcount: int = 0) -> None:
        self._row = row
        self.rowcount = rowcount

    def first(self) -> tuple | None:
        return self._row


class _FakeSession:
    """Имитирует AsyncSession для cancel_task: три ожидаемых execute()."""

    def __init__(self, results: list[_FakeResult]) -> None:
        self._results = list(results)
        self.commits = 0
        self.rollbacks = 0
        self.executed_sql: list[str] = []

    async def execute(self, statement, params=None) -> _FakeResult:
        sql = str(statement)
        self.executed_sql.append(sql)
        if not self._results:
            raise AssertionError(f"Unexpected execute(): {sql[:80]}")
        return self._results.pop(0)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _make_factory(session: _FakeSession):
    """Собирает fake session-factory с поддержкой `async with` контекста."""

    @asynccontextmanager
    async def _ctx():
        yield session

    def _factory() -> Any:
        return _ctx()

    return _factory


@pytest.fixture
def patch_engine_factory(monkeypatch):
    def _apply(session: _FakeSession) -> None:
        monkeypatch.setattr(
            worker_client, "_engine_factory", lambda: _make_factory(session),
        )

    return _apply


class TestCancelTaskRacePreviousStatus:
    async def test_race_returns_refreshed_status(self, patch_engine_factory):
        """SELECT FOR UPDATE → running, UPDATE rowcount=0, re-SELECT → succeeded.

        Финальный response.previous_status должен быть 'succeeded', а не 'running'.
        """
        session = _FakeSession(
            results=[
                _FakeResult(row=("running", "power.on", "srv_1")),
                _FakeResult(rowcount=0),
                _FakeResult(row=("succeeded",)),
            ],
        )
        patch_engine_factory(session)

        result = await worker_client.cancel_task(
            task_id_value="tsk_race",
            cancelled_by="usr_admin",
            cancel_reason="caller too late",
        )

        assert result == {
            "found": True,
            "cancelled": False,
            "previous_status": "succeeded",
            "task_kind": "power.on",
            "target_server_id": "srv_1",
        }
        # Должны были откатить транзакцию (UPDATE затронул 0 строк).
        assert session.rollbacks == 1
        assert session.commits == 0

    async def test_race_re_select_returns_none_falls_back(self, patch_engine_factory):
        """Edge-case: row исчез к моменту re-SELECT.

        Тогда `previous_status` фоллбэчит на значение из первого SELECT —
        это лучше, чем None в audit details.
        """
        session = _FakeSession(
            results=[
                _FakeResult(row=("queued", "inventory.sync", None)),
                _FakeResult(rowcount=0),
                _FakeResult(row=None),
            ],
        )
        patch_engine_factory(session)

        result = await worker_client.cancel_task(
            task_id_value="tsk_vanished",
            cancelled_by="usr_admin",
            cancel_reason=None,
        )

        assert result["previous_status"] == "queued"
        assert result["cancelled"] is False
        assert result["found"] is True
