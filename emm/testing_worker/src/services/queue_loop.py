"""Polling-цикл очереди `testing_service` (§5.5 плана миграции).

`run_polling_loop()` — long-running `asyncio` loop, поднимается как фоновая
задача на `WORKER_STARTUP` (см. `src/main.py`), по образцу
`server_worker/src/services/audit_outbox_publisher.py::run_publisher_loop`.
Задачи очереди исполняются не как отдельные taskiq-таски — диспетчер здесь
один: сам `testing_service` решает, какой item готов (`state=ready`),
`claim()` просто забирает следующий.

Один проход:

1. `testing_client.claim()` — если очередь пуста (или testing_service
   недоступен), `item is None` → короткий сон и следующая попытка.
2. `preflight.wait_for_external_services()` — пока Jira/Confluence/git/
   releases/DNS недоступны, item не стартует: ждём (до
   `preflight_timeout_seconds`, по умолчанию 2 часа, опрос раз в 180 с),
   как это делало легаси перед самым запуском. Вышло время — обычный провал
   item'а с внятной причиной (сжигается retry, но очередь не встаёт);
   оператор может снять item прямо во время ожидания, `interrupt-check`
   опрашивается и здесь.
3. `ssh_executor.write_remote_file(...)` кладёт на стенд сам `starter.sh`
   (`/home/u/starter.sh`, содержимое — `src/assets/starter.sh`). Стенд перед
   прогоном откатывается на образ, поэтому рассчитывать на оставшуюся с
   прошлого раза копию скрипта нельзя: команда `sudo bash /home/u/starter.sh`
   запускает ровно тот файл, который положили здесь.
4. Если `item` несёт `dates_content` — тем же способом на стенд уходит
   `/home/u/<dates_filename>`: `starter.sh` читает этот файл с диска, значит
   он обязан там оказаться раньше. Провал любой из двух записей — фатален для
   item'а целиком, `execute()` не вызывается вообще, сразу
   `report_completed(succeeded=False)`.
5. `ssh_executor.execute(...)` под кредами и хостом из `item` — единственный
   SSH-вызов `sudo bash /home/u/starter.sh ...` (что именно исполняется,
   собрал `testing_service`, здесь просто argv). Пока команда выполняется,
   каждый накопленный кусок вывода уходит наружу через
   `testing_client.log_chunk(...)` (§8.6 — живой лог в консоли сервера).
   Параллельно с исполнением крутится наблюдатель: раз в
   `interrupt_poll_interval_seconds` он спрашивает `interrupt-check`, не
   просил ли оператор снять тест. Если просил — на стенд отдельным коннектом
   уходит kill (`sudo pkill -f starter.sh`), исполнение отменяется, и вместо
   обычного исхода уходит `completed(interrupted=...)`. Обрыв SSH-канала сам
   по себе процесс под `sudo` не гасит, поэтому kill именно явный.
6. Если до исполнения дело дошло (`result.connected`) — один
   `testing_client.log_segment(...)` на весь тест, с полным выводом и
   замаскированной командой (`command_masked`, посчитан `testing_service`'ом
   при `claim`, см. §8.1). Провал на уровне SSH-коннекта сегмента не
   заводит — `completed(succeeded=False)` сам по себе достаточно
   информативен для этого случая.
7. `testing_client.report_completed(...)` с исходом.
8. Сразу следующая итерация, без сна — под нагрузкой воркер вычерпывает
   очередь максимально быстро; пауза нужна только когда реально нечего делать.

Тело цикла обёрнуто в `try/except Exception`: неожиданная ошибка (баг в
коде, а не транзиентная сетевая недоступность — та уже обработана внутри
`testing_client`) не должна убивать весь loop навсегда. На такой ошибке —
ERROR-лог и сон `queue_poll_interval_seconds`, чтобы систематический баг не
заспамил лог тысячами повторов в секунду.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from functools import lru_cache
from pathlib import Path

import asyncssh

from src.core.config import get_settings
from src.services import preflight, ssh_executor, testing_client

logger = logging.getLogger("testing_worker.queue_loop")

_COMMAND_SEGMENT_LABEL = "Выполнение теста"

# Куда кладём скрипт на стенде. Тот же путь зашит в команду запуска на стороне
# testing_service (`services/queue.py`, `_STARTER_SCRIPT_PATH`) — два конца
# одного контракта, менять их можно только вместе.
_STARTER_REMOTE_PATH = "/home/u/starter.sh"

# Эталонный текст скрипта лежит рядом с кодом воркера и едет в образ вместе с
# `src/` (см. docker/Dockerfile) — никаких загрузок по сети в момент запуска
# теста.
_STARTER_ASSET_PATH = Path(__file__).resolve().parents[1] / "assets" / "starter.sh"

# Типы, которыми SFTP-запись сигналит о провале: ошибка протокола/канала,
# сетевой обрыв, невалидный ключ (ValueError из write_remote_file) и таймаут.
_WRITE_ERRORS = (asyncssh.Error, OSError, ValueError, asyncio.TimeoutError, TimeoutError)


@lru_cache(maxsize=1)
def _starter_script_content() -> str:
    """Текст `starter.sh` с диска, прочитанный один раз за жизнь процесса."""
    return _STARTER_ASSET_PATH.read_text(encoding="utf-8")


async def _write_starter_script(item: dict, settings) -> str | None:
    """SFTP-запись самого `starter.sh` на стенд перед его запуском.

    Скрипт обязан приезжать на стенд каждый раз, а не считаться «уже там
    лежащим»: перед прогоном стенд откатывается на образ (ACS/revert в
    `prepare-for-test`), и что именно осталось в `/home/u` после отката —
    свойство образа, а не нашей системы. Легаси по той же причине перезаливало
    его на каждый запуск (`allta_app/backup_image.py:932,992`).

    Контракт возврата — как у `_write_dates_file`: текст ошибки либо `None`.
    """
    try:
        content = _starter_script_content()
    except OSError as exc:
        # Битая сборка образа (файл не доехал в `src/assets`) — чинится
        # пересборкой, но item всё равно завершаем честным провалом.
        logger.error("starter.sh asset is unreadable at %s: %s", _STARTER_ASSET_PATH, exc)
        return f"starter.sh asset unreadable: {type(exc).__name__}"

    try:
        await ssh_executor.write_remote_file(
            item["host"],
            item["test_username"],
            item["test_ssh_private_key"],
            _STARTER_REMOTE_PATH,
            content,
            connect_timeout=settings.ssh_connect_timeout_seconds,
        )
    except _WRITE_ERRORS as exc:
        logger.warning(
            "queue item %s: failed to write %s over SFTP: %s",
            item.get("queue_item_id"), _STARTER_REMOTE_PATH, type(exc).__name__,
        )
        return f"SFTP write of starter.sh failed: {type(exc).__name__}"
    return None


async def _write_dates_file(item: dict, settings) -> str | None:
    """SFTP-запись `dates.conf` на стенд перед запуском `starter.sh`.

    Возвращает текст ошибки, если запись провалилась (вызывающий тогда
    заканчивает item как `succeeded=False`, не пытаясь запустить команду),
    либо `None` на успехе. Не логирует `dates_content` целиком — сырые
    dates-флаги (потенциально с кредами Jira/Confluence) в лог не идут,
    только факт записи и её длина.
    """
    dates_filename = item.get("dates_filename")
    dates_content = item.get("dates_content")
    if not dates_filename or dates_content is None:
        return None

    remote_path = f"/home/u/{dates_filename}"
    try:
        await ssh_executor.write_remote_file(
            item["host"],
            item["test_username"],
            item["test_ssh_private_key"],
            remote_path,
            dates_content,
            connect_timeout=settings.ssh_connect_timeout_seconds,
        )
    except _WRITE_ERRORS as exc:
        logger.warning(
            "queue item %s: failed to write %s (%d bytes) over SFTP: %s",
            item.get("queue_item_id"), remote_path, len(dates_content), type(exc).__name__,
        )
        return f"SFTP write of {dates_filename} failed: {type(exc).__name__}"
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
    execute_task = asyncio.ensure_future(ssh_executor.execute(
        item["host"],
        item["test_username"],
        item["test_ssh_private_key"],
        item["command"],
        connect_timeout=settings.ssh_connect_timeout_seconds,
        command_timeout=item.get("command_timeout_seconds") or settings.ssh_command_timeout_seconds,
        on_output_chunk=on_output_chunk,
    ))
    stop_watch = asyncio.Event()
    watch_task = asyncio.ensure_future(_watch_for_interrupt(
        queue_item_id, settings.interrupt_poll_interval_seconds, stop_watch,
    ))

    try:
        await asyncio.wait({execute_task, watch_task}, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        execute_task.cancel()
        watch_task.cancel()
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


async def _await_external_services(queue_item_id: str) -> preflight.PreflightResult:
    """Дождаться внешних сервисов, не переставая слушать заявку на прерывание.

    Ожидание может тянуться до двух часов, поэтому оператор обязан иметь
    возможность снять item и в это время — `should_abort` спрашивает тот же
    `interrupt-check`, что и наблюдатель во время самого теста. Опрос идёт
    раз в цикл проверки (а не раз в `interrupt_poll_interval_seconds`) —
    задержка реакции в минуты на фазе ожидания приемлема и не плодит лишних
    запросов к `testing_service`.
    """
    async def _note_wait(unavailable: list[str], elapsed: float) -> None:
        await testing_client.log_chunk(
            queue_item_id,
            "Ожидание доступности внешних сервисов "
            f"({', '.join(unavailable)}); прошло {int(elapsed)} с\n",
        )

    async def _interrupt_requested() -> str | None:
        return await testing_client.check_interrupt(queue_item_id)

    return await preflight.wait_for_external_services(
        on_wait=_note_wait, should_abort=_interrupt_requested,
    )


async def _run_one_item(item: dict) -> None:
    """Исполнить одно задание из `claim()`, зафиксировать лог и отчитаться `completed`."""
    settings = get_settings()
    queue_item_id = item["queue_item_id"]

    # Пре-флайт внешних сервисов — до любых действий на стенде. Легаси делало
    # ровно то же и в том же месте (`backup_image.py:1045-1050`): `starter.sh`
    # первым делом клонирует ветку с git.astralinux.ru, а сам тест ходит в
    # Jira/Confluence, поэтому кратковременную недоступность надо пережидать,
    # а не сжигать на ней единственную попытку retry.
    gate = await _await_external_services(queue_item_id)
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

    # Сначала сам скрипт, потом его аргумент-файл — обе записи обязательны,
    # первая же осечка заканчивает item, не доводя до `execute()`.
    write_error = await _write_starter_script(item, settings)
    if write_error is None:
        write_error = await _write_dates_file(item, settings)
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
            label=_COMMAND_SEGMENT_LABEL,
            status="OK" if result.succeeded else "FATAL",
            command_text_masked=shlex.join(item.get("command_masked") or []),
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
    )


async def run_polling_loop() -> None:
    """Бесконечный polling loop. Останавливается только через `CancelledError`."""
    settings = get_settings()
    poll_interval = settings.queue_poll_interval_seconds

    logger.info("queue polling loop started (poll_interval=%ss)", poll_interval)

    while True:
        try:
            item = await testing_client.claim()
            if item is None:
                await asyncio.sleep(poll_interval)
                continue

            await _run_one_item(item)
            # Задание обработано — сразу пробуем забрать следующее, без сна.
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — один сбойный проход не должен убивать loop
            logger.exception("queue polling loop: unexpected error in iteration")
            await asyncio.sleep(poll_interval)
