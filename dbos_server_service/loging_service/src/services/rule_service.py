"""Динамические правила аудита — загрузка, кеширование, применение.

Правила загружаются из БД и кешируются в памяти (TTL 30 сек).
При изменении правил через API кеш сбрасывается немедленно.

Порядок обработки события:
  1. Если severity не задан — назначается из _DEFAULT_SEVERITY (по action+status)
  2. OVERRIDE_SEVERITY — изменяет severity, продолжает цепочку
  3. SUPPRESS          — отбрасывает событие (не сохраняется)
  4. ALLOW             — сохраняет немедленно, прекращает вычисление
  5. Если ни одно правило не дало финального решения — событие сохраняется (default allow)
"""

import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.models.audit_rule import AuditRule
from src.repositories import rules as rule_repo
from src.schemas.events import EventCreate

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
    effect: str
    effect_severity: str | None


def _snapshot_rule(rule: AuditRule) -> _RuleSnapshot:
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
    )

# Severity по умолчанию для каждой пары (action, status).
# Переопределяется через правила OVERRIDE_SEVERITY без деплоя.
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
    # Отделы
    ("department.create", "success"): "CRITICAL",
    ("department.list", "success"): "INFO",
    ("department.service_grant", "success"): "CRITICAL",
    ("department.service_revoke", "success"): "CRITICAL",
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
    ("logging.rules_read",       "success"): "INFO",
    ("logging.rules_write",      "success"): "WARNING",
    ("logging.services_read",    "success"): "INFO",
    ("logging.admin_access",     "success"): "INFO",
    ("logging.retention_read",   "success"): "INFO",
    ("logging.retention_write",  "success"): "WARNING",
    ("logging.retention_sweep",  "success"): "INFO",
}


def action_matches_pattern(action: str, pattern: str) -> bool:
    """Совпадает ли *action* с *pattern*.

    `*` совпадает с любой непустой последовательностью символов в ОДНОМ
    dot-сегменте. `user.*` matchит `user.login`, но НЕ `user.login.extra`.
    """
    if "*" not in pattern:
        return action == pattern
    regex = re.escape(pattern).replace(r"\*", r"[^.]+")
    return bool(re.fullmatch(regex, action))


def _resolve_default_severity(action: str, status: str) -> str:
    """Возвращает severity по умолчанию из встроенной таблицы."""
    if (action, status) in _DEFAULT_SEVERITY:
        return _DEFAULT_SEVERITY[(action, status)]
    return "WARNING" if status in ("failure", "denied") else "INFO"


class _RuleCache:
    """In-memory кеш активных правил с TTL-обновлением.

    При истечении TTL сравнивает MAX(updated_at) из БД с моментом последней загрузки —
    если правила не менялись, просто продлевает TTL без полной перезагрузки.
    Это позволяет корректно работать с несколькими воркерами:
    изменение правил через любой из них будет подхвачено остальными.
    При недоступности БД возвращает устаревший кэш с логированием.
    """

    def __init__(self, ttl_seconds: int = 30) -> None:
        self._rules: list[_RuleSnapshot] = []
        # `_loaded_at` (wall-clock) сравниваем с `MAX(updated_at)` из БД —
        # это межсервисный timestamp, его нужно держать в UTC. TTL же
        # считаем по `_loaded_monotonic`, чтобы NTP step / переключение
        # часов не запирали кеш на десятки минут или, наоборот, не
        # сбрасывали его внеплановым refresh'ем.
        self._loaded_at: datetime | None = None
        self._loaded_monotonic: float | None = None
        # Запоминаем, что предыдущий tick видел пустую БД (MAX(updated_at) = NULL).
        # Без этого флага условие `db_updated_at is None` каждый раз даёт True
        # и тянет лишний SELECT active_sorted каждые TTL-секунд на пустой БД.
        self._db_empty: bool = False
        self._ttl = ttl_seconds
        self._lock = threading.Lock()

    def _ttl_fresh(self, mono_now: float) -> bool:
        return (
            self._loaded_monotonic is not None
            and (mono_now - self._loaded_monotonic) <= self._ttl
        )

    def get(self, db: Session) -> list[_RuleSnapshot]:
        mono_now = time.monotonic()
        if self._ttl_fresh(mono_now):
            return self._rules
        with self._lock:
            mono_now = time.monotonic()
            if self._ttl_fresh(mono_now):
                return self._rules
            try:
                db_updated_at = rule_repo.get_max_updated_at(db)
                # Пустая БД (NULL MAX) при пустом кеше — стабильное состояние,
                # лишний SELECT active_sorted не нужен. Как только в БД
                # появится первая row — db_updated_at станет non-NULL и
                # ветка ниже подтянет её.
                db_empty_now = db_updated_at is None
                first_load = self._loaded_at is None
                changed = (
                    not db_empty_now
                    and (first_load or db_updated_at > self._loaded_at)
                )
                # first_load (после invalidate или cold start) — всегда тянем
                # фактический snapshot, даже если БД пустая. Это лишний SELECT
                # на абсолютно пустой инсталляции один раз за TTL, но invalidate
                # должен гарантированно сбросить кеш.
                if changed or first_load or (db_empty_now and not self._db_empty):
                    fresh_orm = rule_repo.get_active_sorted(db)
                    # Снимаем frozen-dataclass с каждой ORM-row до выхода из
                    # session-скоупа — кеш не должен зависеть ни от Session,
                    # ни от lazy-loading'а добавленных в будущем relationship'ов.
                    self._rules = [_snapshot_rule(r) for r in fresh_orm]
                self._db_empty = db_empty_now
                self._loaded_at = datetime.now(timezone.utc)
                self._loaded_monotonic = mono_now
            except Exception:
                if self._loaded_monotonic is not None:
                    logger.error("RuleCache: DB reload failed — serving stale cache")
                    # Сдвигаем TTL чтобы не долбить БД до следующего окна.
                    self._loaded_monotonic = mono_now
                    self._loaded_at = datetime.now(timezone.utc)
                else:
                    raise
        return self._rules

    def invalidate(self) -> None:
        """Принудительный сброс для текущего воркера.
        Другие воркеры подхватят изменения через MAX(updated_at) при следующем TTL."""
        with self._lock:
            self._loaded_at = None
            self._loaded_monotonic = None
            self._db_empty = False


_cache = _RuleCache(ttl_seconds=30)


def invalidate_cache() -> None:
    _cache.invalidate()


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

    1. Если severity не задан — берётся из _DEFAULT_SEVERITY.
    2. Правила оцениваются по убыванию priority.
    Возвращает (возможно изменённый) payload для сохранения,
    или None — если событие должно быть подавлено.

    Defence-in-depth: self-audit loging_service всегда обходит правила,
    даже если кто-то по ошибке вызовет `record()` вместо `record_admin_action()`.
    Закрывает «admin создал SUPPRESS match_service='loging_service' и отключил
    весь собственный аудит».
    """
    if payload.service == "loging_service":
        return payload  # self-audit never suppressed, never overridden

    if payload.severity is None:
        payload = payload.model_copy(
            update={"severity": _resolve_default_severity(payload.action, payload.status)}
        )

    rules = _cache.get(db)
    for rule in rules:
        if not _matches(rule, payload):
            continue
        if rule.effect == "SUPPRESS":
            return None
        if rule.effect == "ALLOW":
            return payload
        if rule.effect == "OVERRIDE_SEVERITY" and rule.effect_severity:
            payload = payload.model_copy(update={"severity": rule.effect_severity})
    return payload
