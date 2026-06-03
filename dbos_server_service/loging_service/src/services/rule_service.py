"""Динамические правила аудита — загрузка, кеширование, применение.

Правила загружаются из БД и кешируются в памяти (TTL 30 сек).
При изменении правил через API кеш сбрасывается немедленно.

Порядок обработки события:
  1. Если severity не задан — назначается из _DEFAULT_SEVERITY (по action+status)
  2. Правила перебираются по убыванию priority (`priority DESC`)
  3. SUPPRESS          — отбрасывает событие (не сохраняется), цепочка обрывается
  4. ALLOW             — сохраняет немедленно, цепочка обрывается
  5. OVERRIDE_SEVERITY — меняет severity и обрывает цепочку (highest priority wins)
  6. Если ни одно правило не сматчилось — событие сохраняется (default allow)
"""

import enum
import logging
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
    ("logging.service_events_registered", "success"): "INFO",
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
    """Возвращает severity по умолчанию из встроенной таблицы.

    Fallback для (action, status) пары, которой нет в `_DEFAULT_SEVERITY`:
    `failure`/`denied`/`warning` → WARNING, всё остальное → INFO. Без
    `"warning"` в первой ветке `status="warning"` (soft-mode guard'ы в
    server_service::internal_service._check_target_department) свалился бы
    в INFO — теряется сигнал, что операция прошла, но что-то пахнет.
    """
    if (action, status) in _DEFAULT_SEVERITY:
        return _DEFAULT_SEVERITY[(action, status)]
    return "WARNING" if status in ("failure", "denied", "warning") else "INFO"


class CacheState(enum.Enum):
    """Состояние `_RuleCache`.

    * `UNLOADED` — кеш ни разу не прогрелся (или сброшен через `invalidate`),
      следующий `get` обязан сходить в БД.
    * `LOADING` — в процессе загрузки (зарезервировано на случай вынесения
      refresh в фоновый таск; сейчас get идёт под `self._lock`, состояние
      переходит сразу `UNLOADED → READY|EMPTY`).
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

    @property
    def _db_empty(self) -> bool:
        """Обратная совместимость: `True`, когда last успешный refresh
        видел пустую БД. Тесты исторически читают/пишут этот атрибут."""
        return self._state is CacheState.EMPTY

    @_db_empty.setter
    def _db_empty(self, value: bool) -> None:
        # Сеттер нужен только для тестов, которые форсят флаг. Не трогаем
        # `_loaded_at` / `_loaded_monotonic`: они под контролем `get`/`invalidate`.
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
            return self._rules
        with self._lock:
            mono_now = time.monotonic()
            if self._ttl_fresh(mono_now):
                return self._rules
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

    # Cold-start семантика: если БД упала и кеш ещё не успел заполниться
    # за всю жизнь процесса, `_cache.get` пробросит исключение наверх — caller
    # увидит 500 и retry через outbox. Это сознательный fail-closed: для
    # audit-журнала consistency важнее availability — событие либо прошло
    # rule engine как положено, либо упало и переедет на retry. Альтернатива
    # (fail-open: записать без правил) скрытно протащила бы события, которые
    # active SUPPRESS-правило должно было бы подавить, — это compliance-дыра.
    # После первой удачной загрузки кеш отдаёт stale snapshot при последующих
    # DB-выпадениях (см. `_RuleCache.get` except-ветку), так что окно "500 на
    # ingest" — только до первого успешного refresh'а.
    # Правила отсортированы по `priority DESC` в `get_active_sorted`.
    # Семантика OVERRIDE_SEVERITY (исторический контракт): несколько матчей
    # применяются последовательно, последний переписывает severity. SUPPRESS/
    # ALLOW обрывают цепочку. Вопрос «highest priority wins vs last match wins»
    # — open owner-question (см. obsidian/TODO.md → W18 deferred Q2). Тесты
    # фиксируют текущий контракт «последний выигрывает».
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
