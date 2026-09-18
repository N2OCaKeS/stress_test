"""Общие enum'ы и константы сервиса."""

from enum import StrEnum

SERVICE_NAME = "testing_service"


class TestReadiness(StrEnum):
    READY = "ready"
    REVIEW = "review"
    BROKEN = "broken"
    DEVELOPMENT = "development"


class TestMode(StrEnum):
    """Режим безопасности Astra, под которым тест исполняется.

    Фиксируется на карточке теста при заведении в каталог, а не выбирается
    тем, кто запускает тест или кампанию — какой режим у теста, решает
    владелец теста заранее. `server_worker` переключает режим стенда на
    это значение перед прогоном (шаг mode_switch в prepare-for-test).
    """

    OREL = "orel"
    SMOLENSK = "smolensk"

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
    # открыто любому аутентифицированному актору, под матрицей — create и
    # publish (сводная HTML-таблица статусов в Confluence, department-scoped).
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

    # Платформенные настройки внешнего сервиса пересчёта статистики (ветка
    # `statistics` этого же монорепо, §2.7/§9.3 плана миграции) — один
    # инстанс на всю платформу, не per-department, тот же паттерн, что
    # `acs_settings` в server_service. Чтение открыто любому аутентифи-
    # цированному актору, под матрицей — запись настроек и ручной триггер
    # пересчёта (`POST /statistics/recalculate`) для одиночных тестов.
    STATISTICS_SETTINGS = "statistics_settings"


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
    # Публикация сводной страницы (СТП-матрица) в Confluence — department-scoped
    # операция поверх уже существующих stp_test_runs, отдельная от create.
    PUBLISH = "publish"


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
        Action.CREATE, Action.PUBLISH,
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
    EntityType.STATISTICS_SETTINGS: frozenset({
        Action.VIEW, Action.UPDATE,
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

    `skipped` — терминальное, наравне с `succeeded`/`failed`: тест прерван
    оператором и не досчитан, но слот очереди освобождается сразу и стенд
    продолжает со следующего `queued`. Исхода у такого элемента нет, поэтому
    в СТП/Zephyr он ничего не пишет.

    `paused` — не терминальное и не исполняющееся: тест прерван, но исход не
    зафиксирован, элемент ждёт явного `resume-queue`. Стенд при этом стоит —
    `claim_next`/`get_next_queued_for_stand` такой элемент не видят, а бронь
    за стендом сохраняется.

    `prepared` — терминальное, наравне с `succeeded`/`failed`/`skipped`, но
    только для item'ов с `prepare_only=True`: стенд откачен и подготовлен
    (`prepare.sh` отработал), а вместо запуска теста воркер положил на стенд
    файл с командой, которой был бы запущен тест. Отдельный статус, чтобы не
    путать это с реальным прогоном в статистике/СТП.
    """

    QUEUED = "queued"
    PREPARING = "preparing"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    PAUSED = "paused"
    PREPARED = "prepared"


# Состояния, которые занимают место в очереди стенда — пока у стенда есть
# элемент в одном из них, следующий просто ждёт своей позиции.
ACTIVE_QUEUE_STATES: frozenset[str] = frozenset({
    QueueItemState.QUEUED, QueueItemState.PREPARING, QueueItemState.RUNNING,
    QueueItemState.READY, QueueItemState.PAUSED,
})


class QueueInterruptAction(StrEnum):
    """Что просили сделать с элементом, который прямо сейчас исполняется на стенде.

    Проставляется публичными `/skip` и `/pause` только для `running`-элемента
    — это сигнал `testing_worker`у («прерви, когда в следующий раз спросишь
    `interrupt-check`»), а не состояние само по себе. Сбрасывается в `None`,
    когда воркер отчитался о прерывании через `/completed`.
    """

    SKIP = "skip"
    PAUSE = "pause"


# Состояния, в которых на стенде уже идёт (или вот-вот пойдёт) физическая
# работа по конкретному элементу: подготовка стенда, ожидание воркера или сама
# SSH-сессия. Отличаются от ACTIVE_QUEUE_STATES тем, что `queued`/`paused`
# сюда не входят — они просто занимают место в очереди, цикл на них не крутится.
IN_FLIGHT_QUEUE_STATES: frozenset[str] = frozenset({
    QueueItemState.PREPARING, QueueItemState.READY, QueueItemState.RUNNING,
})


class QueueOrchestrationEventKind(StrEnum):
    """Причины, по которым диспетчер очереди не продвинулся (`queue_orchestration_events`).

    Закрытый список — не журнал произвольных сообщений, каждое значение
    отвечает на конкретный вопрос «что именно не дало тесту стартовать»:

    `stand_busy_blocked` — попытка занять/переключить стадию брони стенда
    (`acquire-for-service`/`service-status`) отбита конфликтом: стенд занят
    кем-то или чем-то вне testing_service (ручная бронь, ACS уже крутится по
    другой причине). Отличается от `prepare_request_failed` тем, что причина
    известна и понятна — ждать освобождения, а не чинить интеграцию.

    `prepare_request_failed` — вызов к server_service (`acquire-for-service`/
    `service-status`/`prepare-for-test`) провалился по любой другой причине:
    сеть, таймаут, 404, неожиданный ответ. В отличие от `stand_busy_blocked`
    это обычно требует внимания оператора/дежурного, не просто ожидания.

    `item_stuck_in_head` — головной item стенда (`preparing`/`ready`) не
    продвинулся дольше `QUEUE_STUCK_THRESHOLD_SECONDS` — самостоятельный
    признак зависания, даже если ни одна отдельная попытка не вернула ошибку
    (например, callback от server_service потерялся, или testing_worker не
    поллит).

    `claim_found_nothing_with_queue` — `claim_next_ready()` не взял ни одной
    строки, хотя `ready`-item с истёкшей выдержкой всё ещё существует —
    самый прямой признак рассинхрона: item должен был уйти воркеру, но
    почему-то остаётся на месте дольше, чем можно списать на обычную гонку
    `SKIP LOCKED` между конкурентными вызовами `claim`.
    """

    STAND_BUSY_BLOCKED = "stand_busy_blocked"
    PREPARE_REQUEST_FAILED = "prepare_request_failed"
    ITEM_STUCK_IN_HEAD = "item_stuck_in_head"
    CLAIM_FOUND_NOTHING_WITH_QUEUE = "claim_found_nothing_with_queue"


# Сколько item может провести в `preparing`/`ready` без прогресса, прежде чем
# это считается зависанием (`item_stuck_in_head`). 15 минут — с запасом
# выше типичного времени ACS revert + prepare.sh (минуты), но достаточно
# короткое, чтобы диагностика не отставала от оператора на часы.
QUEUE_STUCK_THRESHOLD_SECONDS = 15 * 60

# Ниже какой выдержки `ready`-item не считается рассинхроном сам по себе —
# обычная гонка `SKIP LOCKED` между конкурентными вызовами `claim` разрешается
# за миллисекунды, а не секунды.
CLAIM_DESYNC_GRACE_SECONDS = 30


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
    не потерянное молча. `skipped_no_rc_number` — на самой OS-версии не
    проставлен `rc_number` (ручная метка, легаси `"RC3"`); без неё заголовок
    блога заведомо не совпадёт с постом легаси, публиковать нечего. `failed`
    — интеграция не настроена, reveal не прошёл, либо сетевой сбой Confluence.
    """

    POSTED = "posted"
    SKIPPED_NO_BLOG = "skipped_no_blog"
    SKIPPED_NO_STP_PAGE = "skipped_no_stp_page"
    SKIPPED_NO_RC_NUMBER = "skipped_no_rc_number"
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


class StpCompositionScope(StrEnum):
    """Режим состава СТП для пары `(department_id, os_version_id)` (§D4/D5 плана миграции).

    Явный выбор администратора/пользователя (кнопки «Полный набор»/«По
    changelog»), НЕ вычисляется из вида строки версии (RC) — этого требует
    §D4 буквально: угадывание первого/последнего РЦ по последней цифре
    версии запрещено. `full` — все закреплённые за стендом тесты отдела,
    `changelog` — подмножество, затронутое changelog-сервисом (см.
    `services/stp.py::_filter_by_changelog`).
    """

    CHANGELOG = "changelog"
    FULL = "full"


class StpMatrixPublicationStatus(StrEnum):
    """Исход попытки публикации СТП-матрицы в Confluence (§D2/D3 плана миграции).

    `posted` — страница создана либо обновлена (либо тело не изменилось с
    прошлой публикации того же РЦ и Confluence не дёргали лишний раз).
    `skipped_not_configured` — у отдела не настроено пространство/родительская
    страница/credential для этой публикации. `skipped_no_test_runs` — для
    этого РЦ и отдела ещё нет ни одного `stp_test_run` (нечего публиковать).
    `failed` — reveal не прошёл либо сбой самого Confluence API.
    """

    POSTED = "posted"
    SKIPPED_NOT_CONFIGURED = "skipped_not_configured"
    SKIPPED_NO_TEST_RUNS = "skipped_no_test_runs"
    FAILED = "failed"


class StpAddTestOperationStatus(StrEnum):
    """Состояние операции добавления одного теста в СТП (§D6/D7 плана миграции).

    `pending` — заведена, но ещё не все шаги отработали (первый вызов в
    процессе либо предыдущая попытка упала раньше терминального статуса).
    `succeeded` — все четыре шага (testcase в Zephyr, добавление в ран,
    локальная ячейка, публикация СТП-матрицы) отработали. `failed` —
    какой-то шаг провалился, `last_error` несёт причину; уже пройденные шаги
    остаются отмеченными, повтор продолжает с первого непройденного.
    """

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class StpPullOperationStatus(StrEnum):
    """Исход попытки затянуть один Zephyr test-run из life в EMM (§D8 плана миграции).

    `pending` — операция заведена (первая попытка ещё не запускалась/не
    дошла до конца). `succeeded` — прогон и все его ячейки обработаны без
    сетевых сбоев (расхождения статуса — не сбой, см. `conflicts` в ответе
    импорта, они просто оставляют ячейку нетронутой). `failed` — сбой при
    обращении к Zephyr за деталями рана; повтор просто перечитывает его
    заново, никаких недоделанных локальных записей это не оставляет — само
    чтение из Zephyr идемпотентно, ничего в нём не пишется.
    """

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class StatisticsRecalcStatus(StrEnum):
    """Состояние фонового пересчёта статистики внешним сервисом (§2.7, §9.3 плана миграции).

    `idle` — пересчёт ещё ни разу не запускался (строки в БД нет). `running` —
    `POST /all-statistics` сейчас в процессе (может идти минутами — сам
    сервис статистики синхронный внутри себя). `succeeded`/`failed` —
    последняя попытка завершилась, `error` несёт текст причины на `failed`.
    """

    IDLE = "idle"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


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
