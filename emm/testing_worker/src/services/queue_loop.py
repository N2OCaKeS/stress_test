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
   Если `item["prepare_only"]` — следом уходят ещё два файла: легаси
   testenv-маркер (`_write_testenv_marker`, заставляет `starter.sh`
   остановиться после `prepare.sh` и не запускать тест) и `command.txt`
   (`_write_prepare_only_command_file`, текст команды, которой тест был бы
   запущен). testing_service сам решает при `complete_item`, что успешный
   исход такого item'а — не `succeeded`, а `prepared`.
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

`run_polling_loop()` НЕ ждёт шаги 2-7 перед следующим `claim()` — item
запускается как отдельный `asyncio.Task` (`_run_one_item`), а цикл сразу же
идёт за следующим. Никакого искусственного потолка одновременных item'ов
здесь нет и не нужен: `claim_next_ready()` на стороне testing_service отдаёт
только `state=ready` item'ы, а по конструкции самой очереди (сериализация по
стенду, `services/queue.py`) `ready` одновременно может быть не больше одного
на стенд — реальный предел параллельности сам получается «один тест на
стенд», без счётчика, который надо подбирать и держать в синхроне с числом
стендов. SSH-сессия почти всё время ждёт сеть, не грузит CPU — конкурентные
задачи внутри одного процесса вместо последовательной очереди только это и
используют, не более того.

Тело цикла обёрнуто в `try/except Exception`: неожиданная ошибка `claim()`а
(баг в коде, а не транзиентная сетевая недоступность — та уже обработана
внутри `testing_client`) не должна убивать весь loop навсегда. На такой
ошибке — ERROR-лог и сон `queue_poll_interval_seconds`, чтобы систематический
баг не заспамил лог тысячами повторов в секунду. Упавшая задача одного item'а
— тем же приёмом (лог + продолжение), но не тормозит остальные уже
запущенные item'ы: у каждой задачи свой `done`-callback.
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
_PREPARE_ONLY_SEGMENT_LABEL = "Подготовка стенда (testenv)"

# Куда кладём скрипт на стенде. Тот же путь зашит в команду запуска на стороне
# testing_service (`services/queue.py`, `_STARTER_SCRIPT_PATH`) — два конца
# одного контракта, менять их можно только вместе.
_STARTER_REMOTE_PATH = "/home/u/starter.sh"

# Легаси-маркер testenv-режима: `starter.sh` матчит имя глобом
# `testenv_*.conf` и сверяет содержимое с `on` (см. `assets/starter.sh`).
# Само имя файла не принципиально, лишь бы попадало под маску.
_TESTENV_MARKER_REMOTE_PATH = "/home/u/testenv_on.conf"
_TESTENV_MARKER_CONTENT = "on"

# Куда кладём текст команды теста в prepare_only-режиме — стенд подготовлен,
# но сам тест не запускался, и это единственный след того, что было бы
# исполнено.
_PREPARE_ONLY_COMMAND_REMOTE_PATH = "/home/u/command.txt"

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


async def _write_git_token_file(item: dict, settings) -> str | None:
    """SFTP-запись git-токена на стенд отдельным файлом — не argv.

    Раньше токен ехал вторым позиционным аргументом `starter.sh` и оставался
    виден в `ps`/`/proc/<pid>/cmdline` весь срок теста (до
    `command_timeout_seconds`, 12 часов по дефолту). Тот же приём, что уже
    есть у `dates.conf`: файл на стенде, а `starter.sh` подставляет его
    содержимое в заголовок `Authorization` через `cat` и сам стирает файл
    сразу после клонирования.

    Контракт возврата — как у `_write_dates_file`. Отсутствие обоих полей
    (старый/тестовый `item` без них) — не провал, просто нечего писать.
    """
    git_token_filename = item.get("git_token_filename")
    git_token_content = item.get("git_token_content")
    if not git_token_filename or git_token_content is None:
        return None

    remote_path = f"/home/u/{git_token_filename}"
    try:
        await ssh_executor.write_remote_file(
            item["host"],
            item["test_username"],
            item["test_ssh_private_key"],
            remote_path,
            git_token_content,
            connect_timeout=settings.ssh_connect_timeout_seconds,
        )
    except _WRITE_ERRORS as exc:
        logger.warning(
            "queue item %s: failed to write %s over SFTP: %s",
            item.get("queue_item_id"), remote_path, type(exc).__name__,
        )
        return f"SFTP write of {git_token_filename} failed: {type(exc).__name__}"
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


async def _write_testenv_marker(item: dict, settings) -> str | None:
    """SFTP-запись testenv-маркера — легаси-сигнал `starter.sh` не запускать тест.

    `prepare_only=True` на item'е воспроизводит старый ручной режим стенда:
    файл `/home/u/testenv_*.conf` со значением `on` заставлял `starter.sh`
    выполнить `prepare.sh` и выйти, не доходя до `run.py` (см. хвост
    `assets/starter.sh`). Раньше этот файл никто не писал программно — он
    появлялся только руками оператора на стенде, поэтому ветка была
    фактически недостижима. Здесь она наконец имеет источник.

    Контракт возврата — как у `_write_dates_file`: текст ошибки либо `None`.
    """
    try:
        await ssh_executor.write_remote_file(
            item["host"],
            item["test_username"],
            item["test_ssh_private_key"],
            _TESTENV_MARKER_REMOTE_PATH,
            _TESTENV_MARKER_CONTENT,
            connect_timeout=settings.ssh_connect_timeout_seconds,
        )
    except _WRITE_ERRORS as exc:
        logger.warning(
            "queue item %s: failed to write %s over SFTP: %s",
            item.get("queue_item_id"), _TESTENV_MARKER_REMOTE_PATH, type(exc).__name__,
        )
        return f"SFTP write of testenv marker failed: {type(exc).__name__}"
    return None


def _build_prepare_only_command(item: dict) -> str:
    """Собрать текст команды `run.py`, которую запустил бы `starter.sh`.

    В `prepare_only`-режиме сам `starter.sh` выходит раньше, чем дошёл бы до
    этой строчки (testenv-ветка делает `exit 0`), поэтому команду считаем
    здесь, теми же правилами, что и хвост `assets/starter.sh`:

        if [ "$5" == "kernel" ]; then python3 run.py -n "$3" -kn "$5"
        elif [ "$5" == "balance" ]; then python3 run.py -n "$3" -bl "$5"
        elif [ "$5" == "oom" ]; then python3 run.py -n "$3" -oom "$5"
        else python3 run.py -n "$3"

    `$3` — это `dates_filename` (позиционный аргумент `starter.sh`, не имя
    теста, так исторически заведено в легаси), `$5` — `starter_suffix` теста.
    Оба присланы testing_service в `item`.
    """
    dates_filename = item.get("dates_filename") or ""
    suffix = item.get("starter_suffix") or ""
    if suffix == "kernel":
        return f'python3 run.py -n "{dates_filename}" -kn "{suffix}"'
    if suffix == "balance":
        return f'python3 run.py -n "{dates_filename}" -bl "{suffix}"'
    if suffix == "oom":
        return f'python3 run.py -n "{dates_filename}" -oom "{suffix}"'
    return f'python3 run.py -n "{dates_filename}"'


async def _write_prepare_only_command_file(item: dict, settings) -> str | None:
    """SFTP-запись `command.txt` — след того, что было бы запущено на стенде.

    Единственное содержимое, ради которого вообще существует `prepare_only`:
    оператор откатывает и готовит стенд, но вместо результата теста получает
    команду, которую можно скопировать и запустить руками. Содержимое не
    секретно (никаких токенов — только имя dates-файла и суффикс теста),
    маскировать нечего.

    Контракт возврата — как у `_write_dates_file`.
    """
    content = _build_prepare_only_command(item)
    try:
        await ssh_executor.write_remote_file(
            item["host"],
            item["test_username"],
            item["test_ssh_private_key"],
            _PREPARE_ONLY_COMMAND_REMOTE_PATH,
            content,
            connect_timeout=settings.ssh_connect_timeout_seconds,
        )
    except _WRITE_ERRORS as exc:
        logger.warning(
            "queue item %s: failed to write %s over SFTP: %s",
            item.get("queue_item_id"), _PREPARE_ONLY_COMMAND_REMOTE_PATH, type(exc).__name__,
        )
        return f"SFTP write of command.txt failed: {type(exc).__name__}"
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
    git_token_content = item.get("git_token_content")
    execute_task = asyncio.ensure_future(ssh_executor.execute(
        item["host"],
        item["test_username"],
        item["test_ssh_private_key"],
        item["command"],
        connect_timeout=settings.ssh_connect_timeout_seconds,
        command_timeout=item.get("command_timeout_seconds") or settings.ssh_command_timeout_seconds,
        on_output_chunk=on_output_chunk,
        redact_secrets=[git_token_content] if git_token_content else None,
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
    # первая же осечка заканчивает item, не доводя до `execute()`. В
    # prepare_only-режиме следом уходят ещё два файла: testenv-маркер (без
    # него ветка в starter.sh недостижима) и command.txt (иначе после
    # прогона на стенде не остаётся вообще никакого следа от него).
    write_error = await _write_starter_script(item, settings)
    if write_error is None:
        write_error = await _write_git_token_file(item, settings)
    if write_error is None:
        write_error = await _write_dates_file(item, settings)
    if write_error is None and item.get("prepare_only"):
        write_error = await _write_testenv_marker(item, settings)
        if write_error is None:
            write_error = await _write_prepare_only_command_file(item, settings)
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
            label=_PREPARE_ONLY_SEGMENT_LABEL if item.get("prepare_only") else _COMMAND_SEGMENT_LABEL,
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
