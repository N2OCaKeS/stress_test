"""Управление правилами аудита. Требует `platform_role=loging_admin`.

Все действия create/update/delete пишутся в audit безусловно (минуя rule
engine, см. `record_admin_action`), чтобы админ не мог случайно — или
намеренно — заглушить собственный аудит.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.config import get_settings
from src.core.exceptions import (
    AppException,
    ConflictError,
    DomainValidationError,
    NotFoundError,
)
from src.core.limiter import limiter, reader_rate_limit_key
from src.core.limits import MAX_QUERY_LIMIT, MAX_QUERY_OFFSET
from src.dependencies.auth import AdminIdentity
from src.dependencies.db import get_db
from src.repositories import rules as rule_repo
from src.repositories import service_events as se_repo
from src.schemas.common import ErrorEnvelope
from src.schemas.events import EventCreate
from src.schemas.rules import RuleCreate, RuleListResponse, RuleResponse, RuleUpdate
from src.services import event_service, rule_service

router = APIRouter()

# Charset path-параметра `rule_id` — opaque `rl_<hex>` идентификаторы
# (см. `utils/ids.py`). Без bound'а pydantic пропускает unbounded string,
# она уходит WHERE id=$1 — Postgres переварит, но 422 был бы дешевле и не
# раздувал бы logs/metrics. Charset симметричен `EventCreate._id_charset`.
_RULE_ID_PATTERN = r"^[A-Za-z0-9_\-]{1,48}$"


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
        "**Доступ:** только `loging_admin`. `loging_reader` / `account_admin` / "
        "`department_admin` и service-роли в `loging_service` сюда не пускаются — "
        "правила глобальны и leak их состояния (SUPPRESS/OVERRIDE-политики) "
        "раскрывает топологию мониторинга.\n\n"
        "Лимит запросов: `AUDIT_QUERY_RATE_LIMIT` (per-user, fallback на IP; см. README).\n\n"
        "**Связано:** `POST /rules` — создать правило; `GET /services/{svc}/events` — "
        "список action'ов, доступных для `match_action`."
    ),
    responses={
        401: {"model": ErrorEnvelope, "description": "Нет/неверный токен"},
        403: {"model": ErrorEnvelope, "description": "Роль не подходит (`INSUFFICIENT_ROLE`)"},
        429: {"model": ErrorEnvelope, "description": "Превышен per-user rate-limit (fallback на IP)"},
        503: {"model": ErrorEnvelope, "description": "auth_service недоступен (introspect)"},
    },
)
# Read-канал rules чейнится в тот же per-user bucket, что и GET /events
# (`audit_query_rate_limit`, ключ — `sub` из introspect, fallback на IP).
# Без лимита admin-JWT мог бы крутить
# постраничный листинг с большим offset'ом и держать пул коннектов.
@limiter.limit(
    lambda: get_settings().audit_query_rate_limit,
    key_func=reader_rate_limit_key,
)
def list_rules(
    request: Request,
    response: Response,
    identity: AdminIdentity,
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=MAX_QUERY_LIMIT),
    offset: int = Query(default=0, ge=0, le=MAX_QUERY_OFFSET),
    is_default: bool | None = Query(
        default=None,
        description=(
            "Фильтр по типу правила: `true` — только авто-сидируемые дефолты "
            "`(action,status)→severity`, `false` — только managed-правила, "
            "не задан — оба. Дефолты помечены `is_default=true` в ответе."
        ),
    ),
) -> RuleListResponse:
    rules, total = rule_repo.get_all(
        db, limit=limit, offset=offset, is_default=is_default
    )
    return RuleListResponse(
        items=[RuleResponse.model_validate(r) for r in rules],
        total=total,
        has_more=offset + len(rules) < total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{rule_id}",
    response_model=RuleResponse,
    summary="Получить правило по ID",
    description=(
        "**Доступ:** только `loging_admin`.\n\n"
        "Лимит запросов: `AUDIT_QUERY_RATE_LIMIT` (per-user, fallback на IP; см. README)."
    ),
    responses={
        401: {"model": ErrorEnvelope, "description": "Нет/неверный токен"},
        403: {"model": ErrorEnvelope, "description": "Роль не подходит (`INSUFFICIENT_ROLE`)"},
        404: {"model": ErrorEnvelope, "description": "`RULE_NOT_FOUND` — правила с таким ID нет"},
        429: {"model": ErrorEnvelope, "description": "Превышен per-user rate-limit (fallback на IP)"},
        503: {"model": ErrorEnvelope, "description": "auth_service недоступен (introspect)"},
    },
)
@limiter.limit(
    lambda: get_settings().audit_query_rate_limit,
    key_func=reader_rate_limit_key,
)
def get_rule(
    request: Request,
    response: Response,
    identity: AdminIdentity,
    rule_id: str = Path(
        min_length=1,
        max_length=48,
        pattern=_RULE_ID_PATTERN,
        description="ID правила (opaque `rl_<hex>`-токен)",
    ),
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
        "**Доступ:** `platform_role=loging_admin`.\n\n"
        "Лимит запросов: `RULE_WRITE_RATE_LIMIT` (per-user, fallback на IP) — "
        "защита от flood'а скомпрометированным admin-токеном.\n\n"
        "**Связано:** изменение правил сбрасывает in-memory кеш "
        "(`rule_service.invalidate_cache`)."
    ),
    responses={
        401: {"model": ErrorEnvelope, "description": "Нет/неверный токен"},
        403: {"model": ErrorEnvelope, "description": "Роль не подходит (`INSUFFICIENT_ROLE`)"},
        409: {"model": ErrorEnvelope, "description": "`RULE_NAME_CONFLICT` — UNIQUE на name"},
        422: {
            "model": ErrorEnvelope,
            "description": (
                "`VALIDATION_ERROR`, `EFFECT_SEVERITY_REQUIRED`, "
                "`EFFECT_SEVERITY_NOT_ALLOWED`, `UNKNOWN_MATCH_ACTION`"
            ),
        },
        429: {"model": ErrorEnvelope, "description": "Превышен per-user write rate-limit (fallback на IP)"},
        500: {"model": ErrorEnvelope, "description": "`INTERNAL_ERROR` — IntegrityError не из UNIQUE"},
        503: {"model": ErrorEnvelope, "description": "auth_service недоступен (introspect)"},
    },
)
@limiter.limit(
    lambda: get_settings().rule_write_rate_limit,
    key_func=reader_rate_limit_key,
)
def create_rule(
    request: Request,
    response: Response,
    payload: RuleCreate,
    identity: AdminIdentity,
    db: Session = Depends(get_db),
) -> RuleResponse:
    _validate_match_action(payload.match_action, db)
    try:
        rule = rule_repo.create(db, payload, commit=False)
        # Audit + main op в одной транзакции: без этого audit-row коммитится
        # отдельно — окно между двумя commit'ами теряет audit при OOM/network
        # drop, нарушает compliance «все admin-действия аудитируются».
        _audit(db, identity, "logging_rule.create", {"rule_id": rule.id, "rule_name": rule.name})
        # Invalidate ДО commit'а: между `commit()` и `invalidate_cache()` в
        # обратном порядке concurrent ingest на том же воркере держит stale
        # snapshot (TTL не истёк) и пропускает только что созданное SUPPRESS-
        # правило. Обратный порядок безопаснее: если invalidate сработал, а
        # commit упал, следующий cache-miss перечитает БД без новой row'и —
        # никакая запись не теряется, потеря лишь «свежести» кеша,
        # восстанавливается следующим обращением.
        rule_service.invalidate_cache()
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
    return RuleResponse.model_validate(rule)


@router.patch(
    "/{rule_id}",
    response_model=RuleResponse,
    summary="Обновить правило",
    description=(
        "Partial-update: меняет только переданные поля. После обновления "
        "сбрасывает in-memory кеш rule engine.\n\n"
        "**Доступ:** `platform_role=loging_admin`.\n\n"
        "Лимит запросов: `RULE_WRITE_RATE_LIMIT` (per-user, fallback на IP)."
    ),
    responses={
        401: {"model": ErrorEnvelope, "description": "Нет/неверный токен"},
        403: {"model": ErrorEnvelope, "description": "Роль не подходит (`INSUFFICIENT_ROLE`)"},
        404: {"model": ErrorEnvelope, "description": "`RULE_NOT_FOUND`"},
        409: {"model": ErrorEnvelope, "description": "`RULE_NAME_CONFLICT`"},
        422: {
            "model": ErrorEnvelope,
            "description": (
                "`VALIDATION_ERROR`, `EFFECT_SEVERITY_REQUIRED`, "
                "`EFFECT_SEVERITY_NOT_ALLOWED`, `UNKNOWN_MATCH_ACTION`"
            ),
        },
        429: {"model": ErrorEnvelope, "description": "Превышен per-user write rate-limit (fallback на IP)"},
        500: {"model": ErrorEnvelope, "description": "`INTERNAL_ERROR`"},
        503: {"model": ErrorEnvelope, "description": "auth_service недоступен (introspect)"},
    },
)
@limiter.limit(
    lambda: get_settings().rule_write_rate_limit,
    key_func=reader_rate_limit_key,
)
def update_rule(
    request: Request,
    response: Response,
    payload: RuleUpdate,
    identity: AdminIdentity,
    rule_id: str = Path(
        min_length=1,
        max_length=48,
        pattern=_RULE_ID_PATTERN,
        description="ID правила (opaque `rl_<hex>`-токен)",
    ),
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
        # Invalidate ДО commit'а — см. комментарий в `create_rule`.
        rule_service.invalidate_cache()
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
    return RuleResponse.model_validate(updated)


@router.delete(
    "/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить правило",
    description=(
        "Полностью удаляет правило и сбрасывает кеш rule engine.\n\n"
        "**Доступ:** `platform_role=loging_admin`.\n\n"
        "Лимит запросов: `RULE_WRITE_RATE_LIMIT` (per-user, fallback на IP)."
    ),
    responses={
        401: {"model": ErrorEnvelope, "description": "Нет/неверный токен"},
        403: {"model": ErrorEnvelope, "description": "Роль не подходит (`INSUFFICIENT_ROLE`)"},
        404: {"model": ErrorEnvelope, "description": "`RULE_NOT_FOUND`"},
        429: {"model": ErrorEnvelope, "description": "Превышен per-user write rate-limit (fallback на IP)"},
        503: {"model": ErrorEnvelope, "description": "auth_service недоступен (introspect)"},
    },
)
@limiter.limit(
    lambda: get_settings().rule_write_rate_limit,
    key_func=reader_rate_limit_key,
)
def delete_rule(
    request: Request,
    response: Response,
    identity: AdminIdentity,
    rule_id: str = Path(
        min_length=1,
        max_length=48,
        pattern=_RULE_ID_PATTERN,
        description="ID правила (opaque `rl_<hex>`-токен)",
    ),
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
    # Invalidate ДО commit'а — см. комментарий в `create_rule`.
    rule_service.invalidate_cache()
    db.commit()


def _validate_match_action(match_action: str | None, db: Session) -> None:
    if match_action is None:
        return
    # Globs `logging.*` и `audit.*` админу создавать незачем: self-audit
    # loging_service всегда обходит rule engine (см. apply_rules), так что
    # SUPPRESS/OVERRIDE на эти префиксы — мёртвая конфигурация и источник
    # путаницы при последующем разборе журнала. Отбиваем заранее.
    if _is_self_audit_match(match_action):
        raise DomainValidationError(
            error_code="UNKNOWN_MATCH_ACTION",
            message=(
                f"Action '{match_action}' targets loging_service self-audit "
                "which always bypasses the rule engine. Use a different "
                "prefix (e.g. 'user.*', 'server.*')."
            ),
            details={"match_action": match_action},
        )
    if "*" in match_action:
        return
    if not se_repo.action_is_registered(db, match_action):
        if se_repo.has_any(db):
            raise DomainValidationError(
                error_code="UNKNOWN_MATCH_ACTION",
                message=(
                    f"Action '{match_action}' is not registered by any service. "
                    "Use GET /services/{service}/events to see available actions, "
                    "or use a glob pattern (e.g. 'user.*')."
                ),
                details={"match_action": match_action},
            )


_SELF_AUDIT_PREFIXES: tuple[str, ...] = (
    "logging.",
    "logging_rule.",
    "audit.",
)


def _is_self_audit_match(match_action: str) -> bool:
    """True для action или glob, нацеленных на self-audit loging_service.

    Self-audit пишется с префиксами `logging.` / `logging_rule.` / `audit.`
    (см. event_service, retention.py, services.py, main._retention_loop,
    rules.py admin-audit). Любой match_action с этими префиксами заведомо
    бесполезен — rule engine выходит раньше, чем доходит до сравнения
    (apply_rules: `if payload.service == "loging_service": return payload`).
    Отбиваем такие правила при создании/обновлении, иначе админ может
    создать мёртвую конфигурацию и потом потратить время на её отладку.
    """
    lowered = match_action.lower()
    return lowered.startswith(_SELF_AUDIT_PREFIXES)
