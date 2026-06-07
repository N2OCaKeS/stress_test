"""Liveness и readiness пробы. Без авторизации."""

import time

import sqlalchemy

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from src.core.config import get_settings
from src.dependencies.db import get_db

router = APIRouter()


@router.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}


@router.get("/ready", include_in_schema=False)
def ready(db: Session = Depends(get_db)):
    """Readiness-проба: БД + self-audit pipeline.

    Раньше /ready пинговал только БД: если drain-task self-audit outbox'а
    падал по unhandled exception, или сам outbox не поднялся, k8s продолжал
    роутить трафик в pod, и http.* events валились через sync-fallback
    (`asyncio.to_thread`), насыщая pool ровно как до outbox'а. Аналогично
    daemon-thread retention'а мог тихо умереть — sweep больше не идёт, но
    pod выглядит здоровым.

    Сейчас проверяем три инварианта:
      * `SELECT 1` отвечает за разумное время (БД live);
      * audit_outbox поднят и `_drain_task.done()` == False;
      * retention loop тикает (last-tick timestamp в пределах TTL).

    На любой fail → 503 `{"status":"not_ready","reason":...}` чтобы k8s
    снял pod с балансировщика.
    """
    settings = get_settings()
    # SELECT 1 в pgsql может задержаться больше k8s readinessProbe
    # timeoutSeconds (default 1s) под загруженным пулом — будет flap.
    # На остальных read-эндпоинтах есть `audit_query_statement_timeout_ms`;
    # на /ready ставим явный 500ms через `SET LOCAL`, чтобы probe не висел
    # на полный default-таймаут sqlalchemy.
    try:
        db.execute(sqlalchemy.text("SET LOCAL statement_timeout = 500"))
        db.execute(sqlalchemy.text("SELECT 1"))
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": "db_unreachable", "detail": str(exc)[:200]},
        )

    # self-audit pipeline: импортим в функции, чтобы избежать circular import
    # на module-load (main → endpoints → main).
    from src import main as main_module

    if settings.audit_outbox_enabled:
        outbox = main_module._audit_outbox
        if outbox is None:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": "audit_outbox_not_started"},
            )
        drain_task = getattr(outbox, "_drain_task", None)
        if drain_task is None or drain_task.done():
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": "audit_drain_task_dead"},
            )

    # retention watchdog: если loop включён, требуем свежий tick. None
    # означает «ещё ни одного tick'а не было» — это допустимый startup-window
    # (loop тикает раз в минуту), поэтому 503 кидаем ТОЛЬКО когда tick был и
    # отстал от now() больше TTL.
    if settings.retention_loop_enabled:
        last_tick = main_module.get_retention_last_tick_monotonic()
        if last_tick is not None:
            age = time.monotonic() - last_tick
            if age > main_module._RETENTION_WATCHDOG_TTL_SECONDS:
                return JSONResponse(
                    status_code=503,
                    content={
                        "status": "not_ready",
                        "reason": "retention_loop_stalled",
                        "last_tick_age_seconds": int(age),
                    },
                )

        # Отдельный watchdog: «loop живой, но sweep падает каждый день».
        # Минутный tick обновляется в _retention_loop безусловно, поэтому
        # `retention_loop_stalled` выше не ловит «apply_active молча валится
        # в 00:00 MSK». Сверяем отдельный marker, который двигается только
        # после успешного sweep'а (или после skip'а по advisory-lock'у,
        # когда работу делает другая replica). None — sweep ещё ни разу
        # не прошёл в этом процессе (legitimate startup до первой границы).
        last_sweep = main_module.get_retention_last_successful_sweep_monotonic()
        if last_sweep is not None:
            sweep_age = time.monotonic() - last_sweep
            if sweep_age > main_module._RETENTION_SWEEP_WATCHDOG_TTL_SECONDS:
                return JSONResponse(
                    status_code=503,
                    content={
                        "status": "not_ready",
                        "reason": "retention_sweep_stalled",
                        "last_successful_sweep_age_seconds": int(sweep_age),
                    },
                )

    return {"status": "ready"}
