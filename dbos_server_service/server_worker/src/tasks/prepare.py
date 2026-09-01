"""Бутстрап управления сервером по SSH (`server.prepare`).

Онбординг ещё не управляемого сервера: заходим под одноразовыми bootstrap-
кредами (password-auth), заводим системного управляющего пользователя DBOS,
даём ему sudo и кладём публичный ключ управления. Дальше управление — по ключу
без исходного пароля.

Bootstrap-креды (`bootstrap_login` / `bootstrap_password`) НЕ хранятся в
персистентной `tasks.payload` — иначе plaintext осел бы в worker-БД. Вместо
этого server_service кладёт их в Redis под одноразовый ключ с TTL, а в payload
передаёт только ссылку (`bootstrap_creds_key`). Handler читает креды из Redis
на каждой попытке: пока жив TTL, retry работает; по истечении ключ исчезает сам.
Если ключа нет/истёк — `SSH_BOOTSTRAP_CREDS_MISSING` (task FAILED с понятным
last_error, не молчаливый краш под `root`).

Управляющая SSH-пара и пароль `dbos` свои на каждом сервере: их генерит
server_service на этапе prepare и кладёт в тот же Redis-stash, что и
bootstrap-креды, полем `mgmt_install = {management_user, public_key,
private_key, password}`. Worker читает их оттуда, ставит публичный ключ в
authorized_keys, выставляет пароль (`chpasswd`) и проверяет вход новым ключом
перед хардингом sshd. Глобального env-ключа воркера больше нет. По завершении
POST'им callback `submit_prepared`, server_service помечает сервер
подготовленным (`is_managed`, `prepared_at`, management_user), а ключ из Redis
удаляем (TTL подстраховывает в любом случае).
"""

import json
import logging
import re

import redis.asyncio as aioredis  # noqa: F401 — re-exported for test monkeypatch backward compat
from sqlalchemy import update

from src.clients.ssh import SshError
from src.core.config import get_settings  # noqa: F401 — re-exported for downstream / future tests
from src.core.identifiers import validate_task_id
from src.db.session import AsyncSessionLocal
from src.main import broker
from src.models import Task
from src.repositories import task as task_repo
from src.services import redis_pool, server_service_client, ssh_client
from src.services.redis_stash_crypto import (
    aad_for_redis_stash,
    decrypt_stash,
    stash_id_from_key,
)
from src.tasks._node_exporter_helpers import install_node_exporter
from src.tasks._runner import run_task
from src.tasks._target_runner import DirectRunner

logger = logging.getLogger(__name__)

# В audit пускаем только server_id и имя управляющего юзера — bootstrap-логин
# и пароль наружу не уходят ни при каких обстоятельствах.
AUDIT_SAFE_FIELDS: set[str] = {
    "server_id", "management_user", "management_mode", "prepared",
    "node_exporter",
}

# Формат ключа задаёт server_service (`worker_client.prepare_creds_key` +
# `_new_id("pcd_")` → `dbos:prepare_creds:pcd_<uuid4.hex>`). Жёстко его
# валидируем, чтобы при компрометации payload в очереди (или при отсутствии
# AUTH на Redis в staging/dev) нельзя было подсунуть произвольный ключ и
# прочитать чужие значения через `_read_bootstrap_creds`. Пускаем любое
# Тело — alnum/`_`/`-`, длиной до 128. Smin = 1 (защита от пустого тела
# уже есть); нижняя граница нужна только чтобы не пускать `dbos:prepare_creds:`
# с пустым хвостом — это покрывает `{1,128}`. Длину uuid не хардкодим на
# случай смены id-фабрики.
_BOOTSTRAP_KEY_RE = re.compile(r"^dbos:prepare_creds:[A-Za-z0-9_\-]{1,128}$")

# Маркер «SSH-bootstrap уже отработал успешно» в Redis. Закрывает race:
# bootstrap_management_user прошёл, submit_prepared упал (network к
# server_service лёг), retry приходит позже когда TTL bootstrap-ключа уже
# истёк → без маркера мы валим прогресс с SSH_BOOTSTRAP_CREDS_MISSING,
# хотя хост фактически готов. С маркером retry пропускает SSH-шаг и идёт
# сразу на submit_prepared (он идемпотентен на стороне server_service).
#
# TTL маркера крупно больше суммарного back-off retry'я (exponential base
# 10s × до max_attempts) — оператор успеет либо завершить retry, либо
# увидеть зависшее «prepare без callback» и поднять issue.
_PREPARED_MARKER_PREFIX = "dbos:prepared_marker:"
_PREPARED_MARKER_TTL_SECONDS = 3600

# Маркер «привязанные аккаунты уже заведены» в Redis. Отдельный от
# bootstrap-маркера: SSH-bootstrap и provision привязанных учёток — это два
# независимых шага, и второй может упасть (например, sshd на доли секунды
# недоступен в окне reload'а после хардинга), когда первый уже прошёл и
# хардинг выключил парольный вход. Без отдельного маркера retry видел бы
# bootstrap-маркер, целиком пропускал блок (включая provision) и уходил в
# submit_prepared — сервер репортился подготовленным, а ключи привязанных
# аккаунтов на бокс не доезжали. С маркером retry пропускает только то, что
# реально сделано, и доводит provision до конца под управляющей key-сессией.
_LINKED_PROVISIONED_MARKER_PREFIX = "dbos:linked_provisioned_marker:"
_NODE_EXPORTER_MARKER_PREFIX = "dbos:node_exporter_marker:"


async def _read_bootstrap_creds(creds_key: str) -> dict:
    """Прочитать bootstrap-креды из Redis по ключу-ссылке из payload.

    Креды кладёт server_service при dispatch (с TTL). Читаем на каждой попытке,
    поэтому retry работает, пока жив TTL. Нет ключа / истёк → `SshError(
    SSH_BOOTSTRAP_CREDS_MISSING)` — task завершится FAILED с понятным
    last_error, а не молчаливо зайдёт под `root` без пароля.

    Bootstrap-creds лежат в Redis как envelope-encrypted token: writer
    (server_service.internal_service) и reader (worker) делят общий
    `redis_stash_encryption_key`, value шифруется AES-256-GCM поверх
    HKDF-SHA256 с AAD от stash-id. Mitigation'ы: короткий TTL
    (`PREPARE_CREDS_TTL_SECONDS` ≈ 15 мин в server_service), обязательный
    redis AUTH в prod, неугадываемый суффикс ключа
    (`dbos:prepare_creds:<task_id>`), `_validate_dispatch_creds_key` whitelist
    на формат входящего payload-ключа. Подробнее — `AUDIT_EVENTS.md` секция
    Threat model.
    """
    client = redis_pool.get_redis()
    raw = await client.get(creds_key)
    if raw is None:
        raise SshError(
            error_code="SSH_BOOTSTRAP_CREDS_MISSING",
            host="",
            message=(
                "bootstrap credentials are missing or expired in Redis; "
                "re-run prepare to supply them again"
            ),
        )
    # Stash в Redis — envelope-token (`v<N>$<nonce>$<ct>`) поверх общего с
    # server_service master-key'я. Decrypt с AAD от stash-id; corrupt/swap/
    # mismatch'нутый AAD → AppException(STASH_DECRYPT_*) пробрасывается до
    # `_runner` (task FAILED с явным error_code). Битый AppException не
    # сворачиваем в SSH_BOOTSTRAP_CREDS_MISSING — это разные сигналы для
    # оператора: «истёк TTL» vs «крипто отказало».
    text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
    plaintext = decrypt_stash(
        text, aad=aad_for_redis_stash(stash_id_from_key(creds_key)),
    )
    try:
        return json.loads(plaintext)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SshError(
            error_code="SSH_BOOTSTRAP_CREDS_MISSING",
            host="",
            message=(
                "bootstrap credentials payload is malformed; "
                "re-run prepare to supply them again"
            ),
        ) from exc


async def _mark_bootstrap_succeeded(task_id: str, mode: str | None) -> None:
    """Записать маркер «SSH-bootstrap отработал» в Redis с TTL.

    Ставится сразу после успешного `bootstrap_management_user`. На retry'е
    `_impl` смотрит этот маркер и пропускает SSH-этап (он идемпотентен,
    но требует bootstrap-кред, которые могут истечь по TTL раньше, чем
    retry дойдёт). Маркер живёт `_PREPARED_MARKER_TTL_SECONDS` — за это
    время retry либо доделает callback, либо оператор поднимет сервер
    руками. Ошибки глушим — best-effort оптимизация, без маркера retry
    отработает как раньше через cred'ы.

    В значение кладём детектнутый режим (строкой), чтобы callback-retry,
    пропустивший SSH-этап, передал тот же режим в `submit_prepared`. Если
    режим неизвестен — пишем `"1"` как раньше (back-compat для старых маркеров).
    """
    validate_task_id(task_id)
    client = redis_pool.get_redis()
    try:
        await client.set(
            _PREPARED_MARKER_PREFIX + task_id, mode or "1",
            ex=_PREPARED_MARKER_TTL_SECONDS,
        )
    except Exception:  # noqa: BLE001
        logger.debug("failed to set prepared marker", exc_info=True)


async def _read_bootstrap_succeeded(task_id: str) -> tuple[bool, str | None]:
    """Проверить, отработал ли SSH-bootstrap ранее, и вернуть сохранённый режим.

    Возвращает `(succeeded, mode)`. `mode` — детектнутый режим из значения
    маркера; `None`, если маркера нет либо значение — legacy-`"1"`.
    """
    validate_task_id(task_id)
    client = redis_pool.get_redis()
    raw = await client.get(_PREPARED_MARKER_PREFIX + task_id)
    if raw is None:
        return False, None
    value = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
    mode = value if value and value != "1" else None
    return True, mode


async def _delete_bootstrap_succeeded(task_id: str) -> None:
    """Снять маркер после успешного `submit_prepared` — best-effort cleanup."""
    validate_task_id(task_id)
    client = redis_pool.get_redis()
    try:
        await client.delete(_PREPARED_MARKER_PREFIX + task_id)
    except Exception:  # noqa: BLE001
        logger.debug("failed to delete prepared marker", exc_info=True)


async def _mark_linked_provisioned(task_id: str) -> None:
    """Записать маркер «привязанные аккаунты заведены» в Redis с TTL.

    Ставится сразу после успешного `_provision_linked_accounts`. На retry'е
    `_impl` смотрит этот маркер и пропускает повторный provision (он
    идемпотентен, но требует чтения bootstrap-stash'а, который может истечь
    по TTL раньше, чем retry дойдёт). TTL — общий с bootstrap-маркером.
    Ошибки глушим: best-effort, без маркера retry просто перезаведёт учётки.
    """
    validate_task_id(task_id)
    client = redis_pool.get_redis()
    try:
        await client.set(
            _LINKED_PROVISIONED_MARKER_PREFIX + task_id, "1",
            ex=_PREPARED_MARKER_TTL_SECONDS,
        )
    except Exception:  # noqa: BLE001
        logger.debug("failed to set linked provisioned marker", exc_info=True)


async def _read_linked_provisioned(task_id: str) -> bool:
    """True, если привязанные аккаунты уже были заведены на прошлой попытке."""
    validate_task_id(task_id)
    client = redis_pool.get_redis()
    raw = await client.get(_LINKED_PROVISIONED_MARKER_PREFIX + task_id)
    return raw is not None


async def _delete_linked_provisioned(task_id: str) -> None:
    """Снять маркер после успешного `submit_prepared` — best-effort cleanup."""
    validate_task_id(task_id)
    client = redis_pool.get_redis()
    try:
        await client.delete(_LINKED_PROVISIONED_MARKER_PREFIX + task_id)
    except Exception:  # noqa: BLE001
        logger.debug("failed to delete linked provisioned marker", exc_info=True)


async def _mark_node_exporter_installed(task_id: str) -> None:
    """Записать marker успешной установки node_exporter внутри prepare."""
    validate_task_id(task_id)
    client = redis_pool.get_redis()
    try:
        await client.set(
            _NODE_EXPORTER_MARKER_PREFIX + task_id, "1",
            ex=_PREPARED_MARKER_TTL_SECONDS,
        )
    except Exception:  # noqa: BLE001
        logger.debug("failed to set node_exporter marker", exc_info=True)


async def _read_node_exporter_installed(task_id: str) -> bool:
    """True, если node_exporter уже установлен на прошлой попытке prepare."""
    validate_task_id(task_id)
    client = redis_pool.get_redis()
    raw = await client.get(_NODE_EXPORTER_MARKER_PREFIX + task_id)
    return raw is not None


async def _delete_node_exporter_installed(task_id: str) -> None:
    """Снять marker node_exporter после успешного prepare."""
    validate_task_id(task_id)
    client = redis_pool.get_redis()
    try:
        await client.delete(_NODE_EXPORTER_MARKER_PREFIX + task_id)
    except Exception:  # noqa: BLE001
        logger.debug("failed to delete node_exporter marker", exc_info=True)


async def _delete_bootstrap_creds(creds_key: str) -> None:
    """Удалить bootstrap-креды из Redis после успешного prepare (best-effort).

    TTL подчистит ключ в любом случае; явный DELETE просто сокращает окно
    жизни кред до минимума. Ошибку глушим — это посмертный cleanup.
    """
    client = redis_pool.get_redis()
    try:
        await client.delete(creds_key)
    except Exception:  # noqa: BLE001
        logger.debug("failed to delete bootstrap creds key", exc_info=True)


async def _provision_linked_accounts(
    server_id: str,
    management_user: str,
    *,
    linked_accounts: list[dict],
    payload: dict,
    management_private_key: str | None = None,
) -> None:
    """Завести на свежеподготовленном сервере все привязанные аккаунты.

    Каждый элемент `linked_accounts` — это креды одного server_account'а,
    сложенные server_service'ом в bootstrap-stash рядом с bootstrap-кредами:
    `{login, password?, ssh_public_key?, ssh_private_key?, has_sudo,
    unix_groups, shell, home_dir}`. Провизионим под управляющей сессией по
    ключу (сервер уже managed), переиспользуя обычный `ssh_client.provision_user`
    — useradd + chpasswd (если есть пароль) + authorized_keys (ключ; нет ключа
    — provision_user/create_user отрабатывает без него, как в discovered-флоу).

    `force_replace=True`: prepare — это первичная (или после reimage) раскатка,
    authorized_keys аккаунта приводим к актуальному ключу из server_service,
    а не дописываем поверх возможного мусора со старого образа.

    Ошибку одного аккаунта НЕ глушим: prepare должен дать оператору честный
    сигнал, что бокс подготовлен не полностью. Аккаунты без login'а (битый
    payload) пропускаем — нет смысла тащить их в useradd с None.
    """
    if not linked_accounts:
        return
    # Управляющая сессия: provision_user сам соберёт ключевую сессию под
    # management_user, если credentials помечены managed. Для каждого
    # аккаунта собираем отдельный creds-dict (host/port из payload через
    # apply_session_hints), чтобы _build_session выбрал key-сессию.
    for account in linked_accounts:
        login = account.get("login")
        if not login:
            logger.warning(
                "prepare provision: skipping linked account without login on %s",
                server_id,
            )
            continue
        # Форсируем management-сессию по ключу под management_user — bootstrap
        # только что её настроил и проверил. `apply_session_hints` тут не
        # зовём: в payload prepare сервер на момент dispatch'а ещё
        # is_managed=false, хинты сбросили бы managed-режим. host/port берём
        # из payload напрямую (их кладёт server_service для адресации).
        # Приватный ключ кладём прямо в creds из mgmt_install: на этом этапе
        # сервер ещё pending_apply, fetch управляющих кред вернул бы пусто/
        # старое — используем только что установленную пару.
        creds: dict = {
            "login": login,
            "is_managed": True,
            "management_user": management_user,
            "management_private_key": management_private_key,
        }
        host = payload.get("host") or payload.get("ssh_host")
        if host:
            creds["host"] = host
        port = payload.get("ssh_port") or payload.get("port")
        if port:
            creds["ssh_port"] = port
        await ssh_client.provision_user(
            creds, server_id,
            login=login,
            new_password=account.get("password"),
            groups=account.get("unix_groups") or [],
            has_sudo=bool(account.get("has_sudo")),
            shell=account.get("shell"),
            home_dir=account.get("home_dir"),
            public_key=account.get("ssh_public_key"),
            force_replace=True,
        )


@broker.task("server.prepare")
async def server_prepare(task_id: str) -> None:
    """Бутстрап управления: завести управляющего пользователя + положить ключ.

    Параметры: `task_id`. Payload — `server_id`, `bootstrap_creds_key`
    (ссылка на Redis с одноразовыми bootstrap-кредами), опц.
    `target_department_id`, плюс SSH-поля (`host`/`port`), если server_service
    их положил.

    Поток: прочитать bootstrap-креды из Redis по ссылке →
    `ssh_client.bootstrap_management_user` (useradd управляющего юзера + sudo +
    NOPASSWD + authorized_keys, idempotent) → `submit_prepared` callback →
    удалить креды из Redis.

    Idempotent: повторный prepare не падает на уже заведённом юзере / ключе;
    retry работает, пока жив Redis-TTL ключа кред.

    Возвращает: `{server_id, management_user, prepared}`.
    Связано с: `server.prepare` audit action, `server_service.internal_service
    .record_server_prepared`.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        target_dept = payload.get("target_department_id")
        # `creds_key` объявляется на верхнем уровне функции, а не внутри
        # `if not already_bootstrapped`-ветки ниже: cleanup-блок в конце
        # `_impl` ссылается на эту переменную независимо от того, ходили ли
        # мы по bootstrap-пути или прошли по marker'у. Переезд declare'а
        # внутрь if-блока сломает cleanup с NameError на already_bootstrapped
        # retry'е.
        creds_key = payload.get("bootstrap_creds_key")
        settings = get_settings()
        # Имя управляющей учётки и пер-режимный конфиг — источник истины это
        # ManagementUserConfig в server_service (едет в payload). env остаётся
        # фолбэком для имени, если payload пуст (back-compat).
        management_user = payload.get("management_login") or settings.ssh_management_user
        modes = payload.get("management_modes") or {}
        # Режим определяется на боксе; если SSH-этап пропущен по marker'у
        # (callback-retry), берём ранее сохранённый из payload-scratch.
        detected_mode: str | None = payload.get("detected_management_mode")

        # Два независимых маркера прошлых попыток:
        #   * bootstrap — SSH-bootstrap отработал (юзер/ключ/sudo на месте,
        #     парольный вход, возможно, уже выключен хардингом);
        #   * linked — привязанные аккаунты заведены.
        # Если предыдущая попытка прошла SSH-bootstrap, но упала позже
        # (provision привязанных учёток / submit_prepared) — соответствующий
        # шаг на retry'е пропускаем по маркеру. Иначе при истёкшем TTL
        # bootstrap-ключа мы валим прогресс с SSH_BOOTSTRAP_CREDS_MISSING,
        # хотя хост фактически готов; а повторный bootstrap по паролю после
        # хардинга вообще не зашёл бы (парольный вход выключен).
        already_bootstrapped, marker_mode = await _read_bootstrap_succeeded(task_id)
        if marker_mode is not None:
            detected_mode = marker_mode
        linked_provisioned = await _read_linked_provisioned(task_id)
        node_exporter_installed = await _read_node_exporter_installed(task_id)

        # Bootstrap-stash нужен, пока есть незавершённая SSH-работа: либо ещё не
        # прошёл сам bootstrap, либо привязанные аккаунты не заведены, либо
        # node_exporter не установлен. Когда все SSH-шаги отмечены маркерами,
        # retry идёт прямо в submit_prepared, не трогая Redis-stash (его TTL к
        # этому моменту может уже истечь — для этого маркеры и существуют).
        bootstrap: dict | None = None
        mgmt_install: dict = {}
        mgmt_private_key: str | None = None
        if (
            not already_bootstrapped
            or not linked_provisioned
            or not node_exporter_installed
        ):
            if not creds_key:
                raise SshError(
                    error_code="SSH_BOOTSTRAP_CREDS_MISSING",
                    host="",
                    message="payload has no bootstrap_creds_key reference",
                )
            if not isinstance(creds_key, str) or not _BOOTSTRAP_KEY_RE.fullmatch(creds_key):
                # Не доверяем строке из payload: если очередь
                # скомпрометирована, произвольный ключ дал бы читателю
                # Redis любые значения.
                raise SshError(
                    error_code="SSH_BOOTSTRAP_CREDS_MISSING",
                    host="",
                    message="bootstrap_creds_key has unexpected format",
                )

            # Читаем одноразовые креды из Redis. Пока жив TTL — retry
            # работает; истёк → SSH_BOOTSTRAP_CREDS_MISSING.
            bootstrap = await _read_bootstrap_creds(creds_key)

            # Per-server управляющий материал лежит рядом с bootstrap-кредами в
            # том же stash'е (его генерит и кладёт server_service). Приватный
            # ключ нужен и для bootstrap'а (анти-локаут), и для provision'а
            # привязанных учёток (управляющая key-сессия) — достаём здесь, на
            # обоих путях.
            mgmt_install = bootstrap.get("mgmt_install") or {}
            mgmt_private_key = mgmt_install.get("private_key")
            # Имя из mgmt_install имеет приоритет — это то имя, под которым
            # server_service сгенерил пару; payload/env остаются фолбэком. На
            # already_bootstrapped-retry'е (provision не доделан) имя тоже берём
            # из stash'а, чтобы управляющая сессия зашла под правильным юзером.
            if mgmt_install.get("management_user"):
                management_user = mgmt_install["management_user"]

        if not already_bootstrapped:
            # Без управляющего материала ставить нечего — fail-fast с понятной
            # ошибкой, а не молчаливый bootstrap без ключа/пароля.
            mgmt_public_key = mgmt_install.get("public_key")
            mgmt_password = mgmt_install.get("password")
            missing = [
                name for name, value in (
                    ("public_key", mgmt_public_key),
                    ("private_key", mgmt_private_key),
                    ("password", mgmt_password),
                ) if not value
            ]
            if missing:
                raise SshError(
                    error_code="SSH_MGMT_INSTALL_INCOMPLETE",
                    host="",
                    message=(
                        "prepare stash has no usable mgmt_install material "
                        f"(missing: {', '.join(missing)}); re-run prepare"
                    ),
                )

            creds = {
                "login": bootstrap.get("bootstrap_login"),
                "password": bootstrap.get("bootstrap_password"),
            }
            # Нужны только host/ssh_port из payload —
            # `bootstrap_management_user` сам открывает password-сессию через
            # SshClient, без management-логики. is_managed/management_user
            # выставляются для симметрии с rotate/provision-handler'ами:
            # creds в дальнейшем нигде не переиспользуются, но если кто-то
            # добавит post-bootstrap step с management-сессией — флаги уже
            # правильные.
            ssh_client.apply_session_hints(creds, payload)
            # Перед хардингом sshd worker проверяет, что вход по управляющему
            # ключу реально работает (анти-локаут) — для этого передаём
            # приватный ключ из mgmt_install. Пароль ставим через chpasswd.
            bootstrap_result = await ssh_client.bootstrap_management_user(
                creds, server_id,
                management_user=management_user,
                public_key=mgmt_public_key,
                modes=modes,
                management_private_key=mgmt_private_key,
                management_password=mgmt_password,
                harden_sshd=settings.ssh_harden_after_prepare,
            )
            detected_mode = bootstrap_result.get("management_mode")
            # Маркер ставим ДО provision'а и callback'а — если они упадут,
            # retry увидит маркер и не будет повторять SSH-bootstrap по паролю
            # (после хардинга парольный вход выключен, повтор не зашёл бы).
            await _mark_bootstrap_succeeded(task_id, detected_mode)

        if not linked_provisioned:
            # Завести все привязанные к серверу аккаунты на этапе prepare.
            # Сервер уже фактически управляемый (ключ проверен), поэтому
            # провизионим под управляющей key-сессией. Креды аккаунтов
            # (password + ssh keypair + атрибуты) server_service кладёт в тот же
            # Redis-stash, что и bootstrap — в payload едет только ссылка.
            #
            # Маркер ставим ТОЛЬКО после успеха: provision — отдельный шаг от
            # bootstrap'а и может упасть, когда bootstrap уже прошёл (sshd на
            # миг недоступен в окне reload'а после хардинга, бокс лёг и т.п.).
            # Без отдельного маркера retry пропустил бы provision по
            # bootstrap-маркеру и отрепортил бы сервер подготовленным, а ключи
            # привязанных аккаунтов на бокс не доехали бы. Так retry перечитает
            # stash и доведёт provision до конца; падение provision'а не
            # «съедается» — task остаётся в retry/FAILED с реальной ошибкой.
            await _provision_linked_accounts(
                server_id, management_user,
                linked_accounts=(bootstrap or {}).get("linked_accounts") or [],
                payload=payload,
                management_private_key=mgmt_private_key,
            )
            await _mark_linked_provisioned(task_id)

        if not node_exporter_installed:
            if not mgmt_private_key:
                raise SshError(
                    error_code="SSH_MGMT_INSTALL_INCOMPLETE",
                    host="",
                    message=(
                        "prepare stash has no management private key for "
                        "node_exporter install; re-run prepare"
                    ),
                )
            creds = {
                "is_managed": True,
                "management_user": management_user,
                "management_private_key": mgmt_private_key,
            }
            ssh_client.apply_session_hints(creds, {
                **payload,
                "is_managed": True,
                "management_user": management_user,
            })
            session = ssh_client.build_session(creds, server_id)
            async with session as ssh:
                await install_node_exporter(DirectRunner(ssh), ssh.host)
            await _mark_node_exporter_installed(task_id)

        await server_service_client.submit_prepared(
            server_id, management_user, target_dept,
            management_mode=detected_mode,
        )
        # Cleanup ключа в Redis: ходим только при валидном формате, иначе
        # рискнём задеть чужой keyspace при компрометации payload (тот же
        # guard, что на чтении). Если ключ есть, но формат битый — пишем
        # warning, оператор увидит «висящий до TTL» ключ. Это в основном
        # случается на ретрае с маркером, где предварительный fullmatch на
        # чтении уже не отрабатывал (already_bootstrapped=True).
        if creds_key:
            if isinstance(creds_key, str) and _BOOTSTRAP_KEY_RE.fullmatch(creds_key):
                await _delete_bootstrap_creds(creds_key)
            else:
                logger.warning(
                    "bootstrap_creds_key cleanup skipped: malformed key",
                    extra={"task_id": task_id, "server_id": server_id},
                )
        await _delete_bootstrap_succeeded(task_id)
        await _delete_linked_provisioned(task_id)
        await _delete_node_exporter_installed(task_id)
        # Defense-in-depth: `bootstrap_creds_key` сам по себе — это ссылка
        # в Redis-неймспейс с одноразовыми bootstrap-кредами. TTL и явный
        # DELETE уже закрыли значение в Redis, но ссылка в `tasks.payload`
        # остаётся жить вместе с task-row до retention cleanup'а. Стираем
        # её, чтобы оператор с SELECT на worker.tasks не мог попытаться
        # прочитать секрет (например, если в каком-то окружении TTL ещё
        # не истёк, либо DELETE упал). Best-effort: если scrub упадёт —
        # task уже SUCCEEDED, ронять happy-path нет смысла.
        try:
            async with AsyncSessionLocal() as scrub_session:
                await task_repo.scrub_payload_keys(
                    scrub_session, task_id, ["bootstrap_creds_key"],
                )
                # `mark_succeeded` оставляет last_error как есть, поэтому после
                # упавшей attempt 1 на успешной attempt в last_error висит
                # stale-ошибка. Гасим её здесь: prepare завершился успехом,
                # старая ошибка вводит оператора в заблуждение. Best-effort
                # (task уже SUCCEEDED, ронять happy-path смысла нет).
                await scrub_session.execute(
                    update(Task).where(Task.id == task_id).values(last_error=None)
                )
                await scrub_session.commit()
        except Exception:  # noqa: BLE001
            logger.warning(
                "failed to scrub bootstrap_creds_key from payload",
                exc_info=True,
                extra={"task_id": task_id},
            )
        return {
            "server_id": server_id,
            "management_user": management_user,
            "management_mode": detected_mode,
            "prepared": True,
            "node_exporter": True,
        }

    await run_task(
        task_id,
        audit_action="server.prepare",
        audit_target_type="server",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS,
    )
