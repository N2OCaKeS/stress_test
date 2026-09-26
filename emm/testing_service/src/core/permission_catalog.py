"""Человеческие описания сущностей и действий матрицы прав.

Состав (какие сущности и какие действия существуют) задаёт
:data:`src.core.constants.ENTITY_ACTIONS` — это источник истины. Здесь только
текстовые описания плюс пометка «чувствительное», которая отдаётся
read-эндпоинтом каталога для UI и ИБ-обзора.

Описания обязаны покрывать каждую пару `(entity_type, action)` из
`ENTITY_ACTIONS` — недостающее описание ловит smoke-тест каталога
(`tests/test_permissions_matrix.py`).
"""

from __future__ import annotations

from src.core.constants import Action, EntityType

ENTITY_DESCRIPTIONS: dict[str, str] = {
    EntityType.GLOBAL_VARIABLE: (
        "Платформенный каталог переменных конструктора команд теста (РЦ, "
        "стенд, ядро, режим и т.д.). Чтение открыто любому аутентифицированному "
        "актору, под матрицей — запись."
    ),
    EntityType.TEST_DEFINITION: (
        "Каталог тестов вместе со слотами конструктора команд. Чтение открыто, "
        "под матрицей — создание/изменение/удаление теста и его слотов."
    ),
    EntityType.TEST_STAND: (
        "Тестовый стенд — надстройка над сервером/ВМ из server_service под "
        "очередь тестов. Отдельное действие поверх обычного view — раскрытие "
        "кредов учётки исполнения теста."
    ),
    EntityType.DEPARTMENT_TEST_SETTINGS: (
        "Настройки тестирования отдела: retry-политика, имя тестового "
        "пользователя, расписание HR-отчёта, ожидание вердикта из Zephyr и "
        "маппинг статусов Zephyr. Одна строка на отдел, upsert."
    ),
    EntityType.DEPARTMENT_TEST_ACCOUNT: (
        "Тестовая учётка отдела: логин, пароль и SSH-ключ пользователя "
        "исполнения теста на стендах (credential в secret_service). Чтение "
        "показывает логин и публичный ключ, запись меняет учётку со "
        "следующей подготовки стенда."
    ),
    EntityType.LAUNCH_PROFILE: (
        "Профиль запуска теста: текст starter.sh, клонирование, пути на "
        "стенде, команды запуска и остановки, pty, testenv. Правка создаёт "
        "новую версию; уже запущенные тесты остаются на своей."
    ),
    EntityType.PROVISIONING_PROFILE: (
        "Профиль подготовки стенда: какие упавшие юниты допустимы после "
        "перезагрузки, сколько раз перезагружать при degraded, PAM-правка."
    ),
    EntityType.TEST_RUN: (
        "Fleet-wide кампания прогона (РЦ+ядро+режим на весь выбранный пул "
        "стендов). Чтение открыто любому аутентифицированному актору, под "
        "матрицей — только создание."
    ),
    EntityType.STP_TEST_CASE: (
        "Каталог СТП — тест-кейсы Zephyr Scale, зеркалируемые в testing_service. "
        "Чтение открыто, под матрицей — запись."
    ),
    EntityType.STP_TEST_RUN: (
        "СТП-прогон (Zephyr test-run/execution). Заводится только через "
        "/stp/generate; чтение открыто, под матрицей — создание и публикация "
        "сводной таблицы статусов РЦ в Confluence (department-scoped)."
    ),
    EntityType.STP_CELL: (
        "Ячейка СТП — статус пары (stp_test_case × stp_test_run). Чтение "
        "открыто, под матрицей — только ручной override статуса; событийное "
        "обновление из очереди матрицу не спрашивает."
    ),
    EntityType.DEPARTMENT_INTEGRATION_SETTINGS: (
        "Настройки интеграции отдела с Jira/Zephyr/Confluence (credential_id "
        "и базовые URL'ы). Чтение открыто, запись под матрицей."
    ),
    EntityType.DEPARTMENT_REPORT_MEMBER: (
        "Сотрудник отдела, учитываемый в HR-отчёте по активности. Чтение "
        "открыто, под матрицей — запись."
    ),
    EntityType.DEPARTMENT_ACTIVITY_REPORT: (
        "Попытка генерации HR-отчёта отдела по активности. И чтение истории "
        "генераций, и ручной запуск — department-scoped операции, гейтятся "
        "`require_department_action` (department_admin-bypass + матрица)."
    ),
    EntityType.PERMISSION: (
        "Сама матрица прав: смотреть список грантов, выдавать и отзывать "
        "действия ролям."
    ),
    EntityType.STATISTICS_SETTINGS: (
        "Платформенные настройки внешнего сервиса пересчёта статистики "
        "(ветка `statistics`, один инстанс на всю платформу). Чтение открыто, "
        "под матрицей — запись настроек и ручной триггер пересчёта."
    ),
    EntityType.LEGACY_COMPAT: (
        "Публичный compat `/rest/api/*` для скриптов на стендах: "
        "подсети, из которых он доступен без авторизации, и отдел по "
        "умолчанию для URL Jira/Confluence. Платформенная настройка."
    ),
}

ACTION_DESCRIPTIONS: dict[str, str] = {
    Action.VIEW: "Видеть карточку или список.",
    Action.CREATE: "Создать запись.",
    Action.UPDATE: "Изменить поля записи.",
    Action.DELETE: "Удалить запись.",
    Action.VIEW_TEST_CREDENTIALS: (
        "Раскрыть пароль/приватный SSH-ключ учётки исполнения теста на стенде. "
        "Живая отладка во время/после прогона, человеку, не воркеру."
    ),
    Action.PERMISSION_GRANT: "Выдать роли действие, добавив строку матрицы.",
    Action.PERMISSION_REVOKE: "Отозвать у роли действие.",
    Action.PUBLISH: "Опубликовать сводную страницу отчёта/матрицы в Confluence.",
}

# Чувствительные действия — раскрытие секретов. Аудит уровня CRITICAL; в
# системных дефолтных грантах несёт только admin, кастомным ролям — прицельно.
SENSITIVE_ACTIONS: frozenset[str] = frozenset({
    Action.VIEW_TEST_CREDENTIALS,
})

# testing_service не грантует internal-callback'и через entity_permissions —
# они закрыты отдельным `require_internal_caller` (shared-secret между
# сервисами), в матрицу не попадают. Набор пуст, но флаг остаётся в каталоге
# для паритета формы ответа с server_service/secret_service.
WORKER_ONLY_ACTIONS: frozenset[str] = frozenset()
