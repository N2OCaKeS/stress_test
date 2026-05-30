"""Bearer-token helpers: вытащить из заголовка и отбросить шум по форме.

Канонический источник для копи-паста между сервисами. Эталон — то, что
лежит в `server_service/src/dependencies/auth.py` (там самый аккуратный
shape-precheck со списком допустимых префиксов).

Где сейчас дублируется этот код:
    auth_service/src/dependencies/auth.py     — `_extract_bearer`
    server_service/src/dependencies/auth.py   — `_extract_bearer` + shape-precheck
    server_service/src/middleware/platform_admin_guard.py — свой `_extract_bearer`
    loging_service/src/dependencies/auth.py   — через `HTTPBearer(auto_error=False)`,
                                                 но логика проверки идентичная

Назначение:

* `_extract_bearer(request)` — вытащить значение токена после префикса
  `Bearer ` из заголовка `Authorization`. Возвращает None, если заголовка
  нет или формат не соответствует.

* `_is_token_shape_valid(token)` — дешёвая проверка ДО HTTP-roundtrip'а к
  auth_service. Реальный токен платформы всегда либо JWT (`eyJ…`), либо
  PAT (`dbos_pat_…`), либо bot (`dbos_bot_…`), длиной >= 20. Всё остальное —
  заведомо мусор, отбрасываем без сети.

Slowloris-mitigation: shape-precheck режет trash-bearer'ы вроде "abc",
"test", "12345" до того, как сервис запустит HTTP-вызов в auth_service.
Без него атакующий бесплатно расходует pool коннектов introspect-канала.

Использование (типовой паттерн в FastAPI dependency):

    token = _extract_bearer(request)
    if token is None:
        raise AuthenticationError(error_code="ACCESS_TOKEN_MISSING", ...)
    if not _is_token_shape_valid(token):
        raise AuthenticationError(error_code="INVALID_TOKEN_FORMAT", ...)
    body = await _introspect(token)  # уходит в auth_service
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request


# Минимальная длина токена для shape-precheck. JWT редко короче ~100 символов;
# `dbos_pat_…` / `dbos_bot_…` — минимум 9 (префикс) + энтропийный хвост. 20 —
# заведомо безопасный нижний бар, отсекающий мусор типа "abc", "12345", "test".
_MIN_TOKEN_LENGTH = 20

# Префиксы реальных bearer-токенов, которые выдаёт auth_service:
#   `eyJ`       — JWT (base64-кодированный header `{"alg":...}` всегда начинается с `eyJ`)
#   `dbos_pat_` — Personal Access Token
#   `dbos_bot_` — токен bot-аккаунта
_VALID_TOKEN_PREFIXES = ("eyJ", "dbos_pat_", "dbos_bot_")


def _extract_bearer(request: "Request") -> str | None:
    """Достать сырое значение токена после префикса `Bearer ` из заголовка.

    Возвращает None, если заголовка нет, либо если он не начинается с
    `Bearer ` (с одним пробелом).
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


def _is_token_shape_valid(token: str) -> bool:
    """Дешёвый фильтр bearer-мусора ДО HTTP-вызова в auth_service.

    Реальные токены платформы — `eyJ…` / `dbos_pat_…` / `dbos_bot_…`, длина
    заведомо >= 20. Всё остальное — мусор, который надо отдавать 401 без
    roundtrip'а.
    """
    if not token or len(token) < _MIN_TOKEN_LENGTH:
        return False
    return token.startswith(_VALID_TOKEN_PREFIXES)
