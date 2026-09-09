"""Общие enum'ы и константы сервиса."""

from enum import StrEnum

SERVICE_NAME = "testing_service"

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
}


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


class ServiceRole(StrEnum):
    """Системные service-роли. Промежуточные роли заводятся кастомными."""

    GUEST = "guest"
    ADMIN = "admin"


SYSTEM_SERVICE_ROLES: frozenset[str] = frozenset({ServiceRole.GUEST, ServiceRole.ADMIN})


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
