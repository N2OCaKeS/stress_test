"""Polling-цикл очереди `testing_service`.

`run_polling_loop()` — фоновый `asyncio`-loop с `WORKER_STARTUP`. Диспетчер
один: testing_service решает, какой item готов, `claim()` просто забирает
следующий.

Один проход: `claim()` → `preflight.wait_for_external_services()` (ждём
внешние сервисы по настройкам отдела, вышло время — провал item'а) →
запись файлов задания по SFTP → `ssh_executor.execute(...)` с живым логом
и опросом `interrupt-check` для досрочной остановки → финальный
`log_segment` → `report_completed(...)`.

Каждый item запускается отдельным `asyncio.Task`, цикл не ждёт его
завершения перед следующим `claim()`. Отдельного лимита параллельности нет:
очередь сама не отдаёт больше одного `ready` item'а на стенд.

Неожиданная ошибка `claim()`а или отдельного item'а логируется и не убивает
loop — у каждой задачи свой `done`-callback.
"""

from __future__ import annotations

import asyncio
import logging
import re

import asyncssh

from src.core.config import get_settings
from src.services import preflight, ssh_executor, testing_client

logger = logging.getLogger("testing_worker.queue_loop")

_COMMAND_SEGMENT_LABEL = "Выполнение теста"
_PREPARE_ONLY_SEGMENT_LABEL = "Подготовка стенда (testenv)"


def _segment_label(item: dict) -> str:
    """Подпись сегмента лога; у многоступенчатого теста — с шагом."""
    label = _PREPARE_ONLY_SEGMENT_LABEL if item.get("prepare_only") else _COMMAND_SEGMENT_LABEL
    step = item.get("step") or {}
    count = step.get("count") or 1
    if count <= 1:
        return label
    suffix = f"шаг {int(step.get('index') or 0) + 1}/{count}"
    if step.get("name"):
        suffix += f" «{step['name']}»"
    return f"{label} · {suffix}"

# Типы, которыми SFTP-запись сигналит о провале: ошибка протокола/канала,
# сетевой обрыв, невалидный ключ (ValueError из write_remote_file) и таймаут.
_WRITE_ERRORS = (asyncssh.Error, OSError, ValueError, asyncio.TimeoutError, TimeoutError)

# Маска очистки: абсолютный путь из безопасных символов и `*`/`?`.
_SAFE_GLOB_RE = re.compile(r"/[A-Za-z0-9_./*?\-]*")


async def _cleanup_files(item: dict, settings) -> str | None:
    """Удалить на стенде файлы по `cleanup_globs` задания (опция профиля)."""
    globs = [g for g in item.get("cleanup_globs") or [] if g]
    if not globs:
        return None
    bad = [g for g in globs if not _SAFE_GLOB_RE.fullmatch(g)]
    if bad:
        return f"unsafe cleanup glob: {bad[0]!r}"
    try:
        await ssh_executor.run_remote_command(
            item["host"], item["test_username"], item["test_ssh_private_key"],
            "sudo rm -f -- " + " ".join(globs),
            connect_timeout=settings.ssh_connect_timeout_seconds,
        )
    except _WRITE_ERRORS as exc:
        logger.warning("queue item %s: cleanup failed: %s", item.get("queue_item_id"), type(exc).__name__)
        return f"cleanup of {', '.join(globs)} failed: {type(exc).__name__}"
    return None


async def _write_files(item: dict, settings) -> str | None:
    """SFTP-запись файлов задания в порядке списка.

    Стенд перед прогоном откатывается на образ, поэтому всё, что нужно
    `starter.sh`, едет каждый раз (легаси тоже перезаливало скрипт,
    `allta_app/backup_image.py:932,992`). Содержимое в лог не пишется —
    только путь и длина. Возвращает текст ошибки либо `None`.
    """
    for spec in item.get("files") or []:
        path, content = spec["path"], spec["content"]
        try:
            await ssh_executor.write_remote_file(
                item["host"],
                item["test_username"],
                item["test_ssh_private_key"],
                path,
                content,
                mode=spec.get("mode"),
                connect_timeout=settings.ssh_connect_timeout_seconds,
            )
        except _WRITE_ERRORS as exc:
            logger.warning(
                "queue item %s: failed to write %s (%d bytes) over SFTP: %s",
                item.get("queue_item_id"), path, len(content), type(exc).__name__,
            )
            return f"SFTP write of {path} failed: {type(exc).__name__}"
    return None


async def _watch_for_interrupt(queue_item_id: str, interval: float, stop: asyncio.Event) -> str | None:
    """Опрашивать `interrupt-check`, пока не попросят прервать либо не выставят `stop`.

    Возвращает запрошенное действие (`skip`/`pause`), либо `None`, если тест
    успел закончиться сам. Первый опрос уходит не сразу, а через `interval` —
    заявка на прерывание может появиться только после того, как тест реально
    стартовал, и лишний запрос в момент старта смысла не имеет.
    """
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return None
        except asyncio.TimeoutError:
            pass
        try:
            action = await testing_client.check_interrupt(queue_item_id)
        except Exception:  # noqa: BLE001 — сбой опроса не повод обрывать тест
            logger.exception("queue item %s: interrupt-check raised", queue_item_id)
            continue
        if action is not None:
            return action
    return None


async def _execute_with_interrupt_watch(item: dict, settings, on_output_chunk):
    """Исполнить команду, параллельно следя за заявкой на прерывание.

    Возвращает `(result, interrupted)`: либо обычный `ExecutionResult` и
    `None`, либо `None` и действие, по которому исполнение было оборвано.
    """
    queue_item_id = item["queue_item_id"]
    chunk_options = {}
    if item.get("log_chunk_interval_seconds"):
        chunk_options["chunk_interval_seconds"] = item["log_chunk_interval_seconds"]
    if item.get("log_chunk_max_bytes"):
        chunk_options["chunk_max_bytes"] = item["log_chunk_max_bytes"]
    execute_task = asyncio.ensure_future(ssh_executor.execute(
        item["host"],
        item["test_username"],
        item["test_ssh_private_key"],
        item["launch_command"],
        stop_command=item["stop_command"],
        use_pty=item.get("use_pty", True),
        connect_timeout=settings.ssh_connect_timeout_seconds,
        command_timeout=item.get("command_timeout_seconds") or settings.ssh_command_timeout_seconds,
        on_output_chunk=on_output_chunk,
        redact_secrets=item.get("redact_values") or None,
        **chunk_options,
    ))
    stop_watch = asyncio.Event()
    watch_task = asyncio.ensure_future(_watch_for_interrupt(
        queue_item_id, settings.interrupt_poll_interval_seconds, stop_watch,
    ))

    try:
        await asyncio.wait({execute_task, watch_task}, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        await _quiet_cancel(execute_task)
        await _quiet_cancel(watch_task)
        raise

    interrupted: str | None = None
    if watch_task.done() and not watch_task.cancelled():
        exc = watch_task.exception()
        if exc is not None:
            logger.warning("queue item %s: interrupt watcher failed: %r", queue_item_id, exc)
        else:
            interrupted = watch_task.result()

    # Заявка могла прийти ровно в тот момент, когда тест закончился сам. Тогда
    # у нас на руках настоящий исход — он важнее прерывания, обрывать уже нечего.
    if interrupted is None or execute_task.done():
        stop_watch.set()
        await _quiet_cancel(watch_task)
        return await execute_task, None

    logger.info("queue item %s: interrupt requested (%s), killing remote process", queue_item_id, interrupted)
    await ssh_executor.kill_remote_process(
        item["host"],
        item["test_username"],
        item["test_ssh_private_key"],
        stop_command=item["stop_command"],
        connect_timeout=settings.ssh_connect_timeout_seconds,
    )
    execute_task.cancel()
    await _quiet_cancel(execute_task)
    return None, interrupted


async def _quiet_cancel(task) -> None:
    """Дождаться завершения отменённой/законченной задачи, проглотив её исход."""
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:  # noqa: BLE001 — исход отменённой задачи уже не нужен
        pass


async def _await_external_services(
    queue_item_id: str, preflight_settings: dict | None = None,
) -> preflight.PreflightResult:
    """Дождаться внешних сервисов, не переставая слушать заявку на прерывание.

    Ожидание может тянуться до двух часов, поэтому оператор обязан иметь
    возможность снять item и в это время — `should_abort` спрашивает тот же
    `interrupt-check`, что и наблюдатель во время самого теста. Опрос идёт
    раз в цикл проверки (а не раз в `interrupt_poll_interval_seconds`) —
    задержка реакции в минуты на фазе ожидания приемлема и не плодит лишних
    запросов к `testing_service`.

    `preflight_settings` — `item["preflight"]` (настройки отдела из
    testing_service); `None` — env-фолбэк воркера.
    """
    reported_waiting = False

    async def _note_wait(unavailable: list[str], elapsed: float) -> None:
        nonlocal reported_waiting
        await testing_client.log_chunk(
            queue_item_id,
            "Ожидание доступности внешних сервисов "
            f"({', '.join(unavailable)}); прошло {int(elapsed)} с\n",
        )
        # Левая панель UI: «Ожидание доступности сервисов — тестирование
        # приостановлено».
        await testing_client.report_preflight_state(queue_item_id, waiting=True, unavailable=unavailable)
        reported_waiting = True

    async def _interrupt_requested() -> str | None:
        return await testing_client.check_interrupt(queue_item_id)

    try:
        return await preflight.wait_for_external_services(
            preflight=preflight_settings, on_wait=_note_wait, should_abort=_interrupt_requested,
        )
    finally:
        # Дождались, сдались или item сняли — ожидания больше нет.
        if reported_waiting:
            await testing_client.report_preflight_state(queue_item_id, waiting=False)


async def _run_one_item(item: dict) -> None:
    """Исполнить одно задание из `claim()`, зафиксировать лог и отчитаться `completed`."""
    settings = get_settings()
    queue_item_id = item["queue_item_id"]

    # Пре-флайт внешних сервисов — до любых действий на стенде. Легаси делало
    # ровно то же и в том же месте (`backup_image.py:1045-1050`): `starter.sh`
    # первым делом клонирует ветку с git.astralinux.ru, а сам тест ходит в
    # Jira/Confluence, поэтому кратковременную недоступность надо пережидать,
    # а не сжигать на ней единственную попытку retry.
    gate = await _await_external_services(queue_item_id, item.get("preflight"))
    if gate.aborted is not None:
        logger.info("queue item %s interrupted while waiting for external services", queue_item_id)
        await testing_client.report_completed(
            queue_item_id, succeeded=False, exit_code=None, error=None,
            interrupted=gate.aborted,
        )
        return
    if not gate.ok:
        await testing_client.report_completed(
            queue_item_id, succeeded=False, exit_code=None, error=gate.error,
        )
        return

    # Файлы задания — в порядке списка; первая же осечка заканчивает item,
    # не доводя до `execute()`.
    write_error = await _cleanup_files(item, settings)
    if write_error is None:
        write_error = await _write_files(item, settings)
    if write_error is not None:
        await testing_client.report_completed(
            queue_item_id, succeeded=False, exit_code=None, error=write_error,
        )
        return

    async def _on_output_chunk(text: str) -> None:
        await testing_client.log_chunk(queue_item_id, text)

    result, interrupted = await _execute_with_interrupt_watch(item, settings, _on_output_chunk)

    if interrupted is not None:
        logger.info("queue item %s interrupted: action=%s", queue_item_id, interrupted)
        # Ни исхода, ни полного вывода у оборванной сессии нет — `log-segment`
        # не заводим, уже отданные `log-chunk`и остаются как есть.
        await testing_client.report_completed(
            queue_item_id, succeeded=False, exit_code=None, error=None,
            interrupted=interrupted,
        )
        return

    logger.info(
        "queue item %s finished: connected=%s succeeded=%s exit_code=%s is_retry=%s debug_mode=%s",
        queue_item_id,
        result.connected,
        result.succeeded,
        result.exit_code,
        item.get("is_retry"),
        item.get("debug_mode"),
    )

    if result.connected:
        await testing_client.log_segment(
            queue_item_id,
            kind="command",
            label=_segment_label(item),
            status="OK" if result.succeeded else "FATAL",
            command_text_masked=item.get("launch_command_masked") or "",
            output=result.output,
            host=item["host"],
            started_at=result.started_at,
            finished_at=result.finished_at,
        )

    await testing_client.report_completed(
        queue_item_id,
        succeeded=result.succeeded,
        exit_code=result.exit_code,
        error=result.error,
        timed_out=result.timed_out,
    )


def _on_item_task_done(task: asyncio.Task) -> None:
    """Safety-net на упавшую задачу одного item'а — `_run_one_item` сам ловит
    ожидаемые провалы и всегда репортит `completed`, но неожиданный
    `BaseException`-подкласс (не `CancelledError`) проскочит мимо и задача
    молча завершится. Один упавший item не должен молчать и не должен
    задевать остальные — они независимые задачи."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.critical("queue item task %s failed unexpectedly: %s: %s", task.get_name(), type(exc).__name__, exc)


async def run_polling_loop() -> None:
    """Бесконечный polling loop. Останавливается только через `CancelledError`.

    Каждый забранный item выполняется своей задачей, цикл не ждёт её
    завершения перед следующим `claim()` — см. module docstring про предел
    параллельности «один item на стенд», который получается сам по себе на
    стороне testing_service, без счётчика здесь.
    """
    settings = get_settings()
    poll_interval = settings.queue_poll_interval_seconds
    in_flight: set[asyncio.Task] = set()

    logger.info("queue polling loop started (poll_interval=%ss)", poll_interval)

    try:
        while True:
            try:
                item = await testing_client.claim()
            except Exception:  # noqa: BLE001 — один сбойный claim не должен убивать loop
                logger.exception("queue polling loop: unexpected error in iteration")
                await asyncio.sleep(poll_interval)
                continue

            if item is None:
                await asyncio.sleep(poll_interval)
                continue

            task = asyncio.create_task(_run_one_item(item), name=f"queue_item_{item['queue_item_id']}")
            task.add_done_callback(_on_item_task_done)
            task.add_done_callback(in_flight.discard)
            in_flight.add(task)
            # Без сна и без ожидания завершения — сразу пробуем забрать
            # следующий готовый item, параллельно с уже запущенными.
    except asyncio.CancelledError:
        for task in in_flight:
            task.cancel()
        if in_flight:
            await asyncio.gather(*in_flight, return_exceptions=True)
        raise
