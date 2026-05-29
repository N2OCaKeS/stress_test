"""Постепенная фоновая ре-шифрация секретов под активный мастер-ключ.

Контекст. После смены `SERVER_ENCRYPTION_KEY` старые ciphertext'ы (`v<old>$...`)
остаются читаемыми через `SERVER_ENCRYPTION_KEY__v<old>`, новые пишутся
активной версией. Чтобы можно было дропнуть старый ключ, надо пройтись по
всем строкам в `server_accounts.password_encrypted` и
`ipmi_controllers.password_encrypted`, расшифровать тем ключом, что
соответствует префиксу, и зашифровать обратно активной версией.

Outbox-pattern. Раньше воркер дёргал `POST /reencrypt_batch` синхронно:
server-service держал AsyncSession открытым на весь decrypt-batch →
encrypt-batch → UPDATE цикл, что блокировало pool. Теперь работа разбита
на короткие транзакции через таблицу `secrets_reencrypt_outbox`:

* :func:`seed_outbox` сканит обе owner-таблицы и публикует pending-row'ы
  под активную версию ключа. Запускается из endpoint'а или CLI после bump'а
  `SERVER_ENCRYPTION_KEY_VERSION`.
* :func:`claim_pending` берёт батч pending-row'ов с
  ``FOR UPDATE SKIP LOCKED`` и переводит в `processing`. Параллельные
  воркеры/replica'и не дублируют работу.
* :func:`finalize_done` записывает новый ciphertext в исходную таблицу
  и помечает outbox-row `done`.
* :func:`finalize_failed` помечает outbox-row `failed` и кладёт ошибку.
* :func:`cleanup_done` дропает старые `done` row'ы (housekeeping).

Идемпотентность: уникальный partial-индекс `(entity_type, entity_id)
WHERE status IN ('pending','processing')` запрещает дубли активных задач
на одну owner-строку.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import IpmiController, ReencryptOutboxEntry, ServerAccount
from src.services import secrets_service


# Совпадает с wire-форматом `v<N>$...`. Если строка не начинается с `v` или
# в ней нет `$` — `coalesce(version, NULL)` останется NULL и не попадёт в
# счётчик по версиям. Это корректно: такие строки означают повреждённый
# ciphertext, и пытаться их перешифровать смысла нет — оператор должен
# увидеть их в `total - sum(by_version)`.
_VERSION_PREFIX_RE = re.compile(r"^v(\d+)\$")

ENTITY_SERVER_ACCOUNT = "server_account"
ENTITY_IPMI_CONTROLLER = "ipmi_controller"

STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


def _parse_version(token: str | None) -> int | None:
    """Достать `N` из префикса `v<N>$...`. Вернуть None для NULL / мусора."""
    if not token:
        return None
    m = _VERSION_PREFIX_RE.match(token)
    if m is None:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


async def _count_by_version(db: AsyncSession) -> dict[int, int]:
    """Посчитать `password_encrypted` по wire-версиям из обеих таблиц.

    Делается в Python после SELECT'а: на ~десятках тысяч строк это дешевле
    и проще, чем плодить две вариации regexp-агрегатов под Postgres. Если
    БД вырастет до миллионов — переписать в чистый SQL через
    `substring(col from '^v([0-9]+)\\$')::int` + `GROUP BY`.
    """
    by_version: dict[int, int] = {}

    for column in (ServerAccount.password_encrypted, IpmiController.password_encrypted):
        stmt = select(column).where(column.is_not(None))
        rows = (await db.execute(stmt)).scalars()
        for token in rows:
            v = _parse_version(token)
            if v is None:
                continue
            by_version[v] = by_version.get(v, 0) + 1
    return by_version


async def _total(db: AsyncSession) -> int:
    """Сумма `password_encrypted IS NOT NULL` по обеим таблицам."""
    sa_total = (
        await db.execute(
            select(func.count(ServerAccount.id)).where(
                ServerAccount.password_encrypted.is_not(None)
            )
        )
    ).scalar_one()
    ipmi_total = (
        await db.execute(
            select(func.count(IpmiController.id)).where(
                IpmiController.password_encrypted.is_not(None)
            )
        )
    ).scalar_one()
    return int(sa_total) + int(ipmi_total)


async def _outbox_counts_by_status(db: AsyncSession) -> dict[str, int]:
    """Группировка outbox-row'ов по статусу — для оператора и worker'а."""
    stmt = select(
        ReencryptOutboxEntry.status,
        func.count(ReencryptOutboxEntry.id),
    ).group_by(ReencryptOutboxEntry.status)
    result = await db.execute(stmt)
    return {row[0]: int(row[1]) for row in result.all()}


async def status(db: AsyncSession) -> dict:
    """Сводка по миграции для worker'а / оператора.

    Возвращает: ``{remaining, total, active_version, by_version, app_env,
    outbox}``.

    * ``total`` — все non-NULL `password_encrypted` в обеих таблицах.
    * ``by_version`` — `{N: count}` по версиям из префиксов.
    * ``remaining`` — сумма `count`'ов для версий, отличных от активной;
      malformed строки в `by_version` не попадают.
    * ``outbox`` — `{pending, processing, done, failed}` — сколько работ
      в очереди / в полёте / закрыто / провалено.
    """
    settings = get_settings()
    active = settings.server_encryption_key_version
    by_version = await _count_by_version(db)
    total = await _total(db)
    remaining = sum(c for v, c in by_version.items() if v != active)
    outbox_counts = await _outbox_counts_by_status(db)
    outbox_snapshot = {
        STATUS_PENDING: outbox_counts.get(STATUS_PENDING, 0),
        STATUS_PROCESSING: outbox_counts.get(STATUS_PROCESSING, 0),
        STATUS_DONE: outbox_counts.get(STATUS_DONE, 0),
        STATUS_FAILED: outbox_counts.get(STATUS_FAILED, 0),
    }
    return {
        "remaining": remaining,
        "total": total,
        "active_version": active,
        "by_version": by_version,
        "app_env": settings.app_env,
        "outbox": outbox_snapshot,
    }


# ── Outbox seeding ──────────────────────────────────────────────────────────


async def seed_outbox(db: AsyncSession, limit: int | None = None) -> dict:
    """Заполнить outbox-строки для всех owner-row'ов с legacy ciphertext'ом.

    Скан: `password_encrypted IS NOT NULL` AND wire-version != active.
    Каждый match → INSERT в `secrets_reencrypt_outbox` с
    `ON CONFLICT DO NOTHING` по partial-индексу `(entity_type, entity_id)`
    где `status IN ('pending','processing')` — повторный seed не плодит
    дубли активных задач, но добавит новые row'ы, если предыдущая попытка
    уже `done`/`failed`.

    `limit` ограничивает число INSERT'ов за один вызов, чтобы крупная
    seed-операция не висла в одной транзакции. Caller вызывает несколько
    раз до возврата `inserted=0`.

    Возвращает: `{inserted, scanned, active_version}`. `scanned` — сколько
    кандидатов попало в SELECT (после фильтра по версии).
    """
    settings = get_settings()
    active = settings.server_encryption_key_version
    active_prefix = f"v{active}$%"

    scanned = 0
    inserted = 0

    candidates: list[tuple[str, str, str]] = []  # (entity_type, entity_id, ciphertext)

    sa_stmt = (
        select(ServerAccount.id, ServerAccount.password_encrypted)
        .where(ServerAccount.password_encrypted.is_not(None))
        .where(ServerAccount.password_encrypted.op("~")("^v[0-9]+\\$"))
        .where(ServerAccount.password_encrypted.notlike(active_prefix))
        .order_by(ServerAccount.id)
    )
    if limit is not None:
        sa_stmt = sa_stmt.limit(limit)
    for row in (await db.execute(sa_stmt)).all():
        candidates.append((ENTITY_SERVER_ACCOUNT, row[0], row[1]))

    remaining = None if limit is None else max(0, limit - len(candidates))
    if remaining is None or remaining > 0:
        ipmi_stmt = (
            select(IpmiController.id, IpmiController.password_encrypted)
            .where(IpmiController.password_encrypted.is_not(None))
            .where(IpmiController.password_encrypted.op("~")("^v[0-9]+\\$"))
            .where(IpmiController.password_encrypted.notlike(active_prefix))
            .order_by(IpmiController.id)
        )
        if remaining is not None:
            ipmi_stmt = ipmi_stmt.limit(remaining)
        for row in (await db.execute(ipmi_stmt)).all():
            candidates.append((ENTITY_IPMI_CONTROLLER, row[0], row[1]))

    scanned = len(candidates)

    # Bulk INSERT с ON CONFLICT — opportunistic против partial-unique
    # индекса. Конфликт не имеет explicit named constraint у `pg_insert`,
    # поэтому используем index_where форму через `index_elements` +
    # `index_where`. Для предсказуемости делаем по одной строке: на крупных
    # seed'ах разница ничтожна, а ошибка на одной row не валит весь batch.
    for entity_type, entity_id, ciphertext in candidates:
        stmt = (
            pg_insert(ReencryptOutboxEntry.__table__)
            .values(
                id=f"rox_{uuid4().hex}",
                entity_type=entity_type,
                entity_id=entity_id,
                legacy_ciphertext=ciphertext,
                status=STATUS_PENDING,
            )
            .on_conflict_do_nothing(
                index_elements=["entity_type", "entity_id"],
                index_where=ReencryptOutboxEntry.status.in_(
                    [STATUS_PENDING, STATUS_PROCESSING]
                ),
            )
            .returning(ReencryptOutboxEntry.__table__.c.id)
        )
        # RETURNING scalar надёжнее rowcount: asyncpg на INSERT ... ON CONFLICT
        # DO NOTHING может возвращать -1 в rowcount независимо от результата.
        result = await db.execute(stmt)
        if result.scalar_one_or_none() is not None:
            inserted += 1

    if inserted > 0:
        await db.flush()
        await db.commit()

    return {
        "inserted": inserted,
        "scanned": scanned,
        "active_version": active,
    }


# ── Claim / finalize ────────────────────────────────────────────────────────


async def claim_pending(db: AsyncSession, limit: int) -> list[dict]:
    """Атомарно забрать `limit` pending-row'ов и перевести в `processing`.

    `FOR UPDATE SKIP LOCKED` — параллельные replica'и worker'а не блокируют
    друг друга и не получают одну и ту же строку. Сортировка `created_at,
    id` — стабильный FIFO.

    Возвращает: список dict'ов `{id, entity_type, entity_id,
    legacy_ciphertext, attempts}`. Caller (endpoint) сериализует под
    response_model.
    """
    if limit <= 0:
        return []
    pick_stmt = (
        select(ReencryptOutboxEntry)
        .where(ReencryptOutboxEntry.status == STATUS_PENDING)
        .order_by(ReencryptOutboxEntry.created_at, ReencryptOutboxEntry.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    rows = list((await db.execute(pick_stmt)).scalars().all())
    if not rows:
        return []

    ids = [r.id for r in rows]
    # snapshot до UPDATE/COMMIT — после commit'а SQLAlchemy expire'ит
    # r.attempts, и lazy-load вернёт уже инкрементированное значение,
    # дав attempts+1 наружу неверно (двойной инкремент).
    snapshot = [
        {
            "id": r.id,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "legacy_ciphertext": r.legacy_ciphertext,
            "attempts_before": r.attempts,
        }
        for r in rows
    ]
    await db.execute(
        update(ReencryptOutboxEntry)
        .where(ReencryptOutboxEntry.id.in_(ids))
        .values(
            status=STATUS_PROCESSING,
            attempts=ReencryptOutboxEntry.attempts + 1,
        )
    )
    await db.commit()
    return [
        {
            "id": s["id"],
            "entity_type": s["entity_type"],
            "entity_id": s["entity_id"],
            "legacy_ciphertext": s["legacy_ciphertext"],
            "attempts": s["attempts_before"] + 1,
        }
        for s in snapshot
    ]


def _aad_for_entry(entity_type: str, entity_id: str) -> bytes:
    """Подобрать AAD по типу owner-row'а — see secrets_service."""
    if entity_type == ENTITY_SERVER_ACCOUNT:
        return secrets_service.aad_for_server_account_password(entity_id)
    if entity_type == ENTITY_IPMI_CONTROLLER:
        return secrets_service.aad_for_ipmi_credential(entity_id)
    raise ValueError(f"unknown entity_type {entity_type!r}")


async def finalize_done(db: AsyncSession, outbox_id: str) -> dict:
    """Закрыть outbox-row: расшифровать legacy, зашифровать активной и сохранить.

    Поток (всё — в одной короткой транзакции):

    1. SELECT outbox-row с `FOR UPDATE`, проверяем `status='processing'`.
    2. `decrypt(legacy_ciphertext, aad)` старым ключом → plaintext.
    3. `encrypt(plaintext, aad)` активным ключом → new ciphertext.
    4. Если в owner-row уже не legacy (кто-то параллельно дёрнул rotate)
       — выходим с `skipped=True`, помечаем outbox `done` (работа фактически
       выполнена другим путём, перешифровывать нечего).
    5. UPDATE owner-row.password_encrypted = new ciphertext.
    6. UPDATE outbox-row → `status='done'`, `processed_at=now()`.

    Crypto-операции дешёвые, lock держим только пока активна транзакция
    — это устраняет старую блокировку pool'а на крупных батчах.

    Возвращает: `{id, status: 'done', skipped: bool}`. Ошибки — exception
    наружу; endpoint обработает их через `finalize_failed`.
    """
    sel = (
        select(ReencryptOutboxEntry)
        .where(ReencryptOutboxEntry.id == outbox_id)
        .with_for_update()
    )
    entry = (await db.execute(sel)).scalar_one_or_none()
    if entry is None:
        return {"id": outbox_id, "status": "missing", "skipped": False}
    if entry.status != STATUS_PROCESSING:
        # Закрыта другой ветвью (race / повторный POST). Идемпотентность:
        # не трогаем, отдаём текущий статус.
        return {"id": outbox_id, "status": entry.status, "skipped": True}

    aad = _aad_for_entry(entry.entity_type, entry.entity_id)
    plaintext = secrets_service.decrypt(entry.legacy_ciphertext, aad=aad)
    new_ciphertext = secrets_service.encrypt(plaintext, aad=aad)

    skipped = False
    if entry.entity_type == ENTITY_SERVER_ACCOUNT:
        owner_stmt = (
            select(ServerAccount)
            .where(ServerAccount.id == entry.entity_id)
            .with_for_update()
        )
        owner = (await db.execute(owner_stmt)).scalar_one_or_none()
        if owner is None or owner.password_encrypted != entry.legacy_ciphertext:
            skipped = True
        else:
            owner.password_encrypted = new_ciphertext
    elif entry.entity_type == ENTITY_IPMI_CONTROLLER:
        owner_stmt = (
            select(IpmiController)
            .where(IpmiController.id == entry.entity_id)
            .with_for_update()
        )
        owner = (await db.execute(owner_stmt)).scalar_one_or_none()
        if owner is None or owner.password_encrypted != entry.legacy_ciphertext:
            skipped = True
        else:
            owner.password_encrypted = new_ciphertext
    else:
        # entity_type валидируется на seed; сюда не доедем штатно.
        raise ValueError(f"unknown entity_type {entry.entity_type!r}")

    entry.status = STATUS_DONE
    entry.processed_at = datetime.now(timezone.utc)
    entry.last_error = None
    await db.commit()
    return {"id": outbox_id, "status": STATUS_DONE, "skipped": skipped}


async def finalize_failed(
    db: AsyncSession, outbox_id: str, error: str
) -> dict:
    """Пометить outbox-row `failed` с текстом ошибки.

    Не переоткрываем строку для retry автоматически — оператор разбирается
    по `last_error` (например, проблема в env-конфиге ключей) и через
    `requeue_failed` возвращает row в `pending`.

    Идемпотентно: если уже `failed`/`done`, просто отдаём текущий статус.
    """
    sel = (
        select(ReencryptOutboxEntry)
        .where(ReencryptOutboxEntry.id == outbox_id)
        .with_for_update()
    )
    entry = (await db.execute(sel)).scalar_one_or_none()
    if entry is None:
        return {"id": outbox_id, "status": "missing"}
    if entry.status in (STATUS_DONE, STATUS_FAILED):
        return {"id": outbox_id, "status": entry.status}
    entry.status = STATUS_FAILED
    entry.last_error = error[:4096]  # bound — не льём гигабайты в БД
    entry.processed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"id": outbox_id, "status": STATUS_FAILED}


async def requeue_failed(db: AsyncSession, outbox_id: str) -> dict:
    """Вернуть `failed` row обратно в `pending` — оператор инициирует retry."""
    sel = (
        select(ReencryptOutboxEntry)
        .where(ReencryptOutboxEntry.id == outbox_id)
        .with_for_update()
    )
    entry = (await db.execute(sel)).scalar_one_or_none()
    if entry is None:
        return {"id": outbox_id, "status": "missing"}
    if entry.status != STATUS_FAILED:
        return {"id": outbox_id, "status": entry.status}
    entry.status = STATUS_PENDING
    entry.processed_at = None
    entry.last_error = None
    await db.commit()
    return {"id": outbox_id, "status": STATUS_PENDING}


async def cleanup_done(db: AsyncSession, older_than_hours: int) -> int:
    """DELETE done-row'ы старше N часов. Возвращает число удалённых.

    Запускается рутинной задачей оператора либо периодиком — таблица не
    должна расти бесконечно после каждой ротации ключа.
    """
    if older_than_hours <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
    stmt = (
        delete(ReencryptOutboxEntry)
        .where(ReencryptOutboxEntry.status == STATUS_DONE)
        .where(ReencryptOutboxEntry.processed_at.is_not(None))
        .where(ReencryptOutboxEntry.processed_at < cutoff)
    )
    result = await db.execute(stmt)
    await db.commit()
    return int(result.rowcount or 0)


# ── Legacy compat: старый sync-batch остаётся как backward-compat ──────────


async def _pick_account_batch(db: AsyncSession, active: int, limit: int) -> list[ServerAccount]:
    """Выбрать ServerAccount'ы с префиксом, отличным от активного."""
    active_prefix = f"v{active}$%"
    stmt = (
        select(ServerAccount)
        .where(ServerAccount.password_encrypted.is_not(None))
        .where(ServerAccount.password_encrypted.op("~")("^v[0-9]+\\$"))
        .where(ServerAccount.password_encrypted.notlike(active_prefix))
        .order_by(ServerAccount.id)
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars())


async def _pick_ipmi_batch(db: AsyncSession, active: int, limit: int) -> list[IpmiController]:
    """Симметрично _pick_account_batch — для ipmi_controllers."""
    active_prefix = f"v{active}$%"
    stmt = (
        select(IpmiController)
        .where(IpmiController.password_encrypted.is_not(None))
        .where(IpmiController.password_encrypted.op("~")("^v[0-9]+\\$"))
        .where(IpmiController.password_encrypted.notlike(active_prefix))
        .order_by(IpmiController.id)
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars())


async def reencrypt_batch(db: AsyncSession, limit: int) -> dict:
    """Старый sync-путь. Сохранён для совместимости со старым воркером и
    интеграционных тестов; новый worker идёт через outbox.

    На pool exhaustion здесь больше не страдаем: вызовы редкие и
    ограничены `limit` (≤1000). Логика идентична прежней — decrypt-old →
    encrypt-active с per-row try/except.
    """
    settings = get_settings()
    active = settings.server_encryption_key_version

    if limit <= 0:
        return {"processed": 0, "errors": 0}

    accounts = await _pick_account_batch(db, active, limit)
    remaining_quota = limit - len(accounts)
    ipmis: list[IpmiController] = []
    if remaining_quota > 0:
        ipmis = await _pick_ipmi_batch(db, active, remaining_quota)

    processed = 0
    errors = 0

    for acc in accounts:
        try:
            aad = secrets_service.aad_for_server_account_password(acc.id)
            plain = secrets_service.decrypt(acc.password_encrypted, aad=aad)
            acc.password_encrypted = secrets_service.encrypt(plain, aad=aad)
            processed += 1
        except Exception:  # noqa: BLE001 — любая ошибка decrypt/encrypt
            errors += 1

    for ipmi in ipmis:
        try:
            aad = secrets_service.aad_for_ipmi_credential(ipmi.id)
            plain = secrets_service.decrypt(ipmi.password_encrypted, aad=aad)
            ipmi.password_encrypted = secrets_service.encrypt(plain, aad=aad)
            processed += 1
        except Exception:  # noqa: BLE001
            errors += 1

    if processed > 0:
        await db.flush()
        await db.commit()
    # processed == 0: писать нечего, не трогаем транзакцию — оставляем
    # SAVEPOINT / outer transaction caller'у.

    return {"processed": processed, "errors": errors}
