"""SSH-фасад над `clients.ssh.SshClient`.

Тонкий wrapper, который собирает `host`/`username`/`password` из
`credentials`-словаря (пришедшего из `server_service` internal endpoint'а)
и делегирует работу `SshClient`.

Зачем фасад, а не прямой импорт SshClient'а в handler'ах:

* Handler'ы (`tasks/inventory.py`, `tasks/passwords.py`) работают на
  уровне «передай credentials, получи facts / OK». Им не нужно знать
  про context manager / port — всё это инкапсулировано здесь.
* `inventory_facts_to_payload` мапит сырой `SshClient.get_inventory`-результат
  на flat-schema `InventoryCallbackRequest` server_service'а
  (lscpu/lsblk parse + os-release pick).
* Существующие тесты `tests/test_task_handlers.py` monkeypatch'ат
  `ssh_client.collect_inventory` / `ssh_client.set_account_password` —
  сохраняем эти точки.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import asyncssh

from src.clients.ssh import (
    MODE_ASTRA_OREL,
    MODE_ASTRA_SMOLENSK,
    MODE_ASTRA_VORONEZH,
    SshClient,
    SshError,
)
from src.core.config import get_settings
from src.services import server_service_client

logger = logging.getLogger(__name__)


def _import_management_key(material, host: str):
    """Привести материал управляющего приватного ключа к виду для `client_keys`.

    Per-server ключ прилетает из server_service plaintext-строкой (PEM). asyncssh
    в `client_keys` трактует голую строку как путь к файлу, поэтому PEM нужно
    импортировать в key-объект явно. Уже импортированный key-объект и путь к
    файлу пропускаем как есть. Битый PEM → `SshError(SSH_MANAGEMENT_KEY_INVALID)`,
    чтобы операция не сорвалась внутри asyncssh с невнятным сообщением.
    """
    if isinstance(material, str) and "PRIVATE KEY" in material:
        try:
            return asyncssh.import_private_key(material)
        except (asyncssh.KeyImportError, ValueError, TypeError) as exc:
            raise SshError(
                error_code="SSH_MANAGEMENT_KEY_INVALID",
                host=host,
                message=f"management private key import failed: {type(exc).__name__}",
            ) from exc
    return material


async def attach_management_creds(credentials: dict, server_id: str) -> dict:
    """Подтянуть per-server управляющие креды в `credentials` до сборки сессии.

    На управляемом сервере (`credentials['is_managed']`) приватный ключ
    управляющего пользователя и его пароль больше не берутся из глобального env
    воркера — они свои на каждом сервере и шифруются в server_service. Здесь мы
    just-in-time тянем их через internal-эндпоинт (`fetch_management_credentials`,
    тот же паттерн, что `fetch_account_password`) и кладём в `credentials`,
    откуда их читает `_build_session`.

    Кэш в рамках задачи: ключ уже лежит в `credentials['management_private_key']`
    (его положил prepare из mgmt_install или предыдущий вызов на том же
    credentials-словаре) — повторный fetch не делаем, чтобы не плодить reveal-
    аудит и round-trip'ы при нескольких сессиях к одному боксу.

    Неуправляемый сервер — no-op (self-сессия по паролю аккаунта, как раньше).
    """
    if not credentials.get("is_managed"):
        return credentials
    if credentials.get("management_private_key"):
        return credentials
    mgmt = await server_service_client.fetch_management_credentials(
        server_id, credentials.get("target_department_id"),
    )
    credentials["management_private_key"] = mgmt.get("ssh_private_key")
    # Пароль управляющего пользователя в auth-сессию воркера НЕ идёт (sudo на
    # ключевой сессии работает по NOPASSWD); храним его рядом для возможного
    # console-логина / fallback, но `_build_session` его не использует.
    if mgmt.get("password"):
        credentials["management_password"] = mgmt.get("password")
    if mgmt.get("management_user") and not credentials.get("management_user"):
        credentials["management_user"] = mgmt.get("management_user")
    return credentials


def apply_session_hints(credentials: dict, payload: dict) -> dict:
    """Прокинуть в credentials признаки управляющей сессии и адрес из payload.

    server_service кладёт в payload `is_managed` (прошёл ли сервер prepare) и
    `management_user` (на их основе `_build_session` выбирает между ключевой
    сессией под управляющим пользователем и self-сессией под аккаунтом), а
    также `host` / `ssh_port` — адрес сервера, чтобы не дёргать DNS-резолв из
    `server_id` каждый раз. `target_department_id` нужен `attach_management_creds`
    для cross-tenant-хинта при fetch'е per-server кред.

    Если адрес в credentials уже есть (например, fetch_account_password вернул
    `host`) — payload его не перетирает: credentials обычно несут более свежее
    значение от server_service. Если в payload полей нет (старый dispatch) —
    `_extract_host` будет fallback'иться на `server_id`.
    """
    # `is_managed` ставим всегда (включая False) — иначе при повторной обработке
    # credentials остался бы с предыдущим True, и self-сессия неожиданно стала
    # бы ключевой management-сессией. `_build_session` опирается на этот флаг
    # как на жёсткий инвариант, не на «truthy/missing».
    credentials["is_managed"] = bool(payload.get("is_managed"))
    if credentials["is_managed"]:
        management_user = payload.get("management_user")
        if management_user:
            credentials["management_user"] = management_user
    if not credentials.get("host"):
        host = payload.get("host") or payload.get("ssh_host")
        if host:
            credentials["host"] = host
    if not credentials.get("port") and not credentials.get("ssh_port"):
        port = payload.get("ssh_port") or payload.get("port")
        if port:
            credentials["ssh_port"] = port
    if not credentials.get("target_department_id"):
        dept = payload.get("target_department_id")
        if dept:
            credentials["target_department_id"] = dept
    return credentials


def _extract_host(credentials: dict, server_id: str) -> str:
    """Достать хост из credentials. Несколько fallback'ов:

    * `host` — каноничное имя поля;
    * `endpoint` / `endpoint_url` — для совместимости с IPMI-форматом;
    * `server_id` — последний fallback (для dev/test stand'ов, где
      имя сервера совпадает с DNS).

    Production должен прилетать из server_service в виде `host`, см.
    `services.servers.get_server_credentials_for_worker`.
    """
    for key in ("host", "ssh_host", "endpoint", "endpoint_url"):
        val = credentials.get(key)
        if val:
            return str(val)
    return server_id


def _extract_port(credentials: dict) -> int:
    port = credentials.get("port") or credentials.get("ssh_port")
    if not port:
        return 22
    try:
        return int(port)
    except (TypeError, ValueError):
        return 22


def _mask_bootstrap_login(login: str) -> str:
    """Замаскировать bootstrap-логин для DEBUG-лога.

    Имя одноразовой bootstrap-учётки — чувствительный артефакт первичного
    доступа на ещё не управляемый сервер; в лог пишем только префикс,
    хвост заменяем на `***`. Короткие имена (<=3 символов) маскируются
    целиком, иначе видно слишком много.
    """
    if not login:
        return "***"
    if len(login) <= 3:
        return "***"
    return f"{login[:3]}***"


def build_session(credentials: dict, server_id: str) -> SshClient:
    """Публичный alias для `_build_session` — для handler'ов, которые не
    делают штатный inventory / chpasswd через готовый ssh_client.*-вызов,
    а сами выполняют свои команды поверх сессии (например,
    `installed_packages.list` с pattern-specific dpkg-query/rpm-call'ами).

    Возвращает context-manager `SshClient`. На управляемом сервере (после
    prepare) — ключевая сессия под `management_user`; иначе — self-сессия
    под аккаунтом по паролю. Caller обязан предварительно прогнать
    `credentials` через `apply_session_hints(creds, payload)`.
    """
    return _build_session(credentials, server_id)


def _build_session(credentials: dict, server_id: str) -> SshClient:
    """Собрать `SshClient` под привилегированную операцию.

    Две сессии в зависимости от того, прошёл ли сервер `prepare`:

    * **управляемый** (`credentials['is_managed']` истинно) — заходим под
      управляющим пользователем (`management_user`, из payload или дефолтного
      `SSH_MANAGEMENT_USER`) по приватному ключу. Ключ свой на каждом сервере и
      приходит per-server: его кладёт в `credentials['management_private_key']`
      либо `attach_management_creds` (fetch из server_service), либо prepare
      (из mgmt_install со свежесгенерированной парой). Пароль аккаунта в сессию
      не идёт; sudo на ключевой сессии работает по NOPASSWD управляющего
      пользователя, настроенному на prepare.
    * **не управляемый** — старое поведение: сессия под самим аккаунтом
      (`login` + `password`), как было до онбординга.

    Если сервер помечен управляемым, но per-server ключ в `credentials` не
    подтянут (забыли вызвать `attach_management_creds`, либо fetch вернул
    пусто) — поднимаем понятный `SshError(SSH_MANAGEMENT_CREDS_UNAVAILABLE)`, не
    падая внутри asyncssh с невнятным сообщением.
    """
    host = _extract_host(credentials, server_id)
    port = _extract_port(credentials)

    if credentials.get("is_managed"):
        settings = get_settings()
        key_material = credentials.get("management_private_key")
        if not key_material:
            raise SshError(
                error_code="SSH_MANAGEMENT_CREDS_UNAVAILABLE",
                host=host,
                message=(
                    "server is managed but no per-server management key is "
                    "available (attach_management_creds not called or fetch "
                    "returned nothing)"
                ),
            )
        management_user = (
            credentials.get("management_user") or settings.ssh_management_user
        )
        client_key = _import_management_key(key_material, host)
        # host + management_user — это topology disclosure для management-сети.
        # В закрытой инфраструктуре с ACL по namespace это всё равно лишний шум
        # в INFO-журнале. Сам факт sessions виден из аудита (mgmt-session
        # выписывается отдельным событием), debug-уровень достаточен.
        logger.debug("ssh management session on %s as %s (key)", host, management_user)
        return SshClient(
            host=host,
            username=management_user,
            password=None,
            port=port,
            client_keys=[client_key],
        )

    username = credentials.get("login") or credentials.get("username") or "root"
    password = credentials.get("password")
    return SshClient(
        host=host,
        username=username,
        password=password,
        port=port,
    )


async def collect_inventory(credentials: dict, server_id: str) -> dict:
    """Собрать структурированный inventory сервера по SSH.

    `credentials` — `{login, password, host?, port?}` от
    `server_service_client.fetch_account_password` (с дополнительными
    SSH-полями, если они есть в кредах).

    Возврат — то, что отдал `SshClient.get_inventory`: dict с ключами
    `hostname`, `kernel`, `cpu`, `disks`, `os`, `pci`.

    Ошибки: `SshError` пробрасывается наверх, `_runner` ловит и
    помечает task'у failed / pending-for-retry. Connect / auth /
    timeout — отдельные `error_code`'ы.

    На управляемом сервере (`credentials['is_managed']`) сессия идёт под
    управляющим пользователем по ключу; иначе — под самим аккаунтом. Сбор
    inventory sudo не требует, но единый выбор сессии важен: после prepare
    self-аккаунт может уже не иметь рабочего пароля.
    """
    logger.info("ssh inventory on %s", _extract_host(credentials, server_id))
    await attach_management_creds(credentials, server_id)
    async with _build_session(credentials, server_id) as ssh:
        return await ssh.get_inventory()


async def collect_os_users(credentials: dict, server_id: str) -> dict:
    """Снять список OS-пользователей сервера по SSH.

    `credentials` — `{login, password, host?, port?}` от
    `server_service_client.fetch_account_password` (плюс SSH-поля, если есть).

    Возврат — то, что отдал `SshClient.get_os_users`: dict с ключами
    `passwd`, `group`, `login_defs` (каждый — `_capture_text`-результат).
    Парсинг и UID-фильтр делает `os_users_facts_to_payload`.

    Ошибки: `SshError` пробрасывается наверх, `_runner` ловит.

    На управляемом сервере (`credentials['is_managed']`) сессия идёт под
    управляющим пользователем по ключу; иначе — под самим аккаунтом.
    """
    logger.info("ssh user inventory on %s", _extract_host(credentials, server_id))
    await attach_management_creds(credentials, server_id)
    async with _build_session(credentials, server_id) as ssh:
        return await ssh.get_os_users()


async def set_account_password(
    credentials: dict, server_id: str, login: str, new_password: str,
) -> dict:
    """Сменить пароль `login` на удалённом хосте через `sudo chpasswd`.

    `credentials` — те же поля, что и для `collect_inventory`. `login`
    может отличаться от `credentials.login` — например, текущая сессия
    под `ops`, а ротируем пароль `root`'а.

    Возврат — `{rotated: True}` для совместимости с историческим mock'ом
    (используется только handler'ом для post-processing'а; ничего
    sensitive не уходит наружу).

    Ошибки: `SshError` пробрасывается. Connection / auth / chpasswd
    failure — разные `error_code`'ы.

    На управляемом сервере (`credentials['is_managed']`) сессия идёт под
    управляющим пользователем по ключу (chpasswd через sudo); иначе — под
    самим аккаунтом по паролю.
    """
    # Ротация обязана выставить реальный секрет. Пустой пароль здесь — это
    # баг вызывающей стороны (потерянный stash, пустой fetch), а не
    # passwordless-провижн: если бы он дошёл до chpasswd, тот упал бы с
    # `missing new password`, а set_password ниже сделал бы тихий no-op и
    # storage разъехался бы с хостом. Отбиваем явно, чтобы оператор увидел
    # причину, а не «ничего не произошло».
    if not new_password:
        raise SshError(
            error_code="SSH_EMPTY_PASSWORD",
            host=_extract_host(credentials, server_id),
            cmd_sanitized=f"chpasswd <{login}>",
            message=(
                "refusing to rotate to an empty password; the account has no "
                "password to rotate on the host"
            ),
        )

    # Обычный chpasswd-login считаем не-чувствительным (в отличие от
    # bootstrap-login'а, см. `_mask_bootstrap_login`): admin / db / app —
    # стандартные имена, не подсказка для атакующего. Если политика
    # потребует маскировать, ввести `_mask_for_log(login)` тут симметрично
    # bootstrap-пути.
    logger.info("ssh chpasswd %s on %s", login, _extract_host(credentials, server_id))
    await attach_management_creds(credentials, server_id)
    async with _build_session(credentials, server_id) as ssh:
        await ssh.set_password(login, new_password)
    return {"rotated": True}


async def provision_user(
    credentials: dict,
    server_id: str,
    *,
    login: str,
    new_password: str | None = None,
    groups: list[str] | None = None,
    has_sudo: bool = False,
    shell: str | None = None,
    home_dir: str | None = None,
    public_key: str | None = None,
    force_replace: bool = False,
) -> dict:
    """Завести OS-пользователя `login` на удалённом хосте (`useradd`).

    `credentials` — те же поля, что у `set_account_password` (host/port
    + login/password для self-сессии). На управляемом сервере
    (`credentials['is_managed']`) сессия идёт под управляющим пользователем по
    ключу с sudo; иначе — под самим аккаунтом. Заводим всегда `login`.

    Если задан `public_key` — после useradd пишем его в
    `~/.ssh/authorized_keys` аккаунта; `force_replace=True` затирает
    существующий файл (re-provision после переустановки ОС), иначе ключ
    добавляется идемпотентно (grep по точному совпадению).

    Idempotent: уже существующий пользователь синхронизируется, не падает.

    Возврат — `{provisioned: True}`. Ошибки — `SshError`.
    """
    logger.info("ssh useradd %s on %s", login, _extract_host(credentials, server_id))
    await attach_management_creds(credentials, server_id)
    async with _build_session(credentials, server_id) as ssh:
        await ssh.create_user(
            login,
            new_password=new_password,
            groups=groups,
            has_sudo=has_sudo,
            shell=shell,
            home_dir=home_dir,
            public_key=public_key,
            force_replace=force_replace,
        )
    return {"provisioned": True}


async def apply_authorized_key(
    credentials: dict,
    server_id: str,
    *,
    login: str,
    public_key: str,
    force_replace: bool = False,
) -> dict:
    """Прописать `public_key` в `~/.ssh/authorized_keys` существующего юзера.

    Используется `account.update_on_host`, когда у аккаунта сменился (или
    впервые появился) SSH-ключ: provision кладёт ключ при заведении юзера,
    а update должен донести смену ключа на уже заведённого. Запись
    идемпотентна и помечает наш ключ managed-маркером — ротация заменяет
    именно его, ручные ключи оператора не трогаются. `force_replace=True`
    перезаписывает файл целиком (re-provision после переустановки ОС).

    На управляемом сервере (`credentials['is_managed']`) сессия идёт под
    управляющим пользователем по ключу с sudo; иначе — под самим аккаунтом.
    Возврат — `{key_applied: True}`. Ошибки — `SshError`.
    """
    logger.info(
        "ssh authorized_keys %s on %s", login, _extract_host(credentials, server_id),
    )
    await attach_management_creds(credentials, server_id)
    async with _build_session(credentials, server_id) as ssh:
        await ssh._write_authorized_key(
            login, public_key, force_replace=force_replace,
        )
    return {"key_applied": True}


async def modify_user(
    credentials: dict,
    server_id: str,
    *,
    login: str,
    groups: list[str] | None = None,
    has_sudo: bool = False,
    shell: str | None = None,
) -> dict:
    """Синхронизировать атрибуты пользователя `login` (`usermod`).

    Меняет shell и состав групп (sudo доклеивается при `has_sudo`). Пароль не
    трогает. На управляемом сервере (`credentials['is_managed']`) сессия идёт
    под управляющим пользователем по ключу с sudo; иначе — под самим аккаунтом.
    Возврат — `{modified: True}`. Ошибки — `SshError`.
    """
    logger.info("ssh usermod %s on %s", login, _extract_host(credentials, server_id))
    await attach_management_creds(credentials, server_id)
    async with _build_session(credentials, server_id) as ssh:
        await ssh.modify_user(
            login, groups=groups, has_sudo=has_sudo, shell=shell,
        )
    return {"modified": True}


async def delete_user(
    credentials: dict,
    server_id: str,
    *,
    login: str,
    remove_home: bool = False,
) -> dict:
    """Удалить пользователя `login` на удалённом хосте (`userdel`).

    Idempotent: отсутствующий пользователь — не ошибка. `remove_home=True`
    сносит home. На управляемом сервере (`credentials['is_managed']`) сессия
    идёт под управляющим пользователем по ключу с sudo; иначе — под самим
    аккаунтом (нельзя удалить юзера, под которым залогинен — для этого и нужен
    управляющий пользователь). Возврат — `{deleted: True}`. Ошибки — `SshError`.
    """
    logger.info("ssh userdel %s on %s", login, _extract_host(credentials, server_id))
    await attach_management_creds(credentials, server_id)
    async with _build_session(credentials, server_id) as ssh:
        await ssh.delete_user(login, remove_home=remove_home)
    return {"deleted": True}


async def bootstrap_management_user(
    credentials: dict,
    server_id: str,
    *,
    management_user: str,
    public_key: str,
    modes: dict | None = None,
    management_private_key: str | None = None,
    management_password: str | None = None,
    harden_sshd: bool = False,
) -> dict:
    """Онбординг управления: детект редакции + завести юзера по пер-режимному конфигу.

    `credentials` — одноразовые bootstrap-креды (`{login, password, host?,
    port?}`): под ними SSH-сессия password-auth заходит на ещё
    не управляемый сервер. После prepare управление идёт под `management_user`
    по ключу, исходный пароль больше не нужен и нигде не сохраняется.

    `modes` — пер-режимный конфиг управляющей учётки из server_service:
    `{<mode>: {groups: [...], extra_create_commands: [...]}}` по всем четырём
    режимам. На боксе определяем редакцию ОС (`detect_management_mode`) и
    берём групп/команды именно её режима — детект делается здесь, потому что
    server_service до prepare редакцию не знает. Нет `modes` / нет ключа
    режима → пустые группы и команды (как обычный bootstrap).

    `public_key` / `management_private_key` / `management_password` — это
    per-server материал управляющей учётки, сгенерированный server_service'ом и
    переданный в prepare-stash (`mgmt_install`). Public кладётся в
    authorized_keys, password ставится управляющему пользователю через
    `chpasswd` (для console-логина оператором и как fallback к NOPASSWD-sudo),
    приватный нужен для анти-локаут-проверки входа по ключу перед хардингом.
    Если задан `management_private_key`, после установки ключа worker проверяет,
    что вход под `management_user` по нему реально работает; `harden_sshd=True`
    дополнительно выключает парольный SSH и root-login через drop-in — только
    после успешной проверки ключа.

    Idempotent: повторный prepare не падает на уже заведённом юзере / уже
    добавленном ключе. Возврат — `{prepared: True, management_user,
    management_mode}`. Ошибки — `SshError`.
    """
    host = _extract_host(credentials, server_id)
    username = credentials.get("login") or credentials.get("username") or "root"
    password = credentials.get("password")
    port = _extract_port(credentials)

    # Bootstrap-логин под одноразовыми кредами — это шумная диагностика, не
    # бизнес-событие; в INFO бьёт по громкости логов на массовом prepare'е.
    # Сам bootstrap-username не светим полностью даже в DEBUG: лог-стрим может
    # быть отправлен в external sink, а имя bootstrap-учётки облегчает атаку
    # на ещё не управляемый сервер. Префикс из 3 символов оставляем — этого
    # хватает, чтобы дебажить «не тот логин в payload'е».
    logger.debug(
        "ssh prepare management user %s on %s as %s",
        management_user, host, _mask_bootstrap_login(username),
    )
    async with SshClient(
        host=host,
        username=username,
        password=password,
        port=port,
    ) as ssh:
        # Сначала детект редакции — ещё под bootstrap-сессией, чтобы выбрать
        # пер-режимный набор групп/команд до заведения юзера.
        mode = await ssh.detect_management_mode()
        mode_cfg = (modes or {}).get(mode) or {}
        logger.info("prepare on %s detected management mode %s", host, mode)
        await ssh.bootstrap_management_user(
            management_user, public_key,
            groups=mode_cfg.get("groups") or [],
            extra_create_commands=mode_cfg.get("extra_create_commands") or [],
            management_private_key=management_private_key,
            management_password=management_password,
            harden_sshd=harden_sshd,
        )
    return {
        "prepared": True,
        "management_user": management_user,
        "management_mode": mode,
    }


async def sync_management_user(
    credentials: dict,
    server_id: str,
    *,
    management_user: str,
    public_key: str,
    modes: dict | None = None,
) -> dict:
    """Недеструктивно досинхронизировать управляющую учётку на managed-сервере.

    Вызывается из `management_user_sync` после изменения конфига управляющей
    учётки. Сервер уже подготовлен (`is_managed`), поэтому заходим под
    управляющим пользователем по ключу (key-session), детектим редакцию ОС и
    повторно прогоняем тот же идемпотентный bootstrap: досинхрон групп +
    extra_create_commands нужного режима + ключа. Деструктива нет — useradd на
    существующем юзере вырождается в usermod групп, ключ доклеивается без
    дублей.

    `harden_sshd` не делаем: sshd уже захардён на prepare, повторный хардинг и
    анти-локаут-проверка ключом тут лишние (мы уже зашли по этому ключу).

    Возврат симметричен `bootstrap_management_user`:
    `{synced: True, management_user, management_mode}`. Ошибки — `SshError`.
    """
    # Жёстко форсируем management key-session: сервер managed, входим под
    # управляющим пользователем по ключу. credentials собирает caller
    # (handler) с `is_managed=True` + host/port.
    creds = dict(credentials)
    creds["is_managed"] = True
    creds["management_user"] = management_user
    host = _extract_host(creds, server_id)
    logger.info("ssh management user sync on %s as %s", host, management_user)
    await attach_management_creds(creds, server_id)
    async with _build_session(creds, server_id) as ssh:
        mode = await ssh.detect_management_mode()
        mode_cfg = (modes or {}).get(mode) or {}
        await ssh.bootstrap_management_user(
            management_user, public_key,
            groups=mode_cfg.get("groups") or [],
            extra_create_commands=mode_cfg.get("extra_create_commands") or [],
            management_private_key=None,
            harden_sshd=False,
        )
    return {
        "synced": True,
        "management_user": management_user,
        "management_mode": mode,
    }


async def cutover_management_user(
    credentials: dict,
    server_id: str,
    *,
    old_management_user: str,
    new_management_user: str,
    public_key: str,
    modes: dict | None = None,
) -> dict:
    """Переименовать управляющую учётку: завести новую, проверить, снести старую.

    Cutover-переименование (`management_user_sync` при `rename_pending`, когда
    имя сменилось). Подготовленным (старым) управляющим пользователем заводим
    новую учётку, после подтверждённого входа под новой — удаляем старую со
    всеми данными. Шаги:

    1. Под СТАРЫМ управляющим пользователем (key-сессия, sudo NOPASSWD)
       идемпотентно заводим НОВУЮ учётку `new_management_user`: useradd + sudo
       NOPASSWD + management authorized_key + группы и extra_create_commands
       нужного режима. Переиспользуем `SshClient.bootstrap_management_user`
       поверх ключевой сессии (sshd-харден не трогаем — он уже сделан на
       prepare). Если новая уже заведена и рабочая — bootstrap вырождается в
       no-op (idempotent).
    2. Анти-локаут: открываем независимую key-сессию УЖЕ под новой учёткой и
       проверяем, что вход по management-ключу работает и sudo доступен. Если
       проверка не прошла — старую НЕ трогаем, поднимаем ошибку; сервер
       остаётся с рабочим старым управляющим пользователем.
    3. С НОВОЙ key-сессии (не из-под удаляемого) сносим старую учётку
       `userdel -r` со всеми данными.

    Переименование меняет только имя учётки — per-server ключ остаётся прежним,
    поэтому и под новым именем заходим тем же ключом. Сам ключ тянется
    `attach_management_creds` (fetch из server_service); если его нет —
    `_build_session` поднимет `SSH_MANAGEMENT_CREDS_UNAVAILABLE` на старой
    сессии ДО любого деструктива, старый юзер не тронут.

    Возврат: `{renamed: True, management_user: <новый>, management_mode,
    old_removed: bool}`. Ошибки — `SshError`.
    """
    old_creds = dict(credentials)
    old_creds["is_managed"] = True
    old_creds["management_user"] = old_management_user
    host = _extract_host(old_creds, server_id)
    # Тянем per-server ключ (он один на сервер, не зависит от имени учётки) —
    # под ним заходим и старым, и новым пользователем. Нет ключа →
    # `_build_session` упадёт `SSH_MANAGEMENT_CREDS_UNAVAILABLE` до деструктива.
    await attach_management_creds(old_creds, server_id)
    management_key = old_creds.get("management_private_key")
    logger.info(
        "ssh management user cutover on %s: creating new management user",
        host,
    )
    async with _build_session(old_creds, server_id) as ssh:
        mode = await ssh.detect_management_mode()
        mode_cfg = (modes or {}).get(mode) or {}
        await ssh.bootstrap_management_user(
            new_management_user, public_key,
            groups=mode_cfg.get("groups") or [],
            extra_create_commands=mode_cfg.get("extra_create_commands") or [],
            management_private_key=None,
            harden_sshd=False,
        )

    # Анти-локаут под новой учёткой: независимая key-сессия + дешёвый sudo.
    await _verify_new_management_login(
        host, _extract_port(old_creds),
        management_user=new_management_user,
        management_private_key=management_key,
    )

    # Удаление старой учётки — с НОВОЙ key-сессии (нельзя сносить юзера, под
    # которым залогинен). userdel -r убирает home и mail spool. Под новым именем
    # заходим тем же per-server ключом.
    new_creds = dict(credentials)
    new_creds["is_managed"] = True
    new_creds["management_user"] = new_management_user
    new_creds["management_private_key"] = management_key
    logger.info(
        "ssh management user cutover on %s: removing old management user",
        host,
    )
    async with _build_session(new_creds, server_id) as ssh:
        await ssh.delete_user(old_management_user, remove_home=True)
        await ssh.remove_management_sudoers(old_management_user)

    return {
        "renamed": True,
        "management_user": new_management_user,
        "management_mode": mode,
        "old_removed": True,
    }


async def _verify_new_management_login(
    host: str,
    port: int,
    *,
    management_user: str,
    management_private_key,
) -> None:
    """Подтвердить вход под новой управляющей учёткой по ключу + sudo.

    Анти-локаут перед сносом старой учётки: открываем независимую key-сессию
    под новым пользователем и проверяем дешёвый `sudo -n true`. Любой провал —
    `SshError(SSH_MANAGEMENT_KEY_VERIFY_FAILED)`; caller тогда не удаляет
    старую учётку, и сервер остаётся управляемым под прежним пользователем.

    `management_private_key` — per-server материал ключа (PEM-строка или путь);
    приводим его к виду для `client_keys` через `_import_management_key`.
    """
    client_key = _import_management_key(management_private_key, host)
    try:
        async with SshClient(
            host=host,
            username=management_user,
            password=None,
            port=port,
            client_keys=[client_key],
        ) as verify_ssh:
            rc, _out, _err = await verify_ssh.run("true", sudo=True)
    except SshError as exc:
        raise SshError(
            error_code="SSH_MANAGEMENT_KEY_VERIFY_FAILED",
            host=host,
            cmd_sanitized=f"verify new management login <{management_user}>",
            message=(
                "key-based login as the new management user did not work; "
                "refusing to remove the old management user (anti-lockout)"
            ),
            details={"underlying_error_code": exc.error_code},
        ) from exc
    if rc != 0:
        raise SshError(
            error_code="SSH_MANAGEMENT_KEY_VERIFY_FAILED",
            host=host,
            cmd_sanitized=f"verify new management login <{management_user}>",
            returncode=rc,
            message=(
                "new management user session could not sudo; refusing to "
                "remove the old management user (anti-lockout)"
            ),
        )


async def rotate_management_creds_on_host(
    credentials: dict,
    server_id: str,
    *,
    management_user: str,
    new_public_key: str,
    new_private_key: str,
    new_password: str,
) -> dict:
    """Перевыкатить per-server управляющие креды на боксе (`server.rotate_management_creds`).

    Заходим ТЕКУЩИМ (рабочим на боксе) ключом под управляющим пользователем,
    ставим новый материал и проверяем его, не оставляя окна локаута:

    1. Под текущей key-сессией дописываем НОВЫЙ публичный ключ в
       authorized_keys (managed-маркер) и ставим НОВЫЙ пароль `chpasswd`. На
       этом шаге на боксе валидны оба ключа — старый и новый.
    2. Анти-локаут: независимой сессией под НОВЫМ приватным ключом проверяем
       вход + `sudo true`. Провал → `SshError`, старый ключ остаётся рабочим,
       callback не зовётся.
    3. После подтверждения нового входа переписываем authorized_keys одним
       новым ключом (`force_replace`) — старый ключ убираем уже зайдя по новому.

    `credentials` должен нести текущий per-server ключ
    (`management_private_key`) — его подтягивает caller через
    `attach_management_creds`. Возврат — `{rotated: True, management_user}`.
    Ошибки — `SshError`.
    """
    host = _extract_host(credentials, server_id)
    port = _extract_port(credentials)
    creds = dict(credentials)
    creds["is_managed"] = True
    creds["management_user"] = management_user

    logger.info("ssh management creds rotation on %s as %s", host, management_user)
    # 1. Текущая сессия: дописываем новый ключ (оба валидны) + новый пароль.
    async with _build_session(creds, server_id) as ssh:
        await ssh._write_authorized_key(
            management_user, new_public_key, force_replace=False,
        )
        await ssh.set_password(management_user, new_password)

    # 2. Анти-локаут под НОВЫМ ключом.
    await _verify_new_management_login(
        host, port,
        management_user=management_user,
        management_private_key=new_private_key,
    )

    # 3. Зайдя новым ключом, оставляем в authorized_keys только его — старый
    # ключ убираем. force_replace переписывает файл целиком, поэтому делаем это
    # ПОСЛЕ подтверждённого входа новым ключом.
    new_creds = {
        "is_managed": True,
        "management_user": management_user,
        "host": host,
        "ssh_port": port,
        "management_private_key": new_private_key,
    }
    async with _build_session(new_creds, server_id) as ssh:
        await ssh._write_authorized_key(
            management_user, new_public_key, force_replace=True,
        )
    return {"rotated": True, "management_user": management_user}


def _parse_size_to_gb(size_str: str) -> int:
    """Сконвертировать `lsblk SIZE` ("500G", "1.8T", "256M") в гигабайты.

    Возвращает 0 при unparseable, чтобы не валить весь inventory из-за
    одной строчки. server_service-схема требует `size_gb: int (ge=0)`.
    """
    if not size_str:
        return 0
    s = size_str.strip().upper()
    # lsblk обычно: "500G", "1.8T", "256M", "16K", иногда чистое число
    multipliers = {"K": 1 / (1024 * 1024), "M": 1 / 1024, "G": 1, "T": 1024, "P": 1024 * 1024}
    suffix = s[-1] if s and s[-1] in multipliers else ""
    try:
        if suffix:
            value = float(s[:-1]) * multipliers[suffix]
        else:
            # bytes
            value = float(s) / (1024 ** 3)
    except ValueError:
        return 0
    return max(0, int(value))


def _extract_cpu_facts(cpu_block: dict) -> dict:
    """Из `lscpu -J` JSON достать плоские CPU-факты.

    `lscpu -J` отдаёт `{"lscpu": [{"field": "Model name:", "data": "..."}, ...]}`.
    Возврат — словарь с ключами `cpu_brand`, `cpu_model`, `cpu_cores`,
    `cpu_threads`, `cpu_frequency_ghz`. Поля, которые не нашли, остаются
    `None` (для cores — fallback 1, чтобы пройти server_service-валидацию
    `ge=1`).
    """
    data = cpu_block.get("data") if isinstance(cpu_block, dict) else None
    facts: dict = {
        "cpu_brand": None,
        "cpu_model": None,
        "cpu_cores": 1,
        "cpu_threads": None,
        "cpu_frequency_ghz": None,
    }
    if not isinstance(data, dict):
        return facts
    entries = data.get("lscpu", [])
    threads_per_core: int | None = None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        field = (entry.get("field") or "").strip().rstrip(":")
        value = (entry.get("data") or "").strip()
        if field == "Model name":
            facts["cpu_model"] = value or None
        elif field == "Vendor ID":
            # "GenuineIntel" / "AuthenticAMD" — нормализуем в "Intel" / "AMD".
            facts["cpu_brand"] = _normalize_cpu_vendor(value)
        elif field == "CPU(s)":
            try:
                facts["cpu_cores"] = max(1, int(value))
            except ValueError:
                pass
        elif field == "Thread(s) per core":
            try:
                threads_per_core = int(value)
            except ValueError:
                pass
        elif field == "CPU max MHz" and facts["cpu_frequency_ghz"] is None:
            try:
                facts["cpu_frequency_ghz"] = round(float(value) / 1000.0, 2)
            except ValueError:
                pass
        elif field == "CPU MHz" and facts["cpu_frequency_ghz"] is None:
            try:
                facts["cpu_frequency_ghz"] = round(float(value) / 1000.0, 2)
            except ValueError:
                pass
    if threads_per_core is not None:
        facts["cpu_threads"] = facts["cpu_cores"] * threads_per_core
    return facts


def _normalize_cpu_vendor(value: str) -> str | None:
    """`GenuineIntel`/`AuthenticAMD`/`MCST` → `Intel`/`AMD`/`MCST`.

    Возврат `None` если value пустой или whitespace; неизвестные vendor'ы
    отдаём как есть — лучше показать сырую строку, чем терять данные.
    """
    if not value:
        return None
    norm = value.strip()
    if not norm:
        return None
    if "Intel" in norm:
        return "Intel"
    if "AMD" in norm:
        return "AMD"
    if "MCST" in norm or "Elbrus" in norm:
        return "MCST"
    return norm


def _node_mountpoints(node: dict) -> list[str]:
    """Собрать точки монтирования одного lsblk-узла.

    lsblk до util-linux 2.37 отдаёт одиночный `mountpoint`, новее — список
    `mountpoints` (с возможными null внутри). Поддерживаем оба формата.
    """
    out: list[str] = []
    single = node.get("mountpoint")
    if single:
        out.append(single)
    multi = node.get("mountpoints")
    if isinstance(multi, list):
        out.extend(mp for mp in multi if mp)
    return out


def _collect_mountpoints(node: dict) -> list[str]:
    """Все точки монтирования поддерева устройства (сам узел + дети рекурсивно).

    Раздел, LVM-том или crypt-контейнер могут лежать на несколько уровней
    глубже физического диска — обходим `children` до конца.
    """
    mps = list(_node_mountpoints(node))
    for child in node.get("children") or []:
        if isinstance(child, dict):
            mps.extend(_collect_mountpoints(child))
    return mps


def _disk_size_bytes(raw) -> int:
    """`lsblk -b` даёт размер в байтах (числом или строкой). 0 при мусоре."""
    try:
        return max(0, int(str(raw).strip()))
    except (TypeError, ValueError):
        return 0


def _index_devices_to_disk(disks: list[dict]) -> dict[str, str]:
    """Каждый потомок физического диска (раздел, LVM-том, crypt) → имя диска.

    df-источник смонтированного тома называется не так, как несущий диск:
    штатная разметка Astra SE (LVM, а под luks — тем более) монтирует `/` с
    `/dev/mapper/vg-root`, а не с `/dev/sda2`. Префиксом такое к `sda` не
    свести. lsblk-дерево связывает mapper/LV-узел с физическим диском —
    индексируем имена (`name`) и kernel-имена (`kname`) всех узлов поддерева.
    """
    index: dict[str, str] = {}

    def _walk(node: dict, disk_name: str) -> None:
        for key in (node.get("name"), node.get("kname")):
            k = (key or "").strip()
            if k:
                index.setdefault(k, disk_name)
        for child in node.get("children") or []:
            if isinstance(child, dict):
                _walk(child, disk_name)

    for dev in disks:
        name = (dev.get("name") or "").strip()
        if name:
            _walk(dev, name)
    return index


def _parse_df_used_by_disk(
    df_block: Any, disk_names: list[str], device_index: dict[str, str],
) -> dict[str, int]:
    """`df -B1 --output=source,target,size,used,pcent` → занятые байты на диск.

    Каждую строку df матчим к физическому диску через lsblk-дерево
    (`device_index`): `/dev/sda2` → `sda`, `/dev/mapper/vg-root` → диск, на
    котором лежит этот LV. Суммируем `used` всех ФС диска. Псевдо-ФС (`tmpfs`,
    `overlay`) и mapper-тома, которых нет в дереве, ни к одному диску не
    привязываются и в сумму не попадают.
    """
    used_by_disk: dict[str, int] = {}
    text = _capture_stdout(df_block)
    if not text:
        return used_by_disk
    lines = text.splitlines()
    for raw in lines[1:]:  # первая строка — заголовок df
        parts = raw.split()
        if len(parts) < 4:
            continue
        source = parts[0]
        if not source.startswith("/dev/"):
            continue
        dev = source[len("/dev/"):]
        try:
            used = int(parts[3])
        except ValueError:
            continue
        disk = _disk_of_device(dev, disk_names, device_index)
        if disk is None:
            continue
        used_by_disk[disk] = used_by_disk.get(disk, 0) + max(0, used)
    return used_by_disk


def _disk_of_device(
    device: str, disk_names: list[str], device_index: dict[str, str],
) -> str | None:
    """К какому физическому диску относится устройство из df-строки.

    Сначала ищем узел в lsblk-дереве по имени как есть и по basename'у
    (`mapper/vg-root` → `vg-root`, `dm-0`) — так ловятся LVM/luks-тома, чьё
    имя не совпадает с именем диска. Если в дереве узла нет (частично битый
    lsblk), падаем на исторический матч по самому длинному префиксу имени
    диска, чтобы `nvme0n1p1` ушёл к `nvme0n1`.
    """
    for cand in (device, device.rsplit("/", 1)[-1]):
        disk = device_index.get(cand)
        if disk is not None:
            return disk
    match: str | None = None
    for name in disk_names:
        if device == name or device.startswith(name):
            if match is None or len(name) > len(match):
                match = name
    return match


def _extract_disks(disks_block: dict, df_block: Any = None) -> list[dict]:
    """Из `lsblk -b -J` JSON + `df` собрать список `InventoryDiskItem`-словарей.

    Берём только `type == "disk"` (без partitions). По каждому диску:

    * `size_gb` — из lsblk (байты → ГБ);
    * `is_system` — True, если в поддереве диска есть раздел с mountpoint `/`;
    * `mountpoints` — все точки монтирования поддерева диска;
    * `used_gb` / `used_percent` — из df: сумма занятых байт всех ФС диска,
      процент — относительно полного объёма. df нет / диск не смонтирован →
      обе величины None (частичный inventory лучше полного фейла).
    """
    data = disks_block.get("data") if isinstance(disks_block, dict) else None
    if not isinstance(data, dict):
        return []
    devices = data.get("blockdevices", [])
    disks = [
        dev for dev in devices
        if isinstance(dev, dict) and dev.get("type") == "disk"
        and (dev.get("name") or "").strip()
    ]
    disk_names = [(dev.get("name") or "").strip() for dev in disks]
    device_index = _index_devices_to_disk(disks)
    used_by_disk = _parse_df_used_by_disk(df_block, disk_names, device_index)

    out: list[dict] = []
    for dev in disks:
        name = (dev.get("name") or "").strip()
        size_bytes = _disk_size_bytes(dev.get("size"))
        size_gb = _parse_size_to_gb(str(dev.get("size") or ""))
        mountpoints = sorted(set(_collect_mountpoints(dev)))
        is_system = "/" in mountpoints
        used_bytes = used_by_disk.get(name)
        used_gb: int | None = None
        used_percent: float | None = None
        if used_bytes is not None:
            used_gb = max(0, int(used_bytes / (1024 ** 3)))
            if size_bytes > 0:
                used_percent = round(min(100.0, used_bytes / size_bytes * 100), 1)
        out.append({
            "name": name,
            "size_gb": size_gb,
            "used_gb": used_gb,
            "used_percent": used_percent,
            "model": (dev.get("model") or None) or None,
            "serial": (dev.get("serial") or None) or None,
            "device_path": f"/dev/{name}",
            "mountpoints": mountpoints,
            "is_system": is_system,
        })
    return out


def _extract_memory_mb(meminfo_block: Any) -> int | None:
    """Объём ОЗУ в МБ из `MemTotal` (`/proc/meminfo`, значение в kB).

    None, если файла нет или строка не распознана — память в БД останется как
    была (first-write наступит на следующей успешной инвентаризации).
    """
    text = _capture_stdout(meminfo_block)
    if not text:
        return None
    for line in text.splitlines():
        if not line.startswith("MemTotal:"):
            continue
        parts = line.split()
        # Формат: "MemTotal:       16307128 kB"
        if len(parts) >= 2:
            try:
                kb = int(parts[1])
            except ValueError:
                return None
            return max(0, kb // 1024)
    return None


def _extract_virtualization(facts: dict) -> bool | None:
    """Флаг аппаратной виртуализации (KVM) из inventory-пробы.

    Воркер кладёт в `facts["virtualization"]` результат `test -e /dev/kvm ||
    grep vmx/svm /proc/cpuinfo` строкой `1`/`0`: `1` — KVM есть, `0` — нет.
    Пустой/отсутствующий блок (проба не отработала) → None — способность
    неизвестна, существующее значение сервера трогать нельзя.
    """
    text = _capture_stdout(facts.get("virtualization"))
    if not text:
        return None
    token = text.splitlines()[0].strip()
    return token != "0"


def _extract_network_interfaces(net_block: Any) -> list[str]:
    """Имена активных сетевых интерфейсов из `ip -o link show` (без lo).

    Формат строки: `2: ens192: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu ...`.
    Берём имя (обрезая `@ifX` у VLAN/veth), пропускаем loopback и интерфейсы
    без флага UP. Порядок сохраняем, дубли схлопываем.
    """
    text = _capture_stdout(net_block)
    if not text:
        return []
    out: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^\d+:\s*([^:@]+)(?:@\S+)?:\s*<([^>]*)>", line)
        if not m:
            continue
        name = m.group(1).strip()
        flags = m.group(2).split(",")
        if name == "lo" or "UP" not in flags:
            continue
        if name not in out:
            out.append(name)
    return out


# Название режима защищённости Astra Linux SE для отдельного поля
# os_security_mode. Ключи — те же ManagementMode-строки, что worker кладёт при
# prepare (см. `clients.ssh` MODE_ASTRA_*), значения — редакция латиницей с
# заглавной (UI склеит с версией сам). Меняешь маппинг режимов там — сверься тут.
_ASTRA_MODE_LABELS = {
    MODE_ASTRA_OREL: "Orel",
    MODE_ASTRA_VORONEZH: "Voronezh",
    MODE_ASTRA_SMOLENSK: "Smolensk",
}


def _capture_stdout(block: Any) -> str:
    """stdout из `_capture_text`-блока (`{stdout, ...}` либо `{error, ...}`).

    Пустая строка, если блока нет или он не читается — все вызовы best-effort.
    """
    if isinstance(block, dict):
        return (block.get("stdout") or "").strip()
    return ""


def _detect_astra_mode(license_text: str) -> str | None:
    """Определить режим защищённости Astra по содержимому /etc/astra_license.

    Возвращает ManagementMode-строку (`astra_orel`/`astra_voronezh`/
    `astra_smolensk` — те же, что worker кладёт при prepare), либо None, если
    режим не распознан. prepare детектит режим иначе (через `astra-modeswitch`
    / `mswitch.conf`), поэтому здесь опираемся на само имя редакции в лицензии:
    матчим и русское название, и латинскую транслитерацию — формат файла между
    релизами меняется.
    """
    text = (license_text or "").lower()
    if not text:
        return None
    if "смоленск" in text or "smolensk" in text:
        return MODE_ASTRA_SMOLENSK
    if "воронеж" in text or "voronezh" in text:
        return MODE_ASTRA_VORONEZH
    if "орёл" in text or "орел" in text or "orel" in text:
        return MODE_ASTRA_OREL
    return None


def _extract_os_version(
    os_block: dict,
    astra_build_block: Any = None,
) -> str:
    """Собрать имя версии ОС для каталога os_versions.

    Для Astra Linux (есть `/etc/astra/build_version`) — только версия сборки,
    например `1.7.5` (без «Astra Linux SE» и без режима — режим уходит
    отдельным полем `os_security_mode`, а UI склеивает их сам).

    Не-Astra (build_version нет) — прежнее поведение: `PRETTY_NAME` из
    os-release, fallback на `NAME` + `VERSION_ID`, иначе `"unknown"`.
    """
    build = _capture_stdout(astra_build_block)
    if build:
        # build_version — одна строка с версией; берём первую непустую на
        # случай, если в файле окажется что-то ещё.
        build_version = next(
            (ln.strip() for ln in build.splitlines() if ln.strip()), ""
        )
        if build_version:
            return build_version

    if not isinstance(os_block, dict):
        return "unknown"
    pretty = (os_block.get("PRETTY_NAME") or "").strip()
    if pretty:
        return pretty
    name = (os_block.get("NAME") or "").strip()
    ver = (os_block.get("VERSION_ID") or "").strip()
    if name and ver:
        return f"{name} {ver}"
    return name or "unknown"


def _extract_os_security_mode(astra_license_block: Any) -> str | None:
    """Режим защищённости Astra для поля `os_security_mode`.

    Возвращает `Orel`/`Voronezh`/`Smolensk` (латиницей, с заглавной) по
    содержимому `/etc/astra_license`, либо None — если это не Astra или режим
    не распознан.
    """
    mode = _detect_astra_mode(_capture_stdout(astra_license_block))
    if mode is None:
        return None
    return _ASTRA_MODE_LABELS[mode]


def _extract_repositories(apt_block: Any) -> list[str]:
    """Активные apt-репозитории из `cat` sources.list (+ sources.list.d).

    Берём только строки, начинающиеся с `deb`/`deb-src`; закомментированные
    (`#`) и пустые пропускаем. Каждую строку триммим. Дубликаты не
    схлопываем — оператору важно видеть источники как есть.
    """
    if not isinstance(apt_block, dict):
        return []
    text = apt_block.get("stdout") or ""
    repos: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        first = line.split(None, 1)[0]
        if first in ("deb", "deb-src"):
            repos.append(line)
    return repos


def _extract_hostname(hostname_block: Any) -> str:
    """Достать hostname из `_capture_text`-результата."""
    if isinstance(hostname_block, dict):
        value = (hostname_block.get("stdout") or "").strip()
        if value:
            return value
    return "unknown"


def _extract_kernel(kernel_block: Any) -> str:
    """uname -a → строка для callback'а. Truncate'им если > 256 (limit схемы)."""
    if isinstance(kernel_block, dict):
        value = (kernel_block.get("stdout") or "").strip()
        if value:
            return value[:256]
    return "unknown"


def _extract_lspci(pci_block: Any) -> str | None:
    """`pci.devices` list → multi-line string. None если пусто."""
    if not isinstance(pci_block, dict):
        return None
    lines = pci_block.get("devices") or []
    if not isinstance(lines, list) or not lines:
        return None
    return "\n".join(str(ln) for ln in lines)


def inventory_facts_to_payload(facts: dict) -> dict:
    """Сконвертировать `SshClient.get_inventory()` в payload `InventoryCallbackRequest`.

    Сырой `facts` — вложенный dict (`cpu/disks/os/pci` — sub-blocks с lscpu/lsblk
    JSON и parsed os-release, плюс `astra_build`/`astra_license`/`apt_sources`).
    server_service ждёт flat-schema: `hostname`, `kernel`, `cpu_brand`,
    `cpu_model`, `cpu_cores`, `cpu_threads`, `cpu_frequency_ghz`, `os_version`,
    `os_security_mode?`, `ram_total_mb?`, `virtualization?`,
    `network_interfaces: [...]`, `disks: [...]`, `lspci?`, `repositories: [...]`.

    Для Astra Linux `os_version` — версия сборки (`1.7.5`) из
    `/etc/astra/build_version`, а режим защищённости уходит отдельным полем
    `os_security_mode` (`Orel`/`Voronezh`/`Smolensk` из `/etc/astra_license`).
    Для прочих ОС `os_version` — `PRETTY_NAME` из os-release, `os_security_mode`
    — None. `repositories` — активные `deb`/`deb-src` строки apt-sources.

    CPU-поля пишутся прямо в `servers` (отдельной таблицы-каталога нет),
    поэтому `cpu_brand` / `cpu_model` могут быть `None` если worker не
    смог распарсить lscpu. `cpu_cores` всегда ≥1 (fallback на 1) —
    schema требует `ge=1`.

    Парсеры устойчивы к частично-битым входам (одна команда упала — остальные
    проходят, поля получают `None` / 1 / [] placeholder'ы), потому что
    `SshClient._capture_text/_capture_json` сами не raise'ят — кладут `error`
    в блок.
    """
    cpu_facts = _extract_cpu_facts(facts.get("cpu", {}))
    return {
        "hostname": _extract_hostname(facts.get("hostname")),
        "kernel": _extract_kernel(facts.get("kernel")),
        "cpu_brand": cpu_facts["cpu_brand"],
        "cpu_model": cpu_facts["cpu_model"],
        "cpu_cores": cpu_facts["cpu_cores"],
        "cpu_threads": cpu_facts["cpu_threads"],
        "cpu_frequency_ghz": cpu_facts["cpu_frequency_ghz"],
        "os_version": _extract_os_version(
            facts.get("os", {}),
            facts.get("astra_build"),
        ),
        "os_security_mode": _extract_os_security_mode(facts.get("astra_license")),
        "ram_total_mb": _extract_memory_mb(facts.get("meminfo")),
        "virtualization": _extract_virtualization(facts),
        "network_interfaces": _extract_network_interfaces(facts.get("net_interfaces")),
        "disks": _extract_disks(facts.get("disks", {}), facts.get("df")),
        "lspci": _extract_lspci(facts.get("pci", {})),
        "repositories": _extract_repositories(facts.get("apt_sources")),
    }


# ── OS-user inventory parsing ────────────────────────────────────────────────

# Дефолтный UID_MIN на случай, если /etc/login.defs недоступен или не содержит
# строки. Debian/Astra используют 1000; берём его как разумный fallback.
_DEFAULT_UID_MIN = 1000

# Группы, членство в которых считаем эквивалентом sudo-прав. astra-se может
# использовать `astra-admin`, но базовый набор — sudo/wheel/admin.
_SUDO_GROUPS = frozenset({"sudo", "wheel", "admin", "root"})

# Login совпадает с тем же POSIX-набором, что и server_service-схема — иначе
# мусорная строка getent (с `:`/пробелами) не пройдёт callback-валидацию и
# уронит весь reconcile. Фильтруем такие записи здесь.
_USER_LOGIN_RE = re.compile(r"^[A-Za-z0-9._\-]+$")


def _parse_uid_min(login_defs_block: Any) -> int:
    """Достать UID_MIN из `cat /etc/login.defs`. Fallback — `_DEFAULT_UID_MIN`."""
    if not isinstance(login_defs_block, dict):
        return _DEFAULT_UID_MIN
    text = login_defs_block.get("stdout") or ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "UID_MIN":
            try:
                return int(parts[1])
            except ValueError:
                return _DEFAULT_UID_MIN
    return _DEFAULT_UID_MIN


def _parse_groups(group_block: Any) -> dict[str, set[str]]:
    """`getent group` → `{login: {group_name, ...}}`.

    Формат строки: `name:passwd:gid:member1,member2`. Возвращаем обратный
    индекс «логин → набор групп», чтобы определить sudo-членство.
    """
    user_groups: dict[str, set[str]] = {}
    if not isinstance(group_block, dict):
        return user_groups
    text = group_block.get("stdout") or ""
    for line in text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        fields = line.split(":")
        if len(fields) < 4:
            continue
        group_name = fields[0]
        members = [m for m in fields[3].split(",") if m]
        for member in members:
            user_groups.setdefault(member, set()).add(group_name)
    return user_groups


def os_users_facts_to_payload(facts: dict) -> dict:
    """Сконвертировать `SshClient.get_os_users()` в payload
    `UsersInventoryCallbackRequest` server_service'а.

    Парсит `getent passwd`, отфильтровывает системных по `UID >= UID_MIN` из
    `/etc/login.defs`, доклеивает группы (`getent group`) и помечает sudo по
    членству в secondary-группах (sudo/wheel/admin), которые отдал `getent group`.
    Primary-группу из passwd не учитываем: для inv-сценария достаточно
    secondary, и оператор всегда даёт sudo через привычную membership.

    Возврат — `{"users": [{login, uid, shell, home_dir, unix_groups,
    has_sudo}, ...]}`. Записи с непроходящим по regex login'ом пропускаются.
    """
    uid_min = _parse_uid_min(facts.get("login_defs"))
    user_groups = _parse_groups(facts.get("group"))

    passwd_block = facts.get("passwd")
    text = passwd_block.get("stdout") if isinstance(passwd_block, dict) else ""
    users: list[dict] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        fields = line.split(":")
        if len(fields) < 7:
            continue
        login = fields[0]
        try:
            uid = int(fields[2])
        except ValueError:
            continue
        # nobody (обычно 65534) — тоже системный, отсекаем по верхней границе.
        if uid < uid_min or uid >= 65534:
            continue
        if not _USER_LOGIN_RE.match(login):
            continue
        home_dir = fields[5] or None
        shell = fields[6] or None
        groups = sorted(user_groups.get(login, set()))
        has_sudo = bool(set(groups) & _SUDO_GROUPS)
        users.append({
            "login": login,
            "uid": uid,
            "shell": shell,
            "home_dir": home_dir,
            "unix_groups": groups,
            "has_sudo": has_sudo,
        })
    return {"users": users}


__all__ = [
    "apply_session_hints",
    "attach_management_creds",
    "build_session",
    "rotate_management_creds_on_host",
    "collect_inventory",
    "collect_os_users",
    "set_account_password",
    "provision_user",
    "apply_authorized_key",
    "modify_user",
    "delete_user",
    "bootstrap_management_user",
    "cutover_management_user",
    "inventory_facts_to_payload",
    "os_users_facts_to_payload",
    "SshError",
]
