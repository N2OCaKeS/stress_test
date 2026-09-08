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
        "Каталог версий ОС вместе со списком репозиториев и bootstrap-кредами. "
        "Чтение публичное, под матрицей остаются запись и раскрытие пароля."
    ),
    EntityType.SERVER_CATEGORY: (
        "Каталог категорий серверов по мощности (LowServer / MiddleServer / "
        "HighServer / WorkStation и далее). Платформенный, без привязки к "
        "отделу. Чтение открыто аутентифицированным, под матрицей — запись."
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
    EntityType.VM: (
        "Виртуальная машина на hub-сервере: создание, питание, бронь под тест, "
        "диски и снимки. Подготовка сервера как VMS-hub — тоже действие этой зоны."
    ),
    EntityType.BOX: (
        "Бокс-заготовка (образ) для создания ВМ: формат, источник скачивания, "
        "предустановленный пользователь и список ОС/снимков на диске. Пер-"
        "департамент каталог."
    ),
    EntityType.HOST_SERVICE: (
        "SSH-доступ к отдельскому хосту и список systemd-юнитов, которые "
        "отдел решил выставить на нём (host-service control). Пер-департамент, "
        "без платформенного дефолта."
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
    Action.CONSOLE: (
        "Открыть интерактивную SSH-консоль (PTY) к подготовленному серверу "
        "через WebSocket. Каждая введённая команда логируется в аудит."
    ),
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
    Action.ADOPT_FROM_HOST: (
        "Принять факт-состояние OS-пользователя с конкретного хоста в БД "
        "(пополевно, по drift'у). Обновляет только БД, без fan-out на серверы."
    ),
    Action.MANAGE_IGNORED_LOGINS: (
        "Управлять ignore-list'ом логинов отдела: добавлять и снимать логины, "
        "которые инвентаризация не должна показывать как незнакомых "
        "пользователей."
    ),
    Action.VIEW_CREDENTIALS: (
        "Доступ к credentials BMC: пароль в GET-карточке и метаданные "
        "controller'а user-facing'ом, плюс расшифрованные creds воркеру "
        "через internal endpoint."
    ),
    Action.ROTATE_CREDENTIALS: "Ротация пароля BMC.",
    Action.VIEW_MANAGEMENT_CREDENTIALS: (
        "Получить расшифрованные управляющие креды сервера (приватный SSH-ключ "
        "и пароль пользователя dbos) воркеру через internal endpoint. Воркер "
        "тянет их перед каждой managed-операцией."
    ),
    Action.PERMISSION_GRANT: "Выдать роли действие, добавив строку матрицы.",
    Action.PERMISSION_REVOKE: "Отозвать у роли действие.",
    Action.PROVISION: (
        "Завести OS-пользователя на сервере (useradd). Гейтит dispatch "
        "provision'а; выдаётся ролью."
    ),
    Action.DEPROVISION: (
        "Удалить OS-пользователя с сервера (userdel). Гейтит dispatch "
        "deprovision'а; выдаётся ролью."
    ),
    Action.CANCEL: (
        "Отменить pending/running worker-task'у. Pending пропускается "
        "перед запуском, running доживает текущий stage и не стартует следующий."
    ),
    Action.MANAGE_PACKAGES: (
        "Массово ставить, удалять и обновлять пакеты на серверах через worker "
        "по SSH (apt-get/dnf/apk под sudo). Деструктив на боксе — поверх view."
    ),
    Action.VMS_HUB_PREPARE: (
        "Подготовить сервер как VMS-hub (libvirt/kvm, мост, пул образов) — "
        "dispatch задачи воркеру. Действие зоны vm, таргетит сервер."
    ),
    Action.VM_POWER: (
        "Управлять питанием ВМ: start/shutdown/reboot/reset/destroy через worker."
    ),
    Action.VM_RESERVE: "Забронировать ВМ под тест (run test / debug test / свой логин).",
    Action.VM_RELEASE: "Снять бронь с ВМ (status → free).",
    Action.VM_DISK_MANAGE: "Управлять дисками ВМ: создать/подключить/отключить/resize.",
    Action.VM_SNAPSHOT_MANAGE: "Управлять снимками ВМ: создать/удалить/откатить.",
    Action.VM_PREPARE: "Забутстрапить управление на ВМ (mgmt-креды, per-VM).",
    Action.VM_ASTRA_UPDATE: "Обновить ОС ВМ до версии каталога (astra-update).",
    Action.VM_ALLTA_UPDATE: "Обновить guest-allta на ВМ по всем снимкам.",
    Action.VM_PASSWD: "Сменить пароль пользователя ВМ (перекатка снимков).",
    Action.VM_NET_MANAGE: "Управлять сетью ВМ и IP-пулами (IPAM).",
    Action.VM_PRESET_MANAGE: "Управлять пресетами стандартных ВМ (vm_preset).",
    Action.ACS_SNAPSHOT: (
        "Управлять снимками диска сервера через ACS (Clonezilla): смотреть "
        "список, создавать новые (save-disk), восстанавливать (restore-backup — "
        "полная перезапись диска, необратимо). Один action на все три; "
        "тип-wide only."
    ),
    Action.HOST_SERVICE_MANAGE: (
        "Настраивать host-service control своего отдела: SSH-подключение к "
        "хосту (host/port/user/приватный ключ) и список systemd-юнитов."
    ),
    Action.HOST_SERVICE_CONTROL: (
        "Старт/стоп/рестарт одного systemd-юнита своего отдела на хосте по SSH."
    ),
    Action.VIEW_TEST_CREDENTIALS: (
        "Получить учётку исполнения теста стенда (`GET /servers/{id}/"
        "test-credentials`) — метаданные всегда, пароль и приватный SSH-ключ "
        "при `?reveal=true`. Живая отладка стенда во время/после прогона теста, "
        "человеку, не воркеру."
    ),
}

# Чувствительные действия — раскрытие/ротация секретов, управление питанием,
# выдача sudo. Аудит уровня CRITICAL; в системных дефолтных грантах их несёт
# только admin (плюс точечные worker_bot-гранты), кастомным ролям — прицельно.
SENSITIVE_ACTIONS: frozenset[str] = frozenset({
    Action.VIEW_PASSWORD,
    Action.VIEW_CREDENTIALS,
    Action.ROTATE_PASSWORD,
    Action.ROTATE_CREDENTIALS,
    Action.GRANT_SUDO,
    Action.POWER_ON,
    Action.POWER_OFF,
    Action.POWER_REBOOT,
    # Живой shell на боксе под управляющим пользователем с sudo — самый
    # широкий доступ к серверу.
    Action.CONSOLE,
    # Изменение состава пакетов на боксе под sudo — деструктив на сервере.
    Action.MANAGE_PACKAGES,
    # Раскрытие управляющего приватного ключа и пароля сервера — широкий доступ
    # к боксу под sudo.
    Action.VIEW_MANAGEMENT_CREDENTIALS,
    # Снятие снимка останавливает сервер, восстановление полностью
    # перезаписывает диск — оба необратимы для данных на боксе.
    Action.ACS_SNAPSHOT,
    # Старт/стоп/рестарт живого сервиса на отдельском хосте по SSH — тот же
    # тир, что POWER_*/CONSOLE. host_service_manage (CRUD SSH-конфига/списка
    # юнитов) sensitive не помечен — это department-internal настройка,
    # аудируется INFO, не действие над живой инфраструктурой.
    Action.HOST_SERVICE_CONTROL,
    # Раскрытие пароля/приватного ключа учётки исполнения теста — тот же
    # тир, что VIEW_PASSWORD/VIEW_MANAGEMENT_CREDENTIALS.
    Action.VIEW_TEST_CREDENTIALS,
})

# Служебные гранты воркера: callback'и через internal endpoint'ы. Людям в норме
# не назначаются.
WORKER_CALLBACK_ACTIONS: frozenset[str] = frozenset({
    Action.INVENTORY_SUBMIT,
    Action.PROVISION_ON_HOST,
    Action.PREPARE_CALLBACK,
})

# Действия, которые в матрице прав человеку показывать не нужно: callback'и
# воркера плюс служебный pull управляющих кред сервера (privkey + пароль dbos).
# Всё это идёт через internal endpoint'ы и грантуется только worker_bot'у —
# флаг `worker_only` каталога считается по этому набору, чтобы UI скрывал такие
# действия из матрицы.
WORKER_ONLY_ACTIONS: frozenset[str] = WORKER_CALLBACK_ACTIONS | frozenset({
    Action.VIEW_MANAGEMENT_CREDENTIALS,
})
