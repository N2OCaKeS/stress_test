"""Состояние перешифровки: singleton-строка `reencrypt_state` + gate-кэш.

Один слой над `ReencryptState`. Две группы потребителей:

* self-drain loop — читает режим, пишет снапшот прогресса, снимает force-флаг
  по завершении;
* maintenance-gate (middleware) + status-эндпоинт — читают снапшот, чтобы
  решить, блокировать ли запрос и какой Retry-After отдать.

Чтобы gate не бил в БД на каждый запрос, состояние кэшируется в процессе на
короткий TTL (`settings.reencrypt_gate_cache_ttl_seconds`). Реплики сходятся к
общему состоянию из БД в пределах этого окна — для maintenance-окна, которое
длится минуты, задержка в пару секунд несущественна.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.db.session import AsyncSessionLocal
from src.models import MODE_FORCE, MODE_LAZY, STATE_ROW_ID, ReencryptState


@dataclass(frozen=True)
class StateSnapshot:
    """Иммутабельный слепок строки состояния для читателей."""

    mode: str
    force_active: bool
    remaining: int
    throughput: float
    eta_seconds: int


# Процессный кэш для gate. Значение — (снапшот, monotonic-время записи).
_cache: dict[str, object] = {"snapshot": None, "ts": 0.0}


async def _load_row(db: AsyncSession) -> ReencryptState:
    """Прочитать singleton-строку, создав её при отсутствии.

    Миграция засевает строку id=1, но на всякий случай (свежая БД без
    прогонанной миграции, тест) создаём дефолтную, если её нет.
    """
    row = await db.get(ReencryptState, STATE_ROW_ID)
    if row is None:
        row = ReencryptState(
            id=STATE_ROW_ID,
            mode=MODE_LAZY,
            force_active=False,
            remaining=0,
            throughput=0.0,
            eta_seconds=0,
        )
        db.add(row)
        await db.flush()
    return row


def _to_snapshot(row: ReencryptState) -> StateSnapshot:
    return StateSnapshot(
        mode=row.mode,
        force_active=bool(row.force_active),
        remaining=int(row.remaining or 0),
        throughput=float(row.throughput or 0.0),
        eta_seconds=int(row.eta_seconds or 0),
    )


async def get_state(db: AsyncSession) -> StateSnapshot:
    """Свежий слепок состояния из БД (без кэша)."""
    row = await _load_row(db)
    return _to_snapshot(row)


def invalidate_cache() -> None:
    """Сбросить процессный gate-кэш (после rotate/clear — чтобы gate увидел
    новое состояние немедленно, не дожидаясь TTL)."""
    _cache["snapshot"] = None
    _cache["ts"] = 0.0


async def get_cached_state() -> StateSnapshot:
    """Слепок состояния для gate: из кэша, пока тот не старше TTL.

    Открывает собственную короткую сессию — middleware работает вне
    `Depends(get_db)`. На ошибке чтения возвращает «пропускающий» дефолт
    (force снят): падать в open, а не в closed, — иначе сбой БД превратил бы
    сервис в сплошной 503.
    """
    settings = get_settings()
    ttl = settings.reencrypt_gate_cache_ttl_seconds
    now = time.monotonic()
    snapshot = _cache.get("snapshot")
    if snapshot is not None and (now - float(_cache["ts"])) < ttl:
        return snapshot  # type: ignore[return-value]

    try:
        async with AsyncSessionLocal() as session:
            fresh = await get_state(session)
    except Exception:  # noqa: BLE001 — fail-open, см. docstring
        fresh = StateSnapshot(
            mode=MODE_LAZY, force_active=False, remaining=0,
            throughput=0.0, eta_seconds=0,
        )
    _cache["snapshot"] = fresh
    _cache["ts"] = now
    return fresh


async def enter_force(
    db: AsyncSession, *, remaining: int, throughput: float | None = None
) -> None:
    """Включить force-режим: mode=force, force_active=True, снапшот remaining."""
    row = await _load_row(db)
    row.mode = MODE_FORCE
    row.force_active = True
    row.remaining = int(remaining)
    if throughput is not None:
        row.throughput = float(throughput)
    row.eta_seconds = compute_retry_after(
        remaining=int(remaining), throughput=row.throughput
    )[1]
    row.started_at = datetime.now(timezone.utc)
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    invalidate_cache()


async def set_lazy(db: AsyncSession) -> None:
    """Перевести в lazy и снять force-флаг (обычная ротация без окна)."""
    row = await _load_row(db)
    row.mode = MODE_LAZY
    row.force_active = False
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    invalidate_cache()


async def clear_force(db: AsyncSession) -> None:
    """Снять force-флаг по завершении перешифровки (remaining == 0)."""
    row = await _load_row(db)
    row.mode = MODE_LAZY
    row.force_active = False
    row.remaining = 0
    row.eta_seconds = 0
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    invalidate_cache()


async def update_progress(
    db: AsyncSession, *, remaining: int, throughput: float, eta_seconds: int
) -> None:
    """Обновить снапшот прогресса (пишет self-drain loop на каждом тике)."""
    row = await _load_row(db)
    row.remaining = int(remaining)
    row.throughput = float(throughput)
    row.eta_seconds = int(eta_seconds)
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    invalidate_cache()


def compute_retry_after(
    *, remaining: int, throughput: float
) -> tuple[int, int]:
    """Посчитать `(retry_after, eta_seconds)` для maintenance-gate.

    ETA = ceil(remaining / throughput); если throughput не измерен — берём
    дефолтный из настроек, чтобы не делить на ноль. Retry-After = ETA + буфер,
    зажатый в `[min, max]`. Пересчитывается на каждый запрос.
    """
    settings = get_settings()
    tp = throughput if throughput and throughput > 0 else settings.reencrypt_default_throughput
    remaining = max(int(remaining), 0)
    eta = int(math.ceil(remaining / tp)) if remaining > 0 else 0
    retry_after = eta + settings.reencrypt_retry_after_buffer
    retry_after = max(settings.reencrypt_retry_after_min, retry_after)
    retry_after = min(settings.reencrypt_retry_after_max, retry_after)
    return retry_after, eta
