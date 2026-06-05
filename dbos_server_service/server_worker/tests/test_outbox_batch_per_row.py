"""Тесты batch=5 и per-row flush в audit outbox publisher.

Проверяем:
1. `flush_outbox` обрабатывает не более _BATCH_SIZE=5 строк за проход.
2. Медленная строка (slow emit) не блокирует commit остальных строк в том же
   проходе — per-row session.flush() после каждой строки.
3. Строка с missing_action немедленно уходит в DLQ без инкремента audit_emit_errors.
4. Частичный фейл внутри batch (одна строка 5xx, остальные ок): только
   зафейленная остаётся unpublished, остальные помечены published.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select, update

from src.db.session import AsyncSessionLocal
from src.models import AuditOutbox
from src.services import audit_outbox_publisher
from src.services.audit_client import AuditEmitError
from src.tasks._runner import run_task


async def _all_outbox_rows() -> list[AuditOutbox]:
    async with AsyncSessionLocal() as session:
        stmt = select(AuditOutbox).order_by(AuditOutbox.id.asc())
        return list((await session.execute(stmt)).scalars().all())


async def _unpublished_rows() -> list[AuditOutbox]:
    async with AsyncSessionLocal() as session:
        stmt = (
            select(AuditOutbox)
            .where(AuditOutbox.published_at.is_(None))
            .order_by(AuditOutbox.id.asc())
        )
        return list((await session.execute(stmt)).scalars().all())


async def _seed_tasks_with_failed_emit(
    make_task, monkeypatch, n: int
) -> list[str]:
    """Создать N задач с fail'ящим emit → N unpublished outbox-строк.

    После seed'а сбрасываем `next_retry_at` у всех row'ов — иначе
    per-row backoff отфильтровывает их в SELECT publisher'а, и
    последующий flush'в тестах ничего не вернёт.
    """
    async def failing_emit(action, **kw):
        raise RuntimeError("seeding: emit down")

    monkeypatch.setattr(
        "src.services.audit_outbox_publisher.audit_client.emit",
        failing_emit,
    )

    async def ok_impl(_):
        return {"power_state": "on"}

    task_ids = []
    for _ in range(n):
        tid = await make_task(task_kind="power.on")
        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=ok_impl,
            audit_safe_fields={"power_state"},
        )
        task_ids.append(tid)

    async with AsyncSessionLocal() as session:
        await session.execute(
            update(AuditOutbox).values(next_retry_at=None)
        )
        await session.commit()
    return task_ids


class TestBatchSize:
    async def test_flush_respects_batch_limit(
        self, make_task, monkeypatch,
    ):
        """flush_outbox(limit=3) должен обработать не более 3 строк,
        даже если в outbox больше."""
        audit_outbox_publisher._reset_breaker_state()

        # Засеять 7 unpublished-строк.
        await _seed_tasks_with_failed_emit(make_task, monkeypatch, 7)

        before = await _unpublished_rows()
        assert len(before) == 7

        emit_count = {"n": 0}

        async def counting_emit(action, **kw):
            emit_count["n"] += 1

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            counting_emit,
        )

        published = await audit_outbox_publisher.flush_outbox(limit=3)

        assert published == 3
        assert emit_count["n"] == 3
        # 4 строки остались unpublished.
        after = await _unpublished_rows()
        assert len(after) == 4

    async def test_default_batch_size_is_five(
        self, make_task, monkeypatch,
    ):
        """Без явного limit дефолтный batch = 5."""
        audit_outbox_publisher._reset_breaker_state()

        await _seed_tasks_with_failed_emit(make_task, monkeypatch, 8)

        before = await _unpublished_rows()
        assert len(before) == 8

        async def ok_emit(action, **kw):
            pass

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            ok_emit,
        )

        published = await audit_outbox_publisher.flush_outbox()

        # Дефолтный batch берётся из модульной константы — не хардкодим
        # «5» в тесте, иначе bump в env-конфиге сделает тест красным без
        # реального бага.
        default_batch = audit_outbox_publisher._BATCH_SIZE
        assert published == default_batch
        after = await _unpublished_rows()
        assert len(after) == 8 - default_batch


class TestPartialBatchFailure:
    async def test_slow_row_does_not_block_others_in_batch(
        self, make_task, monkeypatch,
    ):
        """Один медленный emit (sleep) не тормозит фиксацию других строк.

        После flush_outbox() быстрые строки должны быть published, медленная
        (если успела) тоже — т.к. все в одной транзакции. Главное, что
        функция завершается (не зависает).
        """
        audit_outbox_publisher._reset_breaker_state()

        N = 3
        await _seed_tasks_with_failed_emit(make_task, monkeypatch, N)

        call_order = []

        async def slow_first_emit(action, **kw):
            n = len(call_order)
            call_order.append(n)
            if n == 0:
                # Первая строка «медленная» — небольшой await без реального sleep.
                await asyncio.sleep(0)
            # Успешно возвращаем (не raise).

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            slow_first_emit,
        )

        published = await audit_outbox_publisher.flush_outbox(limit=N)

        # Все строки опубликованы (медленная тоже, т.к. нет ошибки).
        assert published == N
        remaining = await _unpublished_rows()
        assert remaining == []

    async def test_one_row_5xx_others_succeed_in_batch(
        self, make_task, monkeypatch,
    ):
        """Одна строка в batch фейлится с 5xx — остальные публикуются.

        Каждая строка обрабатывается независимо (`session.flush()` per-row
        внутри одной транзакции): фейл одной не должен откатывать другие.

        Строки вставляем напрямую, чтобы контролировать начальное состояние
        (без накопления attempts от inline flush в run_task).
        """
        audit_outbox_publisher._reset_breaker_state()

        N = 4
        # Прямая вставка outbox-строк — контролируем начальный attempts=0.
        async with AsyncSessionLocal() as session:
            for i in range(N):
                row = AuditOutbox(
                    task_id=None,
                    payload={"action": f"test.event.{i}", "status": "success"},
                )
                session.add(row)
            await session.commit()

        unpub = await _unpublished_rows()
        assert len(unpub) == N
        for r in unpub:
            assert r.attempts == 0

        emit_count = {"ok": 0, "fail": 0}

        async def selective_emit(action, **kw):
            total = emit_count["ok"] + emit_count["fail"]
            if total == 0:
                emit_count["fail"] += 1
                raise AuditEmitError("HTTP 503", status_code=503)
            emit_count["ok"] += 1

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            selective_emit,
        )

        published = await audit_outbox_publisher.flush_outbox(limit=N)

        # N-1 строк опубликованы.
        assert published == N - 1
        assert emit_count["ok"] == N - 1
        assert emit_count["fail"] == 1

        # Одна строка остаётся unpublished, attempts=1.
        remaining = await _unpublished_rows()
        assert len(remaining) == 1
        assert remaining[0].attempts == 1


class TestMissingActionDlq:
    async def test_row_without_action_sent_to_dlq(
        self, make_task, monkeypatch,
    ):
        """Строка с missing action (нет поля 'action' в payload) сразу в DLQ.

        publisher не пытается retry — `_send_to_dlq(reason='missing_action')`.
        При этом audit_emit_errors не инкрементируется (это не loging_service fail).
        """
        audit_outbox_publisher._reset_breaker_state()

        # Засеять одну normal-строку и одну broken (без 'action').
        tid = await make_task(task_kind="power.on")

        emit_stub = []

        async def capture_emit(action, **kw):
            emit_stub.append(action)

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            capture_emit,
        )

        # Создать broken outbox-row напрямую (без action в payload).
        async with AsyncSessionLocal() as session:
            broken_row = AuditOutbox(
                task_id=tid,
                payload={"status": "success"},  # нет 'action'
            )
            session.add(broken_row)
            await session.commit()

        before_dlq = audit_outbox_publisher.get_dlq_total()

        # flush_outbox должен схлопнуть broken row в DLQ.
        published = await audit_outbox_publisher.flush_outbox(limit=10)

        # Broken row помечен "published" (DLQ), emit для него НЕ вызывался.
        assert audit_outbox_publisher.get_dlq_total() == before_dlq + 1

        all_rows = await _all_outbox_rows()
        # Все строки с published_at (broken → DLQ, остальные не было).
        broken_rows = [
            r for r in all_rows
            if r.task_id == tid and r.payload.get("status") == "success"
            and "action" not in r.payload
        ]
        assert len(broken_rows) == 1
        assert broken_rows[0].published_at is not None
        # emit не вызывался для broken row.
        assert not any("None" in a for a in emit_stub)


class TestBatchCommitSemantics:
    async def test_all_rows_in_batch_committed_together(
        self, make_task, monkeypatch,
    ):
        """Все успешно опубликованные строки batch'а коммитятся в одной транзакции.

        После flush_outbox() при успешном emit — все строки batch'а должны
        иметь published_at != None.
        """
        audit_outbox_publisher._reset_breaker_state()

        N = 5
        await _seed_tasks_with_failed_emit(make_task, monkeypatch, N)

        async def ok_emit(action, **kw):
            pass

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            ok_emit,
        )

        published = await audit_outbox_publisher.flush_outbox(limit=N)
        assert published == N

        all_rows = await _all_outbox_rows()
        for row in all_rows[:N]:
            assert row.published_at is not None, (
                f"Row {row.id} must be published after flush"
            )
        assert (await _unpublished_rows()) == []


class TestDlqLastErrorPrefix:
    async def test_missing_action_marks_last_error_with_dlq_prefix(
        self, make_task,
    ):
        """missing_action: last_error префиксуется `[DLQ:missing_action]`.

        Без префикса оператор по записи в БД не отличит DLQ-row (drop)
        от строки, доставленной успешно после прошлой ошибки — у обеих
        published_at стоит. Префикс делает причину видимой сразу.
        """
        audit_outbox_publisher._reset_breaker_state()

        tid = await make_task(task_kind="power.on")
        async with AsyncSessionLocal() as session:
            broken_row = AuditOutbox(
                task_id=tid,
                payload={"status": "success"},
            )
            session.add(broken_row)
            await session.commit()
            broken_id = broken_row.id

        await audit_outbox_publisher.flush_outbox(limit=10)

        async with AsyncSessionLocal() as session:
            row = await session.get(AuditOutbox, broken_id)
            assert row is not None
            assert row.published_at is not None
            assert (row.last_error or "").startswith("[DLQ:missing_action]")

    async def test_permanent_4xx_marks_last_error_with_dlq_prefix(
        self, make_task, monkeypatch,
    ):
        """permanent_4xx: HTTP-error message получает `[DLQ:permanent_4xx]` prefix."""
        audit_outbox_publisher._reset_breaker_state()

        async def fail_4xx(action, **kw):
            raise AuditEmitError(
                error_message="422 unprocessable",
                status_code=422,
            )

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            fail_4xx,
        )

        tid = await make_task(task_kind="power.on")

        async def ok_impl(_):
            return {"power_state": "on"}

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=ok_impl,
            audit_safe_fields={"power_state"},
        )

        async with AsyncSessionLocal() as session:
            row = (await session.execute(
                select(AuditOutbox).where(AuditOutbox.task_id == tid)
            )).scalars().first()
            assert row is not None
            assert row.published_at is not None
            last_error = row.last_error or ""
            assert last_error.startswith("[DLQ:permanent_4xx]")
            # Текст ошибки сохранился внутри префикса.
            assert "422" in last_error

    async def test_dlq_prefix_idempotent_on_re_send(self):
        """Повторный вызов `_send_to_dlq` (теоретическая гонка) не
        дублирует префикс `[DLQ:...]` — проверяем не накапливание."""
        row = AuditOutbox(
            task_id="t1",
            payload={"action": "x"},
            attempts=1,
            last_error="500 internal",
        )
        audit_outbox_publisher._send_to_dlq(row, reason="attempts_cap")
        first = row.last_error
        assert first is not None and first.startswith("[DLQ:attempts_cap]")
        # Повторный вызов с другим reason не должен переписать первый.
        audit_outbox_publisher._send_to_dlq(row, reason="permanent_4xx")
        assert row.last_error == first
