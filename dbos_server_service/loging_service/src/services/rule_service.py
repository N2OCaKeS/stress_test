"""Динамические правила аудита — загрузка, кеширование, применение.

Правила загружаются из БД и кешируются в памяти (TTL 30 сек).
При изменении правил через API кеш сбрасывается немедленно.

Порядок обработки события:
  1. Если severity не задан — назначается из дефолтного правила БД
     (`is_default`, source of truth — «Матрица событий EMM», статус-агностично)
  2. Правила перебираются по убыванию priority (`priority DESC`)
  3. SUPPRESS          — отбрасывает событие (не сохраняется), цепочка обрывается
  4. ALLOW             — сохраняет немедленно, цепочка обрывается
  5. OVERRIDE_SEVERITY — меняет severity, цепочка продолжается; следующий
                         OVERRIDE-матч переписывает severity заново
                         (контракт «последний выигрывает», см. `apply_rules`)
  6. Если ни одно правило не сматчилось — событие сохраняется (default allow)
"""

import enum
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy.orm import Session

from src.models.audit_rule import AuditRule
from src.repositories import rules as rule_repo
from src.schemas.events import EventCreate

# Канонические значения колонки `audit_rules.effect` ПОСЛЕ нормализации в
# `RuleCreate`/`RuleUpdate` (`DROP` → `SUPPRESS` ещё на pydantic-уровне).
# Держим тип здесь, а не в schemas/rules — snapshot живёт в кеше после
# ORM-чтения и в БД лежит уже канонический набор; type-hint помогает
# mypy/тестам, но runtime-проверки не делает — invariant сохраняется
# pydantic-валидатором на ingest и check-constraint'ом в миграции.
_EffectCanonical = Literal["SUPPRESS", "ALLOW", "OVERRIDE_SEVERITY"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _RuleSnapshot:
    """Иммутабельный снимок `AuditRule` для in-memory кеша.

    Заведён, чтобы кеш не таскал detached ORM-объекты: при истечении сессии
    обращение к ленивым полям ORM кидает `DetachedInstanceError`. Раньше
    решалось через `db.expunge(rule)` — работало, пока snapshot читал только
    eager-loaded колонки, но любое добавление relationship в `AuditRule`
    тихо ломало бы кеш в проде. Dataclass отвязан от Session by-design.
    Атрибут-имена дублируют ORM, чтобы `_matches` ходил по тем же полям.
    """

    id: str
    name: str
    match_service: str | None
    match_action: str | None
    match_status: str | None
    match_severity: str | None
    match_allowed: bool | None
    # `effect` хранится в БД как varchar, тип сужен до канонического Literal
    # после нормализации в RuleCreate/RuleUpdate (`DROP` → `SUPPRESS`).
    # Type-hint только для статической проверки — runtime-валидация
    # делается на ingest pydantic-моделями и check-constraint'ом.
    effect: _EffectCanonical
    effect_severity: str | None
    # `is_default=True` — авто-сидируемое дефолтное правило по матрице EMM
    # (`action → severity`, статус-агностично; ИГНОР → SUPPRESS). Дефолты
    # задают БАЗОВЫЙ severity; managed-правила (is_default=False)
    # накладываются поверх и перекрывают. Если ни дефолт, ни managed-правило
    # не назначили severity — событие дропается (см. `apply_rules`).
    is_default: bool = False


_CANONICAL_EFFECTS = frozenset({"SUPPRESS", "ALLOW", "OVERRIDE_SEVERITY"})


def _snapshot_rule(rule: AuditRule) -> _RuleSnapshot:
    # `effect` нормализуется на ingest (`RuleCreate._validate_effect`)
    # и защищён check-constraint'ом миграции. Если в БД всё-таки оказалось
    # чужое значение (ручной UPDATE / альтернативная миграция / тест) —
    # WARNING, чтобы поломку было видно в логах; cache при этом всё равно
    # положит row, чтобы не валить ingest целиком.
    if rule.effect not in _CANONICAL_EFFECTS:
        logger.warning(
            "_RuleCache: rule %s has non-canonical effect %r — "
            "expected one of %s",
            rule.id, rule.effect, sorted(_CANONICAL_EFFECTS),
        )
    return _RuleSnapshot(
        id=rule.id,
        name=rule.name,
        match_service=rule.match_service,
        match_action=rule.match_action,
        match_status=rule.match_status,
        match_severity=rule.match_severity,
        match_allowed=rule.match_allowed,
        effect=rule.effect,
        effect_severity=rule.effect_severity,
        is_default=bool(getattr(rule, "is_default", False)),
    )

# Дефолтные severity — источник истины «Матрица событий EMM». Таблица
# СТАТУС-АГНОСТИЧНА: один severity на action, применяется ко всем статусам
# события (success/failure/denied/warning). ЭТО НЕ рантайм-источник:
# `seed_default_rules` материализует эти данные в `audit_rules`
# (`is_default=true`), после чего движок берёт severity ТОЛЬКО из БД. Таблица
# живёт как данные сидера (миграция + startup reconcile) и как контракт тестов.
# Удалённый админом дефолт не воскресает — soft-delete оставляет row в таблице,
# reconcile её по имени не пересоздаёт.
_DEFAULT_SEVERITY: dict[str, str] = {
    "audit.idempotency_conflict": "WARNING",
    "audit.outbox_reattempt_manual": "WARNING",
    "bmc.endpoint_blocked": "CRITICAL",
    "bmc.tls_downgrade": "CRITICAL",
    "bmc.tls_verify_disabled": "CRITICAL",
    "bot.create": "INFO",
    "bot.delete": "WARNING",
    "bot.list": "INFO",
    "bot.roles_assign": "WARNING",
    "bot.roles_list": "INFO",
    "bot.roles_purged_on_services_narrowed": "WARNING",
    "bot.roles_revoke": "WARNING",
    "bot.suspicious_multi_ip": "CRITICAL",
    "bot.token_create": "INFO",
    "bot.token_expired": "INFO",
    "bot.token_list": "INFO",
    "bot.token_revoke": "WARNING",
    "bot.update": "INFO",
    "console_macro.create": "INFO",
    "console_macro.delete": "INFO",
    "console_macro.update": "INFO",
    "department.create": "INFO",
    "department.hard_deleted": "CRITICAL",
    "department.list": "INFO",
    "department.service_grant": "WARNING",
    "department.service_revoke": "WARNING",
    "department.updated": "INFO",
    "docker.pull_denied": "WARNING",
    "docker.push_denied": "WARNING",
    "docker.token_issued": "INFO",
    "docker_registry.configure": "INFO",
    "docker_registry.disable": "WARNING",
    "docker_registry.get_config": "INFO",
    "docker_registry.update": "INFO",
    "encryption.admin_retire": "CRITICAL",
    "encryption.admin_rotate": "CRITICAL",
    "fanout_update_on_host.truncated": "WARNING",
    "group.bot_member_add": "INFO",
    "group.bot_member_remove": "INFO",
    "group.create": "INFO",
    "group.delete": "WARNING",
    "group.member_add": "INFO",
    "group.member_remove": "INFO",
    "group.roles_assign": "WARNING",
    "group.roles_revoke": "WARNING",
    "group.service_grant": "WARNING",
    "group.service_revoke": "WARNING",
    "group.update": "INFO",
    "http.access_denied": "WARNING",
    "http.client_error": "INFO",
    "http.platform_admin_blocked": "WARNING",
    "http.server_error": "ERROR",
    "installed_packages.list": "INFO",
    "internal.dept_header_missing": "WARNING",
    "inventory.drift_detected": "WARNING",
    "ipmi_controller.create": "INFO",
    "ipmi_controller.credentials_revealed": "CRITICAL",
    "ipmi_controller.credentials_revealed_throttled": "TRACE",
    "ipmi_controller.credentials_rotated_callback": "WARNING",
    "ipmi_controller.delete": "WARNING",
    "ipmi_controller.list": "WARNING",
    "ipmi_controller.password_rotate": "WARNING",
    "ipmi_controller.rotate_credentials": "INFO",
    "ipmi_controller.rotate_dispatch": "WARNING",
    "ipmi_controller.update": "INFO",
    "ipmi_controller.view": "WARNING",
    "ipmi_controller.view_credentials": "CRITICAL",
    "ipmi_controller.view_credentials_meta": "INFO",
    "lockout_policy.update": "WARNING",
    "logging.admin_access": "INFO",
    "logging.events_exported": "WARNING",
    "logging.retention_read": "INFO",
    "logging.retention_sweep": "DEBUG",
    "logging.retention_write": "CRITICAL",
    "logging.rules_read": "INFO",
    "logging.rules_write": "CRITICAL",
    "logging.service_events_browsed": "INFO",
    "logging.service_events_registered": "INFO",
    "logging.services_read": "INFO",
    "logging_rule.create": "CRITICAL",
    "logging_rule.delete": "CRITICAL",
    "logging_rule.update": "CRITICAL",
    "management_user.sync": "INFO",
    "management_user_config.sync": "INFO",
    "management_user_config.update": "WARNING",
    "management_user_sync_fanout.truncated": "WARNING",
    "mass_rotation.partial_failure": "ERROR",
    "me.updated": "INFO",
    "oauth.authorization_code_issued": "INFO",
    "oauth.client_credentials_token": "INFO",
    "oauth.code_exchanged": "INFO",
    "oauth.pkce_plain_used": "WARNING",
    "oauth.refresh_race": "DEBUG",
    "oauth.refresh_reuse": "CRITICAL",
    "oauth.refresh_token": "INFO",
    "oauth_client.create": "INFO",
    "oauth_client.delete": "WARNING",
    "oauth_client.list": "INFO",
    "ops.encryption_retire": "CRITICAL",
    "ops.encryption_rotate": "CRITICAL",
    "ops.migration_status_read": "INFO",
    "os.unknown_observed": "WARNING",
    "os_version.create": "INFO",
    "os_version.delete": "INFO",
    "os_version.update": "INFO",
    "pat.create": "INFO",
    "pat.list": "INFO",
    "pat.revoke": "WARNING",
    "permission.grant": "WARNING",
    "permission.revoke": "WARNING",
    "secret_lifecycle.notify_failed": "ERROR",
    "secrets.admin_encryption_retire": "CRITICAL",
    "secrets.admin_encryption_rotate": "CRITICAL",
    "secrets.encryption_retire": "CRITICAL",
    "secrets.encryption_rotate": "CRITICAL",
    "secrets.migration.skipped": "INFO",
    "secrets.migration_decrypt_failed": "ERROR",
    "secrets.migration_key_missing": "CRITICAL",
    "secrets.reencrypt_batch": "INFO",
    "secrets.reencrypt_done": "INFO",
    "secrets.reencrypt_failed": "ERROR",
    "secrets.reencrypt_outbox_cleanup": "DEBUG",
    "secrets.reencrypt_process": "INFO",
    "secrets.reencrypt_seed": "INFO",
    "secrets.reencrypt_tick": "DEBUG",
    "server.acquire": "INFO",
    "server.create": "INFO",
    "server.delete": "CRITICAL",
    "server.inventory_received": "INFO",
    "server.inventory_sync": "INFO",
    "server.packages_install": "WARNING",
    "server.packages_remove": "WARNING",
    "server.packages_update": "INFO",
    "server.power_off": "WARNING",
    "server.power_on": "WARNING",
    "server.power_reboot": "WARNING",
    "server.power_status": "INFO",
    "server.power_status_cached": "TRACE",
    "server.prepare": "INFO",
    "server.prepared": "INFO",
    "server.release": "INFO",
    "server.reservation_denied": "WARNING",
    "server.update": "INFO",
    "server.update_os_version": "INFO",
    "server.users_inventory_triggered": "INFO",
    "server.view": "INFO",
    "server.view_drift": "INFO",
    "server_account.adopted_from_host": "INFO",
    "server_account.bootstrap_resolved": "INFO",
    "server_account.create": "INFO",
    "server_account.delete": "WARNING",
    "server_account.deprovision": "WARNING",
    "server_account.drift_detected": "WARNING",
    "server_account.ignored_login_added": "INFO",
    "server_account.ignored_login_removed": "INFO",
    "server_account.ignored_logins_listed": "INFO",
    "server_account.imported_from_host": "INFO",
    "server_account.link_servers": "INFO",
    "server_account.list": "WARNING",
    "server_account.password_revealed": "CRITICAL",
    "server_account.password_revealed_throttled": "TRACE",
    "server_account.password_rotate": "WARNING",
    "server_account.provision": "INFO",
    "server_account.provision_status": "INFO",
    "server_account.recreate_login": "WARNING",
    "server_account.rotate_password": "WARNING",
    "server_account.rotate_password_dispatch": "WARNING",
    "server_account.ssh_key_rotate": "WARNING",
    "server_account.ssh_key_set": "WARNING",
    "server_account.ssh_private_key_revealed": "CRITICAL",
    "server_account.unlink_servers": "INFO",
    "server_account.update": "INFO",
    "server_account.update_on_host": "INFO",
    "server_account.users_inventory": "INFO",
    "server_account.users_inventory_received": "INFO",
    "server_account.view": "WARNING",
    "server_account.view_password": "CRITICAL",
    "service.create": "INFO",
    "service.delete": "WARNING",
    "service.list": "INFO",
    "service.started": "INFO",
    "service_key.generate": "CRITICAL",
    "service_role.bulk_assign": "WARNING",
    "service_role.bulk_revoke": "WARNING",
    "service_role.create": "INFO",
    "service_role.delete": "WARNING",
    "service_role.update": "INFO",
    "ssh_console.command": "WARNING",
    "ssh_console.session_close": "INFO",
    "ssh_console.session_open": "WARNING",
    "task.cancelled": "INFO",
    "task.deleted_midrun": "ERROR",
    "task.view": "INFO",
    "task.worker_orphaned": "ERROR",
    "task.worker_shutdown": "WARNING",
    "token.refresh_race": "DEBUG",
    "token.refresh_reuse": "CRITICAL",
    "tokens.access_denied": "WARNING",
    "tokens.admin_override_delete": "CRITICAL",
    "tokens.create": "INFO",
    "tokens.delete": "WARNING",
    "tokens.dept_grant_added": "WARNING",
    "tokens.dept_grant_revoked": "WARNING",
    "tokens.dept_recipient_cascade": "WARNING",
    "tokens.dept_revoke_cascade": "WARNING",
    "tokens.lockout_triggered": "CRITICAL",
    "tokens.owner_dept_deleted_block": "WARNING",
    "tokens.owner_user_deleted_block": "WARNING",
    "tokens.recover": "WARNING",
    "tokens.revealed": "WARNING",
    "tokens.revealed_blocked_by_validity": "WARNING",
    "tokens.revealed_throttled": "TRACE",
    "tokens.role_acl_added": "WARNING",
    "tokens.role_acl_revoked": "WARNING",
    "tokens.transfer_ownership": "WARNING",
    "tokens.update": "INFO",
    "tokens.user_acl_added": "WARNING",
    "tokens.user_acl_removed": "WARNING",
    "user.ban": "WARNING",
    "user.ban_deactivated_via_status_change": "WARNING",
    "user.create": "INFO",
    "user.force_password_change": "WARNING",
    "user.groups_purged_on_transfer": "WARNING",
    "user.hard_deleted": "CRITICAL",
    "user.list": "INFO",
    "user.locked_list": "INFO",
    "user.login": "WARNING",
    "user.logout": "INFO",
    "user.me": "TRACE",
    "user.must_change_password_cleared": "INFO",
    "user.password_change_required_blocked": "WARNING",
    "user.password_reset": "WARNING",
    "user.pat_revoked_on_block": "WARNING",
    "user.permissions_view": "INFO",
    "user.refresh": "INFO",
    "user.roles_assign": "WARNING",
    "user.roles_purged_on_transfer": "WARNING",
    "user.self_password_reset": "INFO",
    "user.session_admin_revoked_one": "WARNING",
    "user.session_revoked_one": "INFO",
    "user.sessions_admin_listed": "INFO",
    "user.sessions_admin_revoked_all": "WARNING",
    "user.sessions_listed": "INFO",
    "user.sessions_revoked_all": "WARNING",
    "user.sessions_revoked_on_block": "WARNING",
    "user.unban": "WARNING",
    "user.unlock": "WARNING",
    "user.update": "INFO",
    "worker_dispatch.orphan_detected": "ERROR",
}

# Действия с уровнем ИГНОР в матрице — событие НЕ логируется вовсе. Такие
# action'ы сидируются дефолтным SUPPRESS-правилом (см. `seed_default_rules` /
# `apply_rules`): подавление статус-агностично и срабатывает даже при явном
# severity.
_DEFAULT_SUPPRESS: frozenset[str] = frozenset({
    "service.access_check",
    "token.introspect",
})

# Остаток прежней (action, status)-таблицы: пары, чьих action НЕТ в матрице.
# Матрица статус-агностична; здесь остаются статус-зависимые дефолты для
# действий, которые матрица не покрывает. `logging.events_queried` —
# self-audit loging_service (эмитится вне rule-engine, но
# `_resolve_default_severity` всё равно должен назначить ему severity).
_LEGACY_SEVERITY: dict[tuple[str, str], str] = {
    ("logging.events_queried", "success"): "INFO",
    ("logging.events_queried", "warning"): "WARNING",
}


# Ключ в `seed_state`, под которым отмечается «дефолтные severity-правила
# засеяны». Один раз посеяв набор, повторный старт видит маркер и НЕ
# пересоздаёт дефолты — удалённые админом дефолты не воскресают.
DEFAULT_RULES_SEED_KEY = "default_severity_rules"

# Приоритет дефолтных правил. Ниже managed-default (RuleCreate.priority=100),
# поэтому managed-OVERRIDE всегда перекрывает дефолт.
_DEFAULT_RULE_PRIORITY = 0

# Префикс имени дефолтного правила: `default:<action>:<status>`. Имя
# deterministично от пары — повторный сид (если бы маркер потерялся) не
# плодил бы дубли: UNIQUE на name отбил бы вставку.
_DEFAULT_RULE_NAME_PREFIX = "default:"


def default_rule_name(action: str, status: str | None = None) -> str:
    """Детерминированное имя дефолтного правила.

    Матричные (статус-агностичные) правила именуются `default:<action>`,
    legacy-правила с конкретным статусом — `default:<action>:<status>`. Имя
    deterministично: повторный сид не плодит дубли (UNIQUE на `name` отбил бы
    вставку). Усекаем до 128 (`audit_rules.name` max_length): на реальном
    наборе имена уникальны и в потолок влезают.
    """
    if status is None:
        return f"{_DEFAULT_RULE_NAME_PREFIX}{action}"[:128]
    return f"{_DEFAULT_RULE_NAME_PREFIX}{action}:{status}"[:128]


@dataclass(frozen=True, slots=True)
class _DefaultRuleSpec:
    """Описание одного дефолтного правила для сида/reconcile/миграции."""

    name: str
    match_action: str
    match_status: str | None
    effect: _EffectCanonical
    effect_severity: str | None


def iter_default_rule_specs() -> list[_DefaultRuleSpec]:
    """Полный набор дефолтных правил: матрица + ИГНОР-suppress + legacy.

    Порядок стабилен (сортировка по action) — читаемый дифф и детерминированный
    сид. Матричные action'ы → `OVERRIDE_SEVERITY` с `match_status=None`
    (статус-агностично); ИГНОР → `SUPPRESS`; legacy-пары → `OVERRIDE_SEVERITY`
    со своим статусом. Единый источник для `seed_default_rules` и миграции.
    """
    specs: list[_DefaultRuleSpec] = []
    for action in sorted(_DEFAULT_SEVERITY):
        specs.append(
            _DefaultRuleSpec(
                name=default_rule_name(action),
                match_action=action,
                match_status=None,
                effect="OVERRIDE_SEVERITY",
                effect_severity=_DEFAULT_SEVERITY[action],
            )
        )
    for action in sorted(_DEFAULT_SUPPRESS):
        specs.append(
            _DefaultRuleSpec(
                name=default_rule_name(action),
                match_action=action,
                match_status=None,
                effect="SUPPRESS",
                effect_severity=None,
            )
        )
    for action, status in sorted(_LEGACY_SEVERITY):
        specs.append(
            _DefaultRuleSpec(
                name=default_rule_name(action, status),
                match_action=action,
                match_status=status,
                effect="OVERRIDE_SEVERITY",
                effect_severity=_LEGACY_SEVERITY[(action, status)],
            )
        )
    return specs


def seed_default_rules(db: Session) -> int:
    """Сеет и сверяет дефолтные severity-правила в `audit_rules` (reconcile).

    На чистой БД создаёт по одному `is_default=true` правилу на каждый spec из
    `iter_default_rule_specs` (матричные `OVERRIDE_SEVERITY` + `SUPPRESS` для
    ИГНОР + legacy). Все `is_active=true`, `priority=0`.

    Реконсиляция: если маркер `seed_state` уже стоит (обновление инсталляции),
    сид всё равно добирает недостающие правила и правит разошедшиеся — новый
    набор из матрицы доезжает, не блокируясь старым маркером. Идемпотентно:
    повторный запуск на актуальном наборе ничего не меняет и возвращает 0.

    Инвариант «удалённый админом дефолт не воскресает» сохранён: soft-delete
    оставляет tombstone в таблице (`deleted_at IS NOT NULL`) и ПЕРЕИМЕНОВЫВАЕТ
    его (`<name>#deleted-<ns>-<id>`, чтобы не держать UNIQUE-имя). Поэтому
    reconcile ищет tombstone не по имени, а по семантическому ключу
    `(match_action, match_status)`, который delete сохраняет: есть tombstone на
    этот ключ → дефолт НЕ пересоздаётся и НЕ реактивируется.

    Возвращает число ВНОВЬ созданных правил (0 — весь набор уже на месте).

    Вызывается на старте сервиса (`main.lifespan`) и из тестовых фикстур.
    Безопасен под гонку воркеров: UNIQUE на `name` отбивает дубли параллельной
    вставки, конкурентный коммит откатится на конфликте.
    """
    from src.models.audit_rule import AuditRule
    from src.models.seed_state import SeedState
    from src.utils.ids import audit_rule_id

    specs = iter_default_rule_specs()
    all_defaults = db.query(AuditRule).filter(AuditRule.is_default.is_(True)).all()
    # Активные дефолты — по имени (детерминированному). Tombstone'ы — по
    # семантическому ключу `(match_action, match_status)`: soft-delete манглит
    # имя, поэтому по имени их не найти, а action/status он сохраняет.
    active_by_name = {r.name: r for r in all_defaults if r.deleted_at is None}
    tombstoned_keys = {
        (r.match_action, r.match_status)
        for r in all_defaults
        if r.deleted_at is not None
    }
    now = datetime.now(timezone.utc)
    created = 0
    for spec in specs:
        row = active_by_name.get(spec.name)
        if row is None:
            if (spec.match_action, spec.match_status) in tombstoned_keys:
                # Админ снёс этот дефолт (tombstone) — не воскрешаем.
                continue
            db.add(
                AuditRule(
                    id=audit_rule_id(),
                    name=spec.name,
                    description="auto-seeded default severity rule",
                    is_active=True,
                    is_default=True,
                    priority=_DEFAULT_RULE_PRIORITY,
                    match_action=spec.match_action,
                    match_status=spec.match_status,
                    effect=spec.effect,
                    effect_severity=spec.effect_severity,
                    created_at=now,
                    updated_at=now,
                )
            )
            created += 1
            continue
        # Активный дефолт разошёлся с матрицей — подтягиваем к целевому виду.
        if (
            row.effect != spec.effect
            or row.effect_severity != spec.effect_severity
            or row.match_action != spec.match_action
            or row.match_status != spec.match_status
            or not row.is_active
        ):
            row.effect = spec.effect
            row.effect_severity = spec.effect_severity
            row.match_action = spec.match_action
            row.match_status = spec.match_status
            row.is_active = True
            row.updated_at = now
    if db.get(SeedState, DEFAULT_RULES_SEED_KEY) is None:
        db.add(SeedState(key=DEFAULT_RULES_SEED_KEY, seeded_at=now))
    db.commit()
    invalidate_cache()
    return created


def action_matches_pattern(action: str, pattern: str) -> bool:
    """Совпадает ли *action* с *pattern*.

    `*` совпадает с любой непустой последовательностью символов в ОДНОМ
    dot-сегменте. `user.*` matchит `user.login`, но НЕ `user.login.extra`.
    """
    if "*" not in pattern:
        return action == pattern
    regex = re.escape(pattern).replace(r"\*", r"[^.]+")
    return bool(re.fullmatch(regex, action))


def _resolve_default_severity(
    action: str,
    status: str,
    db: Session | None = None,
) -> str:
    """Возвращает severity по умолчанию.

    Порядок lookup'а:
      1. Матрица `_DEFAULT_SEVERITY[action]` — статус-агностично, один severity
         на действие (source of truth «Матрица событий EMM»).
      2. `_LEGACY_SEVERITY[(action, status)]` — остаток прежней статус-зависимой
         таблицы для действий, которых нет в матрице.
      3. Каталог `service_events.default_severity` для *action* — каждый сервис
         объявляет дефолт на регистрации (`register_events`). Lookup закрыт
         через `_CatalogSeverityCache` (TTL=30s), чтобы ingest не дёргал БД на
         каждое событие.
      4. Heuristic: `failure`/`denied`/`warning` → WARNING, остальное → INFO.

    *db* опционален: если caller (юнит-тест, миграция) не передал session,
    catalog lookup пропускается. Production call-sites (`apply_rules`,
    `record_admin_action`) пробрасывают сессию явно.

    Без `"warning"` в heuristic-ветке `status="warning"` (soft-mode guard'ы)
    свалился бы в INFO — теряется сигнал, что операция прошла, но что-то
    пахнет.
    """
    if action in _DEFAULT_SEVERITY:
        return _DEFAULT_SEVERITY[action]
    if (action, status) in _LEGACY_SEVERITY:
        return _LEGACY_SEVERITY[(action, status)]
    if db is not None:
        catalog_severity = _catalog_cache.get(db, action)
        if catalog_severity is not None:
            return catalog_severity
    return "WARNING" if status in ("failure", "denied", "warning") else "INFO"


class _CatalogSeverityCache:
    """TTL-кеш `service_events.default_severity` per-action.

    Hardcoded `_DEFAULT_SEVERITY` покрывает ~40% действий. Остальные сервисы
    объявляют severity через `register_events()` — это валяется в БД, но
    `_resolve_default_severity` раньше его не читал. Catalog lookup на каждом
    ingest'е добавил бы DB-RTT в hot-path; TTL-кеш амортизирует.

    Семантика:
      * первый lookup на action → SELECT default_severity, кладёт в map;
      * последующие в пределах TTL — без БД;
      * `None`-результат тоже кешируется (чтобы повторные miss'ы не
        пилили БД на каждом событии для unregistered-action'а);
      * `invalidate()` — для тестов и для будущего `register_events`-hook'а.

    Lock — `threading.Lock`, симметрично `_RuleCache`. Read-через-lock
    оправдан тем, что эта функция уже сидит после rule-cache lookup'а,
    который тоже за lock'ом — общая latency не меняется заметно.

    Не сделано (намеренно):
      * per-action expiry (точечный TTL) — overkill для ~10k action'ов;
        глобальный TTL сбрасывает map целиком.
      * cross-process инвалидация — между repликами кеши расходятся на
        TTL-окно после изменения catalog'а; для severity-defaults
        приемлемо (изменения редкие, не security-critical).
    """

    def __init__(self, ttl_seconds: float = 30.0) -> None:
        # Sentinel для negative-result'а: храним явный объект, чтобы отличать
        # «не было в кеше» от «было, но None». Без sentinel'а пришлось бы
        # хранить `tuple[bool, str | None]`, что мусоривее.
        self._missing = object()
        self._map: dict[str, object] = {}
        self._loaded_monotonic: float | None = None
        self._ttl = ttl_seconds
        self._lock = threading.Lock()

    def _ttl_expired(self, mono_now: float) -> bool:
        return (
            self._loaded_monotonic is None
            or (mono_now - self._loaded_monotonic) > self._ttl
        )

    def get(self, db: Session, action: str) -> str | None:
        mono_now = time.monotonic()
        with self._lock:
            if self._ttl_expired(mono_now):
                # TTL истёк — сбрасываем map. Не делаем preload всего каталога,
                # потому что (а) preload N+1 пустых ingest'ов в холодный старт,
                # (б) memory profile: ~10k action'ов × 16-32 байта overhead
                # дешевле, чем потенциальные 100k unique action'ов с typo.
                self._map.clear()
                self._loaded_monotonic = mono_now
            cached = self._map.get(action, self._missing)
            if cached is not self._missing:
                return cached  # type: ignore[return-value]
            # Локальный импорт ради разрыва цикла: rule_service ↔ service_events
            # repo сходились бы на import'е через models/schemas.
            from src.repositories import service_events as se_repo

            try:
                severity = se_repo.get_default_severity(db, action)
            except Exception:
                # БД-выпадение — не валим ingest: возвращаем None, caller'у
                # сработает heuristic-fallback. Логируем WARNING, кеш не
                # обновляем (следующий вызов попробует ещё раз).
                logger.warning(
                    "_CatalogSeverityCache: get_default_severity(%r) failed; "
                    "falling back to heuristic",
                    action,
                )
                return None
            self._map[action] = severity if severity is not None else self._missing
            # Возвращаем normalized: пустая строка тоже воспринимается как
            # missing (catalog seed может прислать `""` для action без
            # default'а; treat-as-None убирает спецслучай у caller'а).
            return severity if severity else None

    def invalidate(self) -> None:
        with self._lock:
            self._map.clear()
            self._loaded_monotonic = None


_catalog_cache = _CatalogSeverityCache(ttl_seconds=30.0)


def invalidate_catalog_severity_cache() -> None:
    """Сбрасывает кеш `service_events.default_severity`.

    Вызывается из `register_events` после upsert'а: иначе свежий
    `default_severity` подхватился бы только через TTL (до 30s lag).
    Без вызова поведение корректно, но lag заметен в тестах.
    """
    _catalog_cache.invalidate()


class CacheState(enum.Enum):
    """Состояние `_RuleCache`.

    * `UNLOADED` — кеш ни разу не прогрелся (или сброшен через `invalidate`),
      следующий `get` обязан сходить в БД.
    * `LOADING` — в процессе загрузки. Выставляется внутри `get()` под
      `self._lock` и тут же сменяется на `READY`/`EMPTY` (или
      восстанавливается на `prev_state` при исключении); снаружи никем не
      наблюдается. Оставлено для случая вынесения refresh в фоновый таск,
      когда отдельная корутина будет смотреть на enum-значение без захвата
      lock'а.
    * `READY` — кеш прогрет, в `_rules` лежит непустой snapshot.
    * `EMPTY` — кеш прогрет, БД пустая. Отдельное состояние, чтобы
      `MAX(updated_at) = NULL` на пустой БД не триггерил лишний
      `SELECT active_sorted` каждый TTL-tick.
    """

    UNLOADED = "unloaded"
    LOADING = "loading"
    READY = "ready"
    EMPTY = "empty"


class _RuleCache:
    """In-memory кеш активных правил с TTL-обновлением.

    При истечении TTL сравнивает MAX(updated_at) из БД с моментом последней загрузки —
    если правила не менялись, просто продлевает TTL без полной перезагрузки.
    Это позволяет корректно работать с несколькими воркерами:
    изменение правил через любой из них будет подхвачено остальными.
    При недоступности БД возвращает устаревший кэш с логированием.

    Состояние держится в одном поле `_state: CacheState` (UNLOADED / LOADING
    / READY / EMPTY) вместо набора bool-флагов — раньше пара
    `_loaded_at is None` + `_db_empty: bool` дублировали один и тот же домен
    «cold / warm-empty / warm-non-empty» и расходились в edge-кейсах
    (например, после `invalidate` `_db_empty` сбрасывался отдельно). Bool
    `_db_empty` оставлен как property для обратной совместимости с тестами,
    проксирует `_state == CacheState.EMPTY`.
    """

    def __init__(self, ttl_seconds: int = 30) -> None:
        self._rules: list[_RuleSnapshot] = []
        # `_loaded_at` (wall-clock UTC) — момент последнего успешного refresh'а
        # в этом процессе. Сами «изменилась ли БД» решаем по `_last_db_max`
        # (два DB-side timestamp'а), а `_loaded_at` остаётся как маркер для
        # диагностики и для теста, проверяющего, что watermark фиксируется
        # ДО SELECT'а MAX (load_started_at = пред-окно, не пост-окно). TTL
        # считаем по `_loaded_monotonic`, чтобы NTP step / переключение часов
        # не запирали кеш на десятки минут или не сбрасывали его внеплановым
        # refresh'ем.
        #
        # Эти два поля держатся в одной паре: апдейт обоих атомарен
        # под `self._lock` (см. `get`/`invalidate`). Читаются без lock'а
        # только из `_ttl_fresh` на горячем пути — там нас интересует
        # один `_loaded_monotonic`, а wall-clock `_loaded_at` не трогаем
        # (расхождение «monotonic уже обновлён, _loaded_at ещё нет» на
        # CPython 64-bit невозможно — присваивание int/datetime атомарно
        # на уровне байткода, GIL держит read-modify-write). Если когда-то
        # перейдём на свободный от GIL рантайм или добавим третье связанное
        # поле — `_ttl_fresh` придётся брать под lock.
        self._loaded_at: datetime | None = None
        self._loaded_monotonic: float | None = None
        # MAX(updated_at) последнего успешного refresh'а. Сравниваем именно с
        # этим значением, а не с `_loaded_at`: оба операнда теперь приходят из
        # БД (от одного и того же writer'ского wall-clock'а), и NTP-skew между
        # reader-pod'ом и writer-pod'ом из сравнения выпадает. Раньше
        # сравнение шло `db_updated_at > _loaded_at`, и любая разница часов
        # между pod'ами либо ложно триггерила reload, либо тихо пропускала
        # cross-worker UPDATE до следующего bump'а MAX.
        self._last_db_max: datetime | None = None
        # Состояние кеша: UNLOADED → (READY | EMPTY). Заменяет старую пару
        # «`_loaded_at is None` + `_db_empty: bool`», которые ходили парой,
        # но обновлялись в разных местах и расходились (см. invalidate).
        self._state: CacheState = CacheState.UNLOADED
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        # Observability-счётчики: TTL-hit (fast-path), TTL-miss (slow-path
        # с реальным DB-touch), stale-serve (slow-path упал и вернули
        # предыдущий snapshot). SLA fast-path и slow-path принципиально
        # разные, stale-serve особенно важно отслеживать после DB-outage.
        # Читаются через `get_cache_counters()` (без lock'а на чтение:
        # int-инкременты атомарны под GIL, для observability snapshot'а
        # этого достаточно). Никаких prom-метрик не вешаем — counter'ы
        # должны быть видны как минимум в self-audit/diagnostic-endpoint.
        self._hits: int = 0
        self._misses: int = 0
        self._stale_serves: int = 0

    @property
    def _db_empty(self) -> bool:
        """Обратная совместимость: `True`, когда last успешный refresh
        видел пустую БД. Тесты исторически читают/пишут этот атрибут."""
        return self._state is CacheState.EMPTY

    @_db_empty.setter
    def _db_empty(self, value: bool) -> None:
        # Сеттер — test-only shim для исторических тестов, которые форсят
        # флаг. Production-код сюда не ходит: `_state` меняется внутри `get`
        # / `invalidate` под `self._lock`. Без lock'а здесь сознательно —
        # pytest single-threaded, lock не нужен; но если кто-то позовёт
        # этот сеттер из prod-пути, composite-check на `_state` / `_rules`
        # race'нет с `get`, а `_loaded_at` / `_loaded_monotonic` останутся
        # неконсистентны (мы их тут не трогаем). Защищаемся env-guard'ом
        # на `PYTEST_CURRENT_TEST`: pytest всегда выставляет её на каждый
        # активный тест, любая другая среда (uvicorn / alembic / cli)
        # его не несёт, и `AssertionError` встанет колом ровно там, где
        # появился незаконный setter-call.
        assert os.environ.get("PYTEST_CURRENT_TEST"), (
            "_RuleCache._db_empty setter is test-only; "
            "production must mutate state via get()/invalidate() under lock"
        )
        if value:
            self._state = CacheState.EMPTY
        else:
            # False можно выставить из READY (есть правила) или из UNLOADED
            # (тестовый сброс). Не разрушаем уже прогретый READY-state.
            if self._state is CacheState.EMPTY:
                self._state = (
                    CacheState.READY if self._rules else CacheState.UNLOADED
                )

    def _ttl_fresh(self, mono_now: float) -> bool:
        return (
            self._loaded_monotonic is not None
            and (mono_now - self._loaded_monotonic) <= self._ttl
        )

    def get(self, db: Session) -> list[_RuleSnapshot]:
        mono_now = time.monotonic()
        if self._ttl_fresh(mono_now):
            self._hits += 1
            return self._rules
        with self._lock:
            mono_now = time.monotonic()
            if self._ttl_fresh(mono_now):
                # Гонка: другой тред успел refresh, пока мы ждали lock.
                # Засчитываем hit, не miss — реального DB-touch'а нет.
                self._hits += 1
                return self._rules
            self._misses += 1
            # `_loaded_at` фиксируем ДО SELECT'а MAX — это межсервисный
            # watermark, его читают тесты и стале-fallback. Сравнение «изменилась
            # ли БД» теперь делается через `_last_db_max` (см. ниже), а
            # `_loaded_at` остаётся как маркер «когда был последний успешный
            # refresh» для диагностики и для теста, проверяющего, что
            # `_loaded_at` ≤ before-вызова (фиксируется в начале окна).
            load_started_at = datetime.now(timezone.utc)
            prev_state = self._state
            self._state = CacheState.LOADING
            try:
                db_updated_at = rule_repo.get_max_updated_at(db)
                # Пустая БД (NULL MAX) при пустом кеше — стабильное состояние,
                # лишний SELECT active_sorted не нужен. Как только в БД
                # появится первая row — db_updated_at станет non-NULL и
                # ветка ниже подтянет её.
                db_empty_now = db_updated_at is None
                first_load = prev_state is CacheState.UNLOADED
                # Сравниваем новый MAX с последним наблюдавшимся MAX'ом из БД,
                # а не с `_loaded_at` (wall-clock reader'а). Оба операнда —
                # writer-side timestamps, NTP skew между pod'ами на сравнение
                # не влияет. Если writer переотправил тот же `updated_at`
                # (clock rollback на пишущем pod'е) — пропустим update до
                # следующего bump'а; это ничейный случай, потому что физически
                # «UPDATE прилетел, а MAX в БД не вырос» означает, что row
                # с большим updated_at уже была.
                changed = (
                    not db_empty_now
                    and (
                        first_load
                        or self._last_db_max is None
                        or db_updated_at > self._last_db_max
                    )
                )
                # first_load (после invalidate или cold start) — всегда тянем
                # фактический snapshot, даже если БД пустая. Это лишний SELECT
                # на абсолютно пустой инсталляции один раз за TTL, но invalidate
                # должен гарантированно сбросить кеш.
                empty_to_empty_after_purge = (
                    db_empty_now and prev_state is not CacheState.EMPTY
                )
                if changed or first_load or empty_to_empty_after_purge:
                    fresh_orm = rule_repo.get_active_sorted(db)
                    # Снимаем frozen-dataclass с каждой ORM-row до выхода из
                    # session-скоупа — кеш не должен зависеть ни от Session,
                    # ни от lazy-loading'а добавленных в будущем relationship'ов.
                    self._rules = [_snapshot_rule(r) for r in fresh_orm]
                self._state = CacheState.EMPTY if db_empty_now else CacheState.READY
                self._loaded_at = load_started_at
                self._loaded_monotonic = mono_now
                self._last_db_max = db_updated_at
            except Exception:
                if self._loaded_monotonic is not None:
                    self._stale_serves += 1
                    logger.error("RuleCache: DB reload failed — serving stale cache")
                    # Сдвигаем ТОЛЬКО TTL (monotonic), чтобы не долбить БД
                    # до следующего окна. `_loaded_at` и state оставляем
                    # на последнем подтверждённом значении: при восстановлении
                    # БД следующий tick корректно сравнит `MAX(updated_at)`
                    # с этим watermark'ом и подхватит любой cross-worker
                    # UPDATE, прилетевший во время outage'а. Если двинуть
                    # `_loaded_at` к моменту провалившейся попытки, UPDATE,
                    # попавший в окно [last_good, failed_attempt], потерялся
                    # бы до следующего bump'а MAX (т.е. ещё одного UPDATE).
                    self._loaded_monotonic = mono_now
                    self._state = prev_state
                else:
                    self._state = prev_state
                    raise
        return self._rules

    def invalidate(self) -> None:
        """Принудительный сброс для текущего воркера.
        Другие воркеры подхватят изменения через MAX(updated_at) при следующем TTL."""
        with self._lock:
            self._loaded_at = None
            self._loaded_monotonic = None
            self._last_db_max = None
            self._state = CacheState.UNLOADED


_cache = _RuleCache(ttl_seconds=30)


def invalidate_cache() -> None:
    _cache.invalidate()


def get_cache_counters() -> dict[str, int]:
    """Snapshot in-process counters кеша правил.

    Возвращает `{hits, misses, stale_serves}`. Используется тестами и
    диагностическими endpoint'ами; никаких prom-метрик здесь не вешаем,
    чтобы не тянуть prometheus-client в hot-path. Без lock'а: int-чтение
    атомарно под GIL, для snapshot'а допустим небольшой skew между
    полями.
    """
    return {
        "hits": _cache._hits,
        "misses": _cache._misses,
        "stale_serves": _cache._stale_serves,
    }


def _matches(rule: _RuleSnapshot, event: EventCreate) -> bool:
    """Проверяет, совпадает ли событие с критериями правила.

    test-only: production-путь `apply_rules` ходит через
    `_matches_with_severity` (читает severity из локальной переменной, а не
    `payload.severity`). Эта версия осталась как самостоятельный предикат
    для прямых тестов матчинга — call-graph прода её не зовёт.
    """
    if rule.match_service is not None and rule.match_service != event.service:
        return False
    if rule.match_action is not None:
        if not action_matches_pattern(event.action, rule.match_action):
            return False
    if rule.match_status is not None and rule.match_status != event.status:
        return False
    if rule.match_severity is not None and rule.match_severity != event.severity:
        return False
    if rule.match_allowed is not None and rule.match_allowed != event.allowed:
        return False
    return True


def apply_rules(db: Session, payload: EventCreate) -> EventCreate | None:
    """Назначает severity и применяет правила к событию.

    Источник дефолтного severity — БД (`audit_rules` с `is_default=true`),
    а не хардкод. Порядок:

    1. Если severity задан явно — он сохраняется; событие НЕ дропается
       (caller сам решил severity, это осознанный override).
    2. Если severity не задан — базовый severity берётся из дефолтного
       правила, сматчившегося на `(action, status)`. Если дефолт для пары
       удалён админом (или его никогда не было) и каталог `service_events`
       тоже молчит — у события нет базового severity.
    3. Managed-правила (`is_default=false`) оцениваются по убыванию priority
       поверх базового severity: SUPPRESS дропает, ALLOW фиксирует и
       обрывает цепочку, OVERRIDE_SEVERITY меняет severity и продолжает.
       Managed-правила приоритетнее дефолтов — дефолты сидируются с
       `priority=0`, managed по умолчанию `priority=100`.
    4. Семантика удаления дефолта: если после шагов 2-3 severity так и не
       назначен (нет дефолта, нет managed-OVERRIDE/ALLOW) — событие
       дропается (`return None`). «Нет правила → не логируется».

    Возвращает (возможно изменённый) payload для сохранения, либо None —
    если событие подавлено или у него нет ни одного применимого правила.

    Defence-in-depth: self-audit loging_service всегда обходит правила,
    даже если кто-то по ошибке вызовет `record()` вместо `record_admin_action()`.
    Закрывает «admin создал SUPPRESS match_service='loging_service' и отключил
    весь собственный аудит».
    """
    if payload.service == "loging_service":
        return payload  # self-audit never suppressed, never overridden

    severity = payload.severity
    explicit = severity is not None
    severity_dirty = False

    # Cold-start семантика: если БД упала и кеш ещё не успел заполниться
    # за всю жизнь процесса, `_cache.get` пробросит исключение наверх — caller
    # увидит 500 и retry через outbox. Это сознательный fail-closed: для
    # audit-журнала consistency важнее availability — событие либо прошло
    # rule engine как положено, либо упало и переедет на retry. После первой
    # удачной загрузки кеш отдаёт stale snapshot при последующих DB-выпадениях
    # (см. `_RuleCache.get` except-ветку).
    rules = _cache.get(db)

    # Шаг 1: базовый severity из дефолтного OVERRIDE_SEVERITY-правила. Дефолты
    # сидируются с `priority=0`; они НЕ участвуют в managed-цепочке ниже (иначе
    # priority=0 дефолт перетёр бы managed-OVERRIDE по контракту «последний
    # матч выигрывает»). Берём первый сматчившийся дефолт — на каждый action
    # сидируется ровно один OVERRIDE-дефолт (статус-агностичный). Дефолтный
    # SUPPRESS (матрица=ИГНОР) обрабатывается в шаге 2, чтобы подавление
    # работало и для событий с явным severity.
    if not explicit:
        for rule in rules:
            if not rule.is_default:
                continue
            if rule.effect == "OVERRIDE_SEVERITY" and rule.effect_severity:
                if _matches_with_severity(rule, payload, severity):
                    severity = rule.effect_severity
                    severity_dirty = True
                    break
        if severity is None and db is not None:
            # Каталог `service_events.default_severity` — тоже БД-источник
            # (сервисы регистрируют его через `register_events`), не хардкод.
            # Оставлен как fallback для action'ов, у которых нет дефолтного
            # правила, но есть зарегистрированный каталожный severity.
            catalog_severity = _catalog_cache.get(db, payload.action)
            if catalog_severity is not None:
                severity = catalog_severity
                severity_dirty = True

    # Шаг 2: managed-правила поверх базового severity. Отсортированы по
    # `priority DESC, id ASC` (детерминированный порядок). SUPPRESS/ALLOW
    # обрывают цепочку; OVERRIDE_SEVERITY перетирает severity и продолжает —
    # последний матч выигрывает.
    for rule in rules:
        if rule.is_default:
            # Дефолтные OVERRIDE_SEVERITY задают базовый severity в шаге 1 и в
            # managed-цепочке не участвуют. Дефолтный SUPPRESS (матрица=ИГНОР)
            # подавляет событие на своём priority=0 — если ни одно managed-
            # правило с более высоким приоритетом не оборвало цепочку раньше
            # (ALLOW сохранил бы, SUPPRESS дропнул бы). Работает и для событий
            # с явным severity.
            if rule.effect == "SUPPRESS" and _matches_with_severity(
                rule, payload, severity
            ):
                return None
            continue
        if not _matches_with_severity(rule, payload, severity):
            continue
        if rule.effect == "SUPPRESS":
            return None
        if rule.effect == "ALLOW":
            if severity_dirty:
                return payload.model_copy(update={"severity": severity})
            return payload
        if rule.effect == "OVERRIDE_SEVERITY" and rule.effect_severity:
            if severity != rule.effect_severity:
                severity = rule.effect_severity
                severity_dirty = True

    # Шаг 3: drop-семантика. Severity не задан явно, не назначен ни дефолтом,
    # ни managed-правилом, ни каталогом — для пары `(action, status)` нет
    # базового правила, событие не логируется.
    if severity is None:
        return None

    if severity_dirty:
        return payload.model_copy(update={"severity": severity})
    return payload


def _matches_with_severity(
    rule: _RuleSnapshot, payload: EventCreate, severity: str | None
) -> bool:
    """Версия `_matches`, использующая severity из локальной переменной.

    `_matches` читает `payload.severity` напрямую; в `apply_rules` мы
    держим актуальный severity в локальной переменной (чтобы не плодить
    `model_copy` на каждое правило), поэтому match-проверке тоже нужно
    видеть «текущий» severity, а не зафиксированный на момент входа в
    функцию. Поля сравнения симметричны `_matches`: service/action/status/
    severity/allowed.
    """
    if rule.match_service is not None and rule.match_service != payload.service:
        return False
    if rule.match_action is not None:
        if not action_matches_pattern(payload.action, rule.match_action):
            return False
    if rule.match_status is not None and rule.match_status != payload.status:
        return False
    if rule.match_severity is not None and rule.match_severity != severity:
        return False
    if rule.match_allowed is not None and rule.match_allowed != payload.allowed:
        return False
    return True
