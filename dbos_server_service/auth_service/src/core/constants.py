"""Общие enum-ы и константы (статусы юзеров, роли, префиксы токенов и т.п.)."""

from enum import StrEnum


class UserStatus(StrEnum):
    """Статусы юзера. ACTIVE — обычное состояние, BLOCKED/BANNED — отбивают login."""
    ACTIVE = "active"
    BLOCKED = "blocked"
    BANNED = "banned"


class PlatformRole(StrEnum):
    """Platform-уровень: роли, которые управляет только auth_service."""
    ACCOUNT_ADMIN = "account_admin"
    DEPARTMENT_ADMIN = "department_admin"
    LOGING_ADMIN = "loging_admin"
    # Read-only роль для loging_service — тест test_p2_identity_ban_cache.py
    # парам-итерирует все значения enum'а, бизнес-логика рулится через guard'ы.
    LOGING_READER = "loging_reader"
    # Cross-department админ secret_service: read_for_audit,
    # admin_override_delete, transfer_ownership, recover на всех отделах.
    # Department=NULL (как loging_admin). Enforcement происходит в
    # secret_service; auth_service только хранит роль и кладёт её в JWT.
    SERVICE_ADMIN = "service_admin"


class ServiceRole(StrEnum):
    """Per-service роли, которые применяют все application-сервисы."""
    GUEST = "guest"
    READER = "reader"
    OPERATOR = "operator"
    ADMIN = "admin"


class BanType(StrEnum):
    """Тип бана. TEMPORARY обязательно требует `expires_at`."""
    TEMPORARY = "temporary"
    PERMANENT = "permanent"


class SubjectType(StrEnum):
    """Тип субъекта в introspect-ответе."""
    USER = "user"
    BOT = "bot"
    OAUTH_CLIENT = "oauth_client"


class BotStatus(StrEnum):
    """Статус бота. BLOCKED — отбивает использование токенов.

    Wire-value `"disabled"` — синхронно с `BotUpdate.status` (`Literal["active",
    "disabled"]`) и тем, что лежит в БД (колонка `status` — String, пишется
    как есть из PATCH-body). Имя константы оставлено BLOCKED по доменной
    семантике; ходить через `BotStatus(value)` теперь безопасно для строк
    из API/БД.
    """
    ACTIVE = "active"
    BLOCKED = "disabled"


# Префиксы токенов — raw значение показываем один раз, в БД лежит только hash
PAT_PREFIX = "dbos_pat_"
BOT_TOKEN_PREFIX = "dbos_bot_"

# Сколько символов raw-токена кладём в `token_prefix`-колонку для быстрого
# lookup'а до проверки SHA-256 (хватает на `dbos_pat_` / `dbos_bot_` + три
# первых символа secret'а).
TOKEN_PREFIX_LEN = 12

# Дефолтный TTL для bot-токенов — 6 месяцев (180 дней). Если caller не
# передал `expires_at`, бот-токен живёт ровно столько с момента выдачи. После
# истечения dept_admin перевыпускает токен через `POST /bots/{id}/tokens`.
BOT_TOKEN_TTL_SECONDS = 6 * 30 * 24 * 3600  # 15_552_000


# ── Service identity allow-list (mTLS-partial) ───────────────────────────────
# Соседние сервисы (loging_service, server_service, config_service) ходят в наш
# `/authorization/*` со shared `SERVICE_API_KEY` Bearer + `X-Service-Identity`.
# Пока не приехали per-service API keys, заголовок информационный — но
# allow-list ниже не даёт скомпрометированному pod'у forge'нуть identity вне
# набора. Mismatch'и логируются WARNING'ом (`dependencies/auth.require_service_token`).
#
# Добавляй сюда новый сервис, когда он начинает звать auth_service.
# Значения должны точно совпадать с тем, что caller'ы шлют в `X-Service-Identity`.
KNOWN_SERVICE_IDENTITIES: frozenset[str] = frozenset({
    "loging_service",
    "server_service",
    "config_service",
    # secret_service ходит в auth_service за introspect токенов и сам auth_service
    # ходит в secret_service за lifecycle-callback'ами под тем же identity'ем —
    # без allow-list'а strict-режим резал бы такой trip с 401.
    "secret_service",
})
