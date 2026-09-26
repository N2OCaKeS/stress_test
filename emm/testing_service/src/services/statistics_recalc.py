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
  отдельные семейства тестов (`category`/`categories`) — пер-категорийные
  кнопки легаси (`allta_app/allta_front.py:729-880`), с это справочник
  `statistics_categories` в БД, и в модалке можно выбрать несколько семейств
  сразу: они считаются последовательно одной фоновой задачей.

Статус текущего/последнего пересчёта — одна платформенная строка
`statistics_recalc_status` (индикатор, не журнал попыток): сам внешний
сервис статистики один на всю платформу и не параллелит свои семейства
тестов внутри одного вызова, поэтому одновременно имеет смысл отслеживать
только одну попытку.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.constants import Action, EntityType
from src.core.exceptions import AppException, AuthorizationError, DomainValidationError
from src.dependencies.auth import Identity
from src.repositories import department_integration_settings as dis_repo
from src.repositories import statistics_category as category_repo
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
    categories: "Sequence[statistics_client.CategorySpec] | None" = None,
) -> "asyncio.Task | None":
    """Best-effort шедулинг фонового пересчёта. Никогда не поднимает исключение.

    Тихо пропускает (не ошибка, не лог уровня warning для штатных случаев
    "не настроено"), если `statistics_settings` выключены/без `base_url`,
    department_id не резолвится (для `test_run_id` — кампания не найдена),
    интеграция этого отдела не настроена, либо `credential_id` не задан.
    reveal-сбой credential логируется WARNING — тот же приём, что
    `run_summary.py::_resolve_confluence_bearer`.

    `categories` — уже разрешённые из справочника `statistics_categories`
    семейства (снимки `CategorySpec`, не ORM-строки — задача переживёт сессию)
    либо `None`/пусто для полного пересчёта. Автотриггер по кампании всегда
    полный, семейства передаёт только ручной триггер.

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
            triggered_by=triggered_by, test_run_id=test_run_id,
            categories=tuple(categories or ()),
        )
    )
    _pending_recalc_tasks.add(task)
    task.add_done_callback(_pending_recalc_tasks.discard)
    return task


async def _run_recalc(
    *, base_url: str, username: str, token: str, timeout: float,
    triggered_by: str, test_run_id: str | None,
    categories: "Sequence[statistics_client.CategorySpec]" = (),
) -> None:
    """Тело фоновой задачи — своя сессия БД, независимая от caller'а.

    `schedule_recalc` только ставит задачу в loop и не ждёт её — к моменту
    реального исполнения сессия caller'а могла уже закрыться, поэтому здесь
    всегда открывается свежий `AsyncSessionLocal()` (тот же приём, что
    `main.py::_log_rotation_loop`).

    Несколько семейств считаются последовательно (внешний сервис синхронный
    и один на платформу — параллелить нечего). Сбой одного семейства не
    останавливает остальные: они независимы, а оператор выбрал их все.
    Итог — `failed`, если упало хотя бы одно, текст ошибки перечисляет какие.
    """
    from src.db.session import AsyncSessionLocal

    keys = [spec.key for spec in categories]
    async with AsyncSessionLocal() as db:
        await recalc_repo.mark_running(
            db, triggered_by=triggered_by, test_run_id=test_run_id,
            category=keys[0] if keys else None, categories=keys or None,
        )
        await db.commit()

    errors: list[str] = []
    if not categories:
        message = await _call_safely(
            lambda: statistics_client.trigger_all_statistics(
                base_url=base_url, username=username, token=token, timeout=timeout,
            ),
            category=None,
        )
        if message is not None:
            errors.append(message)
    else:
        for index, spec in enumerate(categories):
            if index > 0:
                async with AsyncSessionLocal() as db:
                    await recalc_repo.set_current_category(db, spec.key)
                    await db.commit()
            message = await _call_safely(
                lambda spec=spec: statistics_client.trigger_category_statistics(
                    base_url=base_url, username=username, token=token, timeout=timeout,
                    spec=spec,
                ),
                category=spec.key,
            )
            if message is not None:
                errors.append(f"{spec.key}: {message}" if len(categories) > 1 else message)
    error = "; ".join(errors) if errors else None

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
            "category": keys[0] if len(keys) == 1 else None,
            "categories": keys or None, "error": error,
        },
    )


async def _call_safely(
    call: "Callable[[], Awaitable[None]]", *, category: str | None,
) -> str | None:
    """Выполнить один вызов внешнего сервиса; вернуть текст ошибки или None."""
    try:
        await call()
    except AppException as exc:
        logger.warning("statistics_recalc: recalc call failed (category=%s): %s", category, exc.message)
        return exc.message
    except Exception as exc:  # noqa: BLE001 — фоновая задача не должна ронять event loop
        logger.warning("statistics_recalc: recalc call failed (category=%s): %s", category, exc)
        return str(exc) or type(exc).__name__
    return None


async def get_status(db: AsyncSession) -> dict:
    """Текущий/последний статус — для `GET /statistics/status` и индикатора UI."""
    row = await recalc_repo.get_singleton(db)
    if row is None:
        return {
            "status": "idle",
            "triggered_by": None,
            "category": None,
            "categories": None,
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
        "categories": row.categories,
        "test_run_id": row.test_run_id,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "error": row.error,
        "updated_at": row.updated_at,
    }


async def resolve_categories(
    db: AsyncSession, keys: Sequence[str],
) -> list[statistics_client.CategorySpec]:
    """Ключи из запроса → снимки строк справочника в порядке `sort_order`.

    Дубли схлопываются. Неизвестный ключ → 422 STATISTICS_CATEGORY_UNKNOWN,
    выключенный → 422 STATISTICS_CATEGORY_DISABLED (в модалке его нет, но
    старый клиент/скрипт мог прислать).
    """
    wanted = list(dict.fromkeys(key.strip() for key in keys if key and key.strip()))
    if not wanted:
        return []
    rows = await category_repo.list_by_keys(db, wanted)
    found = {row.key for row in rows}
    unknown = [key for key in wanted if key not in found]
    if unknown:
        known = [spec.key for spec in await statistics_client.load_categories(db)]
        raise DomainValidationError(
            error_code="STATISTICS_CATEGORY_UNKNOWN",
            message=f"unknown statistics category: {', '.join(unknown)}",
            details={"unknown": unknown, "known": known},
        )
    disabled = [row.key for row in rows if not row.enabled]
    if disabled:
        raise DomainValidationError(
            error_code="STATISTICS_CATEGORY_DISABLED",
            message=f"statistics category is disabled: {', '.join(disabled)}",
            details={"disabled": disabled},
        )
    return [statistics_client.spec_from_row(row) for row in rows]


async def trigger_manual(
    db: AsyncSession, identity: Identity, department_id: str | None,
    category: str | None = None,
    categories: Sequence[str] | None = None,
) -> "asyncio.Task | None":
    """`POST /statistics/recalculate` — ручной триггер (debug-страница, модалка).

    Право: `(statistics_settings, *, update)` — тот же гейт, что редактирование
    платформенных настроек; операция дорогая/редкая, отдельного действия под
    неё не заводили. `department_id` не передан → берётся отдел вызывающего;
    если и у вызывающего его нет (platform-bound identity) — 422.

    `category`/`categories` не переданы — полный пересчёт (`/all-statistics`).
    Переданы — выбранные семейства справочника `statistics_categories`
    (объединение обоих полей), последовательно одной фоновой задачей.
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

    requested = [*([category] if category else []), *(categories or [])]
    specs = await resolve_categories(db, requested)

    resolved = department_id or identity.department_id
    if resolved is None:
        raise DomainValidationError(
            error_code="DEPARTMENT_ID_REQUIRED",
            message="department_id is required when the caller has no department of their own",
        )

    keys = [spec.key for spec in specs]
    audit_service.emit(
        "statistics_recalc.triggered",
        target_type="statistics_recalc_status",
        status="success", allowed=True,
        details={
            "department_id": resolved, "triggered_by": "manual",
            "category": keys[0] if len(keys) == 1 else None,
            "categories": keys or None,
        },
    )
    return await schedule_recalc(db, "manual", department_id=resolved, categories=specs)
