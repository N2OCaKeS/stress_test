"""Генерация prefixed ID. По одной фабрике на каждый prefix."""

import uuid


def _new_id(prefix: str) -> str:
    """`prefix + uuid4.hex` — единственная точка генерации, единый формат."""
    return f"{prefix}{uuid.uuid4().hex}"


def server_id() -> str:
    """`srv_<uuid>` — для таблицы servers."""
    return _new_id("srv_")


def os_version_id() -> str:
    """`osv_<uuid>` — для os_versions. Сейчас неиспользуется (CRUD заглушки)."""
    return _new_id("osv_")


def server_account_id() -> str:
    """`acc_<uuid>` — для server_accounts."""
    return _new_id("acc_")


def server_account_server_id() -> str:
    """`acs_<uuid>` — для строк связки server_account_servers."""
    return _new_id("acs_")


def ipmi_controller_id() -> str:
    """`ipm_<uuid>` — для ipmi_controllers."""
    return _new_id("ipm_")


def ignored_login_id() -> str:
    """`ign_<uuid>` — для строк server_account_ignored_login."""
    return _new_id("ign_")


def console_macro_id() -> str:
    """`cmc_<uuid>` — для строк console_macros (личные/системные макросы консоли)."""
    return _new_id("cmc_")


def entity_permission_id() -> str:
    """`prm_<uuid>` — для entity_permissions."""
    return _new_id("prm_")


def server_disk_id() -> str:
    """`dsk_<uuid>` — для server_disks."""
    return _new_id("dsk_")


def task_id() -> str:
    """`tsk_<uuid>` — для строк worker-БД `dev_server_worker.tasks`.

    Сама таблица живёт в worker-БД, но id выписывает server_service
    при INSERT'е через cross-DB engine (см. `services/worker_client.py`).
    """
    return _new_id("tsk_")


def prepare_creds_id() -> str:
    """`pcd_<uuid>` — для одноразового Redis-ключа bootstrap-кред `prepare`-задачи.

    Plaintext bootstrap-логин/пароль кладутся в Redis под
    `dbos:prepare_creds:<pcd_id>` с TTL, в task-payload едет только ссылка.
    """
    return _new_id("pcd_")


def dispatch_creds_id() -> str:
    """`dcd_<uuid>` — для одноразового Redis-ключа inline-кред provision-таски.

    Plaintext password + ssh_private_key для `account.provision` кладутся
    в Redis под `dbos:dispatch_creds:<dcd_id>` с TTL, в task-payload едет
    только ссылка.
    Без этого plaintext оседал бы в `dev_server_worker.tasks.payload` (JSONB)
    до retention cleanup'а — любой с read к worker-БД видел бы пароль.
    """
    return _new_id("dcd_")


def console_session_id() -> str:
    """`csn_<uuid>` — id интерактивной SSH-консольной сессии.

    Используется как суффикс Redis pub/sub каналов console-моста
    (`console:ctl:<csn_id>` / `console:in:` / `console:out:`). Worker
    валидирует его POSIX-набором перед подпиской — `csn_<hex>` проходит.
    """
    return _new_id("csn_")


def console_creds_id() -> str:
    """`ccd_<uuid>` — id одноразового Redis-ключа кред console-сессии.

    Логин/пароль выбранного server_account кладутся в Redis под
    `dbos:console_creds:<ccd_id>` с TTL; в `start`-control-сообщении едет
    только ссылка на ключ. Plaintext пароля в pub/sub-сообщениях и в
    task-payload'ах не светится.
    """
    return _new_id("ccd_")
