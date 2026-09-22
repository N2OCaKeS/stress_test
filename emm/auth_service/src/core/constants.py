"""Общие enum-ы и константы (статусы юзеров, роли, префиксы токенов и т.п.)."""

from enum import StrEnum


class UserStatus(StrEnum):
    """Статусы юзера. ACTIVE — обычное состояние, BLOCKED/BANNED — отбивают login."""
    ACTIVE = "active"
    BLOCKED = "blocked"
    BANNED = "banned"


class PlatformRole(StrEnum):
    """Platform-уровень: роли, которые управляет только auth_service.

    `department_admin` и `loging_reader_dep` — per-dept (всегда вместе с
    `department_id`), остальные три cross-platform (department_id=NULL
    допустим). Админство в отдельных сервисах (например `admin` в
    secret_service) живёт в service_roles, а не в platform_role: оно
    per-(dept, service) и не даёт cross-dept привилегий.
    """
    ACCOUNT_ADMIN = "account_admin"
    DEPARTMENT_ADMIN = "department_admin"
    LOGING_ADMIN = "loging_admin"
    # Read-only роль для loging_service — параметризованный тест проходит по
    # всем значениям enum'а, бизнес-логика рулится через guard'ы.
    LOGING_READER = "loging_reader"
    # Dept-scoped аудит-читатель: видит события только своего отдела. В отличие
    # от платформенного `loging_reader` (который параметрически тоже dept-scoped,
    # но создаётся под account_admin'ом и обязан нести department_id уже на
    # create) эту роль может выдавать department_admin своим юзерам в своём
    # отделе. Всегда требует department_id (ведёт себя как department_admin).
    LOGING_READER_DEP = "loging_reader_dep"


class ServiceRole(StrEnum):
    """Системные per-service роли, которые сеются в каталог каждого отдела при
    выдаче доступа к сервису (`is_system=True`).

    Только `guest` и `admin` сеются автоматически и защищены от изменения.
    Остальные роли (`reader`, `operator` и любые доменные) — это кастомные
    определения, которые отдел заводит сам; они не перечислены здесь и
    валидируются по каталогу `service_role_definitions`, а не по этому enum'у.
    """
    GUEST = "guest"
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
OAUTH_REFRESH_TOKEN_PREFIX = "dbos_oauth_rt_"

# Сколько символов raw-токена кладём в `token_prefix`-колонку для быстрого
# lookup'а до проверки SHA-256 (хватает на `dbos_pat_` / `dbos_bot_` + три
# первых символа secret'а).
TOKEN_PREFIX_LEN = 12

# Максимальный TTL для любых выдаваемых токенов (PAT и bot-token) — 6 месяцев
# (180 дней). Используется и как дефолт для bot-токена, когда caller не передал
# `expires_at`, и как верхняя граница валидации `expires_at` на обоих эндпоинтах.
# Поменять — в одном месте.
MAX_TOKEN_TTL_DAYS = 180
MAX_TOKEN_TTL_SECONDS = MAX_TOKEN_TTL_DAYS * 24 * 3600  # 15_552_000

# Алиас для обратной совместимости. Раньше использовался как дефолт, когда
# caller не передавал `expires_at`; теперь `expires_at` обязателен для bot-
# токенов, и константа служит только как «верхняя граница TTL» под старым
# именем (есть тест на конкретное значение). Новый код использует
# `MAX_TOKEN_TTL_SECONDS`.
BOT_TOKEN_TTL_SECONDS = MAX_TOKEN_TTL_SECONDS


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


# ── Bootstrap: платформенные сервисы и infra-боты ────────────────────────────
# Канонический список платформенных сервисов, которые auth_service
# регистрирует на старте (см. `services/bootstrap_service.py`). Порядок в
# кортеже = порядок сидирования. Идемпотентно: уже существующие записи
# (заведённые вручную или прошлым стартом) не трогаются, description не
# перетирается. secret_service обязателен — без него grant отдела на secret
# падает 404.
PLATFORM_SERVICES: tuple[tuple[str, str], ...] = (
    ("auth_service", "Аутентификация и управление аккаунтами"),
    ("loging_service", "Аудит и журналирование событий"),
    ("server_service", "Инвентаризация и управление серверами"),
    ("server_worker", "Воркер задач IPMI/SSH"),
    ("secret_service", "Хранилище токенов и учётных данных"),
    ("testing_service", "Каталог тестов, очередь запуска, СТП (allta_app)"),
)

# Системный отдел под infra-ботов (воркер и т.п.). Заводится на старте, если
# отсутствует; имя стабильно, чтобы повторный старт не плодил дубли.
SYSTEM_DEPARTMENT_NAME = "DBOS System"

# Бот воркера: имя, сервис и роль. Токен берётся из `WORKER_BOT_TOKEN` (env);
# роль `worker_bot@server_service` открывает доступ к internal-эндпоинтам
# server_service, которые дёргает server_worker.
WORKER_BOT_NAME = "server_worker"
WORKER_BOT_SERVICE = "server_service"
WORKER_BOT_ROLE = "worker_bot"

# Бот testing_service: нужен, чтобы резолверы choices_source (`dynamic:
# os_versions`/`dynamic:kernels`) могли читать каталог версий ОС у
# server_service — тот эндпоинт открыт любому аутентифицированному актору,
# специальной роли не требует, поэтому системного `guest@server_service`
# достаточно (не заводим отдельную custom-роль, как у worker_bot — там нужен
# был доступ к internal-эндпоинтам, здесь только обычное чтение каталога).
#
# Второй грант — `guest@secret_service`: даёт боту identity-роль в
# secret_service, нужную для будущего `POST /credentials/{id}/reveal`
# резолва department_integration_settings-кред (Jira/Zephyr/Confluence),
# каждая из которых принадлежит СВОЕМУ отделу (owner_dept_id = department_id
# отдела, который её завёл), не системному. Бот живёт в системном отделе —
# межведомственный механизм, которым secret_service разрешит ЧУЖОМУ отделу
# (системному) читать такую креду, на момент этой волны ЕЩЁ НЕ
# СПРОЕКТИРОВАН (кандидат — существующий `cross_department`/`DeptGrant`,
# но форма не зафиксирована владельцем); это отдельная будущая задача на
# secret_service. Роль `guest` сама по себе даёт в тип-wide матрице
# только `read` (метаданные); `reveal` открывается именно per-credential
# `RoleACL`. Заводить боту более широкую роль (`admin`) было бы избыточной
# привилегией — он не должен видеть чужие department секреты за пределами
# явно выданных грантов.
TESTING_SERVICE_BOT_NAME = "testing_service"
TESTING_SERVICE_BOT_SERVICE = "server_service"
TESTING_SERVICE_BOT_ROLE = "guest"
TESTING_SERVICE_BOT_SECRET_SERVICE = "secret_service"

# Бот allta_app_service: резолвит iLO/BMC-креды по номеру стенда через
# server_service вместо чтения локального файла (`/home/u/ilo.py`). Роль
# `allta_bridge` — узкая, заводится и наполняется правами на стороне
# server_service (не auth_service): `(server, view)` для резолва
# номер→server_id и `(ipmi_controller, view_credentials)` для чтения BMC-
# credentials через internal-эндпоинт. Имя роли должно совпадать буквально
# с тем, что заведено в server_service.
ALLTA_APP_SERVICE_BOT_NAME = "allta_app_service"
ALLTA_APP_SERVICE_BOT_SERVICE = "server_service"
ALLTA_APP_SERVICE_BOT_ROLE = "allta_bridge"
