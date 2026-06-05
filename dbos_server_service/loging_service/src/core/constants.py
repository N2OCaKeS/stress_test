"""Доменные константы, общие на весь сервис."""

from typing import Literal


# Шесть уровней severity для audit-событий. Источник истины: эта tuple
# плюс производный `Severity` Literal. Schemas/events.py, schemas/rules.py,
# schemas/services.py, schemas/retention.py и endpoints/events.py
# исторически дублировали список вшитым `Literal[...]` — это безопасно
# (mypy сравнивает структурно), но при добавлении нового уровня менять
# приходилось в 5 местах. Новый код использует `Severity` отсюда.
SEVERITY_LEVELS: tuple[str, ...] = (
    "TRACE",
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
)
Severity = Literal["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


# Платформенные роли, которые видят только свой департамент. Используется
# `dependencies/auth.py::require_reader` для сужения dept-scope read'а.
# Содержательно живёт policy-решением: `loging_reader` сознательно
# dept-scoped (см. README.md → «loging_reader vs loging_admin»),
# `department_admin` по определению — admin своего dept'а. Изменение
# набора — breaking-change для авторизации; согласуй с owner'ом.
DEPT_SCOPED_ROLES: frozenset[str] = frozenset({"loging_reader", "department_admin"})


# Whitelist значений `actor_type` для audit-событий. Должен совпадать с
# Literal у `EventCreate.actor_type` в `src/schemas/events.py` — там
# источник истины для Pydantic-валидации, здесь — для рантайм-резолва в
# точках, где `actor_type` приходит из introspect и может быть unknown.
# При расхождении тест `test_payload_validation` падает.
VALID_ACTOR_TYPES: frozenset[str] = frozenset(
    {"user", "bot", "service", "anonymous", "oauth_client"}
)


# Сервисы, чей audit-журнал нельзя писать через внешний service-token
# endpoint. `loging_service` — единственный сейчас, потому что retention
# инвариант защищает только его. Сравнение идёт через
# `utils.normalization.normalize_service_name` ДО `in`-проверки —
# отбиваются варианты canonical-формы (case-fold, ZWSP-padding,
# Unicode-confusables).
RESERVED_SERVICE_NAMES: frozenset[str] = frozenset({"loging_service"})


# Стабильные ключи `pg_advisory_lock`. PostgreSQL ждёт signed bigint,
# поэтому используем `int.from_bytes(<8-байтная ASCII-метка>, "big")` —
# значение фиксировано и его можно проверить в `pg_locks` глазами.
#
# Под multi-replica только держатель ключа выполняет операцию;
# остальные пропускают итерацию.
ADVISORY_LOCKS: dict[str, int] = {
    # Retention sweep: один daemon на кластер делает DELETE, остальные
    # replica'ы пропускают tick через `pg_try_advisory_lock`.
    "retention_sweep": int.from_bytes(b"loretent", "big"),
}
