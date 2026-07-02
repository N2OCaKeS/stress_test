"""Self-drain loop: перешифровка reencrypt-outbox прямо в процессе secret_service.

В проде нет отдельного worker'а (taskiq-scheduler'а тоже нет), поэтому дренаж
outbox'а живёт здесь — фоновым asyncio-таском из lifespan'а. Он берёт батчи
через `reencrypt_outbox_service.process_batch` (claim идёт `FOR UPDATE SKIP
LOCKED`, так что несколько реплик разбирают непересекающиеся блоки), меряет
throughput, обновляет снапшот прогресса и по завершении авто-выводит опустевшие
версии ключа.

Каденс:

* force — плотный цикл (крупный батч + короткая пауза): maintenance-окно надо
  закрыть как можно быстрее;
* lazy — троттлинг (мелкий батч + пауза подлиннее): фон не мешает нагрузке;
* дренить нечего — длинная idle-пауза.

Loop никогда не валит сервис: любое исключение в тике логируется и глотается
(backoff), на shutdown — чистый выход по CancelledError.
"""

from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.keystore import get_keystore
from src.db.session import AsyncSessionLocal
from src.services import (
    key_rotation_service,
    migration_status_service,
    reencrypt_state_service,
)

logger = logging.getLogger(__name__)

# Сглаживание throughput: EWMA, новый замер весит половину.
_THROUGHPUT_ALPHA = 0.5


async def _measure_remaining(db: AsyncSession) -> tuple[int, int]:
    """`(remaining_legacy, outbox_pending)` — сколько ещё дренить."""
    active = get_keystore().get_active_version()
    status = await migration_status_service.compute(db, active_version=active)
    return int(status.remaining_legacy), int(status.outbox_pending_count)


async def drain_tick(db: AsyncSession) -> dict:
    """Один тик дренажа. Возвращает `{processed, remaining, force_active}`.

    Порядок: читаем режим → обрабатываем батч → меряем остаток → обновляем
    снапшот. Если остатка и pending'а нет — авто-retire опустевших версий и,
    если было force-окно, снимаем force-флаг (разблокировка).
    """
    settings = get_settings()
    state = await reencrypt_state_service.get_state(db)
    force_active = state.force_active

    batch_size = (
        settings.reencrypt_force_batch_size
        if force_active
        else settings.reencrypt_lazy_batch_size
    )

    started = time.monotonic()
    result = await reencrypt_outbox_process(db, batch_size=batch_size)
    elapsed = max(time.monotonic() - started, 1e-6)
    processed = int(result["processed"])

    remaining_legacy, outbox_pending = await _measure_remaining(db)

    # throughput — EWMA по фактически обработанным строкам за тик.
    throughput = state.throughput
    if processed > 0:
        instant = processed / elapsed
        throughput = (
            instant
            if throughput <= 0
            else _THROUGHPUT_ALPHA * instant + (1 - _THROUGHPUT_ALPHA) * throughput
        )

    if remaining_legacy == 0 and outbox_pending == 0:
        # Дренаж завершён. Выводим опустевшие версии и — если было force-окно —
        # снимаем maintenance-gate. Оба шага best-effort в рамках тика.
        try:
            retired = await key_rotation_service.auto_retire_drained(db)
            if retired:
                logger.info("self-drain auto-retired versions %s", retired)
        except Exception:  # noqa: BLE001 — тик не должен падать
            logger.exception("self-drain auto-retire failed")
        if force_active:
            await reencrypt_state_service.clear_force(db)
            logger.info("self-drain: force window cleared, gate lifted")
        else:
            await reencrypt_state_service.update_progress(
                db, remaining=0, throughput=throughput, eta_seconds=0
            )
        return {"processed": processed, "remaining": 0, "force_active": False}

    _, eta = reencrypt_state_service.compute_retry_after(
        remaining=remaining_legacy, throughput=throughput
    )
    await reencrypt_state_service.update_progress(
        db, remaining=remaining_legacy, throughput=throughput, eta_seconds=eta
    )
    return {
        "processed": processed,
        "remaining": remaining_legacy,
        "force_active": force_active,
    }


async def reencrypt_outbox_process(db: AsyncSession, *, batch_size: int) -> dict:
    """Тонкая обёртка над process_batch — чтобы тесты могли подменить дренаж.

    Импорт ленивый: избегаем цикла reencrypt_outbox_service ↔ drain на import.
    """
    from src.services import reencrypt_outbox_service

    return await reencrypt_outbox_service.process_batch(db, batch_size=batch_size)


def _sleep_seconds(tick: dict) -> float:
    """Пауза до следующего тика по режиму и наличию работы."""
    settings = get_settings()
    if tick["force_active"]:
        return settings.reencrypt_force_interval_seconds
    if tick["remaining"] > 0 or tick["processed"] > 0:
        return settings.reencrypt_lazy_interval_seconds
    return settings.reencrypt_idle_interval_seconds


async def drain_loop() -> None:
    """Фоновый task: гоняет `drain_tick` с каденсом по режиму.

    Graceful shutdown — тихий выход на CancelledError. Любая другая ошибка в
    тике логируется и не валит цикл; следующий тик пробует снова после idle-
    паузы (backoff).
    """
    settings = get_settings()
    logger.info(
        "reencrypt drain loop started (lazy_batch=%d, force_batch=%d)",
        settings.reencrypt_lazy_batch_size,
        settings.reencrypt_force_batch_size,
    )
    try:
        while True:
            try:
                async with AsyncSessionLocal() as session:
                    tick = await drain_tick(session)
                sleep_for = _sleep_seconds(tick)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — тик не валит loop
                logger.warning("reencrypt drain tick failed: %s", exc)
                sleep_for = settings.reencrypt_idle_interval_seconds
            await asyncio.sleep(sleep_for)
    except asyncio.CancelledError:
        logger.info("reencrypt drain loop cancelled, exiting cleanly")
        return
