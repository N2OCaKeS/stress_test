"""Общие enum'ы и константы сервиса."""

from enum import StrEnum

SERVICE_NAME = "testing_service"


class TestReadiness(StrEnum):
    READY = "ready"
    REVIEW = "review"
    BROKEN = "broken"
    DEVELOPMENT = "development"

# Health/ready paths, исключаемые из rate-limit / audit / introspect.
HEALTH_PATHS: frozenset[str] = frozenset({
    "/api/testing/v1/health",
    "/api/testing/v1/ready",
})


class EntityType(StrEnum):
    """Типы сущностей для матрицы entity_permissions."""

    # Платформенный каталог динамических переменных конструктора команд.
    # Чтение открыто любому аутентифицированному актору (как os_version /
    # server_category в server_service), под матрицей — запись.
    GLOBAL_VARIABLE = "global_variable"

    # Каталог тестов + слоты конструктора команд теста. Слоты (`test_command_
    # args`) не заводят отдельную защищаемую сущность — редактирование команды
    # это часть редактирования самого теста, поэтому у них тот же entity_type
    # и action=update.
    TEST_DEFINITION = "test_definition"

    # Тестовые стенды — надстройка над Server/Vm из server_service.
    TEST_STAND = "test_stand"

    # Настройки тестирования отдела (retry/имя тестового пользователя/
    # расписание HR-отчёта). Одна строка на department_id, upsert — отдельного
    # create/delete действия нет.
    DEPARTMENT_TEST_SETTINGS = "department_test_settings"

    # Прогон (fleet-wide кампания под один РЦ+ядро+режим на весь выбранный
    # пул стендов, §2.4/§6.1 плана миграции). Чтение открыто любому
    # аутентифицированному актору (как test_definition/test_stand) — под
    # матрицей только создание.
    TEST_RUN = "test_run"

    # Каталог СТП — тест-кейсы Zephyr Scale (§2.5/§6 плана миграции). Чтение
    # открыто любому аутентифицированному актору, под матрицей — запись.
    STP_TEST_CASE = "stp_test_case"

    # СТП-прогон (Zephyr test-run/execution, НЕ то же самое, что `test_run`
    # выше — см. §6.1). Заводится только через `/stp/generate`, чтение —
    # открыто любому аутентифицированному актору, под матрицей — только create.
    STP_TEST_RUN = "stp_test_run"

    # Ячейка СТП — статус (stp_test_case × stp_test_run). Чтение открыто,
    # под матрицей — только ручной override статуса (`update`); событийное
    # обновление из очереди не проходит через матрицу (система, не человек).
    STP_CELL = "stp_cell"

    # Настройки интеграции отдела с Jira/Zephyr/Confluence (§2.4/§3.5 плана
    # миграции: credential_id + base URL'ы). Тот же паттерн, что
    # `department_test_settings` — чтение открыто, запись под матрицей.
    DEPARTMENT_INTEGRATION_SETTINGS = "department_integration_settings"

    # Сотрудник отдела, учитываемый в HR-отчёте по активности (§9.1 плана
    # миграции). Чтение открыто любому аутентифицированному актору, под
    # матрицей — запись, тот же паттерн, что `test_definition`/`test_stand`.
    DEPARTMENT_REPORT_MEMBER = "department_report_member"

    # Попытка генерации HR-отчёта отдела (§2.7/§9.1). В отличие от остальных
    # каталогов здесь и чтение (история генераций), и создание (ручной
    # запуск) — department-scoped операции над бизнес-данными конкретного
    # отдела, поэтому гейтятся `permissions.require_department_action`
    # (department_admin-bypass + матрица), не открытым чтением как у
    # платформенных каталогов.
    DEPARTMENT_ACTIVITY_REPORT = "department_activity_report"

    # Сама матрица прав: смотреть список грантов, выдавать и отзывать действия
    # ролям. Зеркалит одноимённый entity_type в server_service/secret_service.
    PERMISSION = "permission"


class Action(StrEnum):
    """Fine-grained actions матрицы entity_permissions.

    Допустимые пары (entity_type, action) перечислены в ENTITY_ACTIONS.
    """

    VIEW = "view"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    # Отдельное действие поверх обычного `view` стенда — раскрытие пароля/
    # SSH-ключа учётки исполнения теста (§5.3 плана миграции). Держится
    # отдельно от `view`, потому что это уже секрет, не паспортные данные.
    VIEW_TEST_CREDENTIALS = "view_test_credentials"
    # Управление самой матрицей entity_permissions — выдать/отозвать action у роли.
    PERMISSION_GRANT = "permission_grant"
    PERMISSION_REVOKE = "permission_revoke"


# Какие действия вообще осмысленны для каждого типа. Пара вне этой карты —
# ошибка данных: матрица наполняется миграциями и (позже) grant-эндпоинтом.
ENTITY_ACTIONS: dict[str, frozenset[str]] = {
    EntityType.GLOBAL_VARIABLE: frozenset({
        Action.VIEW, Action.CREATE, Action.UPDATE, Action.DELETE,
    }),
    EntityType.TEST_DEFINITION: frozenset({
        Action.VIEW, Action.CREATE, Action.UPDATE, Action.DELETE,
    }),
    EntityType.TEST_STAND: frozenset({
        Action.VIEW, Action.CREATE, Action.UPDATE, Action.DELETE,
        Action.VIEW_TEST_CREDENTIALS,
    }),
    EntityType.DEPARTMENT_TEST_SETTINGS: frozenset({
        Action.VIEW, Action.UPDATE,
    }),
    EntityType.TEST_RUN: frozenset({
        Action.CREATE,
    }),
    EntityType.STP_TEST_CASE: frozenset({
        Action.VIEW, Action.CREATE, Action.UPDATE, Action.DELETE,
    }),
    EntityType.STP_TEST_RUN: frozenset({
        Action.CREATE,
    }),
    EntityType.STP_CELL: frozenset({
        Action.UPDATE,
    }),
    EntityType.DEPARTMENT_INTEGRATION_SETTINGS: frozenset({
        Action.VIEW, Action.UPDATE,
    }),
    EntityType.DEPARTMENT_REPORT_MEMBER: frozenset({
        Action.VIEW, Action.CREATE, Action.UPDATE, Action.DELETE,
    }),
    EntityType.DEPARTMENT_ACTIVITY_REPORT: frozenset({
        Action.VIEW, Action.CREATE,
    }),
    EntityType.PERMISSION: frozenset({
        Action.VIEW, Action.PERMISSION_GRANT, Action.PERMISSION_REVOKE,
    }),
}


def is_valid_action(entity_type: str, action: str) -> bool:
    """True iff пара (entity_type, action) есть в whitelist'е ENTITY_ACTIONS."""
    allowed = ENTITY_ACTIONS.get(entity_type)
    return allowed is not None and action in allowed


class QueueItemState(StrEnum):
    """Состояния элемента очереди (§2.4, §5.5 плана миграции).

    `queued` → `preparing` (запрошен prepare-for-test у server_service) →
    `ready` (callback принёс креды, застэшены) → `running` (забрал
    testing_worker) → `succeeded`/`failed` — терминальные. Провал на
    `preparing`/`running` может породить retry-элемент (см.
    `services/queue.py`), сам провалившийся элемент всё равно уходит в
    `failed`.
    """

    QUEUED = "queued"
    PREPARING = "preparing"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


# Состояния, которые занимают место в очереди стенда — пока у стенда есть
# элемент в одном из них, следующий просто ждёт своей позиции.
ACTIVE_QUEUE_STATES: frozenset[str] = frozenset({
    QueueItemState.QUEUED, QueueItemState.PREPARING, QueueItemState.RUNNING,
    QueueItemState.READY,
})


class TestRunStatus(StrEnum):
    """Агрегатный статус кампании (§2.4, §6.1 плана миграции).

    Пересчитывается из состояний дочерних `queue_items` (см.
    `services/test_run_status.py`), сама кампания своё состояние не ведёт
    независимо. `queued` — кампания заведена, но ни один item ещё не
    появился (все выбранные стенды оказались без закреплённых тестов).
    `running` — есть хоть один нетерминальный item. `succeeded`/`failed` —
    все item'ы терминальны и все в одном исходе. `partially_failed` — все
    терминальны, но исходы разные.
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIALLY_FAILED = "partially_failed"


# Терминальные исходы кампании — триггерят попытку end-of-run комментария в
# Confluence (§2.7, §9.2, `services/run_summary.py`), вызываемую из
# `services/queue.py` там же, где пересчитывается агрегатный статус.
TERMINAL_TEST_RUN_STATUSES: frozenset[str] = frozenset({
    TestRunStatus.SUCCEEDED, TestRunStatus.FAILED, TestRunStatus.PARTIALLY_FAILED,
})


class RunSummaryCommentStatus(StrEnum):
    """Исход попытки публикации end-of-run комментария (§2.7, §9.2 плана миграции).

    `posted` — комментарий создан либо обновлён (либо не менялся с прошлого
    прогона и Confluence не дёргали лишний раз). `skipped_no_stp_page`/
    `skipped_no_blog` — легаси-поведение (тихий no-op), но видимое здесь, а
    не потерянное молча. `failed` — интеграция не настроена, reveal не
    прошёл, либо сетевой сбой Confluence.
    """

    POSTED = "posted"
    SKIPPED_NO_BLOG = "skipped_no_blog"
    SKIPPED_NO_STP_PAGE = "skipped_no_stp_page"
    FAILED = "failed"


class DepartmentActivityReportStatus(StrEnum):
    """Исход попытки генерации HR-отчёта по активности (§2.7, §9.1 плана миграции).

    `generating` — строка заведена, запрос источникам ещё в процессе (видно
    только если кто-то читает список ровно в этот момент — генерация
    синхронна в рамках одного HTTP-запроса). `done` — HTML-таблица собрана и
    опубликована на Confluence (частичный провал ОДНОГО источника данных не
    мешает `done`, см. `services/activity_report.py`). `failed` — интеграция
    отдела не настроена вовсе (`confluence_report_page_space` пуст) либо сбой
    самой публикации на Confluence.
    """

    GENERATING = "generating"
    DONE = "done"
    FAILED = "failed"


class StpCellStatus(StrEnum):
    """Статус ячейки СТП `(stp_test_case × stp_test_run)` (§2.5, §6.2 плана миграции).

    `not_run` — дефолт при создании ячейки (тест-ран заведён в Zephyr, тест
    ещё не запускался). `in_progress`/`pass`/`fail` — зеркало терминальных и
    промежуточных состояний `queue_items.state` (см. `services/stp_status.py`
    за точным маппингом `QueueItemState` → `StpCellStatus`).
    """

    NOT_RUN = "not_run"
    IN_PROGRESS = "in_progress"
    PASSED = "pass"
    FAIL = "fail"


class PlatformRole(StrEnum):
    """Платформенные роли, которые видит testing_service (`Identity.platform_role`).

    `DEPARTMENT_ADMIN`: `permissions.require_department_action` даёт ему
    bypass матрицы прав на бизнес-данных СВОЕГО отдела (тот же приём, что
    `server_service.permissions.require_host_service_action`); тот же bypass
    использует `permission_service` для управления матрицей своего отдела.

    `ACCOUNT_ADMIN`: платформенный мета-админ, без `department_id` и без
    сервисных ролей — обычные бизнес-эндпоинты testing_service ему в принципе
    недоступны (`get_current_identity` отбивает по `SERVICE_ACCESS_DENIED`,
    в allowed_services у него testing_service нет). Пропускается только на
    `/permissions*` через `PermissionMatrixIdentity` — там он мета-админ
    матрицы прав любого отдела, аналогично `server_service`. `loging_admin`/
    `loging_reader` собственной семантики в testing_service не несут.
    """

    ACCOUNT_ADMIN = "account_admin"
    DEPARTMENT_ADMIN = "department_admin"


class ServiceRole(StrEnum):
    """Системные service-роли. Промежуточные роли заводятся кастомными."""

    GUEST = "guest"
    ADMIN = "admin"


SYSTEM_SERVICE_ROLES: frozenset[str] = frozenset({ServiceRole.GUEST, ServiceRole.ADMIN})


def is_system_role(role: str) -> bool:
    """True iff `role` — системная (`admin`/`guest`) с неизменяемой матрицей."""
    return role in SYSTEM_SERVICE_ROLES


class GlobalVariableSource(StrEnum):
    """Откуда берётся значение переменной в момент резолва.

    `launch_context` — из снэпшота параметров запуска (RC/стенд/ядро/режим).
    `static` — фиксированное значение, заданное в самой переменной.
    `per_test_override` — значение задаётся слотом конкретного теста.
    `secret_service` — живой reveal-вызов в secret_service по credential_id.
    """

    LAUNCH_CONTEXT = "launch_context"
    STATIC = "static"
    PER_TEST_OVERRIDE = "per_test_override"
    SECRET_SERVICE = "secret_service"


class GlobalVariableValueType(StrEnum):
    """Тип значения переменной — подсказка UI и валидатору слота."""

    STRING = "string"
    INTEGER = "integer"
    BOOLEAN = "boolean"


class CommandArgKind(StrEnum):
    """Тип слота конструктора команд.

    `literal` — фиксированная строка, задаётся прямо в слоте (`literal_value`).
    `variable` — ссылка на глобальную переменную (`variable_id`), с
    опциональным per-test `override_value` поверх её обычного резолва.
    """

    LITERAL = "literal"
    VARIABLE = "variable"
