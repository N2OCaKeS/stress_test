"""Proactive re-encrypt очередь для credentials.

Lazy-путь (см. `credential_service._lazy_reencrypt_if_needed`) перешифровывает
кред'у при reveal'е, но на «холодных» credential'ах никогда не отработает.
Outbox-pattern закрывает дыру: seed публикует pending row'ы, batch-процессор
обрабатывает их короткими транзакциями.

Flow:

* :func:`seed_outbox` сканит credentials с wire-prefix'ом, не равным
  активному, и публикует pending row'ы. ON CONFLICT DO NOTHING против
  partial-UNIQUE по `credential_id WHERE status = 'pending'` — повторный
  seed не плодит дубли.
* :func:`process_batch` берёт N pending row'ов через `FOR UPDATE SKIP
  LOCKED`, для каждой: decrypt legacy AAD'ом credential.id → encrypt active
  → CAS-UPDATE credentials.secret_encrypted → UPDATE outbox row → commit.
  CAS на старом ciphertext'е защищает от race с lazy-путём или PATCH'ем.
* :func:`migration_status` — сводка по статусам outbox-таблицы.

Аудит-эмиты идут только из endpoint-слоя (см. `secrets_migration` в API);
сервис возвращает counters / dict'ы, чтобы тесты могли проверять чистую
бизнес-логику без mock'ов httpx.
"""

from __future__ import annotations

import logging
import re
import secrets as _secrets
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.exceptions import AppException
from src.models import Credential, ReencryptOutboxEntry
from src.repositories import credentials as cred_repo
from src.services import secrets_service

logger = logging.getLogger(__name__)


_VERSION_PREFIX_RE = re.compile(r"^v(\d+)\$")


STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_ERROR = "error"


def _new_outbox_id() -> str:
    """`rox_<32 hex>` — симметрично `cred_<hex>`."""
    return f"rox_{_secrets.token_hex(16)}"


def _parse_version(token: str | None) -> int | None:
    """Достать `N` из префикса `v<N>$...`. None для NULL / мусора."""
    if not token:
        return None
    m = _VERSION_PREFIX_RE.match(token)
    if m is None:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


# ── Seed ────────────────────────────────────────────────────────────────────


async def seed_outbox(
    db: AsyncSession, *, target_version: int | None = None
) -> dict:
    """Опубликовать pending row'ы для credentials с legacy-префиксом.

    `target_version=None` (дефолт) — используется текущий
    `settings.secret_encryption_key_version`. Параметр оставлен явный, чтобы
    test'ы могли проверить scenario с «принудительной» версией без
    monkeypatch'а Settings.

    Возвращает `{inserted, scanned, active_version}`. `scanned` — сколько
    credential'ов попало в SELECT (после фильтра по версии). `inserted` —
    сколько реально INSERT'нулось (с учётом partial-UNIQUE skip'а).
    """
    if target_version is None:
        target_version = get_settings().secret_encryption_key_version

    active_prefix = f"v{target_version}$%"

    # SELECT credential'ов с wire-prefix'ом, отличным от активного. Берём
    # только id + текущий ciphertext — остальные колонки не нужны.
    stmt = (
        select(Credential.id, Credential.secret_encrypted)
        .where(Credential.secret_encrypted.is_not(None))
        .where(Credential.secret_encrypted.op("~")(r"^v[0-9]+\$"))
        .where(Credential.secret_encrypted.notlike(active_prefix))
        .order_by(Credential.id)
    )
    rows = list((await db.execute(stmt)).all())
    scanned = len(rows)
    inserted = 0

    for cred_id, ciphertext in rows:
        source_version = _parse_version(ciphertext)
        if source_version is None:
            # Битый префикс — пропускаем, в outbox такие не кладём:
            # decrypt всё равно упадёт, оператор увидит их в общей сумме
            # remaining_legacy.
            continue

        ins = (
            pg_insert(ReencryptOutboxEntry.__table__)
            .values(
                id=_new_outbox_id(),
                credential_id=cred_id,
                source_version=source_version,
                target_version=target_version,
                status=STATUS_PENDING,
            )
            .on_conflict_do_nothing(
                index_elements=["credential_id"],
                index_where=(
                    ReencryptOutboxEntry.__table__.c.status == STATUS_PENDING
                ),
            )
            .returning(ReencryptOutboxEntry.__table__.c.id)
        )
        # RETURNING scalar надёжнее rowcount: ON CONFLICT DO NOTHING на
        # asyncpg/psycopg может вернуть rowcount=-1 безотносительно результата.
        result = await db.execute(ins)
        if result.scalar_one_or_none() is not None:
            inserted += 1

    if inserted > 0:
        await db.flush()
        await db.commit()

    return {
        "inserted": inserted,
        "scanned": scanned,
        "active_version": target_version,
    }


# ── Process batch ──────────────────────────────────────────────────────────


def _aad_for(cred_id: str) -> bytes:
    return secrets_service.aad_for_credential(cred_id)


async def _process_single(
    db: AsyncSession,
    entry: ReencryptOutboxEntry,
    *,
    active_version: int,
) -> dict | None:
    """Один outbox-row: decrypt → encrypt → CAS UPDATE credential → mark done.

    Возвращает None на успех; на ошибку — `{outbox_id, credential_id,
    error_class, message}` и помечает row error'ом (commit делает caller
    после собрки счётчиков). На skip (cred удалён / ciphertext успел
    мигрировать конкурентом) — None, status=done без error.
    """
    cred = await cred_repo.get_by_id_for_update(db, entry.credential_id)
    now = datetime.now(timezone.utc)

    if cred is None:
        # Credential удалили (CASCADE снёс бы outbox row, но пока row жив —
        # закрываем done с пустым полем error).
        entry.status = STATUS_DONE
        entry.completed_at = now
        return None

    current_blob = cred.secret_encrypted
    # Если ciphertext уже под активной версией — lazy-путь / PATCH опередил.
    # Закрываем done без crypto-операций.
    if current_blob.startswith(f"v{active_version}$"):
        entry.status = STATUS_DONE
        entry.completed_at = now
        return None

    try:
        aad = _aad_for(cred.id)
        plaintext = secrets_service.decrypt(current_blob, aad=aad)
        new_blob = secrets_service.encrypt(plaintext, aad=aad)
    except AppException as exc:
        entry.status = STATUS_ERROR
        entry.completed_at = now
        entry.error_message = f"{exc.error_code}: {exc.message[:400]}"
        return {
            "outbox_id": entry.id,
            "credential_id": entry.credential_id,
            "error_class": "AppException",
            "error_code": exc.error_code,
        }
    except Exception as exc:  # noqa: BLE001
        entry.status = STATUS_ERROR
        entry.completed_at = now
        entry.error_message = f"{type(exc).__name__}: {str(exc)[:400]}"
        return {
            "outbox_id": entry.id,
            "credential_id": entry.credential_id,
            "error_class": type(exc).__name__,
            "error_code": None,
        }

    # CAS-UPDATE: blob, который мы только что прочитали, должен ещё стоять
    # в БД — иначе lazy-путь / PATCH опередил, и наш new_blob закроет старый
    # state. False тут — норма, не ошибка.
    swapped = await cred_repo.cas_update_secret_encrypted(
        db,
        cred_id=cred.id,
        expected_blob=current_blob,
        new_blob=new_blob,
    )
    if not swapped:
        logger.info(
            "outbox_process_race outbox_id=%s cred_id=%s — cred mutated under us",
            entry.id, entry.credential_id,
        )

    entry.status = STATUS_DONE
    entry.completed_at = now
    entry.error_message = None
    return None


async def process_batch(
    db: AsyncSession, *, batch_size: int = 100
) -> dict:
    """Обработать ≤ `batch_size` pending row'ов. Возвращает счётчики.

    `FOR UPDATE SKIP LOCKED` — параллельные процессоры (если когда-нибудь
    появятся) не блокируют друг друга и не берут одну и ту же row дважды.

    Каждая row перешифровывается атомарно в текущей транзакции: CAS на
    credentials.secret_encrypted защищает от race с lazy-путём, а UPDATE
    outbox.status закрывает задачу. На неудачу — error со сообщением,
    оператор разбирается по `migration_status` / `error_message`.
    """
    if batch_size <= 0:
        return {"processed": 0, "errors": 0, "failed": []}

    active_version = get_settings().secret_encryption_key_version

    pick_stmt = (
        select(ReencryptOutboxEntry)
        .where(ReencryptOutboxEntry.status == STATUS_PENDING)
        .order_by(
            ReencryptOutboxEntry.seeded_at,
            ReencryptOutboxEntry.id,
        )
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    entries = list((await db.execute(pick_stmt)).scalars().all())
    if not entries:
        return {"processed": 0, "errors": 0, "failed": []}

    # Инкрементим attempts всем сразу: дальше каждая запись либо done, либо
    # error, попыток у row'ы всё равно ровно одна в этом батче.
    for entry in entries:
        entry.attempts = (entry.attempts or 0) + 1

    failed: list[dict] = []
    processed = 0
    errors = 0

    for entry in entries:
        failure = await _process_single(
            db, entry, active_version=active_version
        )
        if failure is None:
            processed += 1
        else:
            errors += 1
            failed.append(failure)

    await db.commit()
    return {
        "processed": processed,
        "errors": errors,
        "failed": failed,
    }


# ── Status ────────────────────────────────────────────────────────────────────


async def migration_status(db: AsyncSession) -> dict:
    """Сводка `{pending, done, error, total}` по outbox-таблице.

    Лёгкая ручка: GROUP BY status. Подходит как для оператора (одно число
    на дашборде), так и для health-полла перед `--finalize`.
    """
    stmt = select(
        ReencryptOutboxEntry.status,
        func.count(ReencryptOutboxEntry.id),
    ).group_by(ReencryptOutboxEntry.status)
    counts = {row[0]: int(row[1]) for row in (await db.execute(stmt)).all()}
    pending = counts.get(STATUS_PENDING, 0)
    done = counts.get(STATUS_DONE, 0)
    error = counts.get(STATUS_ERROR, 0)
    return {
        "pending": pending,
        "done": done,
        "error": error,
        "total": pending + done + error,
    }
