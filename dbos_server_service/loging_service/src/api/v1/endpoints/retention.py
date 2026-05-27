"""Retention-политика: настройка срока хранения логов.

Без фильтров активна одна глобальная политика, применяемая ко ВСЕМ
событиям аудита. С `severity_filter`/`service_filter` активным становится
набор строк (одна на пару severity×service). PUT и DELETE работают со
ВСЕМ активным набором сразу — `PUT` гасит прежний набор и ставит новый,
`DELETE` гасит весь набор целиком.

События `loging_service` НИКОГДА не удаляются (защищены от ротации —
безопасностный инвариант, см. `repositories/retention_policies.py::apply_active`).

Фоновая очистка запускается раз в сутки в 00:00 MSK (см.
`main._retention_loop`). Эндпоинта `POST /retention/apply` сейчас нет —
вызывать ротацию вручную из API нельзя.

PUT/DELETE пишут self-audit через `record_admin_action` (минуя rule
engine) — изменение политики хранения не должно теряться из SIEM, даже
если админ создал SUPPRESS-правило на `logging.*`.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from src.dependencies.auth import AdminIdentity, require_admin
from src.dependencies.db import get_db
from src.repositories import retention_policies as repo
from src.schemas.events import EventCreate
from src.schemas.retention import (
    RetentionPolicyCreate,
    RetentionPolicyResponse,
)
from src.services import event_service

router = APIRouter(dependencies=[Depends(require_admin)])


def _audit(db: Session, identity: dict, details: dict) -> None:
    """Self-audit для retention CRUD — симметрично `rules.py::_audit`.

    Зеркалит логику из rules.py: actor_type c fallback на "user", все
    identity-поля проброшены для атрибуции в SIEM (без department_id и
    username SOC видел бы только opaque actor_id).
    """
    actor_type = identity.get("actor_type") or "user"
    event_service.record_admin_action(
        db,
        EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="loging_service",
            action="logging.retention_write",
            actor_id=identity.get("user_id"),
            actor_type=actor_type,
            username=identity.get("username"),
            department_id=identity.get("department_id"),
            status="success",
            allowed=True,
            severity=None,
            details=details,
        ),
    )


def _snapshot(policy) -> dict | None:
    """Сериализуемый снимок политики для details.old/new — без datetime'ов."""
    if policy is None:
        return None
    return {
        "id": policy.id,
        "retain_days": policy.retain_days,
        "description": policy.description,
        "is_active": policy.is_active,
        "severity": policy.severity,
        "service": policy.service,
    }


@router.get(
    "",
    response_model=RetentionPolicyResponse | None,
    summary="Получить текущую retention-политику",
    description=(
        "Возвращает активную политику или `null`, если ни одной не настроено.\n\n"
        "**Доступ:** `platform_role=loging_admin`.\n\n"
        "**Связано:** `PUT /retention` — задать/заменить политику; "
        "`DELETE /retention` — отключить ротацию (хранить вечно)."
    ),
)
def get_policy(db: Session = Depends(get_db)) -> RetentionPolicyResponse | None:
    policy = repo.get_active(db)
    return RetentionPolicyResponse.model_validate(policy) if policy else None


@router.put(
    "",
    response_model=RetentionPolicyResponse,
    summary="Задать retention-политику",
    description=(
        "Создаёт или заменяет активную retention-политику. Все события аудита "
        "старше `retain_days` будут удалены при ближайшей фоновой очистке "
        "(00:00 MSK). События `loging_service` всегда защищены от удаления "
        "(retention-инвариант — see-also `repositories/retention_policies.py`).\n\n"
        "`retain_days` ∈ [30, 3650]. Меньше 30 — нет: минимальный compliance "
        "срок для security-логов.\n\n"
        "**Доступ:** `platform_role=loging_admin`.\n\n"
        "**Возможные ошибки:** 422 — `retain_days` вне диапазона."
    ),
)
def set_policy(
    payload: RetentionPolicyCreate,
    identity: AdminIdentity,
    db: Session = Depends(get_db),
) -> RetentionPolicyResponse:
    existing = repo.get_active(db)
    old_snapshot = _snapshot(existing)

    # PUT replaces: гасим весь прежний активный набор (одна global-строка
    # либо предыдущий Cartesian) и пишем новый. Иначе сброс фильтров оставил
    # бы старые узкие предикаты активными рядом с новой политикой.
    repo.deactivate_all_active(db)
    created = repo.create_policy(db, payload)
    _audit(db, identity, {"old": old_snapshot, "new": _snapshot(created[0])})
    return RetentionPolicyResponse.model_validate(created[0])


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Отключить retention (хранить события вечно)",
    description=(
        "Idempotent: помечает `is_active=false` у ВСЕХ активных политик. "
        "При filtered-режиме активным может быть набор строк (severity×service) "
        "— гасятся все сразу, чтобы фоновая ротация полностью остановилась. "
        "Если активных нет — ничего не делает, 204 всё равно.\n\n"
        "**Доступ:** `platform_role=loging_admin`."
    ),
)
def disable_policy(
    identity: AdminIdentity,
    db: Session = Depends(get_db),
) -> None:
    old_snapshot = _snapshot(repo.get_active(db))
    deactivated = repo.deactivate_all_active(db)
    # old_snapshot — представительская строка набора (самая свежая); при
    # пустой системе None. deactivated_count показывает SOC реальный размер
    # погашенного набора (1 для global, N×M для filtered).
    _audit(
        db,
        identity,
        {"old": old_snapshot, "new": None, "deactivated_count": deactivated},
    )


