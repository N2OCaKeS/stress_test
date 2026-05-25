"""Тесты transactional outbox для audit и whitelist'а details.result.

Покрывает два фикса:

  1. **lifecycle ломает audit-инвариант** — `_runner.run_task` теперь
     пишет audit-row в `audit_outbox` *в той же транзакции*, что
     mark_succeeded/mark_failed. Publisher отправляет в loging_service
     и помечает `published_at`. Если publisher падает (SIGKILL,
     network error) — outbox-row остаётся, следующий проход подхватит.

  2. **audit details.result без whitelist** — handler объявляет
     `AUDIT_SAFE_FIELDS`, всё прочее в audit не уходит. Если whitelist
     не объявлен — в audit идёт sentinel `{emitted: False, reason: ...}`,
     секреты не уезжают.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.models import AuditOutbox
from src.services import audit_outbox_publisher
from src.services.audit_client import AuditEmitError
from src.tasks._runner import run_task


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _all_outbox_rows() -> list[AuditOutbox]:
    async with AsyncSessionLocal() as session:
        stmt = select(AuditOutbox).order_by(AuditOutbox.id.asc())
        return list((await session.execute(stmt)).scalars().all())


async def _unpublished_outbox_rows() -> list[AuditOutbox]:
    async with AsyncSessionLocal() as session:
        stmt = (
            select(AuditOutbox)
            .where(AuditOutbox.published_at.is_(None))
            .order_by(AuditOutbox.id.asc())
        )
        return list((await session.execute(stmt)).scalars().all())


# ── 1. Outbox: запись + publisher ────────────────────────────────────────────

class TestOutboxHappyPath:
    """После успешного `run_task` outbox-row создан и опубликован."""

    async def test_success_run_creates_published_row(
        self, make_task, fetch_task, captured_audit,
    ):
        tid = await make_task(task_kind="power.on", payload={"server_id": "srv_1"})

        async def impl(_):
            return {"power_state": "on"}

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
            audit_safe_fields={"power_state"},
        )

        # Task в БД succeeded.
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED

        # Outbox: ровно одна строка, published_at != None (publisher
        # вызван в _safe_flush_outbox после commit'а).
        rows = await _all_outbox_rows()
        assert len(rows) == 1
        row = rows[0]
        assert row.task_id == tid
        assert row.published_at is not None
        assert row.attempts == 0
        assert row.payload["action"] == "server.power_on"
        assert row.payload["status"] == "success"

        # И сам emit состоялся — captured_audit получил событие.
        assert len(captured_audit) == 1
        assert captured_audit[0]["action"] == "server.power_on"

    async def test_failure_run_creates_published_row(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        # Чтобы зафиксировать terminal-failure (status=FAILED), создаём
        # task с max_attempts=1. Семантика «failure → pending для retry»
        # покрыта в test_p1_retry_and_shutdown.py::TestRetryOnFailure.
        from sqlalchemy import update

        from src.models import Task
        from src.tasks import _runner

        tid = await make_task(task_kind="power.on")
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Task).where(Task.id == tid).values(max_attempts=1)
            )
            await session.commit()

        # _schedule_retry не должен дёргаться (max_attempts=1) — но на
        # всякий случай заглушим, чтобы не утекало.
        async def noop(*args, **kwargs):
            pass

        monkeypatch.setattr(_runner, "_schedule_retry", noop)

        async def boom(_):
            raise RuntimeError("kaboom")

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=boom,
        )

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED

        rows = await _all_outbox_rows()
        assert len(rows) == 1
        assert rows[0].payload["status"] == "failure"
        assert rows[0].payload["details"]["error"].startswith("RuntimeError")
        assert rows[0].published_at is not None

    async def test_task_not_found_creates_outbox_row(self, captured_audit):
        await run_task(
            "tsk_phantom",
            audit_action="server.power_on",
            audit_target_type="server",
            impl=lambda p: None,  # не вызовется
        )

        rows = await _all_outbox_rows()
        assert len(rows) == 1
        assert rows[0].task_id is None  # Task-row нет
        assert rows[0].payload["details"]["reason"] == "task_not_found"
        assert rows[0].published_at is not None


class TestOutboxRetryOnPublisherFailure:
    """Если publisher падает (network down) — outbox-row остаётся
    unpublished и подхватится следующим проходом."""

    async def test_emit_failure_leaves_row_unpublished(
        self, make_task, fetch_task, monkeypatch,
    ):
        tid = await make_task(task_kind="power.on")

        # Force emit() to fail — симулируем разрыв сети до loging_service.
        async def failing_emit(action, **kw):
            raise RuntimeError("network down")

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            failing_emit,
        )

        async def impl(_):
            return {"power_state": "on"}

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
            audit_safe_fields={"power_state"},
        )

        # Task в БД — succeeded (commit прошёл независимо от emit).
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED

        # Outbox: row создан, но published_at пуст, attempts инкрементирован.
        rows = await _unpublished_outbox_rows()
        assert len(rows) == 1
        assert rows[0].published_at is None
        assert rows[0].attempts == 1
        assert "RuntimeError" in (rows[0].last_error or "")

    async def test_emit_failure_redacts_url_credentials_in_last_error(
        self, make_task, fetch_task, monkeypatch,
    ):
        """Если emit бросает ошибку с URL credentials в repr (например,
        httpx.ConnectError со ссылкой `https://user:pass@host/...`) — в
        `audit_outbox.last_error` должен быть `<PASSWORD>` placeholder, а
        не plaintext пароль.

        Регрессия: `_runner.py` для `task.last_error` прогоняет через
        `redact_error_message`, а outbox publisher раньше пропускал.
        """
        tid = await make_task(task_kind="power.on")

        # Симулируем реальный httpx-style сценарий: текст ошибки содержит
        # полный URL с basic-auth credentials (RFC 3986 валиден). Берём
        # сразу узнаваемый класс — RuntimeError, чтобы простой assert на
        # имя класса в существующих тестах не зависел от типа.
        secret_password = "s3cr3t_p4ss"
        leaky_url = f"https://user:{secret_password}@logging.internal/events"

        async def emit_with_leaky_url(action, **kw):
            raise RuntimeError(
                f"Connection refused for {leaky_url} after 3 retries"
            )

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            emit_with_leaky_url,
        )

        async def impl(_):
            return {"power_state": "on"}

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
            audit_safe_fields={"power_state"},
        )

        rows = await _unpublished_outbox_rows()
        assert len(rows) == 1
        last_error = rows[0].last_error or ""

        # Plaintext пароль не должен утечь в worker-DB.
        assert secret_password not in last_error
        # `user` (username) тоже маскируется регэкспом URL-credentials.
        assert "user:" not in last_error
        # Placeholder есть — значит redaction отработала, а не просто
        # обрезалась строка по длине.
        assert "<PASSWORD>" in last_error
        # Имя класса исключения сохраняется (нужно для диагностики).
        assert "RuntimeError" in last_error

    async def test_subsequent_flush_publishes_row(
        self, make_task, monkeypatch,
    ):
        """Симуляция: первый flush_outbox упал, второй уже на «починенной»
        сети — выпустил."""
        tid = await make_task(task_kind="power.on")

        emit_calls = {"n": 0}

        async def flaky_emit(action, **kw):
            emit_calls["n"] += 1
            if emit_calls["n"] == 1:
                raise RuntimeError("temporary")
            # вторая попытка — ок

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            flaky_emit,
        )

        async def impl(_):
            return {"power_state": "on"}

        # Первый запуск: emit упал → outbox unpublished
        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
            audit_safe_fields={"power_state"},
        )

        before = await _unpublished_outbox_rows()
        assert len(before) == 1
        assert before[0].published_at is None

        # Имитируем следующий тик publisher-loop'а (или next-task flush)
        published_count = await audit_outbox_publisher.flush_outbox()
        assert published_count == 1

        after = await _unpublished_outbox_rows()
        assert after == []  # всё опубликовано
        all_rows = await _all_outbox_rows()
        assert all_rows[0].published_at is not None
        assert all_rows[0].attempts == 1  # счётчик с первой неудачи остался


class TestOutboxAtomicityWithStatus:
    """Statuts и audit-outbox row коммитятся одной транзакцией."""

    async def test_status_change_implies_outbox_row(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Если task.status в финальном состоянии — outbox-row точно есть.

        Это базовый инвариант outbox-паттерна: один commit пишет и status,
        и audit-row. Тест проверяет на happy и failure пути одновременно.

        Failure-task создаётся с `max_attempts=1` чтобы исключить
        retry-ветку: первая ошибка сразу → terminal FAILED.
        """
        from sqlalchemy import update

        from src.models import Task
        from src.tasks import _runner

        async def noop(*args, **kwargs):
            pass

        monkeypatch.setattr(_runner, "_schedule_retry", noop)

        async def ok_impl(_):
            return {"power_state": "on"}

        async def boom(_):
            raise ValueError("x")

        # happy
        tid_ok = await make_task(task_kind="power.on")
        await run_task(
            tid_ok,
            audit_action="server.power_on",
            impl=ok_impl,
            audit_safe_fields={"power_state"},
        )

        # failure
        tid_fail = await make_task(task_kind="power.on")
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Task).where(Task.id == tid_fail).values(max_attempts=1)
            )
            await session.commit()
        await run_task(
            tid_fail,
            audit_action="server.power_on",
            impl=boom,
        )

        ok = await fetch_task(tid_ok)
        fail = await fetch_task(tid_fail)
        assert ok.status == TaskStatus.SUCCEEDED
        assert fail.status == TaskStatus.FAILED

        all_rows = await _all_outbox_rows()
        rows_by_task = {r.task_id: r for r in all_rows}
        assert tid_ok in rows_by_task
        assert tid_fail in rows_by_task
        assert rows_by_task[tid_ok].payload["status"] == "success"
        assert rows_by_task[tid_fail].payload["status"] == "failure"


# ── 2. Whitelist для details.result ──────────────────────────────────────────

class TestResultWhitelist:
    """`audit_safe_fields` фильтрует ключи результата перед записью в audit."""

    async def test_only_whitelisted_keys_in_audit(
        self, make_task, captured_audit,
    ):
        tid = await make_task(task_kind="power.on")

        # Симулируем «новый handler с raw stdout» — возвращает безопасное
        # поле `a` и опасное `b` (как бы plaintext password).
        async def impl(_):
            return {"a": 1, "b": "PLAINTEXT_SECRET"}

        await run_task(
            tid,
            audit_action="x.action",
            impl=impl,
            audit_safe_fields={"a"},
        )

        assert len(captured_audit) == 1
        details = captured_audit[0]["details"]
        assert details["result"] == {"a": 1}
        # Секрет НЕ должен оказаться в audit details ни под каким видом.
        assert "PLAINTEXT_SECRET" not in str(captured_audit)
        assert "b" not in details["result"]

    async def test_no_whitelist_emits_sentinel(self, make_task, captured_audit):
        """Default: handler не объявил safe_fields → audit получает
        sentinel, никакие ключи не пробрасываются."""
        tid = await make_task(task_kind="power.on")

        async def impl(_):
            return {"a": 1, "b": "secret"}

        await run_task(
            tid,
            audit_action="x.action",
            impl=impl,
            # audit_safe_fields НЕ передан
        )

        assert len(captured_audit) == 1
        result = captured_audit[0]["details"]["result"]
        assert result == {"emitted": False, "reason": "no_whitelist"}
        # Ни значения, ни ключи handler-result в audit не уехали.
        assert "secret" not in str(captured_audit)
        assert '"a"' not in str(captured_audit)

    async def test_empty_whitelist_emits_empty_dict(
        self, make_task, captured_audit,
    ):
        """Явный пустой whitelist == «handler видел data, но в audit
        ничего не пускаю» — отличим от 'no_whitelist'."""
        tid = await make_task(task_kind="power.on")

        async def impl(_):
            return {"sensitive": "yes"}

        await run_task(
            tid,
            audit_action="x.action",
            impl=impl,
            audit_safe_fields=set(),
        )

        result = captured_audit[0]["details"]["result"]
        assert result == {}  # пустой dict, не sentinel
        assert "sensitive" not in str(captured_audit)

    async def test_none_result_still_none_in_audit(
        self, make_task, captured_audit,
    ):
        tid = await make_task(task_kind="power.on")

        async def impl(_):
            return None

        await run_task(
            tid,
            audit_action="x.action",
            impl=impl,
            audit_safe_fields={"anything"},
        )

        assert captured_audit[0]["details"]["result"] is None

    async def test_non_dict_result_emits_sentinel(
        self, make_task, captured_audit,
    ):
        """Защита: если handler ошибочно вернул не-dict (строка, число) —
        в audit идёт sentinel."""
        tid = await make_task(task_kind="power.on")

        async def impl(_):
            return "raw stdout with secret=hunter2"  # тип: str

        await run_task(
            tid,
            audit_action="x.action",
            impl=impl,
            audit_safe_fields={"a"},
        )

        result = captured_audit[0]["details"]["result"]
        assert result == {"emitted": False, "reason": "result_not_dict"}
        assert "hunter2" not in str(captured_audit)


class TestHandlersDeclareWhitelist:
    """Регистровая проверка: все 8 production-handler'ов объявили
    `AUDIT_SAFE_FIELDS`. Защищает от регрессии «забыли whitelist у
    нового handler'а»."""

    def test_power_module_declares_whitelist(self):
        from src.tasks import power
        assert hasattr(power, "AUDIT_SAFE_FIELDS")
        assert isinstance(power.AUDIT_SAFE_FIELDS, set)
        assert "power_state" in power.AUDIT_SAFE_FIELDS

    def test_inventory_module_declares_whitelist(self):
        from src.tasks import inventory
        assert hasattr(inventory, "AUDIT_SAFE_FIELDS")
        # facts НЕ должны быть в whitelist (потенциально содержат
        # kernel/packages/disks).
        assert "facts" not in inventory.AUDIT_SAFE_FIELDS

    def test_passwords_module_declares_whitelist(self):
        from src.tasks import passwords
        assert hasattr(passwords, "AUDIT_SAFE_FIELDS_ACCOUNT_ROTATE")
        assert hasattr(passwords, "AUDIT_SAFE_FIELDS_IPMI_ROTATE")
        # password/passwords НЕ должны быть в whitelist ни в одном
        # rotation handler'е.
        for fields in (
            passwords.AUDIT_SAFE_FIELDS_ACCOUNT_ROTATE,
            passwords.AUDIT_SAFE_FIELDS_IPMI_ROTATE,
        ):
            assert "password" not in fields
            assert "new_password" not in fields
            assert "old_password" not in fields



# ── 3. FOR UPDATE SKIP LOCKED в flush_outbox ─────────────────────────────────


class TestFlushOutboxSkipLocked:
    """`flush_outbox` берёт `FOR UPDATE SKIP LOCKED` на SELECT'е unpublished
    строк — иначе при `replicas > 1` два publisher-loop'а одновременно
    подхватят одну и ту же строку и пошлют дубликат в loging_service.

    Регрессия: `run_publisher_loop` автозапуск на каждой replica'е через
    `taskiq.TaskiqEvents.WORKER_STARTUP` — без SKIP LOCKED replica'ы
    дублировали бы emit.
    """

    # ── 3a. SQL-stmt inspection (без подключения к БД) ───────────────────

    def test_select_unpublished_stmt_uses_for_update_skip_locked(self):
        """Контракт: query, который собирает `_select_unpublished`, в
        compile'е содержит `FOR UPDATE SKIP LOCKED`.

        Compile под Postgres-диалект — `SKIP LOCKED` это PG-фича; default
        диалект отрендерит просто `FOR UPDATE` без skip-clause, и тест
        зря пройдёт.
        """
        from sqlalchemy.dialects import postgresql

        stmt = audit_outbox_publisher._select_unpublished(50)
        compiled = str(
            stmt.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        assert "FOR UPDATE" in compiled
        assert "SKIP LOCKED" in compiled
        # Sanity: фильтр и порядок не сломаны рефакторингом.
        assert "published_at IS NULL" in compiled
        assert "ORDER BY" in compiled
        assert "audit_outbox.created_at ASC" in compiled

    def test_select_unpublished_stmt_respects_limit(self):
        """`limit` корректно прокидывается — guard от регрессии, где
        SELECT мог бы случайно стянуть всю очередь."""
        from sqlalchemy.dialects import postgresql

        stmt = audit_outbox_publisher._select_unpublished(7)
        compiled = str(
            stmt.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        assert "LIMIT 7" in compiled

    # ── 3b. Functional: два concurrent publisher'а делят строки ──────────

    async def test_concurrent_publishers_do_not_double_publish(
        self, make_task, monkeypatch,
    ):
        """Реальный multi-replica сценарий: два `flush_outbox()`,
        запущенных через `asyncio.gather`, обрабатывают разные строки —
        каждая строка эмитится ровно один раз.

        Замысел: создаём N unpublished-строк через `run_task` (которые
        упали по emit), потом параллельно запускаем два publisher-pass'а
        с одним и тем же fake_emit (счётчик). Сумма published у двух
        runner'ов == N; ни одна строка не получает двух emit'ов.
        """
        import asyncio
        from collections import Counter

        # 1) Загоняем N задач: emit падает → outbox unpublished.
        N = 6

        async def failing_emit_first_pass(action, **kw):
            raise RuntimeError("seed: down")

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            failing_emit_first_pass,
        )

        async def ok_impl(_):
            return {"power_state": "on"}

        task_ids = []
        for _ in range(N):
            tid = await make_task(task_kind="power.on")
            await run_task(
                tid,
                audit_action="server.power_on",
                audit_target_type="server",
                impl=ok_impl,
                audit_safe_fields={"power_state"},
            )
            task_ids.append(tid)

        # Sanity: все строки сейчас unpublished.
        before = await _unpublished_outbox_rows()
        assert len(before) == N

        # 2) Теперь "чиним сеть": emit работает, но счётчик ловит, кто
        # сколько раз отправил каждое событие.
        emit_calls: Counter = Counter()
        emit_lock = asyncio.Lock()
        # Барьер: оба publisher'а должны дойти до emit до того, как
        # первый закоммитит — иначе skip-locked нечему защищать (второй
        # ничего не увидит). Один маленький await внутри emit вынуждает
        # обоих стартануть SELECT'ы примерно одновременно.

        async def counting_emit(action, **kw):
            # Дёшево «зеваем» — даём другому publisher'у шанс
            # докрутить свой SELECT и попробовать те же строки.
            await asyncio.sleep(0.01)
            async with emit_lock:
                # ключ — task_id из payload (он уникален на строку).
                tid = kw.get("details", {}).get("task_id") or kw.get(
                    "target_id"
                ) or str(kw)
                emit_calls[tid] += 1

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            counting_emit,
        )

        # 3) Два publisher'а параллельно — имитируем две replica'и.
        published_a, published_b = await asyncio.gather(
            audit_outbox_publisher.flush_outbox(),
            audit_outbox_publisher.flush_outbox(),
        )

        # Каждая строка опубликована ровно один раз суммарно по обоим
        # publisher'ам.
        assert published_a + published_b == N, (
            f"expected exactly {N} publishes, got "
            f"{published_a} + {published_b}"
        )

        # Ни одна строка не отправилась дважды — это ключевая
        # инварианта skip-locked.
        duplicates = {k: v for k, v in emit_calls.items() if v > 1}
        assert not duplicates, (
            f"some outbox rows were published more than once: {duplicates}"
        )

        # И всё опубликовано — нет «забытых» строк.
        remaining = await _unpublished_outbox_rows()
        assert remaining == []

    async def test_concurrent_publishers_split_work_between_replicas(
        self, make_task, monkeypatch,
    ):
        """Skip-locked даёт реальный параллелизм: оба publisher'а
        получают непустые подмножества (а не «один забрал всё, другой
        ничего»). Подтверждает, что lock работает на гранулярности
        строки, а не таблицы.

        NB: это не строгая гарантия — теоретически второй publisher
        мог стартовать после того, как первый уже зафиксировал все
        строки. Поэтому в проверке требуем «оба published >= 1» только
        если первый не взял всё за один проход. Главная проверка —
        отсутствие дубликатов (см. test выше); этот тест — про
        отсутствие over-locking'а (FOR UPDATE без skip_locked заставил
        бы второй publisher блокироваться, а не работать).
        """
        import asyncio

        N = 8

        async def failing_emit(action, **kw):
            raise RuntimeError("seed: down")

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            failing_emit,
        )

        async def ok_impl(_):
            return {"power_state": "on"}

        for _ in range(N):
            tid = await make_task(task_kind="power.on")
            await run_task(
                tid,
                audit_action="server.power_on",
                audit_target_type="server",
                impl=ok_impl,
                audit_safe_fields={"power_state"},
            )

        # "Сеть починилась" — emit просто успешен, со sleep'ом, чтобы
        # publisher'ы реально жили параллельно.
        async def slow_ok_emit(action, **kw):
            await asyncio.sleep(0.02)

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            slow_ok_emit,
        )

        # Ограничиваем batch — иначе первый publisher схватит сразу все
        # N и второму нечего будет ловить (legit-сценарий, но не
        # проверяющий параллелизм).
        published_a, published_b = await asyncio.gather(
            audit_outbox_publisher.flush_outbox(limit=N // 2),
            audit_outbox_publisher.flush_outbox(limit=N // 2),
        )

        # Сумма == N (всё опубликовано, дубликатов нет).
        assert published_a + published_b == N
        # Оба publisher'а получили работу — это значит, skip-locked
        # отработал, а не FOR UPDATE заблокировал второй select.
        assert published_a > 0 and published_b > 0, (
            f"only one publisher got rows: a={published_a} b={published_b}"
            " — skip-locked may not be effective"
        )


# ── 4. fail-loud на 4xx/5xx из loging_service ────────────────────────────────


class TestPublishHttpFailLoud:
    """Регрессия «audit_client.emit swallow 4xx/5xx →
    published-but-not-delivered».

    До фикса `audit_client.emit` логировал 4xx/5xx warning'ом и возвращал
    None. `_publish_one` принимал это за успех → `row.published_at=now()`
    → событие безвозвратно терялось (retry не подхватывал). Теперь
    `emit` raise'ит `AuditEmitError`; publisher ловит → attempts++,
    last_error, row остаётся unpublished.

    Эти тесты mock'ают именно `audit_client.emit` (а не httpx-слой) —
    нам важна интеграция publisher → emit, а не payload composition
    (это покрыто в `tests/unit/test_audit_client.py`).
    """

    async def test_emit_500_leaves_row_unpublished_increments_attempts(
        self, make_task, fetch_task, monkeypatch,
    ):
        """Mock loging_service возвращает 500 → emit raise'ит
        AuditEmitError → publisher НЕ помечает published, attempts++."""
        tid = await make_task(task_kind="power.on")

        async def fake_emit_500(action, **kw):
            raise AuditEmitError(
                f"loging_service returned HTTP 500 for {action}"
            )

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            fake_emit_500,
        )

        async def impl(_):
            return {"power_state": "on"}

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
            audit_safe_fields={"power_state"},
        )

        # Task succeeded (lifecycle коммит проходит независимо от emit).
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED

        # Outbox: row остался unpublished, attempts инкрементирован.
        rows = await _unpublished_outbox_rows()
        assert len(rows) == 1, "AuditEmitError должен оставить row unpublished"
        assert rows[0].published_at is None
        assert rows[0].attempts == 1
        assert "500" in (rows[0].last_error or "")

    @pytest.mark.parametrize("status_code", [400, 401, 403, 404, 409, 422, 429])
    async def test_emit_4xx_sends_row_to_dlq(
        self, status_code, make_task, fetch_task, monkeypatch,
    ):
        """4xx — permanent-fatal: publisher классифицирует и сразу шлёт
        row в DLQ (`published_at=now()` + counter++). Retry'ить нечего:
        loging_service ответил, что этот payload неприемлем (плохая
        схема, dead key, отозванный actor).

        Раньше 4xx ретраились как 5xx — `attempts` рос бесконечно,
        выборка засорялась. Сейчас 4xx закрывается одним flush'ем.
        """
        tid = await make_task(task_kind="power.on")

        async def fake_emit_4xx(action, **kw):
            raise AuditEmitError(
                f"loging_service returned HTTP {status_code} for {action}",
                status_code=status_code,
            )

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            fake_emit_4xx,
        )

        async def impl(_):
            return {"power_state": "on"}

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
            audit_safe_fields={"power_state"},
        )

        # Row помечен published (DLQ), attempts=1 — одна попытка была.
        rows = await _all_outbox_rows()
        assert len(rows) == 1
        assert rows[0].published_at is not None
        assert rows[0].attempts == 1
        assert str(status_code) in (rows[0].last_error or "")
        # SELECT по unpublished пустой.
        assert (await _unpublished_outbox_rows()) == []

    async def test_emit_200_marks_row_published(
        self, make_task, fetch_task, monkeypatch, captured_audit,
    ):
        """Happy-path: emit вернул None (HTTP 2xx) → row помечен published,
        attempts остался 0."""
        tid = await make_task(task_kind="power.on")

        # `captured_audit` fixture уже подменяет emit на in-memory
        # collector, который возвращает None — это соответствует 2xx
        # successful path. Мы здесь просто проверяем результат.

        async def impl(_):
            return {"power_state": "on"}

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
            audit_safe_fields={"power_state"},
        )

        rows = await _all_outbox_rows()
        assert len(rows) == 1
        assert rows[0].published_at is not None
        assert rows[0].attempts == 0
        # И само событие зафиксировано (fake_emit добавил в captured_audit).
        assert len(captured_audit) == 1
        assert captured_audit[0]["action"] == "server.power_on"

    async def test_emit_transport_error_leaves_row_unpublished(
        self, make_task, fetch_task, monkeypatch,
    ):
        """`emit` бросает `AuditEmitError`, обёрнутый поверх
        `httpx.ConnectError` — publisher тоже оставляет unpublished.

        Подтверждает, что transport-fail (network down, DNS, TLS) идёт
        через тот же fail-loud путь, что и HTTP-fail. До фикса httpx
        ловились в audit_client'е и тоже глотались.
        """
        tid = await make_task(task_kind="power.on")

        async def fake_emit_transport(action, **kw):
            # Симулируем как это бросал бы реальный emit() — ConnectError
            # → AuditEmitError с сохранением имени класса в message.
            try:
                raise httpx.ConnectError("getaddrinfo failed")
            except httpx.HTTPError as exc:
                raise AuditEmitError(f"{type(exc).__name__}: {exc}") from exc

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            fake_emit_transport,
        )

        async def impl(_):
            return {"power_state": "on"}

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
            audit_safe_fields={"power_state"},
        )

        rows = await _unpublished_outbox_rows()
        assert len(rows) == 1
        assert rows[0].published_at is None
        assert rows[0].attempts == 1
        assert "ConnectError" in (rows[0].last_error or "")

    async def test_recovery_after_5xx_publishes_on_next_flush(
        self, make_task, monkeypatch,
    ):
        """End-to-end retry: первый прогон emit падает с 500, второй —
        ок. Row помечается published на втором проходе, attempts=1
        сохраняется как историческое значение."""
        tid = await make_task(task_kind="power.on")

        emit_calls = {"n": 0}

        async def flaky_emit(action, **kw):
            emit_calls["n"] += 1
            if emit_calls["n"] == 1:
                raise AuditEmitError(
                    f"loging_service returned HTTP 500 for {action}"
                )
            # Второй прогон — успех (2xx → return None).

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            flaky_emit,
        )

        async def impl(_):
            return {"power_state": "on"}

        # Первый запуск — emit'нул 500, row unpublished.
        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
            audit_safe_fields={"power_state"},
        )
        before = await _unpublished_outbox_rows()
        assert len(before) == 1
        assert before[0].attempts == 1
        assert "500" in (before[0].last_error or "")

        # Имитируем next-tick publisher loop.
        published = await audit_outbox_publisher.flush_outbox()
        assert published == 1

        after_all = await _all_outbox_rows()
        assert after_all[0].published_at is not None
        # attempts с первой неудачи сохраняется (диагностический след).
        assert after_all[0].attempts == 1


# ── 5. Poison-pill: cap по attempts ──────────────────────────────────────────


class TestOutboxPoisonPill:
    """`MAX_PUBLISH_ATTEMPTS` (env, default 50) — soft-cap, после которого
    publisher метит row как дропнутый: ставит `published_at=now()` + ERROR.

    Кейс: loging_service перманентно отвечает 422 (malformed payload).
    Без cap'а row ретраится вечно, attempts растёт без верхней границы,
    SKIP LOCKED выборка засоряется. После фикса — row однократно
    отравляется, дальше SELECT его не видит.
    """

    async def test_row_at_cap_gets_poisoned_and_skipped(
        self, make_task, monkeypatch,
    ):
        from sqlalchemy import update

        tid = await make_task(task_kind="power.on")

        # Глушим emit так, чтобы row упал первой попыткой → attempts=1
        # → не в cap'е.
        async def boom_emit(action, **kw):
            raise AuditEmitError("loging returned HTTP 422 (malformed)")

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            boom_emit,
        )

        async def ok_impl(_):
            return {"power_state": "on"}

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=ok_impl,
            audit_safe_fields={"power_state"},
        )
        unpub = await _unpublished_outbox_rows()
        assert len(unpub) == 1
        assert unpub[0].attempts == 1
        row_id = unpub[0].id

        # Жёстко выставляем attempts = cap-1: следующий publish даст cap,
        # poison-логика должна сработать.
        cap = 50
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(AuditOutbox)
                .where(AuditOutbox.id == row_id)
                .values(attempts=cap - 1)
            )
            await session.commit()

        # Прогон publisher'а: row должен быть отравлен.
        await audit_outbox_publisher.flush_outbox()

        # Sanity: row теперь имеет published_at, attempts ровно cap.
        after = await _all_outbox_rows()
        assert len(after) == 1
        assert after[0].id == row_id
        assert after[0].attempts == cap
        assert after[0].published_at is not None

        # Второй прогон: row не должен повторно подбираться SELECT'ом —
        # потому что published_at != NULL.
        unpub_after = await _unpublished_outbox_rows()
        assert unpub_after == []

        published = await audit_outbox_publisher.flush_outbox()
        assert published == 0

    async def test_cap_overridable_via_env(self, make_task, monkeypatch):
        """Понизить cap до 2 через env: row, попавший на attempts=2, отравлен."""
        from sqlalchemy import update

        monkeypatch.setenv("MAX_PUBLISH_ATTEMPTS", "2")

        tid = await make_task(task_kind="power.on")

        async def boom_emit(action, **kw):
            raise AuditEmitError("HTTP 422")

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            boom_emit,
        )

        async def ok_impl(_):
            return {"power_state": "on"}

        # Первый прогон в _runner делает inline flush → attempts=1.
        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=ok_impl,
            audit_safe_fields={"power_state"},
        )
        rows = await _unpublished_outbox_rows()
        assert len(rows) == 1
        assert rows[0].attempts == 1

        # Дёрнем cap руками: на следующем flush attempts станет 2 == cap →
        # poison.
        await audit_outbox_publisher.flush_outbox()
        after = await _all_outbox_rows()
        assert after[0].attempts == 2
        assert after[0].published_at is not None
        assert (await _unpublished_outbox_rows()) == []


# ── 6. Classify 4xx как permanent → DLQ сразу + counter ──────────────────────


class TestOutboxClassify4xx:
    """4xx от loging_service — permanent-fatal (плохой payload, dead key,
    отозванный actor). Retry не починит. Publisher должен сразу пометить
    row published'ом (DLQ-семантика) и инкрементить counter, минуя
    обычный attempts-cap путь.

    5xx и transport-fail (status_code=None) — наоборот, transient: row
    остаётся unpublished, attempts++.
    """

    async def test_4xx_sends_row_to_dlq_immediately(
        self, make_task, monkeypatch,
    ):
        from src.services.audit_outbox_publisher import _reset_breaker_state

        _reset_breaker_state()
        baseline = audit_outbox_publisher.get_dlq_total()

        tid = await make_task(task_kind="power.on")

        async def boom_4xx(action, **kw):
            raise AuditEmitError("HTTP 422 malformed", status_code=422)

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            boom_4xx,
        )

        async def ok_impl(_):
            return {"power_state": "on"}

        # Первый прогон: inline flush в _runner ловит 422 → DLQ.
        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=ok_impl,
            audit_safe_fields={"power_state"},
        )

        rows = await _all_outbox_rows()
        assert len(rows) == 1
        # Row помечен published, attempts=1 (одна попытка была), DLQ counter вырос.
        assert rows[0].published_at is not None
        assert rows[0].attempts == 1
        assert audit_outbox_publisher.get_dlq_total() == baseline + 1
        # SELECT по unpublished пустой — row больше не подбирается.
        assert (await _unpublished_outbox_rows()) == []

    async def test_5xx_keeps_row_unpublished_for_retry(
        self, make_task, monkeypatch,
    ):
        """5xx → row остаётся unpublished, attempts++, в DLQ не уходит."""
        from src.services.audit_outbox_publisher import _reset_breaker_state

        _reset_breaker_state()
        baseline = audit_outbox_publisher.get_dlq_total()

        tid = await make_task(task_kind="power.on")

        async def boom_5xx(action, **kw):
            raise AuditEmitError("HTTP 500", status_code=500)

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            boom_5xx,
        )

        async def ok_impl(_):
            return {"power_state": "on"}

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=ok_impl,
            audit_safe_fields={"power_state"},
        )

        rows = await _all_outbox_rows()
        assert len(rows) == 1
        assert rows[0].published_at is None  # ещё попробуем
        assert rows[0].attempts == 1
        # DLQ counter не вырос — это transient, не permanent.
        assert audit_outbox_publisher.get_dlq_total() == baseline

    async def test_transport_error_status_none_keeps_row_unpublished(
        self, make_task, monkeypatch,
    ):
        """status_code=None (timeout/connect) → transient, retry."""
        from src.services.audit_outbox_publisher import _reset_breaker_state

        _reset_breaker_state()
        baseline = audit_outbox_publisher.get_dlq_total()

        tid = await make_task(task_kind="power.on")

        async def boom_transport(action, **kw):
            raise AuditEmitError("TimeoutException: slow", status_code=None)

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            boom_transport,
        )

        async def ok_impl(_):
            return {"power_state": "on"}

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=ok_impl,
            audit_safe_fields={"power_state"},
        )

        rows = await _all_outbox_rows()
        assert rows[0].published_at is None
        assert rows[0].attempts == 1
        assert audit_outbox_publisher.get_dlq_total() == baseline

    async def test_dlq_counter_grows_on_attempts_cap_too(
        self, make_task, monkeypatch,
    ):
        """DLQ counter растёт и при cap-induced poison, не только при 4xx.

        Симметричный кейс: один и тот же `_send_to_dlq` дёргается из обоих
        путей (4xx и attempts_cap), counter — единое значение, регулярно
        растёт.
        """
        from sqlalchemy import update
        from src.services.audit_outbox_publisher import _reset_breaker_state

        _reset_breaker_state()
        baseline = audit_outbox_publisher.get_dlq_total()

        tid = await make_task(task_kind="power.on")

        async def boom_5xx(action, **kw):
            # 5xx чтобы запустить cap-путь (а не 4xx-short-circuit).
            raise AuditEmitError("HTTP 503", status_code=503)

        monkeypatch.setattr(
            "src.services.audit_outbox_publisher.audit_client.emit",
            boom_5xx,
        )

        async def ok_impl(_):
            return {"power_state": "on"}

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=ok_impl,
            audit_safe_fields={"power_state"},
        )

        rows = await _unpublished_outbox_rows()
        assert len(rows) == 1
        row_id = rows[0].id

        # Жёстко поднимаем attempts до cap-1 — следующий flush попадёт
        # в cap → DLQ через attempts_cap reason.
        cap = 50
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(AuditOutbox)
                .where(AuditOutbox.id == row_id)
                .values(attempts=cap - 1)
            )
            await session.commit()

        await audit_outbox_publisher.flush_outbox()
        assert audit_outbox_publisher.get_dlq_total() == baseline + 1
