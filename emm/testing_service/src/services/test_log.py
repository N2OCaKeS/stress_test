"""Приём и чтение логов прогонов (§2.6, §8 плана миграции).

Единый источник истины формата блока лога — `format_block()` здесь, не на
стороне `testing_worker`: воркер шлёт структурные поля (`kind`/`label`/
`status`/`command_text_masked`/`output`/`host`/`started_at`/`finished_at`),
`testing_service` сам собирает из них текст один в один с легаси-декоратором
(`libs/allta/allta/_vm_controller/_decorator/_logger.py`).

Сырой текст лога (`test_log_blobs.content`) читается и переписывается целиком
в память на каждом append'е — ожидаемый объём (тексты прогонов тестов, не
гигабайты) это позволяет и сильно упрощает код (никаких байтовых офсетов на
стороне Postgres, никакой возни с `substring`/`octet_length`). Смещения
сегментов — это индексы Python-строки на момент записи, согласованные между
собой той же функцией, которая их считает.

Конкурентная запись одного лога (в т.ч. лениво создающие его первый чанк и
первый сегмент) сериализуется через `SELECT ... FOR UPDATE` на строке
`test_logs` (см. `repositories/test_log.py::get_by_queue_item_id_for_update`).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AuthorizationError, DomainValidationError, NotFoundError
from src.dependencies.auth import Identity
from src.models import TestLog, TestLogSegment
from src.repositories import queue_item as queue_item_repo
from src.repositories import test_definition as test_definition_repo
from src.repositories import test_log as test_log_repo
from src.repositories import test_log_blob as test_log_blob_repo
from src.repositories import test_log_segment as test_log_segment_repo
from src.repositories import test_stand as stand_repo
from src.schemas.test_log import LogSegmentRequest
from src.services import log_rotation, permissions
from src.utils.ids import test_log_id as new_log_id
from src.utils.ids import test_log_segment_id as new_segment_id

_MSK = ZoneInfo("Europe/Moscow")
_BORDER_LEN = 66
_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _to_msk(dt: datetime) -> datetime:
    """Naive datetime трактуется как UTC (Pydantic парсит ISO-строки без offset'а как naive)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_MSK)


def format_block(
    *, label: str, host: str, status: str, command_text_masked: str | None, output: str, started_at: datetime,
) -> str:
    """Один блок лога — буквальный формат легаси-декоратора (§8.1 плана миграции).

    `#`-рамка только у `FATAL`, иначе `*`. Реальный легаси-код не несёт "MSK"
    суффикса в метке времени (только `HH:MM:SS DD-MM-YYYY`, с ведущим и
    завершающим пробелом внутри квадратных скобок) — этот код воспроизводит
    именно его, а не более раннюю (неточную) редакцию плана.
    """
    border_char = "#" if status == "FATAL" else "*"
    border_line = border_char * _BORDER_LEN
    ts = _to_msk(started_at).strftime("%H:%M:%S %d-%m-%Y ")
    command = command_text_masked or ""
    return (
        f"{border_line}\n"
        f"TASK [{label}: {host}]\n"
        f"[ {ts}]\n"
        f"STATUS [{status}]\n"
        f"COMMAND: {command}\n\n"
        f"CONCLUSION: {output}\n"
        f"{border_line}\n\n\n"
    )


async def _build_internal_path(db: AsyncSession, queue_item) -> tuple[str, str | None, str | None, str | None]:
    """Собрать логический `internal_path` + снэпшот os_version_major/rc/kernel.

    `department_id` участвует только в пути (не персистится отдельной
    колонкой на `test_logs` — план не заводит такое поле, ссылка на отдел
    и так восстановима через `stand_id` при необходимости).
    """
    stand = await stand_repo.get_by_id(db, queue_item.stand_id)
    department_id = stand.department_id if stand is not None else "unknown"
    ctx = queue_item.launch_context or {}
    rc = ctx.get("RC")
    kernel = ctx.get("KERNEL")
    os_version_major = ctx.get("OS_VERSION_MAJOR")
    internal_path = (
        f"logs/{department_id}/{os_version_major or 'unknown'}/{rc or 'unknown'}/"
        f"{queue_item.stand_id}/{queue_item.id}.log"
    )
    return internal_path, os_version_major, rc, kernel


async def get_or_create_log(db: AsyncSession, queue_item_id: str) -> TestLog:
    """Найти лог этого queue_item'а или лениво завести его на первом чанке/сегменте."""
    log = await test_log_repo.get_by_queue_item_id_for_update(db, queue_item_id)
    if log is not None:
        return log

    queue_item = await queue_item_repo.get_by_id(db, queue_item_id)
    if queue_item is None:
        raise NotFoundError(
            error_code="QUEUE_ITEM_NOT_FOUND",
            message="Queue item not found",
        )

    internal_path, os_version_major, rc, kernel = await _build_internal_path(db, queue_item)
    data = {
        "id": new_log_id(),
        "queue_item_id": queue_item.id,
        "stand_id": queue_item.stand_id,
        "test_id": queue_item.test_id,
        "os_version_major": os_version_major,
        "rc": rc,
        "kernel": kernel,
        "internal_path": internal_path,
        "size_bytes": 0,
        "protected": False,
    }
    try:
        async with db.begin_nested():
            log = await test_log_repo.create(db, data)
            await test_log_blob_repo.create(db, log.id)
    except IntegrityError:
        # Конкурентная реплика уже создала лог для этого queue_item_id между
        # нашим SELECT и INSERT'ом (UNIQUE(queue_item_id) откатывает savepoint)
        # — просто перечитываем то, что она успела вставить.
        log = await test_log_repo.get_by_queue_item_id_for_update(db, queue_item_id)
        if log is None:
            raise
        return log

    if os_version_major:
        await log_rotation.recompute_protection_for_branch(db, os_version_major)
        await db.refresh(log)
    return log


async def append_chunk(db: AsyncSession, queue_item_id: str, text: str) -> TestLog:
    """Накопить сырой вывод ещё выполняющейся команды. Не создаёт сегмент."""
    log = await get_or_create_log(db, queue_item_id)
    blob = await test_log_blob_repo.get_by_log_id(db, log.id)
    content = (blob.content if blob else "") + text
    blob.content = content
    log.size_bytes = len(content)
    await db.commit()
    await db.refresh(log)
    return log


async def append_segment(db: AsyncSession, queue_item_id: str, payload: LogSegmentRequest) -> TestLog:
    """Зафиксировать один завершённый шаг: аппендить блок в blob + вставить строку сегмента."""
    log = await get_or_create_log(db, queue_item_id)
    blob = await test_log_blob_repo.get_by_log_id(db, log.id)
    current = blob.content or ""
    block = format_block(
        label=payload.label, host=payload.host, status=payload.status,
        command_text_masked=payload.command_text_masked, output=payload.output,
        started_at=payload.started_at,
    )
    start = len(current)
    blob.content = current + block
    end = start + len(block)
    log.size_bytes = end

    position = await test_log_segment_repo.next_position(db, log.id)
    await test_log_segment_repo.create(db, {
        "id": new_segment_id(),
        "log_id": log.id,
        "position": position,
        "kind": payload.kind,
        "label": payload.label,
        "command_text_masked": payload.command_text_masked,
        "status": payload.status,
        "started_at": payload.started_at,
        "finished_at": payload.finished_at,
        "byte_offset_start": start,
        "byte_offset_end": end,
    })
    await db.commit()
    await db.refresh(log)
    return log


async def stand_department_id(db: AsyncSession, stand_id: str) -> str | None:
    """Отдел-владелец стенда. `test_logs` своей колонки отдела не несут (см. модуль модели)."""
    stand = await stand_repo.get_by_id(db, stand_id)
    return stand.department_id if stand is not None else None


async def require_stand_access(db: AsyncSession, identity: Identity, stand_id: str) -> None:
    """Гейт чтения лога: текст прогона видит только отдел-владелец стенда.

    Стенд не найден (снесён после появления лога) — отдел не восстановить,
    считаем лог невидимым, а не общедоступным.
    """
    department_id = await stand_department_id(db, stand_id)
    if department_id is None:
        raise AuthorizationError(
            error_code="DEPARTMENT_ISOLATION",
            message="Cannot resolve the owning department of this log",
        )
    permissions.require_own_department(identity, department_id)


async def get_full_log(db: AsyncSession, identity: Identity, queue_item_id: str) -> tuple[TestLog, str]:
    """Лог + полный текст. 404, если для этого queue_item лога ещё нет."""
    log = await test_log_repo.get_by_queue_item_id(db, queue_item_id)
    if log is None:
        raise NotFoundError(
            error_code="TEST_LOG_NOT_FOUND",
            message="No log for this queue item yet (test has not started or produced its first chunk)",
        )
    await require_stand_access(db, identity, log.stand_id)
    blob = await test_log_blob_repo.get_by_log_id(db, log.id)
    return log, (blob.content if blob else "")


async def get_log_range(
    db: AsyncSession, identity: Identity, queue_item_id: str, from_: int | None, to_: int | None,
) -> tuple[TestLog, str]:
    """Диапазон текста лога. Отсутствующая граница — 0/конец. Невалидный диапазон — 422."""
    log, content = await get_full_log(db, identity, queue_item_id)
    length = len(content)
    start = 0 if from_ is None else from_
    end = length if to_ is None else to_
    if start < 0 or end < start or end > length:
        raise DomainValidationError(
            error_code="LOG_RANGE_INVALID",
            message="Invalid range for this log",
            details={"from": start, "to": end, "length": length},
        )
    return log, content[start:end]


async def list_segments(
    db: AsyncSession, identity: Identity, queue_item_id: str, *,
    status: str | None = None, limit: int = 500, offset: int = 0,
) -> tuple[list[TestLogSegment], int]:
    """Страница сегментов лога. 404, если для этого queue_item лога ещё нет."""
    log = await test_log_repo.get_by_queue_item_id(db, queue_item_id)
    if log is None:
        raise NotFoundError(
            error_code="TEST_LOG_NOT_FOUND",
            message="No log for this queue item yet (test has not started or produced its first chunk)",
        )
    await require_stand_access(db, identity, log.stand_id)
    items = await test_log_segment_repo.list_by_log(db, log.id, status=status, limit=limit, offset=offset)
    total = await test_log_segment_repo.count_by_log(db, log.id, status=status)
    return items, total


def _sanitize_filename_part(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    cleaned = _FILENAME_SAFE_RE.sub("_", value).strip("_")
    return cleaned or fallback


async def build_filename(db: AsyncSession, log: TestLog) -> str:
    """Человекочитаемое имя файла для Content-Disposition (§8.3 плана миграции).

    `<stand>` — `stand_id` (у `test_stands` пока нет отдельного display-имени,
    см. отчёт волны). `<testname>` — `test_definitions.code` (уже
    filename-безопасный по конвенции каталога, в отличие от `full_name`).
    """
    test = await test_definition_repo.get_by_id(db, log.test_id)
    testname = _sanitize_filename_part(test.code if test else None, "test")
    stand = _sanitize_filename_part(log.stand_id, "stand")
    os_version = _sanitize_filename_part(log.rc, "rc")
    kernel = _sanitize_filename_part(log.kernel, "kernel")
    date_str = log.created_at.strftime("%Y%m%d")
    return f"{stand}_{testname}_{os_version}_{kernel}_{date_str}.log"
