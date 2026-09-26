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


def server_category_id() -> str:
    """`scat_<uuid>` — для server_categories (каталог категорий по мощности)."""
    return _new_id("scat_")


def server_account_id() -> str:
    """`acc_<uuid>` — для server_accounts."""
    return _new_id("acc_")


def server_account_server_id() -> str:
    """`acs_<uuid>` — для строк связки server_account_servers."""
    return _new_id("acs_")


def server_account_vm_id() -> str:
    """`acv_<uuid>` — для строк связки server_account_vms."""
    return _new_id("acv_")


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


def resource_role_permission_id() -> str:
    """`rrp_<uuid>` — для resource_role_permissions (инстанс-уровневый ACL)."""
    return _new_id("rrp_")


def server_disk_id() -> str:
    """`dsk_<uuid>` — для server_disks."""
    return _new_id("dsk_")


def vm_id() -> str:
    """`vm_<uuid>` — для таблицы vms (виртуальные машины на hub-серверах)."""
    return _new_id("vm_")


def box_id() -> str:
    """`box_<uuid>` — для таблицы boxes (заготовки-образы для создания ВМ)."""
    return _new_id("box_")


def vm_disk_id() -> str:
    """`vmd_<uuid>` — для таблицы vm_disks (диски виртуальных машин)."""
    return _new_id("vmd_")


def vm_image_id() -> str:
    """`vmi_<uuid>` — для таблицы vm_images (каталог боксов-образов ВМ)."""
    return _new_id("vmi_")


def vm_snapshot_id() -> str:
    """`snp_<uuid>` — для таблицы vm_snapshots (снимки виртуальных машин)."""
    return _new_id("snp_")


def vm_ip_pool_id() -> str:
    """`pool_<uuid>` — для таблицы vm_ip_pool (пулы IP-адресов ВМ, IPAM)."""
    return _new_id("pool_")


def vm_preset_id() -> str:
    """`vps_<uuid>` — для таблицы vm_preset (шаблоны стандартных ВМ отдела)."""
    return _new_id("vps_")


def vm_console_token() -> str:
    """`vmc_<uuid>` — короткоживущий токен доступа к консоли ВМ (vnc/serial/ssh).

    Отдаётся UI в ответе `POST /vms/{id}/console`; websockify/PTY-прокси (ставится
    отдельной волной) валидирует его перед проксированием к VNC/serial/SSH ВМ.
    Токен не персистится server_service'ом — контракт на его проверку держит
    прокси (короткий TTL, одноразовость — на его стороне).
    """
    return _new_id("vmc_")


def task_id() -> str:
    """`tsk_<uuid>` — для строк worker-БД `dev_server_worker.tasks`.

    Сама таблица живёт в worker-БД, но id выписывает server_service
    при INSERT'е через cross-DB engine (см. `services/worker_client.py`).
    """
    return _new_id("tsk_")


def rotation_batch_id() -> str:
    """`bat_<uuid>` — сквозной id батча массовой ротации паролей.

    Не персистится: служит корреляционным ключом между per-task результатами
    в ответе dispatch'а и аудит-событиями батча, чтобы UI и SIEM сшивали
    задачи одного запроса.
    """
    return _new_id("bat_")


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


def acs_department_access_id() -> str:
    """`ada_<uuid>` — для строк acs_department_access (per-department opt-in ACS)."""
    return _new_id("ada_")


def os_version_bootstrap_password_id() -> str:
    """`obp_<uuid>` — для строк os_version_bootstrap_passwords (1:1 c os_versions)."""
    return _new_id("obp_")


def host_service_unit_id() -> str:
    """`hsu_<uuid>` — для строк host_service_units (per-department список юнитов)."""
    return _new_id("hsu_")


def prepare_for_test_request_id() -> str:
    """`prep_<uuid>` — id асинхронного запроса `prepare-for-test`.

    Уезжает наружу: testing_service получает его в 202-ответе и видит в пути
    callback'а (`/internal/prepare-for-test/{prepare_request_id}/completed`).
    """
    return _new_id("prep_")


def server_test_credentials_id() -> str:
    """`stc_<uuid>` — для строк server_test_credentials (учётка исполнения теста)."""
    return _new_id("stc_")


def stand_setup_request_id() -> str:
    """`ssr_<uuid>` — id запроса «настройка стенда без restore»."""
    return _new_id("ssr_")
