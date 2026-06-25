"""Обработчики lifecycle-событий от auth_service.

Каждый handler атомарен в рамках одной DB-транзакции: либо проходит
полностью, либо откатывается. Audit-эмиты — best-effort, не блокируют
основной flow (см. `audit_service.emit`).

Семантика веток — README §«Lifecycle»:

* personal cred, owner=user удалён:
    - есть RoleACL grantees → cred → blocked + WARNING. admin secret_service
      того же dept'а потом может transfer'ить в 30-day окне;
    - нет ACL → hard delete, удалять некому.
* department/cross_department cred, owner=dep удалён:
    - cred → blocked + WARNING. account_admin в 30-day окне делает transfer.
* dep удалён как recipient cross-dep:
    - DeptGrant + RoleACL для этого dep'а — cascade delete + CRITICAL.
* dep отозван service-access (`secret_service`):
    - все DeptGrant'ы где он recipient + RoleACL'и — cascade delete + CRITICAL.
    - свои cred'ы dep'а НЕ трогаем (это handle_dept_deleted_as_owner).
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import SERVICE_NAME
from src.models import Credential, DeptGrant, RoleACL
from src.repositories import credentials as cred_repo
from src.repositories import role_acls as acl_repo
from src.services import audit_service

logger = logging.getLogger(__name__)


# Аудит-payload для cascade'ов не должен раздуваться на dep с сотнями кред.
# Хвост ID-шников сверх лимита отрезаем + ставим `truncated: True`.
_AUDIT_CRED_IDS_CAP = 50


def _cap_cred_ids(ids: list[str]) -> tuple[list[str], bool]:
    if len(ids) <= _AUDIT_CRED_IDS_CAP:
        return ids, False
    return ids[:_AUDIT_CRED_IDS_CAP], True


# ── Internal queries (нет нужды раздувать репо ради двух одноразовых select'ов) ─


async def _list_personal_for_user(db: AsyncSession, user_id: str) -> list[Credential]:
    stmt = select(Credential).where(
        Credential.owner_user_id == user_id,
        Credential.scope == "personal",
    )
    return list((await db.execute(stmt)).scalars())


async def _list_owned_by_dept(db: AsyncSession, dept_id: str) -> list[Credential]:
    stmt = select(Credential).where(Credential.owner_dept_id == dept_id)
    return list((await db.execute(stmt)).scalars())


async def _list_dept_grants_for_recipient(
    db: AsyncSession, dept_id: str
) -> list[DeptGrant]:
    stmt = select(DeptGrant).where(DeptGrant.recipient_dept_id == dept_id)
    return list((await db.execute(stmt)).scalars())


async def _count_role_acls_for_cred(db: AsyncSession, cred_id: str) -> int:
    """Дешевле, чем тащить полный список — нам важно только наличие."""
    stmt = select(func.count()).select_from(RoleACL).where(RoleACL.cred_id == cred_id)
    return (await db.execute(stmt)).scalar_one()


# ── Handlers ───────────────────────────────────────────────────────────────────


async def handle_user_deleted(
    db: AsyncSession, user_id: str, actor_id: str
) -> dict:
    """Обработать `delete_user(user_id)` — personal-креды этого user'а.

    Возвращает summary: `{blocked_count, deleted_count, errors}`. Транзакция
    атомарная: при любой ошибке rollback, summary возвращается с заполненным
    `errors`.
    """
    summary = {
        "blocked_count": 0,
        "deleted_count": 0,
        "role_acls_revoked": 0,
        "errors": [],
    }
    try:
        creds = await _list_personal_for_user(db, user_id)
        for cred in creds:
            # ACL личной кред'ы легитимен только в dep'е владельца. Чужие
            # dep-ACL'и сносим сразу: после block→transfer они бы оставили
            # стороннему dep'у доступ к кред'е нового владельца. Если dep
            # владельца неизвестен (старые строки без owner_user_dept_id) —
            # отличить «свой» ACL от «чужого» нельзя, оставляем как есть.
            if cred.owner_user_dept_id is not None:
                stale = await acl_repo.delete_outside_dept(
                    db, cred.id, cred.owner_user_dept_id
                )
                summary["role_acls_revoked"] += stale
            acl_count = await _count_role_acls_for_cred(db, cred.id)
            if acl_count > 0:
                await cred_repo.mark_blocked(db, cred, reason="owner_user_deleted")
                summary["blocked_count"] += 1
                audit_service.emit(
                    "tokens.owner_user_deleted_block",
                    actor_id=actor_id,
                    target_id=cred.id,
                    target_type="credential",
                    severity="WARNING",
                    details={
                        "owner_user_id": user_id,
                        "scope": cred.scope,
                        "service": cred.service,
                        "role_acl_count": acl_count,
                    },
                )
            else:
                cred_id_snap = cred.id
                cred_scope = cred.scope
                cred_service = cred.service
                cred_name = cred.name
                await cred_repo.delete(db, cred)
                summary["deleted_count"] += 1
                audit_service.emit(
                    "tokens.delete",
                    actor_id=actor_id,
                    target_id=cred_id_snap,
                    target_type="credential",
                    details={
                        "scope": cred_scope,
                        "service": cred_service,
                        "name": cred_name,
                        "auto_delete": True,
                        "reason": "owner_user_deleted_no_grantees",
                    },
                )
        await db.commit()
    except Exception as exc:  # noqa: BLE001 — best-effort, rollback и сообщаем caller'у
        await db.rollback()
        logger.exception("handle_user_deleted: rolled back: %s", exc)
        summary["errors"].append(f"{type(exc).__name__}: {exc}")
    return summary


async def handle_dept_deleted_as_owner(
    db: AsyncSession, dept_id: str, actor_id: str
) -> dict:
    """Обработать `delete_dept(dept_id)` — все cred'ы где он owner → blocked.

    Окно 30 дней даёт account_admin'у возможность transfer'нуть. По
    истечении — sweep подберёт.
    """
    summary = {"blocked_count": 0, "errors": []}
    try:
        creds = await _list_owned_by_dept(db, dept_id)
        for cred in creds:
            if cred.status == "blocked":
                # Идемпотентность: повторный event не плодит дубль аудита.
                continue
            await cred_repo.mark_blocked(db, cred, reason="owner_dept_deleted")
            summary["blocked_count"] += 1
            audit_service.emit(
                "tokens.owner_dept_deleted_block",
                actor_id=actor_id,
                target_id=cred.id,
                target_type="credential",
                severity="WARNING",
                details={
                    "owner_dept_id": dept_id,
                    "scope": cred.scope,
                    "service": cred.service,
                },
            )
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.exception("handle_dept_deleted_as_owner: rolled back: %s", exc)
        summary["errors"].append(f"{type(exc).__name__}: {exc}")
    return summary


async def handle_dept_deleted_as_recipient(
    db: AsyncSession, dept_id: str, actor_id: str
) -> dict:
    """Обработать `delete_dept(dept_id)` — он recipient в чужих cross-dep cred'ах.

    Cascade: для каждого DeptGrant(_, dept_id) → удалить связанные
    RoleACL(cred_id, dept_id, *) → удалить сам grant. Один CRITICAL audit с
    list cred_ids, чтобы не плодить десятки событий на массовом delete.
    """
    summary = {"dept_grants_revoked": 0, "role_acls_revoked": 0, "errors": []}
    try:
        grants = await _list_dept_grants_for_recipient(db, dept_id)
        if not grants:
            # Идемпотентность: повторный event без новых grant'ов — no-op,
            # симметрично handle_dept_deleted_as_owner.
            return summary
        affected_cred_ids: list[str] = []
        for grant in grants:
            removed = await acl_repo.delete_for_cred_dept(db, grant.cred_id, dept_id)
            summary["role_acls_revoked"] += removed
            affected_cred_ids.append(grant.cred_id)
            await db.delete(grant)
            await db.flush()
            summary["dept_grants_revoked"] += 1
        if affected_cred_ids:
            capped_ids, truncated = _cap_cred_ids(affected_cred_ids)
            audit_details = {
                "recipient_dept_id": dept_id,
                "cred_ids": capped_ids,
                "removed_dept_grants": summary["dept_grants_revoked"],
                "removed_role_acls": summary["role_acls_revoked"],
            }
            if truncated:
                audit_details["truncated"] = True
                audit_details["total_cred_ids"] = len(affected_cred_ids)
            audit_service.emit(
                "tokens.dept_recipient_cascade",
                actor_id=actor_id,
                target_id=dept_id,
                target_type="department",
                severity="CRITICAL",
                details=audit_details,
            )
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.exception("handle_dept_deleted_as_recipient: rolled back: %s", exc)
        summary["errors"].append(f"{type(exc).__name__}: {exc}")
    return summary


async def handle_dept_service_access_revoked(
    db: AsyncSession, dept_id: str, service: str, actor_id: str
) -> dict:
    """`revoke department_service_access(secret_service, dept_id)` cascade.

    Сносим всё, что давало dep'у доступ к чужим cross-dep cred'ам:
    DeptGrant'ы (где dep — recipient) + RoleACL'и для этого dep'а
    (включая те, что прицеплены к чужим personal-cred'ам).

    Owned-by-dept cred'ы НЕ трогаем — у dep'а ещё может остаться
    account_admin transfer-окно, отдельный сценарий это `delete_dept`.
    """
    summary = {"dept_grants_revoked": 0, "role_acls_revoked": 0, "errors": []}
    if service != SERVICE_NAME:
        # Событие пришло про другой сервис — нас не касается, no-op.
        return summary
    try:
        grants = await _list_dept_grants_for_recipient(db, dept_id)
        affected_cred_ids: list[str] = []
        for grant in grants:
            removed = await acl_repo.delete_for_cred_dept(db, grant.cred_id, dept_id)
            summary["role_acls_revoked"] += removed
            affected_cred_ids.append(grant.cred_id)
            await db.delete(grant)
            await db.flush()
            summary["dept_grants_revoked"] += 1

        # Дополнительно — RoleACL'и для этого dep'а на cred'ах, к которым у
        # него есть прямой grant НЕ через DeptGrant (personal-cred + ACL на
        # его dep). delete_for_cred_dept выше уже снёс часть, для оставшихся
        # пройдёмся по всем оставшимся ACL'ям с dept_id == dept_id.
        stmt = select(RoleACL).where(RoleACL.dept_id == dept_id)
        leftover = list((await db.execute(stmt)).scalars())
        for acl in leftover:
            cred_id = acl.cred_id
            await acl_repo.delete(db, acl)
            summary["role_acls_revoked"] += 1
            if cred_id not in affected_cred_ids:
                affected_cred_ids.append(cred_id)

        if not affected_cred_ids:
            # Идемпотентность: ни grant'ов, ни ACL'ей у dep'а нет — повторный
            # event либо доступ к сервису у dep'а отсутствовал изначально.
            # Пустой CRITICAL в SOC-канал не шлём.
            await db.commit()
            return summary

        capped_ids, truncated = _cap_cred_ids(affected_cred_ids)
        cascade_details: dict = {
            "dept_id": dept_id,
            "service": service,
            "cred_ids": capped_ids,
            "removed_dept_grants": summary["dept_grants_revoked"],
            "removed_role_acls": summary["role_acls_revoked"],
        }
        if truncated:
            cascade_details["truncated"] = True
            cascade_details["total_cred_ids"] = len(affected_cred_ids)
        audit_service.emit(
            "tokens.dept_revoke_cascade",
            actor_id=actor_id,
            target_id=dept_id,
            target_type="department",
            severity="CRITICAL",
            details=cascade_details,
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.exception("handle_dept_service_access_revoked: rolled back: %s", exc)
        summary["errors"].append(f"{type(exc).__name__}: {exc}")
    return summary
