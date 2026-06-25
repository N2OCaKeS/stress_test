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

import logging
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import BigInteger, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.keystore import get_keystore
from src.models import IpmiController, ReencryptOutboxEntry, ServerAccount
from src.services import audit_service, metrics, secrets_service

logger = logging.getLogger(__name__)


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


def _version_count_stmt(column):
    """SQL `GROUP BY substring(col,'^v(\\d+)\\$')` под одну ciphertext-колонку.

    `substring(col from '^v([0-9]+)\\$')` извлекает версию из wire-префикса
    `v<N>$...`. Malformed-токены (без префикса) попадают в NULL — отбрасываем
    через `WHERE prefix IS NOT NULL`, чтобы dict[int, int] был bounded и
    `::bigint`-каст не подвисал на null'ах.

    `::bigint` (не `::int`): в secret_service отдельный migration_status уже
    бил overflow на `::int` для крупных таблиц (>2.1B row'ов в счётчике после
    bump'а), здесь сразу 64-бит.
    """
    version_expr = func.substring(column, r"^v([0-9]+)\$")
    return (
        select(
            version_expr.cast(BigInteger).label("version"),
            func.count().label("cnt"),
        )
        .where(column.is_not(None))
        .where(version_expr.is_not(None))
        .group_by(version_expr)
    )


async def _count_by_version_for_column(db: AsyncSession, column) -> dict[int, int]:
    """`{N: count}` по wire-префиксам для одной колонки — чистый SQL.

    Раньше тянул все ciphertext'ы в Python и парсил regex'ом per-row; на
    миллионных таблицах это выедало память и RTT'ы pool'а. Теперь —
    `substring(col,'^v(\\d+)\\$')::bigint + GROUP BY` целиком на стороне БД.
    """
    by_version: dict[int, int] = {}
    rows = (await db.execute(_version_count_stmt(column))).all()
    for row in rows:
        version = row[0]
        cnt = row[1]
        if version is None:
            continue
        by_version[int(version)] = int(cnt)
    return by_version


async def _count_by_version(db: AsyncSession) -> dict[int, int]:
    """Объединить per-column счётчики по обеим owner-таблицам."""
    combined: dict[int, int] = {}
    for column in (ServerAccount.password_encrypted, IpmiController.password_encrypted):
        per_column = await _count_by_version_for_column(db, column)
        for v, c in per_column.items():
            combined[v] = combined.get(v, 0) + c
    return combined


async def _column_breakdown(
    db: AsyncSession, column, active: int,
) -> dict:
    """Per-column сводка `{total, by_version, remaining_legacy}`.

    `remaining_legacy` — сумма счётчиков по версиям, отличным от
    ``active``; malformed-токены (без `v<N>$` префикса) не учитываются.
    """
    total = int(
        (
            await db.execute(
                select(func.count()).where(column.is_not(None))
            )
        ).scalar_one()
    )
    by_version = await _count_by_version_for_column(db, column)
    remaining_legacy = sum(c for v, c in by_version.items() if v != active)
    return {
        "total": total,
        "by_version": by_version,
        "remaining_legacy": remaining_legacy,
    }


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
    outbox, server_account_password_encrypted, ipmi_controller_password_encrypted,
    remaining_legacy_total, migrated_pct, outbox_pending}``.

    * ``total`` — все non-NULL `password_encrypted` в обеих таблицах.
    * ``by_version`` — `{N: count}` по версиям из префиксов (агрегат).
    * ``remaining`` — сумма `count`'ов для версий, отличных от активной;
      malformed строки в `by_version` не попадают (это legacy-поле,
      эквивалентное ``remaining_legacy_total``).
    * ``outbox`` — `{pending, processing, done, failed}` — сколько работ
      в очереди / в полёте / закрыто / провалено.
    * ``server_account_password_encrypted`` / ``ipmi_controller_password_encrypted``
      — per-column `{total, by_version, remaining_legacy}` для прицельной
      диагностики: оператор видит, какая колонка тащит legacy-токены.
    * ``remaining_legacy_total`` — суммарный `remaining_legacy` по обеим
      колонкам. Дублирует ``remaining`` ради явности контракта.
    * ``migrated_pct`` — доля row'ов под активной версией, ``0..100``.
      Когда `total=0`, отдаём ``100.0`` (нечего мигрировать = всё мигрировано).
    * ``outbox_pending`` — синоним ``outbox.pending`` под top-level именем,
      чтобы caller'у с lazy-страницы не лезть в nested-объект.
    """
    settings = get_settings()
    active = get_keystore().get_active_version()
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
    sa_breakdown = await _column_breakdown(
        db, ServerAccount.password_encrypted, active
    )
    ipmi_breakdown = await _column_breakdown(
        db, IpmiController.password_encrypted, active
    )
    remaining_legacy_total = (
        sa_breakdown["remaining_legacy"] + ipmi_breakdown["remaining_legacy"]
    )
    if total == 0:
        migrated_pct = 100.0
    else:
        migrated_count = total - remaining_legacy_total
        # round до десятых — оператору не нужны 14 знаков после запятой,
        # SIEM-индексы дольше парсят длинные float'ы.
        migrated_pct = round(100.0 * migrated_count / total, 1)
    return {
        "remaining": remaining,
        "total": total,
        "active_version": active,
        "by_version": by_version,
        "app_env": settings.app_env,
        "outbox": outbox_snapshot,
        "server_account_password_encrypted": sa_breakdown,
        "ipmi_controller_password_encrypted": ipmi_breakdown,
        "remaining_legacy_total": remaining_legacy_total,
        "migrated_pct": migrated_pct,
        "outbox_pending": outbox_snapshot[STATUS_PENDING],
    }


# ── Outbox seeding ──────────────────────────────────────────────────────────


def _legacy_ciphertext_filter(column, active_prefix: str):
    """Общий predicate для legacy-ciphertext SELECT'ов.

    Возвращает три where-кляузы: not-NULL, wire-prefix `v<N>$`, и
    not-LIKE-активного префикса. Применяется к
    ``ServerAccount.password_encrypted`` / ``IpmiController.password_encrypted``
    и к любым другим аналогичным колонкам, если такие появятся.
    """
    return (
        column.is_not(None),
        column.op("~")("^v[0-9]+\\$"),
        column.notlike(active_prefix),
    )


async def _pick_legacy_ciphertext_rows(
    db: AsyncSession,
    model,
    active: int,
    limit: int | None,
    *,
    scalars: bool = True,
):
    """SELECT row'ов с legacy-ciphertext'ом для заданного owner-модели.

    `scalars=True` — отдаём полные ORM-объекты (использует `reencrypt_batch`).
    `scalars=False` — отдаём пары `(id, ciphertext)` для `seed_outbox`,
    без материализации остальной row'ы.

    `limit=None` — без LIMIT'а (использует `seed_outbox` при не заданном
    quota'е). Иначе clause LIMIT добавляется в SELECT.
    """
    active_prefix = f"v{active}$%"
    column = model.password_encrypted
    if scalars:
        stmt = select(model)
    else:
        stmt = select(model.id, column)
    for clause in _legacy_ciphertext_filter(column, active_prefix):
        stmt = stmt.where(clause)
    stmt = stmt.order_by(model.id)
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    if scalars:
        return list(result.scalars())
    return list(result.all())


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
    active = get_keystore().get_active_version()

    inserted = 0

    candidates: list[tuple[str, str, str]] = []  # (entity_type, entity_id, ciphertext)

    sa_rows = await _pick_legacy_ciphertext_rows(
        db, ServerAccount, active, limit, scalars=False
    )
    for row in sa_rows:
        candidates.append((ENTITY_SERVER_ACCOUNT, row[0], row[1]))

    remaining = None if limit is None else max(0, limit - len(candidates))
    if remaining is None or remaining > 0:
        ipmi_rows = await _pick_legacy_ciphertext_rows(
            db, IpmiController, active, remaining, scalars=False
        )
        for row in ipmi_rows:
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
    2. SELECT owner-row с `FOR UPDATE`. Если row пропал или ciphertext
       разошёлся с legacy (параллельный rotate) — закрываем outbox-row
       `done`/`skipped=True` без crypto-операций.
    3. `decrypt(legacy_ciphertext, aad)` старым ключом → plaintext.
    4. `encrypt(plaintext, aad)` активным ключом → new ciphertext.
    5. UPDATE owner-row.password_encrypted = new ciphertext.
    6. UPDATE outbox-row → `status='done'`, `processed_at=now()`.

    Crypto-операции дешёвые, lock держим только пока активна транзакция
    — это устраняет старую блокировку pool'а на крупных батчах.

    Возвращает: `{id, status: 'done', skipped: bool, skip_reason: str | None}`.
    `skip_reason` — внутреннее поле для endpoint-аудита (`owner_vanished` /
    `owner_ciphertext_changed` / `status_not_processing`), наружу wire-схема
    его не несёт. Ошибки — exception наружу; endpoint обработает их через
    `finalize_failed`.
    """
    sel = (
        select(ReencryptOutboxEntry)
        .where(ReencryptOutboxEntry.id == outbox_id)
        .with_for_update()
    )
    entry = (await db.execute(sel)).scalar_one_or_none()
    if entry is None:
        return {"id": outbox_id, "status": "missing", "skipped": False}
    # Skipped/done emit'ы буферизуем и отправляем ПОСЛЕ commit'а — иначе
    # fire-and-forget emit может опередить commit, SIEM увидит skipped-event
    # для outbox-row, который реально остался в `processing` (если commit упал
    # или текущий код передумает). См. паттерн `receive_users_inventory`
    # с `drift_emits: list[dict]` буфером.
    deferred_emits: list[dict] = []
    if entry.status != STATUS_PROCESSING:
        # Закрыта другой ветвью (race / повторный POST). Идемпотентность:
        # не трогаем, отдаём текущий статус. На этом пути нет последующего
        # commit'а, поэтому эмитим сразу — нечего ждать.
        audit_service.emit(
            "secrets.migration.skipped",
            target_id=outbox_id,
            target_type="secrets_reencrypt_outbox",
            status="warning",
            allowed=True,
            details={
                "reason": "status_not_processing",
                "entity_type": entry.entity_type,
                "entity_id": entry.entity_id,
                "current_status": entry.status,
            },
        )
        return {
            "id": outbox_id,
            "status": entry.status,
            "skipped": True,
            "skip_reason": "status_not_processing",
        }

    # Сначала проверяем существование owner-row и совпадение ciphertext'а:
    # decrypt+encrypt — это AES-GCM раунд и HKDF, дешёво, но не бесплатно,
    # и на массовом cleanup'е (owner-row уже удалён или ротирован параллельно)
    # обидно тратить CPU на работу, результат которой будет выброшен. Сначала
    # тянем owner с FOR UPDATE — если пропал или ciphertext разошёлся, выходим
    # по skipped-ветке до crypto-операций.
    skipped = False
    skip_reason: str | None = None
    owner: ServerAccount | IpmiController | None
    if entry.entity_type == ENTITY_SERVER_ACCOUNT:
        owner_stmt = (
            select(ServerAccount)
            .where(ServerAccount.id == entry.entity_id)
            .with_for_update()
        )
        owner = (await db.execute(owner_stmt)).scalar_one_or_none()
    elif entry.entity_type == ENTITY_IPMI_CONTROLLER:
        owner_stmt = (
            select(IpmiController)
            .where(IpmiController.id == entry.entity_id)
            .with_for_update()
        )
        owner = (await db.execute(owner_stmt)).scalar_one_or_none()
    else:
        # entity_type валидируется на seed; сюда не доедем штатно.
        raise ValueError(f"unknown entity_type {entry.entity_type!r}")

    if owner is None:
        skipped = True
        skip_reason = "owner_vanished"
    elif owner.password_encrypted != entry.legacy_ciphertext:
        skipped = True
        skip_reason = "owner_ciphertext_changed"
    else:
        aad = _aad_for_entry(entry.entity_type, entry.entity_id)
        try:
            plaintext = secrets_service.decrypt(entry.legacy_ciphertext, aad=aad)
        except Exception:
            # `secrets_service.decrypt` поднимает AppException на decrypt-fail;
            # пропускаем выше (endpoint оборачивает в `secrets.reencrypt_failed`),
            # но успеваем поднять process-level counter — оператор видит
            # волну fail'ов до того, как finalize_failed напишет audit'ы.
            metrics.increment_secrets_decrypt_failures()
            raise
        new_ciphertext = secrets_service.encrypt(plaintext, aad=aad)
        owner.password_encrypted = new_ciphertext

    if skipped:
        # Owner-row пропал / ротировал ciphertext параллельно — outbox-row всё
        # равно закрываем `done`, но эмитим warning, чтобы оператор увидел
        # массовые `vanished` (rare-edge mid-migration drop) или фоновые гонки
        # с user-initiated rotate'ом. Откладываем emit до commit'а.
        deferred_emits.append({
            "action": "secrets.migration.skipped",
            "target_id": outbox_id,
            "target_type": "secrets_reencrypt_outbox",
            "status": "warning",
            "allowed": True,
            "details": {
                "reason": skip_reason,
                "entity_type": entry.entity_type,
                "entity_id": entry.entity_id,
            },
        })

    entry.status = STATUS_DONE
    entry.processed_at = datetime.now(timezone.utc)
    entry.last_error = None
    await db.commit()
    for emit_kwargs in deferred_emits:
        action = emit_kwargs.pop("action")
        audit_service.emit(action, **emit_kwargs)
    return {
        "id": outbox_id,
        "status": STATUS_DONE,
        "skipped": skipped,
        "skip_reason": skip_reason,
    }


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
    # Сбрасываем счётчик попыток. Сейчас max-attempts cap у нас нет, но если
    # его добавят (например, чтобы автоматически переводить «вечно падающие»
    # row'ы в `failed-final`), оператор-инициированный requeue должен дать
    # row'у честный fresh start, а не упереться в прошлый счётчик через
    # один тик.
    entry.attempts = 0
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
    return await _pick_legacy_ciphertext_rows(db, ServerAccount, active, limit)


async def _pick_ipmi_batch(db: AsyncSession, active: int, limit: int) -> list[IpmiController]:
    """Симметрично _pick_account_batch — для ipmi_controllers."""
    return await _pick_legacy_ciphertext_rows(db, IpmiController, active, limit)


def _reencrypt_row(row, *, aad_fn, entity_type: str, log_label: str) -> dict | None:
    """Перешифровать один ciphertext-row под активный ключ.

    Возвращает `None` на успех (и обновляет `row.password_encrypted` in-place)
    либо dict `{entity_type, entity_id, error_class}` на сбой decrypt/encrypt.
    Любые исключения decrypt/encrypt здесь поглощаются — caller считает
    `processed` / `errors` по возврату.

    `aad_fn` — `secrets_service.aad_for_server_account_password` или
    `aad_for_ipmi_credential`; вызывается с `row.id`.
    `log_label` — человеческое имя источника для WARNING'а (например
    `"server_account"`).
    """
    try:
        aad = aad_fn(row.id)
        plain = secrets_service.decrypt(row.password_encrypted, aad=aad)
        row.password_encrypted = secrets_service.encrypt(plain, aad=aad)
        return None
    except Exception as exc:  # noqa: BLE001 — любая ошибка decrypt/encrypt
        # Тип эксепшна важен для диагностики (ключ ушёл из env vs битый
        # ciphertext vs decrypt с чужим AAD'ом). Сам plaintext или ключ
        # из exc-сообщений не достанем — пишем только класс.
        exc_class = type(exc).__name__
        error_code = getattr(exc, "error_code", None)
        metrics.increment_secrets_decrypt_failures()
        logger.warning(
            "reencrypt_batch %s row %s failed: %s",
            log_label, row.id, exc_class,
        )
        # Сбой re-encrypt'а — это либо пропавший мастер-ключ (мисконфиг env,
        # критичная операционная ошибка), либо неаутентичный/битый ciphertext.
        # WARNING-лога мало: оба случая надо доносить до SIEM явным событием,
        # иначе ключ, выпавший из env, утонет в шуме. Ключ-missing выделяем
        # отдельным action'ом, остальное — generic decrypt-failure.
        if error_code == "ENCRYPTION_KEY_MISSING":
            audit_action = "secrets.migration_key_missing"
            reason = "encryption_key_missing"
        else:
            audit_action = "secrets.migration_decrypt_failed"
            reason = "decrypt_failed"
        audit_service.emit(
            audit_action,
            target_id=row.id,
            target_type=entity_type,
            status="failure",
            allowed=True,
            details={
                "reason": reason,
                "entity_type": entity_type,
                "error_class": exc_class,
                "error_code": error_code,
            },
        )
        return {
            "entity_type": entity_type,
            "entity_id": row.id,
            "error_class": exc_class,
        }


async def reencrypt_batch(db: AsyncSession, limit: int) -> dict:
    """Старый sync-путь. Сохранён для совместимости со старым воркером и
    интеграционных тестов; новый worker идёт через outbox.

    На pool exhaustion здесь больше не страдаем: вызовы редкие и
    ограничены `limit` (≤1000). Логика идентична прежней — decrypt-old →
    encrypt-active с per-row try/except.

    Возвращает `{processed, errors, failed_rows}`. `failed_rows` — список
    `{entity_type, entity_id, error_class}` для row'ов, упавших на
    decrypt/encrypt. Endpoint кладёт этот sample в audit-details, чтобы
    оператор видел не только число ошибок, но и какие именно entity_id.
    Wire-схема `ReencryptBatchResponse` остаётся `{processed, errors}` —
    `failed_rows` уходит только в аудит.

    Edge: на `limit <= 0` возвращаем сокращённый `{processed: 0, errors: 0}`
    без `failed_rows` — батч не запускался, списку обрабатывать нечего.
    Endpoint читает `failed_rows` через `.get(..., [])`, так что несовместимости
    нет.
    """
    active = get_keystore().get_active_version()

    if limit <= 0:
        return {"processed": 0, "errors": 0}

    accounts = await _pick_account_batch(db, active, limit)
    remaining_quota = limit - len(accounts)
    ipmis: list[IpmiController] = []
    if remaining_quota > 0:
        ipmis = await _pick_ipmi_batch(db, active, remaining_quota)

    processed = 0
    errors = 0
    # Список упавших row'ов с минимумом полей для диагностики оператору.
    # `entity_id` уже логируется через `logger.warning` ниже, но в summary
    # удобно отдать структурированно — endpoint кладёт это в audit details,
    # чтобы SIEM мог разложить инциденты по таблицам.
    failed_rows: list[dict[str, str]] = []

    batches = (
        (accounts, secrets_service.aad_for_server_account_password,
         ENTITY_SERVER_ACCOUNT, "server_account"),
        (ipmis, secrets_service.aad_for_ipmi_credential,
         ENTITY_IPMI_CONTROLLER, "ipmi_controller"),
    )
    for rows, aad_fn, entity_type, log_label in batches:
        for row in rows:
            failure = _reencrypt_row(
                row, aad_fn=aad_fn, entity_type=entity_type, log_label=log_label
            )
            if failure is None:
                processed += 1
            else:
                errors += 1
                failed_rows.append(failure)

    if processed > 0:
        await db.flush()
        await db.commit()
    # processed == 0: писать нечего, не трогаем транзакцию — оставляем
    # SAVEPOINT / outer transaction caller'у.

    return {"processed": processed, "errors": errors, "failed_rows": failed_rows}
