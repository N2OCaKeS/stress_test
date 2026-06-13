"""Человеческие описания сущностей и действий матрицы прав.

Состав (какие сущности и какие действия существуют) задаёт
:data:`src.core.constants.ENTITY_ACTIONS` — это источник истины. Здесь только
текстовые описания плюс пометки «чувствительное» и «служебный callback воркера»,
которые отдаются read-эндпоинтом каталога для UI и ИБ-обзора. Тексты совпадают
с разделом «Каталог сущностей и действий» ролевой модели.

Описания обязаны покрывать каждую пару `(entity_type, action)` из
`ENTITY_ACTIONS`; недостающее описание ловит smoke-тест каталога.
"""

from __future__ import annotations

from src.core.constants import Action, EntityType

ENTITY_DESCRIPTIONS: dict[str, str] = {
    EntityType.SERVER: (
        "Физический или тестовый сервер: инвентарь, питание, занятость под "
        "тест, привязка версии ОС."
    ),
    EntityType.SERVER_ACCOUNT: (
        "Учётная запись ОС на одном или нескольких серверах: логин, пароль, "
        "sudo, группы, shell, home. Одна личность с общим паролем на несколько "
        "хостов."
    ),
    EntityType.OS_VERSION: (
        "Каталог версий ОС вместе со списком репозиториев. Чтение публичное, "
        "под матрицей остаётся только запись."
    ),
    EntityType.IPMI_CONTROLLER: (
        "BMC/IPMI-контроллер сервера: управление питанием железа и креды "
        "доступа к BMC."
    ),
    EntityType.PERMISSION: (
        "Сама матрица прав: смотреть список грантов, выдавать и отзывать "
        "действия ролям."
    ),
    EntityType.TASK: (
        "Worker-таска (power, inventory, prepare, rotate и т.д.) в dev_server_worker.tasks. "
        "Под матрицей доступен только cancel — отмена pending/running задачи."
    ),
}

ACTION_DESCRIPTIONS: dict[str, str] = {
    Action.VIEW: "Видеть карточку или список.",
    Action.CREATE: "Создать запись.",
    Action.UPDATE: "Изменить поля записи.",
    Action.DELETE: "Удалить запись.",
    Action.BUSY_ACQUIRE: "Захватить сервер под тест или задачу.",
    Action.BUSY_RELEASE: "Освободить сервер.",
    Action.OS_SYNC: "Привязать или сменить версию ОС сервера вручную.",
    Action.POWER_ON: "Включить сервер через IPMI.",
    Action.POWER_OFF: "Жёстко выключить сервер через IPMI.",
    Action.POWER_REBOOT: "Перезагрузить сервер через IPMI.",
    Action.POWER_STATUS: "Опросить состояние питания.",
    Action.INVENTORY_TRIGGER: "Запустить инвентаризацию железа и/или ОС-пользователей.",
    Action.INVENTORY_SUBMIT: "Callback воркера с результатом инвентаризации.",
    Action.VIEW_DRIFT: (
        "Прочитать агрегированную сводку drift'ов аккаунтов на сервере "
        "(события server_account.drift_detected из loging)."
    ),
    Action.PREPARE_CALLBACK: (
        "Callback воркера: пометить сервер подготовленным после bootstrap."
    ),
    Action.VIEW_PASSWORD: "Увидеть расшифрованный пароль аккаунта в GET-карточке.",
    Action.ROTATE_PASSWORD: "Ротация пароля аккаунта по политике.",
    Action.GRANT_SUDO: "Создать или поднять аккаунт с правами sudo.",
    Action.PROVISION_ON_HOST: (
        "Callback воркера: результат useradd/usermod/userdel на боксе."
    ),
    Action.VIEW_CREDENTIALS: (
        "Доступ к credentials BMC: пароль в GET-карточке и метаданные "
        "controller'а user-facing'ом, плюс расшифрованные creds воркеру "
        "через internal endpoint."
    ),
    Action.ROTATE_CREDENTIALS: "Ротация пароля BMC.",
    Action.PERMISSION_GRANT: "Выдать роли действие, добавив строку матрицы.",
    Action.PERMISSION_REVOKE: "Отозвать у роли действие.",
    Action.CANCEL: (
        "Отменить pending/running worker-task'у. Pending пропускается "
        "перед запуском, running доживает текущий stage и не стартует следующий."
    ),
}

# Чувствительные действия — раскрытие/ротация секретов, управление питанием,
# выдача sudo. Аудит уровня CRITICAL; в дефолтных грантах operator'у не выдаются.
SENSITIVE_ACTIONS: frozenset[str] = frozenset({
    Action.VIEW_PASSWORD,
    Action.VIEW_CREDENTIALS,
    Action.ROTATE_PASSWORD,
    Action.ROTATE_CREDENTIALS,
    Action.GRANT_SUDO,
    Action.POWER_ON,
    Action.POWER_OFF,
    Action.POWER_REBOOT,
})

# Служебные гранты воркера: callback'и через internal endpoint'ы. Людям в норме
# не назначаются.
WORKER_CALLBACK_ACTIONS: frozenset[str] = frozenset({
    Action.INVENTORY_SUBMIT,
    Action.PROVISION_ON_HOST,
    Action.PREPARE_CALLBACK,
})
