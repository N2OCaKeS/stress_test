"""Хвост пайплайна `prepare-for-test` (`server.prepare_for_test`).

К этому моменту restore и бутстрап управления уже сделаны цепочкой
`acs.snapshot_restore` → `server.prepare`. Дальше:

  1. `user_provision` — учётка исполнения теста (пароль/ключ из stash);
  2. `pam_fix` — PAM-правка профиля подготовки, если флаг; `skip_pam_fix`
     в payload (флаг стенда сценария) снимает шаг независимо от профиля;
  3. `stand_setup` с `phase=before_kernel`;
  4. `kernel_change` — пакеты ядра + `GRUB_DEFAULT` + параметры из
     `stand_setup` + `update-grub`;
  5. `mode_switch` — режим безопасности Astra (`astra-modeswitch`); при
     `skip_mode_switch` в payload (подготовка `revert_only`) шаг пропускается;
  6. `reboot_verify` — ребут и ожидание готовности по профилю подготовки;
  7. `stand_setup` с `phase=after_boot` и, если `reboot_after`, ещё один
     `reboot_verify`.

`server.stand_setup` использует тот же порядок — шаги 2–7 без учётки,
смены ядра и режима.

Все шаги идут от платформенной учётки `dbos`; пароль и ключ тестовой
учётки едут одноразовым Redis-stash'ем, в payload — только ссылка.

Исход докладывается server_service (`submit_prepare_for_test_result`).
Задача одноразовая: авто-retry после половины переключённого grub'а
сделал бы только хуже.
"""

from __future__ import annotations

import json
import logging
import re

from src.clients.ssh import SshClient, SshError
from src.core.config import get_settings
from src.core.constants import LAST_ERROR_MAX_LEN
from src.main import broker
from src.services import redis_pool, server_service_client, ssh_client
from src.services.redis_stash_crypto import (
    aad_for_redis_stash,
    decrypt_stash,
    stash_id_from_key,
)
from src.tasks._account_helpers import resolve_ssh_creds
from src.tasks._runner import run_task
from src.tasks._stand_setup_helpers import (
    PHASE_AFTER_BOOT,
    PHASE_BEFORE_KERNEL,
    STEP_PAM_FIX,
    STEP_STAND_SETUP,
    add_kernel_cmdline_params,
    apply_pam_fix,
    parse_provisioning,
    parse_stand_setup,
    run_setup_script,
    update_grub,
)
from src.tasks.acs_snapshots import (
    _await_reachability,
    _resolve_ssh_port,
    verify_system_running,
)
from src.utils.redaction import redact_error_message

logger = logging.getLogger(__name__)

# Ни пароль тестовой учётки, ни её ключ в audit не идут — только факт шагов.
AUDIT_SAFE_FIELDS: set[str] = {
    "server_id", "prepare_request_id", "kernel", "mode", "test_username",
    "succeeded",
}

STEP_USER_PROVISION = "user_provision"
STEP_KERNEL_CHANGE = "kernel_change"
STEP_MODE_SWITCH = "mode_switch"
STEP_REBOOT_VERIFY = "reboot_verify"

# Уровень безопасности Astra, который выставляет `astra-modeswitch`. Воронеж
# (1) в этом пайплайне никогда не запрашивается, но `get` в теории может его
# вернуть на боксе с нестандартной историей — умеем распознать для warning'а.
_MODE_TO_LEVEL = {"orel": "0", "smolensk": "2"}
_LEVEL_TO_MODE = {"0": "orel", "1": "voronezh", "2": "smolensk"}

# Формат ключа задаёт server_service (`worker_client.dispatch_creds_key`).
# Валидируем так же жёстко, как `server.prepare` валидирует свой
# bootstrap-ключ: иначе подменённый payload дал бы чтение чужого keyspace.
_TEST_CREDS_KEY_RE = re.compile(r"^dbos:dispatch_creds:[A-Za-z0-9_\-]{1,128}$")

# Версия ядра из каталога РЦ подставляется в имена пакетов и в grep, то есть
# уходит в shell-строку. server_service уже сверил её со списком
# `os_versions.kernels`, но на дне держим allow-list: имя пакета Debian —
# буквы, цифры, точка, дефис, плюс, тильда, подчёркивание.
_KERNEL_RE = re.compile(r"^[A-Za-z0-9._+~-]{1,128}$")

# `menuentry_id` из grub.cfg подставляется в sed-выражение, где `/` — ещё и
# разделитель самого выражения. Слэш и любые метасимволы отбиваем: реальные
# id выглядят как `gnulinux-<ver>-advanced-<uuid>`.
_MENUENTRY_RE = re.compile(r"^[A-Za-z0-9._+~:-]{1,256}$")


async def _read_test_creds(creds_key: str) -> dict:
    """Прочитать креды тестовой учётки из Redis по ссылке из payload.

    Кладёт их server_service при диспатче (envelope-encrypted, с TTL), читаем
    один раз за попытку. Ключа нет / истёк → понятный `SSH_TEST_CREDS_MISSING`,
    а не молчаливое создание пользователя без пароля.
    """
    if not isinstance(creds_key, str) or not _TEST_CREDS_KEY_RE.fullmatch(creds_key):
        raise SshError(
            error_code="SSH_TEST_CREDS_MISSING",
            host="",
            message="prepare_for_test payload has no valid test_creds_key reference",
        )
    client = redis_pool.get_redis()
    raw = await client.get(creds_key)
    if raw is None:
        raise SshError(
            error_code="SSH_TEST_CREDS_MISSING",
            host="",
            message=(
                "test-user credentials are missing or expired in Redis; "
                "re-run prepare-for-test to supply them again"
            ),
        )
    text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
    plaintext = decrypt_stash(
        text, aad=aad_for_redis_stash(stash_id_from_key(creds_key)),
    )
    try:
        stash = json.loads(plaintext)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SshError(
            error_code="SSH_TEST_CREDS_MISSING",
            host="",
            message="test-user credentials payload is malformed",
        ) from exc
    # Логин, пароль и публичный ключ обязательны: пустой пароль `create_user`
    # молча пропустил бы (chpasswd не зовётся), а без ключа воркер
    # testing_service не войдёт на стенд.
    required = ("test_username", "test_password", "test_ssh_public_key")
    if not isinstance(stash, dict) or not all(
        isinstance(stash.get(name), str) and stash.get(name) for name in required
    ):
        raise SshError(
            error_code="SSH_TEST_CREDS_MISSING",
            host="",
            message="test-user credentials payload lacks login, password or public key",
        )
    return stash


async def _read_stash(key: str) -> dict:
    """Прочитать произвольный stash по ссылке (скрипт настройки стенда)."""
    if not isinstance(key, str) or not _TEST_CREDS_KEY_RE.fullmatch(key):
        raise SshError(error_code="SSH_TEST_CREDS_MISSING", host="", message="invalid stash key reference")
    raw = await redis_pool.get_redis().get(key)
    if raw is None:
        raise SshError(error_code="SSH_TEST_CREDS_MISSING", host="", message="stash is missing or expired")
    text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
    plaintext = decrypt_stash(text, aad=aad_for_redis_stash(stash_id_from_key(key)))
    try:
        data = json.loads(plaintext)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SshError(error_code="SSH_TEST_CREDS_MISSING", host="", message="stash payload is malformed") from exc
    return data if isinstance(data, dict) else {}


async def _delete_test_creds(creds_key: str) -> None:
    """Снять stash после использования — best-effort, TTL подстрахует."""
    try:
        client = redis_pool.get_redis()
        await client.delete(creds_key)
    except Exception:  # noqa: BLE001
        logger.debug("failed to delete prepare_for_test creds stash", exc_info=True)


def _validate_kernel(kernel: str, host: str) -> str:
    if not isinstance(kernel, str) or not _KERNEL_RE.fullmatch(kernel):
        raise SshError(
            error_code="PREPARE_FOR_TEST_INVALID_KERNEL",
            host=host,
            message="kernel version contains characters disallowed for a package name",
        )
    return kernel


def _validate_mode(mode: str, host: str) -> str:
    if mode not in _MODE_TO_LEVEL:
        raise SshError(
            error_code="PREPARE_FOR_TEST_INVALID_MODE",
            host=host,
            message=f"unsupported astra security mode {mode!r}",
        )
    return mode


async def _install_kernel_packages(ssh: SshClient, kernel: str) -> None:
    """Доставить linux-image / linux-headers / linux-astra-modules нужной версии.

    Ненулевой код возврата не валим здесь: apt может ругаться на уже
    установленный пакет из другого источника, настоящий сигнал провала —
    отсутствие menuentry для этого ядра на следующем шаге.
    """
    for package in (
        f"linux-image-{kernel}",
        f"linux-headers-{kernel}",
        f"linux-astra-modules-{kernel}",
    ):
        rc, _stdout, stderr = await ssh.run(
            f"sh -c 'dpkg -s {package} > /dev/null 2>&1 || "
            f"DEBIAN_FRONTEND=noninteractive apt-get install {package} -y'",
            sudo=True,
        )
        if rc != 0:
            logger.warning(
                "prepare_for_test: package %s returned rc=%s: %s",
                package, rc, (stderr or "").strip()[:200],
            )


async def _switch_default_kernel(ssh: SshClient, kernel: str, host: str) -> str:
    """Переключить `GRUB_DEFAULT` на menuentry запрошенного ядра.

    Вытаскивает `menuentry_id` нужного ядра из `grub.cfg` (17-е поле строки),
    подставляет в `GRUB_DEFAULT`; пустой результат grep'а — ошибка.
    `update-grub` вызывается отдельно, общий с параметрами ядра из
    `stand_setup`.
    """
    # Форма 1:1 из legacy: под sudo идёт только `cat` (grub.cfg читается
    # root'ом), остальное — обычный пайп в шелле сессии. Оборачивать всё в
    # `sh -c "..."` тут нельзя: `$17` внутри двойных кавычек раскрылся бы
    # внешним шеллом ещё до awk.
    rc, stdout, stderr = await ssh.run(
        "cat /boot/grub/grub.cfg | grep menuentry_id | "
        f"awk '{{print $17}}' | grep {kernel} | tr -d \"'\"",
        sudo=True,
    )
    kernel_conf = (stdout or "").strip().splitlines()
    if rc != 0 or not kernel_conf or not kernel_conf[0]:
        raise SshError(
            error_code="PREPARE_FOR_TEST_KERNEL_NOT_IN_GRUB",
            host=host,
            cmd_sanitized="grep menuentry_id /boot/grub/grub.cfg",
            returncode=rc,
            stderr=(stderr or "").strip(),
            message=f"no grub menuentry found for kernel {kernel}",
        )
    menuentry = kernel_conf[0]
    if not _MENUENTRY_RE.fullmatch(menuentry):
        raise SshError(
            error_code="PREPARE_FOR_TEST_KERNEL_NOT_IN_GRUB",
            host=host,
            message="grub menuentry id contains unexpected characters",
        )

    rc, _stdout, stderr = await ssh.run(
        "sed -i 's/.*GRUB_DEFAULT=.*/GRUB_DEFAULT="
        f"{menuentry}/' /etc/default/grub",
        sudo=True,
    )
    if rc != 0:
        raise SshError(
            error_code="PREPARE_FOR_TEST_GRUB_WRITE_FAILED",
            host=host,
            cmd_sanitized="sed -i GRUB_DEFAULT /etc/default/grub",
            returncode=rc,
            stderr=(stderr or "").strip(),
            message="failed to set GRUB_DEFAULT",
        )
    return menuentry


async def _read_astra_mode(ssh: SshClient, host: str) -> str:
    """Прочитать текущий уровень безопасности через `astra-modeswitch get`.

    Ожидаемый вывод — один из `0`/`1`/`2`. Что угодно ещё (мусор,
    ненулевой rc) — считаем провалом шага, не гадаем.
    """
    rc, stdout, stderr = await ssh.run("astra-modeswitch get", sudo=True)
    level = (stdout or "").strip()
    if rc != 0 or level not in _LEVEL_TO_MODE:
        raise SshError(
            error_code="PREPARE_FOR_TEST_MODE_SWITCH_FAILED",
            host=host,
            cmd_sanitized="astra-modeswitch get",
            returncode=rc,
            stderr=(stderr or "").strip(),
            message=f"unexpected astra-modeswitch get output: {level!r}",
        )
    return _LEVEL_TO_MODE[level]


async def _enable_smolensk_controls(ssh: SshClient, host: str) -> None:
    """МРД + МКЦ — второй и третий шаг перевода в Смоленск, оба идемпотентны."""
    for cmd in ("astra-mac-control enable", "astra-mic-control enable"):
        rc, _stdout, stderr = await ssh.run(cmd, sudo=True)
        if rc != 0:
            raise SshError(
                error_code="PREPARE_FOR_TEST_MODE_SWITCH_FAILED",
                host=host,
                cmd_sanitized=cmd,
                returncode=rc,
                stderr=(stderr or "").strip(),
                message=f"{cmd} failed",
            )


async def _switch_security_mode(ssh: SshClient, mode: str, host: str) -> str | None:
    """Перевести бокс в запрошенный режим безопасности, если он ещё не в нём.

    `astra-modeswitch get` до и после `set`: до — чтобы не дублировать вызов,
    если бокс уже на нужном уровне (и заодно поймать нестандартную историю
    диска — расхождение попадает в возвращаемый non-fatal warning, а не
    молчаливо теряется); после — чтобы убедиться, что `set` реально
    применился, до того как мы уйдём в общий ребут вместе со сменой ядра.

    Возвращает текст предупреждения (уровень до смены не совпадал с
    запрошенным) либо `None`, если бокс уже был на нужном уровне.
    """
    before = await _read_astra_mode(ssh, host)
    if before == mode:
        if mode == "smolensk":
            await _enable_smolensk_controls(ssh, host)
        return None

    warning = (
        f"astra security mode before prepare-for-test was {before!r}, "
        f"requested mode is {mode!r}"
    )
    target_level = _MODE_TO_LEVEL[mode]
    rc, _stdout, stderr = await ssh.run(
        f"astra-modeswitch set {target_level}", sudo=True,
    )
    if rc != 0:
        raise SshError(
            error_code="PREPARE_FOR_TEST_MODE_SWITCH_FAILED",
            host=host,
            cmd_sanitized=f"astra-modeswitch set {target_level}",
            returncode=rc,
            stderr=(stderr or "").strip(),
            message=f"astra-modeswitch set {target_level} failed",
        )
    if mode == "smolensk":
        await _enable_smolensk_controls(ssh, host)

    after = await _read_astra_mode(ssh, host)
    if after != mode:
        raise SshError(
            error_code="PREPARE_FOR_TEST_MODE_SWITCH_FAILED",
            host=host,
            message=(
                f"astra-modeswitch set {target_level} did not take effect: "
                f"get reports {after!r} after set"
            ),
        )
    return warning


async def _issue_reboot(ssh: SshClient) -> None:
    """Отдать `reboot` под sudo.

    Сессия рвётся под нами — это нормальный исход команды, а не ошибка:
    sshd уходит вместе с системой, и `run` увидит оборванный канал. Ловим
    `SshError` и идём ждать сеть; если ребут на самом деле не случился,
    это поймает ожидание «сервер ушёл из сети» следующим шагом.
    """
    try:
        await ssh.run("reboot", sudo=True)
    except SshError as exc:
        logger.info(
            "prepare_for_test: reboot dropped the SSH session as expected (%s)",
            type(exc).__name__,
        )


async def _wait_reboot_completed(host: str, ssh_port: int, settings) -> None:
    """Дождаться, что сервер ушёл из сети и вернулся.

    Тот же примитив, что у ACS-цепочки (`_await_reachability`), и те же
    бюджеты: `acs_down_wait_seconds` на уход, `acs_reachability_timeout_seconds`
    на возврат. Отдельных настроек под ребут не заводим — «пропал и вернулся»
    здесь ровно то же ожидание, что после Clonezilla, только короче по факту.
    """
    went_down = await _await_reachability(
        host,
        ssh_port=ssh_port,
        want_up=False,
        deadline_seconds=settings.acs_down_wait_seconds,
        poll_interval_seconds=settings.acs_reachability_poll_interval_seconds,
    )
    if not went_down:
        raise SshError(
            error_code="PREPARE_FOR_TEST_REBOOT_NOT_STARTED",
            host=host,
            message=(
                "server never went offline after reboot was issued — "
                "the new kernel is not running"
            ),
        )
    came_back = await _await_reachability(
        host,
        ssh_port=ssh_port,
        want_up=True,
        deadline_seconds=settings.acs_reachability_timeout_seconds,
        poll_interval_seconds=settings.acs_reachability_poll_interval_seconds,
    )
    if not came_back:
        raise SshError(
            error_code="PREPARE_FOR_TEST_REBOOT_TIMEOUT",
            host=host,
            message="server did not become reachable again after reboot",
        )


async def _reboot_and_wait(creds: dict, server_id: str, host: str, ssh_port: int, settings) -> None:
    async with ssh_client.build_session(creds, server_id) as ssh:
        await _issue_reboot(ssh)
    await _wait_reboot_completed(host, ssh_port, settings)


def pipeline_mode(payload: dict, mode: str) -> str | None:
    """Режим для `run_setup_pipeline`: None — шаг `mode_switch` не выполняется."""
    return None if payload.get("skip_mode_switch") else mode


def pipeline_provisioning(payload: dict) -> dict:
    """Профиль подготовки для `run_setup_pipeline`; `skip_pam_fix` снимает PAM-правку."""
    provisioning = parse_provisioning(payload.get("provisioning"))
    if payload.get("skip_pam_fix"):
        provisioning["disable_pam_lastlog_inactive"] = False
    return provisioning


async def run_setup_pipeline(
    *,
    creds: dict,
    server_id: str,
    host: str,
    ssh_port: int,
    settings,
    provisioning: dict,
    stand_setup: dict | None,
    test_username: str,
    kernel: str | None,
    mode: str | None,
    on_step,
) -> str | None:
    """Шаги 4–10 (после `user_provision`).

    `kernel`/`mode` = None — операция «настройка без restore»:
    ядро не переключается (только параметры из `stand_setup` + общий
    `update-grub`), режим не трогается. `on_step(name)` сообщает текущий
    шаг — от него зависит `failed_step` провала. Возвращает non-fatal
    предупреждение смены режима.
    """
    mode_warning: str | None = None
    kernel_params = (stand_setup or {}).get("kernel_cmdline_extra") or []
    phase = (stand_setup or {}).get("phase", PHASE_AFTER_BOOT)

    async with ssh_client.build_session(creds, server_id) as ssh:
        if provisioning.get("disable_pam_lastlog_inactive"):
            on_step(STEP_PAM_FIX)
            await apply_pam_fix(ssh, host)

        if stand_setup and phase == PHASE_BEFORE_KERNEL:
            on_step(STEP_STAND_SETUP)
            await run_setup_script(ssh, stand_setup, test_username, host)

        on_step(STEP_KERNEL_CHANGE if kernel else STEP_STAND_SETUP)
        grub_changed = await add_kernel_cmdline_params(ssh, kernel_params, host)
        if kernel:
            await _install_kernel_packages(ssh, kernel)
            menuentry = await _switch_default_kernel(ssh, kernel, host)
            logger.info("prepare_for_test: server_id=%s GRUB_DEFAULT -> %s", server_id, menuentry)
            grub_changed = True
        if grub_changed:
            await update_grub(ssh, host)

        if mode:
            on_step(STEP_MODE_SWITCH)
            mode_warning = await _switch_security_mode(ssh, mode, host)

    def _session() -> SshClient:
        return ssh_client.build_session(creds, server_id)

    async def _reboot() -> None:
        await _reboot_and_wait(creds, server_id, host, ssh_port, settings)

    # Перезагрузка нужна, если менялись ядро/режим/параметры ядра или скрипт
    # до смены ядра; у «настройки без restore» только со скриптом after_boot
    # первая перезагрузка не нужна.
    if kernel or mode or grub_changed or (stand_setup and phase == PHASE_BEFORE_KERNEL):
        on_step(STEP_REBOOT_VERIFY)
        await _reboot()
        await verify_system_running(
            host, _session, label="prepare_for_test: post-reboot",
            provisioning=provisioning, reboot=_reboot,
        )

    if stand_setup and phase == PHASE_AFTER_BOOT and (stand_setup.get("script") or "").strip():
        on_step(STEP_STAND_SETUP)
        async with ssh_client.build_session(creds, server_id) as ssh:
            await run_setup_script(ssh, stand_setup, test_username, host)
        if stand_setup.get("reboot_after"):
            on_step(STEP_REBOOT_VERIFY)
            await _reboot()
            await verify_system_running(
                host, _session, label="prepare_for_test: after stand_setup",
                provisioning=provisioning, reboot=_reboot,
            )
    return mode_warning


@broker.task("server.prepare_for_test")
async def server_prepare_for_test(task_id: str) -> None:
    """Довести подготовленный сервер до состояния «можно гонять тест» (см. module docstring).

    Payload: `server_id`, `prepare_request_id`, `kernel`, `mode`,
    `skip_mode_switch`, `skip_pam_fix`, `test_creds_key` (Redis-stash с кредами тестовой
    учётки и скриптом настройки), `stand_setup`, `provisioning`,
    `host`/`ssh_port`, `is_managed`, `management_user`, `target_department_id`.

    На любой ошибке — callback с `succeeded=False` и именем упавшего шага,
    затем task падает. Связано: `POST /internal/servers/{id}/prepare-for-test`,
    callback `record_prepare_for_test_done`.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        request_id = payload["prepare_request_id"]
        target_dept = payload.get("target_department_id")
        creds_key = payload.get("test_creds_key")
        ssh_port = _resolve_ssh_port(payload)
        settings = get_settings()
        step = {"name": STEP_USER_PROVISION}
        test_username = ""
        kernel = ""
        mode = ""
        mode_warning: str | None = None
        provisioning = pipeline_provisioning(payload)

        def _on_step(name: str) -> None:
            step["name"] = name

        try:
            stash = await _read_test_creds(creds_key)
            test_username = str(stash.get("test_username") or "")
            raw_setup = dict(payload.get("stand_setup") or {})
            if stash.get("stand_setup_script"):
                raw_setup["script"] = stash["stand_setup_script"]
            stand_setup = parse_stand_setup(raw_setup)

            creds = await resolve_ssh_creds(
                payload, server_id, account_id=None,
                target_dept=target_dept, is_managed=True,
            )
            ssh_client.apply_session_hints(creds, payload)
            await ssh_client.attach_management_creds(creds, server_id)
            host = str(creds.get("host") or creds.get("ssh_host") or server_id)
            kernel = _validate_kernel(payload.get("kernel"), host)
            mode = _validate_mode(payload.get("mode"), host)

            async with ssh_client.build_session(creds, server_id) as ssh:
                # Учётка исполнения теста. `force_replace=True` — после
                # reimage прежний authorized_keys невалиден целиком, а на
                # повторной подготовке того же стенда старый ключ прогона
                # оставлять нельзя.
                await ssh.create_user(
                    test_username,
                    new_password=stash.get("test_password"),
                    public_key=stash.get("test_ssh_public_key"),
                    has_sudo=True,
                    force_replace=True,
                )

            mode_warning = await run_setup_pipeline(
                creds=creds, server_id=server_id, host=host, ssh_port=ssh_port,
                settings=settings, provisioning=provisioning, stand_setup=stand_setup,
                test_username=test_username, kernel=kernel,
                mode=pipeline_mode(payload, mode), on_step=_on_step,
            )
        except Exception as exc:
            error_message = redact_error_message(
                f"{type(exc).__name__}: {exc}"
            )[:LAST_ERROR_MAX_LEN]
            try:
                await server_service_client.submit_prepare_for_test_result(
                    server_id, request_id, False, target_dept,
                    failed_step=step["name"], error_message=error_message,
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "prepare_for_test failed-callback errored server_id=%s; "
                    "testing_service will not learn the outcome",
                    server_id, exc_info=True,
                )
            raise
        finally:
            if isinstance(creds_key, str):
                await _delete_test_creds(creds_key)

        await server_service_client.submit_prepare_for_test_result(
            server_id, request_id, True, target_dept,
            error_message=mode_warning,
        )
        return {
            "server_id": server_id,
            "prepare_request_id": request_id,
            "kernel": kernel,
            "mode": mode,
            "test_username": test_username,
            "succeeded": True,
        }

    await run_task(
        task_id,
        audit_action="server.prepare_for_test",
        audit_target_type="server",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS,
    )


@broker.task("server.stand_setup")
async def server_stand_setup(task_id: str) -> None:
    """Настройка стенда без restore: `pam_fix` + `stand_setup` + reboot_verify.

    Без restore, учётки, смены ядра и режима — для многоступенчатых тестов,
    где между ступенями стенд перенастраивается, а не переналивается.
    Бронь сервера держит вызывающий.

    Payload: `server_id`, `stand_setup_request_id`, `stand_setup`,
    `script_key`, `provisioning`, `test_username`, `host`/`ssh_port`,
    `is_managed`, `management_user`, `target_department_id`. Исход —
    callback `submit_stand_setup_result`.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        request_id = payload["stand_setup_request_id"]
        target_dept = payload.get("target_department_id")
        script_key = payload.get("script_key")
        ssh_port = _resolve_ssh_port(payload)
        settings = get_settings()
        step = {"name": STEP_STAND_SETUP}
        provisioning = parse_provisioning(payload.get("provisioning"))

        def _on_step(name: str) -> None:
            step["name"] = name

        try:
            raw_setup = dict(payload.get("stand_setup") or {})
            if script_key:
                stash = await _read_stash(script_key)
                raw_setup["script"] = stash.get("stand_setup_script") or ""
            stand_setup = parse_stand_setup(raw_setup)
            creds = await resolve_ssh_creds(
                payload, server_id, account_id=None,
                target_dept=target_dept, is_managed=True,
            )
            ssh_client.apply_session_hints(creds, payload)
            await ssh_client.attach_management_creds(creds, server_id)
            host = str(creds.get("host") or creds.get("ssh_host") or server_id)
            await run_setup_pipeline(
                creds=creds, server_id=server_id, host=host, ssh_port=ssh_port,
                settings=settings, provisioning=provisioning, stand_setup=stand_setup,
                test_username=str(payload.get("test_username") or ""),
                kernel=None, mode=None, on_step=_on_step,
            )
        except Exception as exc:
            error_message = redact_error_message(f"{type(exc).__name__}: {exc}")[:LAST_ERROR_MAX_LEN]
            try:
                await server_service_client.submit_stand_setup_result(
                    server_id, request_id, False, target_dept,
                    failed_step=step["name"], error_message=error_message,
                )
            except Exception:  # noqa: BLE001
                logger.warning("stand_setup failed-callback errored server_id=%s", server_id, exc_info=True)
            raise
        finally:
            if isinstance(script_key, str):
                await _delete_test_creds(script_key)

        await server_service_client.submit_stand_setup_result(server_id, request_id, True, target_dept)
        return {"server_id": server_id, "stand_setup_request_id": request_id, "succeeded": True}

    await run_task(
        task_id,
        audit_action="server.stand_setup",
        audit_target_type="server",
        impl=_impl,
        audit_safe_fields={"server_id", "stand_setup_request_id", "succeeded"},
    )
