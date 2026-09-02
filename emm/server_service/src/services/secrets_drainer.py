"""Фоновый дренер reencrypt-outbox внутри server_service.

Раньше осушением outbox'а занимался server_worker через taskiq-scheduler,
которого в проде нет, — из-за этого дренаж не шёл и старый ключ нельзя было
вывести. Теперь каждый инстанс server_service поднимает собственный
asyncio-таск (см. lifespan в `main.py`):

* батчи клеймятся через `claim_pending` (`FOR UPDATE SKIP LOCKED`), поэтому
  несколько реплик берут непересекающиеся блоки — работа шардится сама;
* каждая строка перешифровывается существующей логикой `finalize_done`
  (decrypt старым ключом → encrypt активным), которая заодно хукает
  авто-retire опустевшей версии;
* каденс зависит от режима: lazy — малый батч и заметная пауза (не мешать
  пользователям), force — крупный батч и минимальная пауза (осушить быстро);
* когда очередь пуста и остаток равен нулю, `finish_if_drained` доводит
  авто-retire и снимает force-флаг (разблокирует сервис).

Таск устойчив: любое исключение итерации логируется и уходит в backoff, цикл
продолжается. На shutdown таск получает stop-событие и завершается.
"""

from __future__ import annotations

import asyncio
import logging

from src.core.config import Settings
from src.services import secrets_migration_service

logger = logging.getLogger(__name__)

# Сколько ждать штатного завершения дренера на shutdown, прежде чем отменить.
_STOP_TIMEOUT_SECONDS = 10.0


async def _process_claimed(db, claimed: list[dict]) -> int:
    """Финализировать claim'нутые строки. Возвращает число успешно закрытых.

    На успех — `finalize_done` (перешифровка + close). На ошибке decrypt/encrypt
    откатываем текущую транзакцию и помечаем строку `failed`, чтобы она не
    зависла в `processing`; оператор разберётся по `last_error` и при желании
    вернёт её в `pending`.
    """
    processed = 0
    for item in claimed:
        outbox_id = item["id"]
        try:
            await secrets_migration_service.finalize_done(db, outbox_id)
            processed += 1
        except Exception as exc:  # noqa: BLE001 — битый ciphertext / пропавший ключ
            error = f"drain finalize_done failed: {type(exc).__name__}"
            logger.warning("reencrypt drain: %s (outbox=%s)", error, outbox_id)
            try:
                await db.rollback()
            except Exception:  # noqa: BLE001
                pass
            try:
                await secrets_migration_service.finalize_failed(db, outbox_id, error=error)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "reencrypt drain: could not mark outbox %s failed", outbox_id
                )
    return processed


async def drain_once(db, *, force: bool, settings: Settings) -> dict:
    """Одна итерация дренажа поверх переданной сессии.

    Клеймит батч (размер зависит от режима), финализирует его. Если очередь
    пуста — проверяет завершение миграции; при активном force и непустом
    остатке без outbox-строк добивает seed (закрывает возможный gap).

    Возвращает `{claimed, processed, finished, reseeded}`.
    """
    batch = (
        settings.reencrypt_force_batch_size
        if force
        else settings.reencrypt_lazy_batch_size
    )
    claimed = await secrets_migration_service.claim_pending(db, limit=batch)
    processed = await _process_claimed(db, claimed)

    finished = False
    reseeded = 0
    if not claimed:
        finished = await secrets_migration_service.finish_if_drained(db)
        if not finished and force:
            # Force активен, pending пуст, но остаток есть — outbox недосеян
            # (например, ротация засеяла не всё). Досеваем, чтобы force дошёл
            # до конца, а не завис навсегда с взведённым флагом.
            remaining = await secrets_migration_service.remaining_legacy(db)
            if remaining > 0:
                seed = await secrets_migration_service.seed_outbox(db)
                reseeded = seed["inserted"]
    return {
        "claimed": len(claimed),
        "processed": processed,
        "finished": finished,
        "reseeded": reseeded,
    }


class DrainLoop:
    """Долгоживущий asyncio-таск, осушающий reencrypt-outbox текущего инстанса."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Запустить фоновый таск. Идемпотентно."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="reencrypt-drainer")

    async def stop(self) -> None:
        """Просигналить остановку и дождаться (или отменить по таймауту)."""
        self._stop.set()
        if self._task is None:
            return
        try:
            await asyncio.wait_for(self._task, timeout=_STOP_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        finally:
            self._task = None

    async def _sleep(self, seconds: float) -> None:
        """Спать `seconds`, но проснуться сразу на stop-событии."""
        if seconds <= 0:
            return
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _run(self) -> None:
        logger.info("reencrypt drain loop started")
        while not self._stop.is_set():
            try:
                sleep_for = await self._tick()
            except Exception as exc:  # noqa: BLE001 — не роняем таск на сбое итерации
                logger.warning(
                    "reencrypt drain tick failed (%s); backing off",
                    type(exc).__name__,
                )
                sleep_for = self._settings.reencrypt_idle_sleep_seconds
            await self._sleep(sleep_for)
        logger.info("reencrypt drain loop stopped")

    async def _tick(self) -> float:
        """Одна итерация в собственной короткой сессии. Возвращает паузу до след."""
        from src.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            force = await secrets_migration_service.get_force_active(db)
            result = await drain_once(db, force=force, settings=self._settings)

        did_work = result["claimed"] > 0 or result["reseeded"] > 0
        if did_work:
            return (
                self._settings.reencrypt_force_sleep_seconds
                if force
                else self._settings.reencrypt_lazy_sleep_seconds
            )
        return self._settings.reencrypt_idle_sleep_seconds
