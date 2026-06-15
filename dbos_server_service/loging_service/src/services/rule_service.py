"""Динамические правила аудита — загрузка, кеширование, применение.

Правила загружаются из БД и кешируются в памяти (TTL 30 сек).
При изменении правил через API кеш сбрасывается немедленно.

Порядок обработки события:
  1. Если severity не задан — назначается из _DEFAULT_SEVERITY (по action+status)
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
    # `is_default=True` — авто-сидируемое дефолтное правило `(action,status) →
    # severity`. Дефолты задают БАЗОВЫЙ severity (заменяют прежнюю хардкод-
    # таблицу `_DEFAULT_SEVERITY`); managed-правила (is_default=False)
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

# Сид-данные для дефолтных правил severity. ЭТО НЕ рантайм-источник истины:
# `seed_default_rules` один раз материализует эти пары в таблицу `audit_rules`
# (`is_default=true`), после чего движок берёт severity ТОЛЬКО из БД. Таблица
# оставлена как данные сидера (миграция + startup seed) и как контракт для
# `test_audit_events_md_sync`. Удалённый админом дефолт не воскресает — см.
# маркер `seed_state`.
_DEFAULT_SEVERITY: dict[tuple[str, str], str] = {
    # HTTP middleware
    ("http.access_denied", "denied"): "CRITICAL",
    ("http.client_error", "failure"): "WARNING",
    ("http.server_error", "failure"): "CRITICAL",
    # Аутентификация
    ("user.login", "success"): "INFO",
    ("user.login", "failure"): "CRITICAL",
    ("user.refresh", "success"): "INFO",
    ("user.logout", "success"): "INFO",
    ("user.me", "success"): "INFO",
    ("token.refresh_reuse", "failure"): "CRITICAL",
    # Пользователи
    ("user.create", "success"): "INFO",
    ("user.update", "success"): "INFO",
    ("user.roles_assign", "success"): "INFO",
    ("user.password_reset", "success"): "CRITICAL",
    ("user.ban", "success"): "CRITICAL",
    ("user.unban", "success"): "CRITICAL",
    ("user.hard_deleted", "success"): "CRITICAL",
    # Отделы
    ("department.create", "success"): "CRITICAL",
    ("department.list", "success"): "INFO",
    ("department.service_grant", "success"): "CRITICAL",
    ("department.service_revoke", "success"): "CRITICAL",
    ("department.hard_deleted", "success"): "CRITICAL",
    # Группы
    ("group.create", "success"): "INFO",
    ("group.update", "success"): "INFO",
    ("group.delete", "success"): "CRITICAL",
    ("group.member_add", "success"): "WARNING",
    ("group.member_remove", "success"): "WARNING",
    ("group.service_grant", "success"): "CRITICAL",
    ("group.service_revoke", "success"): "CRITICAL",
    ("group.roles_assign", "success"): "CRITICAL",
    ("group.roles_revoke", "success"): "CRITICAL",
    # Платформенные сервисы
    ("service.create", "success"): "CRITICAL",
    ("service.delete", "success"): "CRITICAL",
    ("service.list", "success"): "INFO",
    # Роли сервисов
    ("service_role.create", "success"): "INFO",
    ("service_role.update", "success"): "INFO",
    ("service_role.delete", "success"): "CRITICAL",
    ("service_role.bulk_assign", "success"): "INFO",
    ("service_role.bulk_revoke", "success"): "INFO",
    # PAT
    ("pat.create", "success"): "INFO",
    ("pat.list", "success"): "INFO",
    ("pat.revoke", "success"): "WARNING",
    # Боты
    ("bot.create", "success"): "WARNING",
    ("bot.list", "success"): "INFO",
    ("bot.update", "success"): "WARNING",
    ("bot.token_create", "success"): "WARNING",
    ("bot.token_list", "success"): "INFO",
    ("bot.token_revoke", "success"): "WARNING",
    # OAuth2-клиенты
    ("oauth_client.create", "success"): "CRITICAL",
    ("oauth_client.list", "success"): "INFO",
    ("oauth_client.delete", "success"): "CRITICAL",
    ("oauth.authorization_code_issued", "success"): "INFO",
    ("oauth.code_exchanged", "success"): "INFO",
    ("oauth.client_credentials_token", "success"): "INFO",
    # Интроспекция токенов
    ("token.introspect", "success"): "INFO",
    ("token.introspect", "failure"): "CRITICAL",
    # Проверка доступа к сервису
    ("service.access_check", "success"): "INFO",
    ("service.access_check", "denied"): "WARNING",
    # Docker Registry
    ("docker_registry.configure", "success"): "CRITICAL",
    ("docker_registry.update", "success"): "CRITICAL",
    ("docker_registry.get_config", "success"): "INFO",
    ("docker_registry.disable", "success"): "CRITICAL",
    ("docker.token_issued", "success"): "INFO",
    # Администрирование loging_service (loging_admin)
    ("logging_rule.create", "success"): "WARNING",
    ("logging_rule.update", "success"): "CRITICAL",
    ("logging_rule.delete", "success"): "CRITICAL",
    # secret_service tokens.* — зеркало `_DEFAULT_SEVERITY` из
    # `secret_service/src/services/audit_events.py`. Держим тут копию, чтобы
    # ingest без explicit severity получал ту же иерархию, что в источнике
    # (reveal/transfer/cross-dep grant — CRITICAL; CRUD-чтения — INFO;
    # auto-block/delete/recover — WARNING).
    ("tokens.create", "success"): "INFO",
    ("tokens.update", "success"): "INFO",
    ("tokens.delete", "success"): "WARNING",
    ("tokens.admin_override_delete", "success"): "CRITICAL",
    ("tokens.revealed", "success"): "CRITICAL",
    ("tokens.revealed_throttled", "success"): "INFO",
    ("tokens.dept_grant_added", "success"): "CRITICAL",
    ("tokens.dept_grant_revoked", "success"): "CRITICAL",
    ("tokens.dept_revoke_cascade", "success"): "CRITICAL",
    ("tokens.dept_recipient_cascade", "success"): "CRITICAL",
    ("tokens.role_acl_added", "success"): "INFO",
    ("tokens.role_acl_revoked", "success"): "INFO",
    ("tokens.owner_user_deleted_block", "success"): "WARNING",
    ("tokens.owner_dept_deleted_block", "success"): "WARNING",
    ("tokens.transfer_ownership", "success"): "CRITICAL",
    ("tokens.recover", "success"): "WARNING",
    ("tokens.access_denied", "failure"): "INFO",
    ("tokens.revealed_blocked_by_validity", "failure"): "INFO",
    ("user.password_change_required_blocked", "failure"): "INFO",
    ("user.must_change_password_cleared", "success"): "INFO",
    # auth_service secret_lifecycle: callback в secret_service упал на
    # транспортном уровне — WARNING (не CRITICAL: один сбой компенсируется
    # retry'ем, статус сервиса не страдает).
    ("secret_lifecycle.notify_failed", "failure"): "WARNING",
    # Управление серверами (server_service / server_worker)
    ("server.prepare", "success"): "CRITICAL",
    ("server.prepare", "failure"): "CRITICAL",
    ("server.prepared", "success"): "CRITICAL",
    ("server.prepared", "failure"): "CRITICAL",
    # Удаление сервера — destructive, без отката.
    ("server.delete", "success"): "CRITICAL",
    # Раскрытие расшифрованных секретов пользователю (b64) — отдельный action,
    # эмитится только когда GET /ipmi или /server-accounts/{id} вернул plaintext.
    ("ipmi_controller.credentials_revealed", "success"): "CRITICAL",
    ("server_account.password_revealed", "success"): "CRITICAL",
    # IPMI power-операции — действия над железом.
    ("server.power_on", "success"): "WARNING",
    ("server.power_on", "failure"): "CRITICAL",
    ("server.power_on", "denied"): "WARNING",
    ("server.power_off", "success"): "WARNING",
    ("server.power_off", "failure"): "CRITICAL",
    ("server.power_off", "denied"): "WARNING",
    ("server.power_reboot", "success"): "WARNING",
    ("server.power_reboot", "failure"): "CRITICAL",
    ("server.power_reboot", "denied"): "WARNING",
    ("server.power_status", "success"): "INFO",
    ("server.power_status", "failure"): "WARNING",
    ("server.power_status", "denied"): "WARNING",
    # Inventory / users-inventory / installed_packages — read-only probes.
    ("server.inventory_sync", "success"): "INFO",
    ("server.inventory_sync", "failure"): "WARNING",
    ("server.inventory_sync", "denied"): "WARNING",
    ("server_account.users_inventory", "success"): "INFO",
    ("server_account.users_inventory", "failure"): "WARNING",
    ("server_account.users_inventory", "denied"): "WARNING",
    ("installed_packages.list", "success"): "INFO",
    ("installed_packages.list", "failure"): "WARNING",
    ("installed_packages.list", "denied"): "WARNING",
    # Ротации секретов — sensitive, но штатный поток.
    ("server_account.password_rotate", "success"): "WARNING",
    ("server_account.password_rotate", "failure"): "CRITICAL",
    ("server_account.password_rotate", "denied"): "WARNING",
    ("ipmi_controller.password_rotate", "success"): "WARNING",
    ("ipmi_controller.password_rotate", "failure"): "CRITICAL",
    ("ipmi_controller.password_rotate", "denied"): "WARNING",
    # Сервисные OS-учётки на хостах
    ("server_account.provision", "success"): "WARNING",
    ("server_account.provision", "failure"): "CRITICAL",
    ("server_account.update_on_host", "success"): "INFO",
    ("server_account.deprovision", "success"): "WARNING",
    ("server_account.drift_detected", "success"): "WARNING",
    # Обращения к loging_service (все сохраняются без ротации)
    ("logging.events_queried",   "success"): "INFO",
    ("logging.events_queried",   "warning"): "WARNING",
    ("logging.events_exported",  "success"): "WARNING",
    ("logging.rules_read",       "success"): "INFO",
    ("logging.rules_write",      "success"): "WARNING",
    ("logging.services_read",    "success"): "INFO",
    ("logging.service_events_browsed", "success"): "INFO",
    ("logging.admin_access",     "success"): "INFO",
    ("logging.retention_read",   "success"): "INFO",
    ("logging.retention_write",  "success"): "WARNING",
    ("logging.retention_sweep",  "success"): "INFO",
    ("logging.service_events_registered", "success"): "INFO",
    ("audit.idempotency_conflict", "warning"): "WARNING",
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


def default_rule_name(action: str, status: str) -> str:
    """Детерминированное имя дефолтного правила для пары `(action, status)`.

    Усекаем до 128 (`audit_rules.name` max_length): пара
    `action(≤128) + status(≤16)` + префикс не влезает в потолок, поэтому
    режем хвост. Коллизия двух разных пар в одно имя теоретически возможна
    только на патологически длинных action'ах (action сам ≤128 по схеме),
    на реальном наборе `_DEFAULT_SEVERITY` имена уникальны.
    """
    return f"{_DEFAULT_RULE_NAME_PREFIX}{action}:{status}"[:128]


def seed_default_rules(db: Session) -> int:
    """Идемпотентно сеет дефолтные severity-правила в `audit_rules`.

    Контракт:
      * если маркер `seed_state[DEFAULT_RULES_SEED_KEY]` уже стоит — ничего
        не делает (удалённые админом дефолты НЕ воскресают);
      * иначе создаёт по одному `is_default=true` OVERRIDE_SEVERITY-правилу
        на каждую пару из `_DEFAULT_SEVERITY` и ставит маркер;
      * всё в одной транзакции — либо весь набор + маркер, либо ничего.

    Возвращает число созданных правил (0 — уже сеяли).

    Вызывается на старте сервиса (`main.lifespan`) и из тестовых фикстур.
    Безопасен под гонку нескольких воркеров: маркер — PK в `seed_state`,
    параллельная вставка второго воркера упадёт на PK-конфликте, и его
    `seed_default_rules` откатится, не наплодив дублей (UNIQUE на rule.name
    ловит вторую попытку даже без маркера).
    """
    from datetime import datetime, timezone

    from src.models.audit_rule import AuditRule
    from src.models.seed_state import SeedState
    from src.utils.ids import audit_rule_id

    existing = db.get(SeedState, DEFAULT_RULES_SEED_KEY)
    if existing is not None:
        return 0

    now = datetime.now(timezone.utc)
    created = 0
    for (action, status), severity in _DEFAULT_SEVERITY.items():
        db.add(
            AuditRule(
                id=audit_rule_id(),
                name=default_rule_name(action, status),
                description="auto-seeded default severity rule",
                is_active=True,
                is_default=True,
                priority=_DEFAULT_RULE_PRIORITY,
                match_action=action,
                match_status=status,
                effect="OVERRIDE_SEVERITY",
                effect_severity=severity,
                created_at=now,
                updated_at=now,
            )
        )
        created += 1
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
      1. Hardcoded `_DEFAULT_SEVERITY[(action, status)]` — статус-зависимая
         таблица (failure/denied отдельным severity'ем от success).
      2. Каталог `service_events.default_severity` для *action* — каждый сервис
         объявляет дефолт на регистрации (`register_events`), и каталог
         обычно полнее хардкода (~60% action'ов в hardcoded dict'е нет).
         Lookup закрыт через `_CatalogSeverityCache` (TTL=30s), чтобы ingest
         не дёргал БД на каждое событие.
      3. Heuristic: `failure`/`denied`/`warning` → WARNING, остальное → INFO.

    *db* опционален: если caller (юнит-тест, миграция) не передал session,
    catalog lookup пропускается. Production call-sites (`apply_rules`,
    `record_admin_action`) пробрасывают сессию явно.

    Без `"warning"` в heuristic-ветке `status="warning"` (soft-mode guard'ы)
    свалился бы в INFO — теряется сигнал, что операция прошла, но что-то
    пахнет.
    """
    if (action, status) in _DEFAULT_SEVERITY:
        return _DEFAULT_SEVERITY[(action, status)]
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
    """Проверяет, совпадает ли событие с критериями правила."""
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

    # Шаг 1: базовый severity из дефолтного правила. Дефолты сидируются как
    # OVERRIDE_SEVERITY с `priority=0`; они НЕ участвуют в managed-цепочке
    # ниже (иначе priority=0 дефолт перетёр бы managed-OVERRIDE по контракту
    # «последний матч выигрывает»). Берём первый сматчившийся дефолт —
    # на каждую `(action, status)` сидируется ровно один.
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
