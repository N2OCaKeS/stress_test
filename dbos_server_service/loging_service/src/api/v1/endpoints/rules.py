"""Управление правилами аудита. Требует `platform_role=loging_admin`.

Все действия create/update/delete пишутся в audit безусловно (минуя rule
engine, см. `record_admin_action`), чтобы админ не мог случайно — или
намеренно — заглушить собственный аудит.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.config import get_settings
from src.core.exceptions import (
    AppException,
    ConflictError,
    DomainValidationError,
    NotFoundError,
)
from src.core.limiter import limiter
from src.dependencies.auth import (
    RulesAdminIdentity,
    require_admin_or_account_admin,
)
from src.dependencies.db import get_db
from src.repositories import rules as rule_repo
from src.repositories import service_events as se_repo
from src.schemas.events import EventCreate
from src.schemas.rules import RuleCreate, RuleListResponse, RuleResponse, RuleUpdate
from src.services import event_service, rule_service

router = APIRouter()

# Симметрично `_MAX_OFFSET` в `endpoints/events.py`: верхняя граница для
# постраничного offset'а. На таблице `audit_rules` редко бывает много row'ов,
# но гард единообразный и страхует от patalogical OFFSET.
_MAX_OFFSET = 10_000_000


def _is_unique_violation(exc: IntegrityError) -> bool:
    """True, если `IntegrityError` пришёл из UNIQUE-constraint'а (pgcode 23505).

    psycopg3 хранит SQLSTATE в `sqlstate`; psycopg2/SA fallback — `pgcode`
    (либо как строка, либо как enum-обёртка с `.value`). Третий fallback —
    имя класса orig (`UniqueViolation`) на случай custom dbapi без pgcode.
    Без этой проверки любая IntegrityError (FK / NOT NULL / CHECK) врала бы
    SOC'у про дубликат имени, которого нет.
    """
    orig = getattr(exc, "orig", None)
    pgcode = (
        getattr(orig, "sqlstate", None)
        or getattr(getattr(orig, "pgcode", None), "value", None)
        or getattr(orig, "pgcode", None)
    )
    orig_cls = type(orig).__name__ if orig is not None else ""
    return pgcode == "23505" or orig_cls == "UniqueViolation"


def _get_or_404(db: Session, rule_id: str):
    rule = rule_repo.get_by_id(db, rule_id)
    if not rule:
        raise NotFoundError(
            error_code="RULE_NOT_FOUND",
            message=f"Rule '{rule_id}' not found",
            details={"rule_id": rule_id},
        )
    return rule


def _audit(db: Session, identity: dict, action: str, details: dict) -> None:
    """Пишет admin-действие безусловно (минуя SUPPRESS-правила).

    `actor_type` идёт из identity (`_fetch_identity` пробрасывает
    `subject_type` от auth_service introspect). Поля нет → fallback `"user"`.

    `username` и `department_id` пробрасываются для симметрии с
    `main.py::audit_access` — без них SIEM видит только opaque `actor_id`
    и не может атрибутировать `logging_rule.*` к человеку/отделу.

    `commit=False` — call-site CRUD + audit живёт в одной транзакции,
    единственный `db.commit()` делает endpoint после обеих DML.
    """
    actor_type = identity.get("actor_type") or "user"
    event_service.record_admin_action(
        db,
        EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="loging_service",
            action=action,
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


@router.get(
    "",
    response_model=RuleListResponse,
    summary="Список всех правил аудита",
    description=(
        "Постранично отдаёт правила, отсортированные по `priority DESC`.\n\n"
        "**Доступ:** только `loging_admin` или `account_admin`. `loging_reader` / "
        "`department_admin` / service-роли в `loging_service` сюда не пускаются — "
        "правила глобальны и leak их состояния (SUPPRESS/OVERRIDE-политики) "
        "раскрывает топологию мониторинга.\n\n"
        "**Связано:** `POST /rules` — создать правило; `GET /services/{svc}/events` — "
        "список action'ов, доступных для `match_action`."
    ),
)
# Read-канал rules чейнится в тот же per-IP bucket, что и GET /events
# (`audit_query_rate_limit`). Без лимита admin-JWT мог бы крутить
# постраничный листинг с большим offset'ом и держать пул коннектов.
@limiter.limit(
    lambda: get_settings().audit_query_rate_limit,
)
def list_rules(
    request: Request,
    response: Response,
    identity: RulesAdminIdentity,
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0, le=_MAX_OFFSET),
) -> RuleListResponse:
    rules, total = rule_repo.get_all(db, limit=limit, offset=offset)
    return RuleListResponse(
        items=[RuleResponse.model_validate(r) for r in rules],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{rule_id}",
    response_model=RuleResponse,
    summary="Получить правило по ID",
    description=(
        "**Доступ:** только `loging_admin` или `account_admin`.\n\n"
        "**Возможные ошибки:** 404 — правила с таким ID нет."
    ),
)
@limiter.limit(
    lambda: get_settings().audit_query_rate_limit,
)
def get_rule(
    rule_id: str,
    request: Request,
    response: Response,
    identity: RulesAdminIdentity,
    db: Session = Depends(get_db),
) -> RuleResponse:
    return RuleResponse.model_validate(_get_or_404(db, rule_id))


@router.post(
    "",
    response_model=RuleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать правило аудита",
    description=(
        "Создаёт правило фильтрации. Эффекты: `SUPPRESS` (дроп события), "
        "`ALLOW` (записать и не применять правила ниже), `OVERRIDE_SEVERITY` "
        "(поменять severity, продолжить цепочку).\n\n"
        "`match_action` принимает точное имя action или glob с одной звёздочкой "
        "на сегмент: `user.*` совпадает с `user.login`, но не с "
        "`user.login.extra`. Точное `match_action` валидируется против реестра "
        "`service_events` — нельзя завести правило на action, которого никто не "
        "регистрировал (но только если реестр непустой).\n\n"
        "**Доступ:** `platform_role` ∈ {`loging_admin`, `account_admin`}.\n\n"
        "**Возможные ошибки:**\n"
        "- 409 — правило с таким `name` уже существует (UNIQUE constraint);\n"
        "- 422 — невалидный effect_severity (см. правило про OVERRIDE_SEVERITY) "
        "либо неизвестный `match_action`.\n\n"
        "**Связано:** изменение правил сбрасывает in-memory кеш "
        "(`rule_service.invalidate_cache`)."
    ),
    dependencies=[Depends(require_admin_or_account_admin)],
)
def create_rule(
    payload: RuleCreate,
    identity: RulesAdminIdentity,
    db: Session = Depends(get_db),
) -> RuleResponse:
    _validate_match_action(payload.match_action, db)
    try:
        rule = rule_repo.create(db, payload, commit=False)
        # Audit + main op в одной транзакции: без этого audit-row коммитится
        # отдельно — окно между двумя commit'ами теряет audit при OOM/network
        # drop, нарушает compliance «все admin-действия аудитируются».
        _audit(db, identity, "logging_rule.create", {"rule_id": rule.id, "rule_name": rule.name})
        db.commit()
        db.refresh(rule)
    except IntegrityError as exc:
        db.rollback()
        # Единственный UNIQUE на audit_rules — `name`, значит pgcode 23505
        # здесь это реальный конфликт имён → 409. Любая другая IntegrityError
        # (FK / NOT NULL / CHECK) шла бы как фейковый NAME_CONFLICT и врала
        # SOC'у про дубликат, которого нет.
        if _is_unique_violation(exc):
            raise ConflictError(
                error_code="RULE_NAME_CONFLICT",
                message=f"Rule with name '{payload.name}' already exists",
                details={"name": payload.name},
            )
        raise AppException(
            http_status=500,
            error_code="INTERNAL_ERROR",
            message="Database constraint violation during rule create",
            details={"name": payload.name},
        )
    rule_service.invalidate_cache()
    return RuleResponse.model_validate(rule)


@router.patch(
    "/{rule_id}",
    response_model=RuleResponse,
    summary="Обновить правило",
    description=(
        "Partial-update: меняет только переданные поля. После обновления "
        "сбрасывает in-memory кеш rule engine.\n\n"
        "**Доступ:** `platform_role` ∈ {`loging_admin`, `account_admin`}.\n\n"
        "**Возможные ошибки:**\n"
        "- 404 — правила с таким ID нет;\n"
        "- 409 — переименование конфликтует с существующим именем;\n"
        "- 422 — нарушение invariant'а effect ↔ effect_severity, либо "
        "неизвестный `match_action`."
    ),
    dependencies=[Depends(require_admin_or_account_admin)],
)
def update_rule(
    rule_id: str,
    payload: RuleUpdate,
    identity: RulesAdminIdentity,
    db: Session = Depends(get_db),
) -> RuleResponse:
    _validate_match_action(payload.match_action, db)
    rule = _get_or_404(db, rule_id)
    new_effect = payload.effect if payload.effect is not None else rule.effect
    new_effect_severity = (
        payload.effect_severity
        if "effect_severity" in payload.model_fields_set
        else rule.effect_severity
    )
    if new_effect == "OVERRIDE_SEVERITY" and not new_effect_severity:
        raise DomainValidationError(
            error_code="EFFECT_SEVERITY_REQUIRED",
            message="effect_severity is required when effect=OVERRIDE_SEVERITY",
        )
    if new_effect != "OVERRIDE_SEVERITY" and new_effect_severity:
        raise DomainValidationError(
            error_code="EFFECT_SEVERITY_NOT_ALLOWED",
            message="effect_severity is only valid with effect=OVERRIDE_SEVERITY",
        )
    try:
        updated = rule_repo.update(db, rule, payload, commit=False)
        _audit(db, identity, "logging_rule.update", {"rule_id": rule_id, "changes": payload.model_dump(exclude_unset=True)})
        db.commit()
        db.refresh(updated)
    except IntegrityError as exc:
        db.rollback()
        # PATCH ловит IntegrityError по двум осям: имя (UNIQUE на name) и
        # прочие constraint'ы (NOT NULL / FK / CHECK на других полях). Слепой
        # 409 RULE_NAME_CONFLICT для всех случаев врёт SOC'у про дубликат,
        # которого может не быть. Симметрично с `create_rule`: только pgcode
        # 23505 мапится в 409 — payload.name тут может быть None (rename не
        # затронут в PATCH), это вычитывается из БД через `rule.name`.
        if _is_unique_violation(exc):
            conflict_name = payload.name if payload.name is not None else rule.name
            raise ConflictError(
                error_code="RULE_NAME_CONFLICT",
                message=f"Rule with name '{conflict_name}' already exists",
                details={"name": conflict_name},
            )
        raise AppException(
            http_status=500,
            error_code="INTERNAL_ERROR",
            message="Database constraint violation during rule update",
            details={"rule_id": rule_id},
        )
    rule_service.invalidate_cache()
    return RuleResponse.model_validate(updated)


@router.delete(
    "/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить правило",
    description=(
        "Полностью удаляет правило и сбрасывает кеш rule engine.\n\n"
        "**Доступ:** `platform_role` ∈ {`loging_admin`, `account_admin`}.\n\n"
        "**Возможные ошибки:** 404 — правила с таким ID нет."
    ),
    dependencies=[Depends(require_admin_or_account_admin)],
)
def delete_rule(
    rule_id: str,
    identity: RulesAdminIdentity,
    db: Session = Depends(get_db),
) -> None:
    rule = _get_or_404(db, rule_id)
    # Сначала сохраняем имя для audit-details (репозиторий переименует row
    # на soft-delete, чтобы освободить UNIQUE на name).
    original_name = rule.name
    # Порядок: delete → audit. Если delete упадёт — audit не пишется,
    # SIEM не врёт «удалено» о неудалённом правиле.
    rule_repo.delete(db, rule, commit=False)
    _audit(db, identity, "logging_rule.delete", {"rule_id": rule_id, "rule_name": original_name})
    db.commit()
    rule_service.invalidate_cache()


def _validate_match_action(match_action: str | None, db: Session) -> None:
    if match_action is None or "*" in match_action:
        return
    if not se_repo.action_is_registered(db, match_action):
        if se_repo.has_any_registered(db):
            raise DomainValidationError(
                error_code="UNKNOWN_MATCH_ACTION",
                message=(
                    f"Action '{match_action}' is not registered by any service. "
                    "Use GET /services/{service}/events to see available actions, "
                    "or use a glob pattern (e.g. 'user.*')."
                ),
                details={"match_action": match_action},
            )
