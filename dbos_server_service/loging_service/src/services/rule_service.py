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
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.models.audit_rule import AuditRule
from src.repositories import rules as rule_repo
from src.schemas.events import EventCreate

logger = logging.getLogger(__name__)

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
    # Обращения к loging_service (все сохраняются без ротации)
    ("logging.events_queried",   "success"): "INFO",
    ("logging.rules_read",       "success"): "INFO",
    ("logging.rules_write",      "success"): "WARNING",
    ("logging.services_read",    "success"): "INFO",
    ("logging.admin_access",     "success"): "INFO",
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
        self._rules: list[AuditRule] = []
        self._loaded_at: datetime | None = None
        self._ttl = ttl_seconds
        self._lock = threading.Lock()

    def get(self, db: Session) -> list[AuditRule]:
        now = datetime.now(timezone.utc)
        if self._loaded_at is not None and (now - self._loaded_at).total_seconds() <= self._ttl:
            return self._rules
        with self._lock:
            now = datetime.now(timezone.utc)
            if self._loaded_at is not None and (now - self._loaded_at).total_seconds() <= self._ttl:
                return self._rules
            try:
                db_updated_at = rule_repo.get_max_updated_at(db)
                if self._loaded_at is None or db_updated_at is None or db_updated_at > self._loaded_at:
                    fresh = rule_repo.get_active_sorted(db)
                    # Отвязываем правила от этой сессии — иначе последующие
                    # чтения (в других сессиях / после expire-on-commit)
                    # триггерят refresh и роняют `DetachedInstanceError`.
                    for rule in fresh:
                        db.expunge(rule)
                    self._rules = fresh
                self._loaded_at = now
            except Exception:
                if self._loaded_at is not None:
                    logger.error("RuleCache: DB reload failed — serving stale cache")
                    self._loaded_at = now  # сброс TTL чтобы не молотить БД
                else:
                    raise
        return self._rules

    def invalidate(self) -> None:
        """Принудительный сброс для текущего воркера.
        Другие воркеры подхватят изменения через MAX(updated_at) при следующем TTL."""
        with self._lock:
            self._loaded_at = None


_cache = _RuleCache(ttl_seconds=30)


def invalidate_cache() -> None:
    _cache.invalidate()


def _matches(rule: AuditRule, event: EventCreate) -> bool:
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
