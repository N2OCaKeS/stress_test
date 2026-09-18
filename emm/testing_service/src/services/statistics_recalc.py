"""Фоновый пересчёт статистики через внешний сервис (§2.7, §9.3 плана миграции).

Легаси (`allta_app/allta_back.py:413-423 calc_all_statistics()`) дёргал
`POST /all-statistics` синхронно в конце каждого прогона и блокировал
дальнейший запуск тестов на всё время пересчёта (может идти минутами — сам
сервис статистики синхронный внутри себя, см. `services/statistics_client.py`).
Явное решение владельца: не повторять это в EMM — пересчёт запускается в
фоне (`asyncio.create_task`), не блокируя ни постановку новых тестов в
очередь, ни HTTP-ответ вызывающему.

Два триггера:

* `test_run` — `services/queue.py::_maybe_post_run_summary` зовёт сюда же, на
  том же терминальном переходе кампании, что и end-of-run комментарий
  (§2.4/§6.1). Пока кампания не терминальна (идут retry/повторы её item'ов),
  пересчёт не запускается — по построению это уже покрывает "пересчитать
  после прогона и всех починенных по ходу тестов".
* `manual` — ручная кнопка/переключатель в UI для одиночных (standalone)
  тестов, `POST /statistics/recalculate`. Легаси не пересчитывало статистику
  на каждый одиночный тест — здесь тоже нет автотриггера на одиночный тест,
  только явный запрос оператора; индикатор состояния (`get_status`) виден
  независимо от того, что именно запустило пересчёт. Ручной триггер умеет и
  одно семейство тестов (`category`) — восемь пер-категорийных кнопок легаси
  (`allta_app/allta_front.py:729-880`) плюс «всё сразу» = те же девять.

Статус текущего/последнего пересчёта — одна платформенная строка
`statistics_recalc_status` (индикатор, не журнал попыток): сам внешний
сервис статистики один на всю платформу и не параллелит свои семейства
тестов внутри одного вызова, поэтому одновременно имеет смысл отслеживать
только одну попытку.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.constants import Action, EntityType
from src.core.exceptions import AppException, AuthorizationError, DomainValidationError
from src.dependencies.auth import Identity
from src.repositories import department_integration_settings as dis_repo
from src.repositories import statistics_recalc as recalc_repo
from src.repositories import statistics_settings as settings_repo
from src.repositories import test_run as test_run_repo
from src.services import audit_service, permissions, secret_client, statistics_client

logger = logging.getLogger(__name__)

# Ссылки на fire-and-forget задачи — без этого event loop может собрать
# задачу мусором до завершения (тот же приём, что `audit_service._pending_
# audit_tasks`).
_pending_recalc_tasks: "set[asyncio.Task]" = set()


async def schedule_recalc(
    db: AsyncSession,
    triggered_by: str,
    *,
    test_run_id: str | None = None,
    department_id: str | None = None,
    category: str | None = None,
) -> "asyncio.Task | None":
    """Best-effort шедулинг фонового пересчёта. Никогда не поднимает исключение.

    Тихо пропускает (не ошибка, не лог уровня warning для штатных случаев
    "не настроено"), если `statistics_settings` выключены/без `base_url`,
    department_id не резолвится (для `test_run_id` — кампания не найдена),
    интеграция этого отдела не настроена, либо `credential_id` не задан.
    reveal-сбой credential логируется WARNING — тот же приём, что
    `run_summary.py::_resolve_confluence_bearer`.

    `category` — ключ семейства тестов (`services/statistics_client.CATEGORIES`)
    либо `None` для полного пересчёта. Автотриггер по кампании всегда полный,
    категорию передаёт только ручная кнопка.

    `test_run_id`/`department_id` — ровно один из них задаётся вызывающим
    (для `triggered_by="test_run"` department резолвится из самой кампании;
    для `triggered_by="manual"` — передаётся явно).

    Возвращает поставленную задачу (или `None`, если пропущено) — обычные
    вызывающие (`queue.py`, `trigger_manual`) её игнорируют (fire-and-forget),
    тесты используют, чтобы детерминированно дождаться завершения фоновой
    работы вместо `sleep`-поллинга.
    """
    try:
        settings_row = await settings_repo.get_singleton(db)
        if settings_row is None or not settings_row.enabled or not settings_row.base_url:
            return None

        resolved_department_id = department_id
        if test_run_id is not None:
            run = await test_run_repo.get_by_id(db, test_run_id)
            resolved_department_id = run.department_id if run is not None else None
        if resolved_department_id is None:
            return None

        dis = await dis_repo.get_by_department(db, resolved_department_id)
        if dis is None or not dis.credential_id:
            return None

        try:
            username, token = await secret_client.reveal_credential(dis.credential_id)
        except AppException as exc:
            logger.warning(
                "statistics_recalc: reveal_credential failed for dept=%s cred=%s: %s",
                resolved_department_id, dis.credential_id, exc.message,
            )
            return None
        if not token:
            return None

        base_url = settings_row.base_url
        timeout = get_settings().statistics_request_timeout_seconds
    except Exception as exc:  # noqa: BLE001 — шедулинг best-effort, не должен ронять caller'а
        logger.warning("statistics_recalc.schedule_recalc failed to resolve prerequisites: %s", exc)
        return None

    loop = asyncio.get_running_loop()
    task = loop.create_task(
        _run_recalc(
            base_url=base_url, username=username, token=token, timeout=timeout,
            triggered_by=triggered_by, test_run_id=test_run_id, category=category,
        )
    )
    _pending_recalc_tasks.add(task)
    task.add_done_callback(_pending_recalc_tasks.discard)
    return task


async def _run_recalc(
    *, base_url: str, username: str, token: str, timeout: float,
    triggered_by: str, test_run_id: str | None, category: str | None = None,
) -> None:
    """Тело фоновой задачи — своя сессия БД, независимая от caller'а.

    `schedule_recalc` только ставит задачу в loop и не ждёт её — к моменту
    реального исполнения сессия caller'а могла уже закрыться, поэтому здесь
    всегда открывается свежий `AsyncSessionLocal()` (тот же приём, что
    `main.py::_log_rotation_loop`).
    """
    from src.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        await recalc_repo.mark_running(
            db, triggered_by=triggered_by, test_run_id=test_run_id, category=category,
        )
        await db.commit()

    error: str | None = None
    try:
        if category is None:
            await statistics_client.trigger_all_statistics(
                base_url=base_url, username=username, token=token, timeout=timeout,
            )
        else:
            await statistics_client.trigger_category_statistics(
                base_url=base_url, username=username, token=token, timeout=timeout,
                category=category,
            )
    except AppException as exc:
        error = exc.message
        logger.warning("statistics_recalc: recalc call failed (category=%s): %s", category, exc.message)
    except Exception as exc:  # noqa: BLE001 — фоновая задача не должна ронять event loop
        error = str(exc) or type(exc).__name__
        logger.warning("statistics_recalc: recalc call failed (category=%s): %s", category, exc)

    async with AsyncSessionLocal() as db:
        await recalc_repo.mark_finished(db, succeeded=error is None, error=error)
        await db.commit()

    audit_service.emit(
        "statistics_recalc.completed",
        target_type="statistics_recalc_status",
        status="success" if error is None else "failure",
        allowed=True,
        details={
            "triggered_by": triggered_by, "test_run_id": test_run_id,
            "category": category, "error": error,
        },
    )


async def get_status(db: AsyncSession) -> dict:
    """Текущий/последний статус — для `GET /statistics/status` и индикатора UI."""
    row = await recalc_repo.get_singleton(db)
    if row is None:
        return {
            "status": "idle",
            "triggered_by": None,
            "category": None,
            "test_run_id": None,
            "started_at": None,
            "finished_at": None,
            "error": None,
            "updated_at": None,
        }
    return {
        "status": row.status,
        "triggered_by": row.triggered_by,
        "category": row.category,
        "test_run_id": row.test_run_id,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "error": row.error,
        "updated_at": row.updated_at,
    }


async def trigger_manual(
    db: AsyncSession, identity: Identity, department_id: str | None,
    category: str | None = None,
) -> "asyncio.Task | None":
    """`POST /statistics/recalculate` — ручной триггер для одиночных тестов.

    Право: `(statistics_settings, *, update)` — тот же гейт, что редактирование
    платформенных настроек; операция дорогая/редкая, отдельного действия под
    неё не заводили. `department_id` не передан → берётся отдел вызывающего;
    если и у вызывающего его нет (platform-bound identity) — 422.

    `category` не передана — полный пересчёт (`/all-statistics`), как и было.
    Передана — одно семейство тестов, как в легаси-меню из девяти кнопок.
    """
    try:
        await permissions.require_action(db, identity, EntityType.STATISTICS_SETTINGS, Action.UPDATE)
    except AuthorizationError:
        audit_service.emit(
            "statistics_recalc.triggered",
            target_type="statistics_recalc_status",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise

    if category is not None and category not in statistics_client.CATEGORIES:
        raise DomainValidationError(
            error_code="STATISTICS_CATEGORY_UNKNOWN",
            message=f"unknown statistics category: {category}",
            details={"known": sorted(statistics_client.CATEGORIES)},
        )

    resolved = department_id or identity.department_id
    if resolved is None:
        raise DomainValidationError(
            error_code="DEPARTMENT_ID_REQUIRED",
            message="department_id is required when the caller has no department of their own",
        )

    audit_service.emit(
        "statistics_recalc.triggered",
        target_type="statistics_recalc_status",
        status="success", allowed=True,
        details={"department_id": resolved, "triggered_by": "manual", "category": category},
    )
    return await schedule_recalc(db, "manual", department_id=resolved, category=category)
