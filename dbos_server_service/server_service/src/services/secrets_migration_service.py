"""Постепенная фоновая ре-шифрация секретов под активный мастер-ключ.

Контекст. После смены `SERVER_ENCRYPTION_KEY` старые ciphertext'ы (`v<old>$...`)
остаются читаемыми через `SERVER_ENCRYPTION_KEY__v<old>`, новые пишутся
активной версией. Чтобы можно было дропнуть старый ключ, надо пройтись по всем
шифр-колонкам всех таблиц сервиса (реестр `ENCRYPTED_COLUMNS`: пароль и
ssh-ключ аккаунта + previous, пароль BMC, mgmt-пароль и mgmt-ключ сервера +
previous), расшифровать тем ключом, что соответствует префиксу, и своим AAD, и
зашифровать обратно активной версией тем же AAD.

Outbox-pattern. Раньше воркер дёргал `POST /reencrypt_batch` синхронно:
server-service держал AsyncSession открытым на весь decrypt-batch →
encrypt-batch → UPDATE цикл, что блокировало pool. Теперь работа разбита
на короткие транзакции через таблицу `secrets_reencrypt_outbox`:

* :func:`seed_outbox` сканит все шифр-колонки реестра и публикует pending-row'ы
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
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import BigInteger, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.keystore import get_keystore
from src.models import (
    IpmiController,
    ReencryptOutboxEntry,
    SecretsMigrationState,
    Server,
    ServerAccount,
)
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
ENTITY_SERVER = "server"

STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

# Единственная строка `secrets_migration_state`.
SINGLETON_STATE_ID = "singleton"

# Предикат частичного unique-индекса `uq_secrets_reencrypt_outbox_entity_active`,
# записанный литералами. В ON CONFLICT его надо давать именно литералом, а не
# `status.in_([...])`: параметризованный `status IN ($1,$2)` в generic-плане
# server-side prepared statement (psycopg готовит запрос после N повторов)
# Postgres не может сопоставить с литеральным предикатом индекса и роняет
# «no unique or exclusion constraint matching the ON CONFLICT specification».
_OUTBOX_ACTIVE_INDEX_WHERE = text("status IN ('pending', 'processing')")


# ── Реестр шифр-колонок ──────────────────────────────────────────────────────
#
# Единый источник правды по всем зашифрованным колонкам сервиса. Seed, дренаж,
# счётчики remaining и retire-гард ходят по этому списку, а не по хардкоду
# отдельных полей. Добавить новую шифр-колонку = одна запись здесь (плюс, если
# у неё свой AAD-kind, соответствующий `aad_for_*` в secrets_service).
#
# AAD у каждой колонки берётся ТОТ ЖЕ, которым её реально шифруют/расшифровывают
# в сервисном слое — иначе decrypt при перешифровке упал бы InvalidTag:
#
# * server_accounts.password_encrypted / previous_password_encrypted —
#   aad_for_server_account_password (previous_* переезжает из password_encrypted
#   тем же AAD, см. server_account.rotate_password / reveal_previous_password);
# * server_accounts.ssh_private_key_encrypted — aad_for_server_account_ssh_key;
# * ipmi_controllers.password_encrypted — aad_for_ipmi_credential;
# * servers.mgmt_password_encrypted / previous_mgmt_password_encrypted —
#   aad_for_server_mgmt_password (previous_* переезжает из mgmt_password_encrypted,
#   см. management_creds.stash_new / internal_service.fetch mgmt creds);
# * servers.mgmt_ssh_private_key_encrypted / previous_mgmt_ssh_private_key_encrypted —
#   aad_for_server_mgmt_ssh_key.


@dataclass(frozen=True)
class EncryptedColumnSpec:
    """Одна зашифрованная колонка: owner-модель, имя поля и её AAD-функция."""

    entity_type: str
    model: type
    column_name: str
    aad_fn: Callable[[str], bytes]

    @property
    def column(self):
        """ORM-атрибут колонки (для SELECT/WHERE)."""
        return getattr(self.model, self.column_name)


# Порядок важен: server_account-колонки идут раньше ipmi и server, чтобы
# quota-split в seed_outbox/reencrypt_batch оставался предсказуемым (сначала
# аккаунты, потом BMC, потом управляющие креды серверов).
ENCRYPTED_COLUMNS: tuple[EncryptedColumnSpec, ...] = (
    EncryptedColumnSpec(
        ENTITY_SERVER_ACCOUNT, ServerAccount, "password_encrypted",
        secrets_service.aad_for_server_account_password,
    ),
    EncryptedColumnSpec(
        ENTITY_SERVER_ACCOUNT, ServerAccount, "previous_password_encrypted",
        secrets_service.aad_for_server_account_password,
    ),
    EncryptedColumnSpec(
        ENTITY_SERVER_ACCOUNT, ServerAccount, "ssh_private_key_encrypted",
        secrets_service.aad_for_server_account_ssh_key,
    ),
    EncryptedColumnSpec(
        ENTITY_IPMI_CONTROLLER, IpmiController, "password_encrypted",
        secrets_service.aad_for_ipmi_credential,
    ),
    EncryptedColumnSpec(
        ENTITY_SERVER, Server, "mgmt_password_encrypted",
        secrets_service.aad_for_server_mgmt_password,
    ),
    EncryptedColumnSpec(
        ENTITY_SERVER, Server, "previous_mgmt_password_encrypted",
        secrets_service.aad_for_server_mgmt_password,
    ),
    EncryptedColumnSpec(
        ENTITY_SERVER, Server, "mgmt_ssh_private_key_encrypted",
        secrets_service.aad_for_server_mgmt_ssh_key,
    ),
    EncryptedColumnSpec(
        ENTITY_SERVER, Server, "previous_mgmt_ssh_private_key_encrypted",
        secrets_service.aad_for_server_mgmt_ssh_key,
    ),
)

# Быстрый lookup по (entity_type, column_name) — finalize_done читает spec по
# полям outbox-row'а.
_SPEC_BY_KEY: dict[tuple[str, str], EncryptedColumnSpec] = {
    (s.entity_type, s.column_name): s for s in ENCRYPTED_COLUMNS
}


def _spec_for(entity_type: str, column_name: str) -> EncryptedColumnSpec:
    """Найти spec по паре (entity_type, column_name) или бросить ValueError."""
    spec = _SPEC_BY_KEY.get((entity_type, column_name))
    if spec is None:
        raise ValueError(
            f"unknown encrypted column {entity_type!r}/{column_name!r}"
        )
    return spec


# ── Force-режим: durable флаг + in-process кэш под maintenance-gate ──────────
#
# `force_active` живёт в БД (общий для реплик, переживает рестарт). Gate читает
# его на каждый запрос, поэтому поверх лежит короткий TTL-кэш: под нагрузкой
# флаг не долбит БД, при снятии force сервис разблокируется с задержкой не
# больше TTL. Кэш инвалидируется явно из `set_force_active` в том же процессе.
_gate_cache: dict[str, object] = {"data": None, "ts": 0.0}


def _invalidate_gate_cache() -> None:
    _gate_cache["data"] = None
    _gate_cache["ts"] = 0.0


async def get_force_active(db: AsyncSession) -> bool:
    """Прочитать durable флаг force-режима из singleton-строки.

    Отсутствие строки (старая БД без миграции) трактуется как выключенный
    force — fail-open, чтобы отсутствие state-таблицы не блокировало сервис.
    """
    row = await db.get(SecretsMigrationState, SINGLETON_STATE_ID)
    return bool(row.force_active) if row is not None else False


async def set_force_active(db: AsyncSession, active: bool) -> None:
    """Взвести/снять durable флаг force-режима (upsert singleton + commit)."""
    now = datetime.now(timezone.utc)
    stmt = (
        pg_insert(SecretsMigrationState.__table__)
        .values(id=SINGLETON_STATE_ID, force_active=active, updated_at=now)
        .on_conflict_do_update(
            index_elements=["id"],
            set_={"force_active": active, "updated_at": now},
        )
    )
    await db.execute(stmt)
    await db.commit()
    _invalidate_gate_cache()


def throughput_per_second() -> float:
    """Оценка пропускной способности перешифровки (строк/сек) из конфига."""
    return float(get_settings().reencrypt_throughput_per_second)


def compute_eta_seconds(remaining: int) -> float:
    """ETA осушения `remaining` строк при текущей оценке throughput'а."""
    if remaining <= 0:
        return 0.0
    tp = throughput_per_second()
    if tp <= 0:
        return float(remaining)
    return remaining / tp


def compute_retry_after(remaining: int) -> int:
    """Значение заголовка Retry-After: ceil(eta)+буфер, зажатое в [min, max]."""
    settings = get_settings()
    eta = compute_eta_seconds(remaining)
    value = math.ceil(eta) + settings.reencrypt_retry_after_buffer_seconds
    value = max(value, settings.reencrypt_retry_after_min_seconds)
    value = min(value, settings.reencrypt_retry_after_max_seconds)
    return int(value)


async def remaining_legacy(db: AsyncSession) -> int:
    """Сколько шифр-ячеек ещё под не-активной версией ключа (все колонки реестра)."""
    active = get_keystore().get_active_version()
    by_version = await _count_by_version(db)
    return sum(c for v, c in by_version.items() if v != active)


async def force_gate_state_cached() -> dict:
    """Снимок состояния force-режима для maintenance-gate'а (с TTL-кэшем).

    Возвращает `{force_active, remaining, retry_after, eta_seconds}`. Открывает
    собственную короткую сессию (gate работает вне request-scoped DI). Любая
    ошибка чтения трактуется как выключенный force — сбой БД не должен ронять
    весь сервис в 503.
    """
    settings = get_settings()
    ttl = settings.reencrypt_gate_cache_ttl_seconds
    now = time.monotonic()
    cached = _gate_cache["data"]
    if cached is not None and (now - float(_gate_cache["ts"])) < ttl:
        return cached  # type: ignore[return-value]

    from src.db.session import AsyncSessionLocal

    force = False
    remaining = 0
    try:
        async with AsyncSessionLocal() as session:
            force = await get_force_active(session)
            if force:
                remaining = await remaining_legacy(session)
    except Exception as exc:  # noqa: BLE001 — fail-open: gate не валит сервис
        logger.warning(
            "reencrypt gate state read failed (%s) — treating force as inactive",
            type(exc).__name__,
        )
        force = False
        remaining = 0

    if force:
        retry_after = compute_retry_after(remaining)
        eta = compute_eta_seconds(remaining)
    else:
        retry_after = settings.reencrypt_retry_after_min_seconds
        eta = 0.0
    data = {
        "force_active": force,
        "remaining": remaining,
        "retry_after": retry_after,
        "eta_seconds": eta,
    }
    _gate_cache["data"] = data
    _gate_cache["ts"] = now
    return data


async def finish_if_drained(db: AsyncSession) -> bool:
    """Если миграция осушена целиком — вывести старые ключи и снять force.

    «Осушена» = 0 owner-строк под не-активными версиями И пустой outbox
    (pending+processing == 0). Тогда:

    * best-effort авто-retire всех опустевших не-активных версий keystore
      (жёсткий инвариант «0 строк на версии» проверяется внутри `retire`);
    * если был взведён force — снять его (разблокировать сервис) и записать
      `encryption.force_reencrypt_cleared`.

    Возвращает True, если миграция завершена (независимо от того, был ли
    force). Идемпотентно.
    """
    active = get_keystore().get_active_version()
    by_version = await _count_by_version(db)
    remaining = sum(c for v, c in by_version.items() if v != active)
    outbox = await _outbox_counts_by_status(db)
    open_outbox = outbox.get(STATUS_PENDING, 0) + outbox.get(STATUS_PROCESSING, 0)
    if remaining > 0 or open_outbox > 0:
        return False

    await _maybe_auto_retire(db, None)
    if await get_force_active(db):
        await set_force_active(db, False)
        audit_service.emit(
            "encryption.force_reencrypt_cleared",
            actor_id=None,
            actor_type="system",
            target_id=None,
            target_type="secret",
            status="success",
            allowed=True,
            details={"active_version": active},
        )
    return True


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
    """Объединить per-column счётчики по всем шифр-колонкам реестра."""
    combined: dict[int, int] = {}
    for spec in ENCRYPTED_COLUMNS:
        per_column = await _count_by_version_for_column(db, spec.column)
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
    """Сумма non-NULL ciphertext'ов по всем шифр-колонкам реестра."""
    total = 0
    for spec in ENCRYPTED_COLUMNS:
        cnt = (
            await db.execute(
                select(func.count()).where(spec.column.is_not(None))
            )
        ).scalar_one()
        total += int(cnt)
    return int(total)


async def _outbox_counts_by_status(db: AsyncSession) -> dict[str, int]:
    """Группировка outbox-row'ов по статусу — для оператора и worker'а."""
    stmt = select(
        ReencryptOutboxEntry.status,
        func.count(ReencryptOutboxEntry.id),
    ).group_by(ReencryptOutboxEntry.status)
    result = await db.execute(stmt)
    return {row[0]: int(row[1]) for row in result.all()}


async def has_rows_on_version(db: AsyncSession, version: int) -> bool:
    """Есть ли хоть одна шифр-колонка любой таблицы с ciphertext'ом версии ``version``.

    Инвариант retire: версию нельзя выводить, пока хоть в одном шифр-поле любой
    owner-row остаётся ciphertext на этой версии. Дешёвый `EXISTS` по каждой
    колонке реестра через `LIKE 'v<N>$%'` с `LIMIT 1`. Разделитель `$` после
    номера не даёт `v1$%` зацепить `v10$...`. Пока на версии остаются строки,
    Postgres находит совпадение сразу; полный скан случается только когда строк
    уже нет (конец миграции), и редко.
    """
    prefix = f"v{int(version)}$%"
    for spec in ENCRYPTED_COLUMNS:
        column = spec.column
        stmt = (
            select(spec.model.id)
            .where(column.is_not(None))
            .where(column.like(prefix))
            .limit(1)
        )
        if (await db.execute(stmt)).first() is not None:
            return True
    return False


async def has_open_outbox_on_version(db: AsyncSession, version: int) -> bool:
    """Есть ли незакрытые (pending/processing) outbox-row'ы на версии ``version``.

    Версия читается из `v<N>$` префикса `legacy_ciphertext`. Нужна, чтобы не
    вывести ключ, по которому ещё висит неотработанная задача перешифровки.
    """
    prefix = f"v{int(version)}$%"
    stmt = (
        select(ReencryptOutboxEntry.id)
        .where(ReencryptOutboxEntry.status.in_([STATUS_PENDING, STATUS_PROCESSING]))
        .where(ReencryptOutboxEntry.legacy_ciphertext.like(prefix))
        .limit(1)
    )
    return (await db.execute(stmt)).first() is not None


async def _maybe_auto_retire(
    db: AsyncSession, candidate_versions: list[int] | None
) -> None:
    """Best-effort авто-вывод опустевших не-активных версий ключа.

    Вызывается после commit'а перешифровочного батча. Делегирует в
    `key_rotation_service.auto_retire_drained_versions` (ленивый импорт —
    разрывает цикл import'ов между сервисами). Любая ошибка глушится в
    WARNING: закрытие outbox-row важнее, чем мгновенный retire — он всё равно
    случится на следующем батче.

    `candidate_versions=None` — проверить все версии keystore (legacy
    sync-путь); список — сузить до конкретных (хук из finalize_done передаёт
    версию только что закрытой строки).
    """
    try:
        from src.services import key_rotation_service

        await key_rotation_service.auto_retire_drained_versions(
            db, candidate_versions=candidate_versions
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, не валим основной поток
        logger.warning(
            "auto-retire after reencrypt failed: %s", type(exc).__name__
        )


async def status(db: AsyncSession) -> dict:
    """Сводка по миграции для worker'а / оператора.

    Возвращает: ``{remaining, total, active_version, by_version, app_env,
    outbox, server_account_password_encrypted, ipmi_controller_password_encrypted,
    remaining_legacy_total, migrated_pct, outbox_pending}``.

    * ``total`` — все non-NULL шифр-ячейки по всем колонкам реестра.
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
    keystore = get_keystore()
    active = keystore.get_active_version()
    by_version = await _count_by_version(db)
    total = await _total(db)
    remaining = sum(c for v, c in by_version.items() if v != active)
    force_active = await get_force_active(db)
    outbox_counts = await _outbox_counts_by_status(db)
    outbox_snapshot = {
        STATUS_PENDING: outbox_counts.get(STATUS_PENDING, 0),
        STATUS_PROCESSING: outbox_counts.get(STATUS_PROCESSING, 0),
        STATUS_DONE: outbox_counts.get(STATUS_DONE, 0),
        STATUS_FAILED: outbox_counts.get(STATUS_FAILED, 0),
    }
    # Полный per-column breakdown по всему реестру: ключ — `<table>.<column>`.
    # Оператор видит, какое именно шифр-поле тащит legacy-токены (mgmt-пароль
    # сервера, ssh-ключ аккаунта и т.д.).
    columns_breakdown: dict[str, dict] = {}
    remaining_legacy_total = 0
    for spec in ENCRYPTED_COLUMNS:
        bd = await _column_breakdown(db, spec.column, active)
        columns_breakdown[f"{spec.model.__tablename__}.{spec.column_name}"] = bd
        remaining_legacy_total += bd["remaining_legacy"]
    # Два исторических top-level поля оставлены под старый контракт схемы.
    sa_breakdown = columns_breakdown["server_accounts.password_encrypted"]
    ipmi_breakdown = columns_breakdown["ipmi_controllers.password_encrypted"]
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
        "columns": columns_breakdown,
        "remaining_legacy_total": remaining_legacy_total,
        "migrated_pct": migrated_pct,
        "outbox_pending": outbox_snapshot[STATUS_PENDING],
        # Режим и прогресс перешифровки для UI/gate: `mode` производный от
        # force-флага, `eta_seconds` / `throughput` — для обратного отсчёта.
        "mode": "force" if force_active else "lazy",
        "force_active": force_active,
        "eta_seconds": compute_eta_seconds(remaining),
        "throughput": throughput_per_second(),
        "versions_in_keystore": keystore.list_versions(),
    }


# ── Outbox seeding ──────────────────────────────────────────────────────────


def _legacy_ciphertext_filter(column, active_prefix: str):
    """Общий predicate для legacy-ciphertext SELECT'ов.

    Возвращает три where-кляузы: not-NULL, wire-prefix `v<N>$`, и
    not-LIKE-активного префикса. Применяется к любой шифр-колонке реестра.
    """
    return (
        column.is_not(None),
        column.op("~")("^v[0-9]+\\$"),
        column.notlike(active_prefix),
    )


async def _pick_legacy_ciphertext_rows(
    db: AsyncSession,
    spec: EncryptedColumnSpec,
    active: int,
    limit: int | None,
    *,
    scalars: bool = True,
):
    """SELECT row'ов с legacy-ciphertext'ом для заданной шифр-колонки реестра.

    `scalars=True` — отдаём полные ORM-объекты (использует `reencrypt_batch`).
    `scalars=False` — отдаём пары `(id, ciphertext)` для `seed_outbox`,
    без материализации остальной row'ы.

    `limit=None` — без LIMIT'а (использует `seed_outbox` при не заданном
    quota'е). Иначе clause LIMIT добавляется в SELECT.
    """
    active_prefix = f"v{active}$%"
    model = spec.model
    column = spec.column
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

    # (entity_type, entity_id, column_name, ciphertext)
    candidates: list[tuple[str, str, str, str]] = []

    for spec in ENCRYPTED_COLUMNS:
        remaining = None if limit is None else max(0, limit - len(candidates))
        if remaining == 0:
            break
        rows = await _pick_legacy_ciphertext_rows(
            db, spec, active, remaining, scalars=False
        )
        for row in rows:
            candidates.append((spec.entity_type, row[0], spec.column_name, row[1]))

    scanned = len(candidates)

    # Bulk INSERT с ON CONFLICT — opportunistic против partial-unique
    # индекса. Конфликт не имеет explicit named constraint у `pg_insert`,
    # поэтому используем index_where форму через `index_elements` +
    # `index_where`. Для предсказуемости делаем по одной строке: на крупных
    # seed'ах разница ничтожна, а ошибка на одной row не валит весь batch.
    for entity_type, entity_id, column_name, ciphertext in candidates:
        stmt = (
            pg_insert(ReencryptOutboxEntry.__table__)
            .values(
                id=f"rox_{uuid4().hex}",
                entity_type=entity_type,
                entity_id=entity_id,
                column_name=column_name,
                legacy_ciphertext=ciphertext,
                status=STATUS_PENDING,
            )
            .on_conflict_do_nothing(
                index_elements=["entity_type", "entity_id", "column_name"],
                index_where=_OUTBOX_ACTIVE_INDEX_WHERE,
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
            "column_name": r.column_name,
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
            "column_name": s["column_name"],
            "legacy_ciphertext": s["legacy_ciphertext"],
            "attempts": s["attempts_before"] + 1,
        }
        for s in snapshot
    ]


async def finalize_done(db: AsyncSession, outbox_id: str) -> dict:
    """Закрыть outbox-row: расшифровать legacy, зашифровать активной и сохранить.

    Поток (всё — в одной короткой транзакции):

    1. SELECT outbox-row с `FOR UPDATE`, проверяем `status='processing'`.
    2. SELECT owner-row с `FOR UPDATE`. Если row пропал или ciphertext
       нужной колонки разошёлся с legacy (параллельный rotate) — закрываем
       outbox-row `done`/`skipped=True` без crypto-операций.
    3. `decrypt(legacy_ciphertext, aad)` старым ключом → plaintext.
    4. `encrypt(plaintext, aad)` активным ключом → new ciphertext.
    5. UPDATE owner-row.<column_name> = new ciphertext (колонка из outbox-row).
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
    # spec по (entity_type, column_name) — какую именно колонку owner-row'ы
    # перешифровываем и каким AAD. Валидируется на seed; сюда не доедем штатно.
    spec = _spec_for(entry.entity_type, entry.column_name)
    owner_stmt = (
        select(spec.model)
        .where(spec.model.id == entry.entity_id)
        .with_for_update()
    )
    owner = (await db.execute(owner_stmt)).scalar_one_or_none()

    if owner is None:
        skipped = True
        skip_reason = "owner_vanished"
    elif getattr(owner, spec.column_name) != entry.legacy_ciphertext:
        skipped = True
        skip_reason = "owner_ciphertext_changed"
    else:
        aad = spec.aad_fn(entry.entity_id)
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
        setattr(owner, spec.column_name, new_ciphertext)

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
                "column_name": entry.column_name,
            },
        })

    # Версию закрываемой строки снимаем до commit'а — после него атрибуты
    # entry экспайрятся, а нам нужно знать, какую версию проверять на retire.
    drained_version = _parse_version(entry.legacy_ciphertext)
    entry.status = STATUS_DONE
    entry.processed_at = datetime.now(timezone.utc)
    entry.last_error = None
    await db.commit()
    for emit_kwargs in deferred_emits:
        action = emit_kwargs.pop("action")
        audit_service.emit(action, **emit_kwargs)
    if drained_version is not None:
        await _maybe_auto_retire(db, [drained_version])
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


_SA_PASSWORD_SPEC = _SPEC_BY_KEY[(ENTITY_SERVER_ACCOUNT, "password_encrypted")]
_IPMI_PASSWORD_SPEC = _SPEC_BY_KEY[(ENTITY_IPMI_CONTROLLER, "password_encrypted")]


async def _pick_account_batch(db: AsyncSession, active: int, limit: int) -> list[ServerAccount]:
    """Выбрать ServerAccount'ы с password-префиксом, отличным от активного."""
    return await _pick_legacy_ciphertext_rows(db, _SA_PASSWORD_SPEC, active, limit)


async def _pick_ipmi_batch(db: AsyncSession, active: int, limit: int) -> list[IpmiController]:
    """Симметрично _pick_account_batch — для ipmi_controllers.password."""
    return await _pick_legacy_ciphertext_rows(db, _IPMI_PASSWORD_SPEC, active, limit)


async def _pick_rows_for_spec(
    db: AsyncSession, spec: EncryptedColumnSpec, active: int, limit: int
):
    """Выбрать legacy-row'ы под одну колонку реестра.

    Два password-поля роутятся через исторические `_pick_account_batch` /
    `_pick_ipmi_batch` (их monkeypatch'ат старые тесты), остальные колонки —
    через общий `_pick_legacy_ciphertext_rows`.
    """
    if spec is _SA_PASSWORD_SPEC:
        return await _pick_account_batch(db, active, limit)
    if spec is _IPMI_PASSWORD_SPEC:
        return await _pick_ipmi_batch(db, active, limit)
    return await _pick_legacy_ciphertext_rows(db, spec, active, limit)


def _reencrypt_row(
    row, *, aad_fn, entity_type: str, log_label: str,
    column_name: str = "password_encrypted",
) -> dict | None:
    """Перешифровать один ciphertext-row под активный ключ.

    Возвращает `None` на успех (и обновляет `row.<column_name>` in-place)
    либо dict `{entity_type, entity_id, error_class}` на сбой decrypt/encrypt.
    Любые исключения decrypt/encrypt здесь поглощаются — caller считает
    `processed` / `errors` по возврату.

    `aad_fn` — AAD-функция колонки (см. реестр `ENCRYPTED_COLUMNS`); вызывается
    с `row.id`. `column_name` — какое шифр-поле row'ы перешифровываем.
    `log_label` — человеческое имя источника для WARNING'а (например
    `"server_account.password_encrypted"`).
    """
    try:
        aad = aad_fn(row.id)
        plain = secrets_service.decrypt(getattr(row, column_name), aad=aad)
        setattr(row, column_name, secrets_service.encrypt(plain, aad=aad))
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

    processed = 0
    errors = 0
    # Список упавших row'ов с минимумом полей для диагностики оператору.
    # `entity_id` уже логируется через `logger.warning` ниже, но в summary
    # удобно отдать структурированно — endpoint кладёт это в audit details,
    # чтобы SIEM мог разложить инциденты по таблицам.
    failed_rows: list[dict[str, str]] = []

    # Идём по всему реестру шифр-колонок, деля quota между ними по порядку:
    # каждая колонка забирает остаток limit'а. Так один батч может закрыть
    # разные поля одной и той же owner-row'ы.
    remaining_quota = limit
    for spec in ENCRYPTED_COLUMNS:
        if remaining_quota <= 0:
            break
        rows = await _pick_rows_for_spec(db, spec, active, remaining_quota)
        remaining_quota -= len(rows)
        log_label = f"{spec.entity_type}.{spec.column_name}"
        for row in rows:
            failure = _reencrypt_row(
                row, aad_fn=spec.aad_fn, entity_type=spec.entity_type,
                log_label=log_label, column_name=spec.column_name,
            )
            if failure is None:
                processed += 1
            else:
                errors += 1
                failed_rows.append(failure)

    if processed > 0:
        await db.flush()
        await db.commit()
        # Батч мог осушить старую версию целиком — пробуем вывести опустевшие.
        await _maybe_auto_retire(db, None)
    # processed == 0: писать нечего, не трогаем транзакцию — оставляем
    # SAVEPOINT / outer transaction caller'у.

    return {"processed": processed, "errors": errors, "failed_rows": failed_rows}
