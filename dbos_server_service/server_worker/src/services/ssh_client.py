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
import os
import re
from typing import Any

from src.clients.ssh import SshClient, SshError
from src.core.config import get_settings

logger = logging.getLogger(__name__)


def apply_session_hints(credentials: dict, payload: dict) -> dict:
    """Прокинуть в credentials признаки управляющей сессии и адрес из payload.

    server_service кладёт в payload `is_managed` (прошёл ли сервер prepare) и
    `management_user` (на их основе `_build_session` выбирает между ключевой
    сессией под управляющим пользователем и self-сессией под аккаунтом), а
    также `host` / `ssh_port` — адрес сервера, чтобы не дёргать DNS-резолв из
    `server_id` каждый раз.

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
      `SSH_MANAGEMENT_USER`) по приватному ключу (`SSH_MANAGEMENT_PRIVATE_KEY_PATH`).
      Пароль аккаунта в сессию не идёт; sudo на ключевой сессии работает
      через настроенный во время prepare доступ управляющего пользователя.
    * **не управляемый** — старое поведение: сессия под самим аккаунтом
      (`login` + `password`), как было до онбординга.

    Если сервер помечен управляемым, но управляющий ключ в конфиге не задан —
    поднимаем понятный `SshError(SSH_MANAGEMENT_KEY_MISSING)`, не падая внутри
    asyncssh с невнятным сообщением.
    """
    host = _extract_host(credentials, server_id)
    port = _extract_port(credentials)

    if credentials.get("is_managed"):
        settings = get_settings()
        key_path = settings.ssh_management_private_key_path
        if not key_path:
            raise SshError(
                error_code="SSH_MANAGEMENT_KEY_MISSING",
                host=host,
                message=(
                    "server is managed but SSH_MANAGEMENT_PRIVATE_KEY_PATH is "
                    "not configured on the worker"
                ),
            )
        if not os.path.isfile(key_path):
            raise SshError(
                error_code="SSH_MANAGEMENT_KEY_MISSING",
                host=host,
                message="management private key file not found at configured path",
            )
        management_user = (
            credentials.get("management_user") or settings.ssh_management_user
        )
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
            client_keys=[key_path],
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
    management_private_key_path: str | None = None,
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

    Если задан `management_private_key_path`, после установки ключа worker
    проверяет, что вход под `management_user` по этому ключу реально работает
    (анти-локаут). `harden_sshd=True` дополнительно выключает парольный SSH и
    root-login через drop-in — только после успешной проверки ключа.

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
            management_private_key_path=management_private_key_path,
            harden_sshd=harden_sshd,
        )
    return {
        "prepared": True,
        "management_user": management_user,
        "management_mode": mode,
    }


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


def _extract_disks(disks_block: dict) -> list[dict]:
    """Из `lsblk -J -o NAME,SIZE,TYPE,MODEL,SERIAL` JSON собрать список
    `InventoryDiskItem`-словарей.

    Берём только `type == "disk"` (без partitions); если `model` пустой —
    оставляем None. `is_system` остаётся False — определять системный диск
    по mount-point'у нужно отдельной командой (lsblk -o MOUNTPOINT), здесь
    не делаем.
    """
    data = disks_block.get("data") if isinstance(disks_block, dict) else None
    if not isinstance(data, dict):
        return []
    devices = data.get("blockdevices", [])
    out: list[dict] = []
    for dev in devices:
        if not isinstance(dev, dict):
            continue
        if dev.get("type") != "disk":
            continue
        name = (dev.get("name") or "").strip()
        if not name:
            continue
        size_gb = _parse_size_to_gb(str(dev.get("size") or ""))
        out.append({
            "name": name,
            "size_gb": size_gb,
            "model": (dev.get("model") or None) or None,
            "serial": (dev.get("serial") or None) or None,
            "device_path": f"/dev/{name}",
            "is_system": False,
        })
    return out


def _extract_os_version(os_block: dict) -> str:
    """Из распарсенного /etc/os-release собрать строку для каталога os_versions.

    Предпочтительно `PRETTY_NAME` (например, "Astra Linux SE 1.7"), fallback
    на `NAME` + `VERSION_ID`. Если нет ничего — `"unknown"` placeholder.
    """
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
    JSON и parsed os-release). server_service ждёт flat-schema: `hostname`,
    `kernel`, `cpu_brand`, `cpu_model`, `cpu_cores`, `cpu_threads`,
    `cpu_frequency_ghz`, `os_version`, `disks: [...]`, `lspci?`.

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
        "os_version": _extract_os_version(facts.get("os", {})),
        "disks": _extract_disks(facts.get("disks", {})),
        "lspci": _extract_lspci(facts.get("pci", {})),
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
    "build_session",
    "collect_inventory",
    "collect_os_users",
    "set_account_password",
    "provision_user",
    "apply_authorized_key",
    "modify_user",
    "delete_user",
    "bootstrap_management_user",
    "inventory_facts_to_payload",
    "os_users_facts_to_payload",
    "SshError",
]
