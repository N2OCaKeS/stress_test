"""Доменные константы, общие на весь сервис."""


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
