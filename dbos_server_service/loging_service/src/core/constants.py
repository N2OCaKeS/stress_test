"""Доменные константы, общие на весь сервис."""


# Сервисы, чей audit-журнал нельзя писать через внешний service-token
# endpoint. `loging_service` — единственный сейчас, потому что retention
# инвариант защищает только его. Сравнение идёт через
# `utils.normalization.normalize_service_name` ДО `in`-проверки —
# отбиваются варианты canonical-формы (case-fold, ZWSP-padding,
# Unicode-confusables).
RESERVED_SERVICE_NAMES: frozenset[str] = frozenset({"loging_service"})
