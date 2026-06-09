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

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from src.core.config import get_settings
from src.core.limiter import limiter, reader_rate_limit_key
from src.dependencies.auth import AdminIdentity, require_admin
from src.dependencies.db import get_db
from src.repositories import retention_policies as repo
from src.schemas.common import ErrorEnvelope
from src.schemas.events import EventCreate
from src.schemas.retention import (
    RetentionPolicyCreate,
    RetentionPolicyResponse,
)
from src.services import event_service

router = APIRouter(dependencies=[Depends(require_admin)])

# Общий каталог ошибок для retention endpoint'ов. Все три эндпоинта закрыты
# router-level `require_admin` — auth-ветка одинаковая. Различия — write-only
# 422 (`PUT` валидирует `retain_days`) и `RATE_LIMIT_EXCEEDED` (только на GET).
_AUTH_RESPONSES = {
    401: {"model": ErrorEnvelope, "description": "Нет/неверный токен"},
    403: {"model": ErrorEnvelope, "description": "`INSUFFICIENT_ROLE` — нужен loging_admin"},
    503: {"model": ErrorEnvelope, "description": "auth_service недоступен (introspect)"},
}


def _audit(db: Session, identity: dict, details: dict) -> None:
    """Self-audit для retention CRUD — симметрично `rules.py::_audit`.

    Зеркалит логику из rules.py: actor_type c fallback на "user", все
    identity-поля проброшены для атрибуции в SIEM (без department_id и
    username SOC видел бы только opaque actor_id).

    `commit=False` — retention-CRUD и audit живут в одной транзакции
    endpoint'а, единственный `db.commit()` делает caller.
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
        commit=False,
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


def _snapshot_list(policies) -> list[dict]:
    """Компактный снимок набора политик для audit-details.

    Filtered-PUT/DELETE затрагивает Cartesian product (severity × service)
    политик: одна PUT с 6 severity × 64 сервиса — это 384 row'и. Полная
    сериализация раздувала бы `details` за 64 KB лимит
    `EventCreate._details_size` и легитимный admin-PUT падал бы с 422 в
    self-audit.

    Группируем по `(retain_days, severity, description, is_active)`,
    схлопывая только сервис-измерение: для каждой группы выводим один
    summary с агрегированным списком `services` (отсортированный, NULL → "*")
    и `count`. Severity оставляем разным row'ам — для SOC важно видеть, что,
    скажем, ERROR-события стали хранить иначе, чем INFO.

    SOC видит изменение целиком: какие пары (severity, retain_days)
    действовали для каких сервисов до и после PUT — без 384 одинаковых
    row'ей в JSON-простыне.
    """
    if not policies:
        return []

    groups: dict[tuple, dict] = {}
    for p in policies:
        if p is None:
            continue
        key = (p.retain_days, p.severity, p.description, p.is_active)
        bucket = groups.setdefault(
            key,
            {
                "retain_days": p.retain_days,
                "severity": p.severity,
                "description": p.description,
                "is_active": p.is_active,
                "services": set(),
                "count": 0,
                "sample_id": p.id,
            },
        )
        bucket["services"].add(p.service if p.service is not None else "*")
        bucket["count"] += 1

    summaries: list[dict] = []
    for bucket in groups.values():
        bucket["services"] = sorted(bucket["services"])
        summaries.append(bucket)
    # Детерминированный порядок: retain_days, потом severity (None → пустая
    # строка, чтобы сортировка не падала на смешанных типах).
    summaries.sort(key=lambda b: (b["retain_days"], b["severity"] or ""))
    return summaries


@router.get(
    "",
    response_model=RetentionPolicyResponse | None,
    summary="Получить текущую retention-политику",
    description=(
        "Возвращает активную политику или `null`, если ни одной не настроено.\n\n"
        "**Доступ:** `platform_role=loging_admin`.\n\n"
        "Лимит запросов: `AUDIT_QUERY_RATE_LIMIT` (per-IP, см. README).\n\n"
        "**Связано:** `PUT /retention` — задать/заменить политику; "
        "`DELETE /retention` — отключить ротацию (хранить вечно)."
    ),
    responses={
        **_AUTH_RESPONSES,
        429: {"model": ErrorEnvelope, "description": "Превышен per-IP rate-limit"},
    },
)
# Симметрично остальным read-эндпоинтам (`GET /events`, `GET /rules`,
# `GET /services`) ставим `audit_query_rate_limit`. `require_admin`
# сужает поверхность до loging_admin, но без лимита единообразие read-
# канала ломается — пусть лучше единый bucket за per-IP, чем
# admin-JWT с правом крутить SELECT на active retention без потолка.
@limiter.limit(
    lambda: get_settings().audit_query_rate_limit,
    key_func=reader_rate_limit_key,
)
def get_policy(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> RetentionPolicyResponse | None:
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
        "Rate-limit: **не применяется** — admin write. PUT всегда возвращает "
        "200 даже при первой настройке политики (новая row создаётся, прежний "
        "активный набор гасится в той же транзакции)."
    ),
    responses={
        **_AUTH_RESPONSES,
        422: {"model": ErrorEnvelope, "description": "`VALIDATION_ERROR` — `retain_days` вне диапазона"},
    },
)
def set_policy(
    payload: RetentionPolicyCreate,
    identity: AdminIdentity,
    db: Session = Depends(get_db),
) -> RetentionPolicyResponse:
    # Полный снимок прежнего активного набора (filtered-PUT мог дать
    # Cartesian N×M строк). get_active даёт только одну, audit-details
    # показал бы частичную картину.
    old_snapshot = _snapshot_list(repo.list_active(db))

    # PUT replaces: гасим весь прежний активный набор (одна global-строка
    # либо предыдущий Cartesian) и пишем новый. Иначе сброс фильтров оставил
    # бы старые узкие предикаты активными рядом с новой политикой.
    # Всё в одной транзакции: deactivate + create + audit, единственный
    # commit в конце. Иначе окно между раздельными commit'ами теряет audit
    # при OOM/connection drop — нарушение compliance.
    repo.deactivate_all_active(db, commit=False)
    created = repo.create_policy(db, payload, commit=False)
    new_snapshot = _snapshot_list(created)
    _audit(db, identity, {"old": old_snapshot, "new": new_snapshot})
    db.commit()
    for p in created:
        db.refresh(p)
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
        "**Доступ:** `platform_role=loging_admin`.\n\n"
        "Rate-limit: **не применяется** — admin write."
    ),
    responses=_AUTH_RESPONSES,
)
def disable_policy(
    identity: AdminIdentity,
    db: Session = Depends(get_db),
) -> None:
    # Полный снимок всего набора (filtered-режим даёт N×M строк);
    # представительская get_active() показывала бы только одну.
    old_snapshot = _snapshot_list(repo.list_active(db))
    deactivated = repo.deactivate_all_active(db, commit=False)
    _audit(
        db,
        identity,
        {"old": old_snapshot, "new": None, "deactivated_count": deactivated},
    )
    db.commit()


