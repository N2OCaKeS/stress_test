"""Постепенная фоновая ре-шифрация секретов под активный мастер-ключ.

Контекст. После смены `SERVER_ENCRYPTION_KEY` старые ciphertext'ы (`v<old>$...`)
остаются читаемыми через `SERVER_ENCRYPTION_KEY__v<old>`, новые пишутся
активной версией. Чтобы можно было дропнуть старый ключ, надо пройтись по
всем строкам в `server_accounts.password_encrypted` и
`ipmi_controllers.password_encrypted`, расшифровать тем ключом, что
соответствует префиксу, и зашифровать обратно активной версией.

Этот модуль даёт две операции:

* :func:`status` — счётчики по версиям, чтобы worker и оператор знали, есть
  ли вообще что мигрировать.
* :func:`reencrypt_batch` — забрать N строк (любой из двух таблиц), у которых
  префикс не совпадает с активной версией, расшифровать и переписать.
  Идемпотентна: повторный вызов не пишет одну и ту же строку дважды
  (мы выбираем только `version != active`).

Worker зовёт оба эндпоинта через `secrets_reencrypt_lazy` periodic task'у.
"""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import IpmiController, ServerAccount
from src.services import secrets_service


# Совпадает с wire-форматом `v<N>$...`. Если строка не начинается с `v` или
# в ней нет `$` — `coalesce(version, NULL)` останется NULL и не попадёт в
# счётчик по версиям. Это корректно: такие строки означают повреждённый
# ciphertext, и пытаться их перешифровать смысла нет — оператор должен
# увидеть их в `total - sum(by_version)`.
_VERSION_PREFIX_RE = re.compile(r"^v(\d+)\$")


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


async def status(db: AsyncSession) -> dict:
    """Сводка по миграции для worker'а / оператора.

    Возвращает: ``{remaining, total, active_version, by_version}``.

    * ``total`` — все non-NULL `password_encrypted` в обеих таблицах.
    * ``by_version`` — `{N: count}` по версиям из префиксов.
    * ``remaining`` — сумма `count`'ов для версий, отличных от активной;
      включает строки с malformed префиксом? Нет — те не парсятся и не
      попадают в `by_version`. `remaining` — сколько ЕЩЁ есть, что worker
      может переcнифровать; malformed строки worker не вылечит.
    """
    settings = get_settings()
    active = settings.server_encryption_key_version
    by_version = await _count_by_version(db)
    total = await _total(db)
    remaining = sum(c for v, c in by_version.items() if v != active)
    return {
        "remaining": remaining,
        "total": total,
        "active_version": active,
        "by_version": by_version,
        "app_env": settings.app_env,
    }


async def _pick_account_batch(db: AsyncSession, active: int, limit: int) -> list[ServerAccount]:
    """Выбрать ServerAccount'ы с префиксом, отличным от активного.

    `~ '^v[0-9]+\\$'` — отсекает NULL и malformed; `NOT LIKE 'v<active>$%'`
    — отсекает уже мигрированные. ORDER BY id — детерминизм для idempotency
    проверки в тестах.
    """
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
    """Перешифровать до `limit` строк активной версией ключа.

    Берём строки из обеих таблиц (сначала server_accounts, потом ipmi —
    порядок выбора неважен, главное чтобы общий счёт не превысил limit).
    Каждую строку: `decrypt` старым (по wire-версии) → `encrypt` активным.
    Если decrypt упал (например, в env нет ключа для старой версии или
    ciphertext повреждён) — инкрементим `errors` и едем дальше, чтобы
    одна порча не блокировала всю миграцию. commit — один раз в конце
    батча, чтобы либо мигрируем все, либо ни одной (worker увидит
    `processed=0, errors=N`, оператор поднимет вопрос).

    Возвращает: ``{processed, errors}``.
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
    # SAVEPOINT / outer transaction caller'у. Это важно для тестов с
    # rollback-on-teardown: лишний `db.rollback()` сбросил бы SAVEPOINT.

    return {"processed": processed, "errors": errors}
