"""Скачивание/импорт бокса реестра на VMS-hub.

`box.download` тянет артефакт бокса по `download_url` в storage-pool боксов
hub'а — тот самый каталог, где `vm.create` ищет `<box>.qcow2` при сборке ВМ
(`VMS_DEFAULT_POOL_PATH`, по умолчанию `/vms`). Формат `tar`/`tar.gz`/`tgz`
распаковывается в каталог (артефакт несёт готовый `<box>.qcow2`), `qcow`/`qcow2`
кладётся как `<box>.qcow2`, `raw` — как `<box>.raw`.

Заход на hub — под управляющей учёткой по ключу (`open_hub_session`), всё под
root/sudo, как остальные VM-задачи. Пароль базовой учётки бокса тут не нужен:
качаем образ, в гостя не заходим. Схема URL и формат берутся из whitelist'а, имя
бокса прогоняется через `validate_name`, URL подставляется в команду через
`shlex.quote`, поэтому shell-инъекция через payload невозможна. Идемпотентно:
перекачка перезаписывает файл/распаковку.

Исход докладывается server_service callback'ом (`ready`/`error`).
"""

from __future__ import annotations

import logging
import shlex
from urllib.parse import urlparse

from src.clients.ssh import SshError
from src.core.constants import VMS_DEFAULT_POOL_PATH
from src.main import broker
from src.services import server_service_client
from src.tasks._runner import run_task
from src.tasks._vms_helpers import (
    open_hub_session,
    run_hub_cmd,
    validate_name,
    validate_path,
)

logger = logging.getLogger(__name__)

_run = run_hub_cmd

AUDIT_SAFE_FIELDS_DOWNLOAD: set[str] = {
    "box_id", "box_name", "format", "target_path", "status",
}

# Схемы URL, по которым качаем бокс на hub. smb/cifs требуют smbget на хосте —
# если его нет, отдаём понятную ошибку, а не молчаливый провал.
_HTTP_SCHEMES = frozenset({"http", "https", "ftp", "ftps"})
_SMB_SCHEMES = frozenset({"smb", "cifs"})
_ALLOWED_SCHEMES = _HTTP_SCHEMES | _SMB_SCHEMES

# Форматы артефакта бокса. tar-набор распаковывается в пул, остальные кладутся
# файлом с каноничным расширением.
_TAR_FORMATS = frozenset({"tar", "tar.gz", "targz", "tgz", "tar.xz", "txz"})
_DISK_FORMATS = {"qcow": "qcow2", "qcow2": "qcow2", "raw": "raw"}
_ALLOWED_FORMATS = _TAR_FORMATS | set(_DISK_FORMATS)


def _normalize_format(value) -> str:
    """Привести строку формата к каноничному ключу whitelist'а."""
    return str(value or "").strip().lower().replace("-", "")


def _validate_download_url(url: str, host: str) -> tuple[str, str]:
    """Провалидировать URL скачивания. Вернуть `(url, scheme)`.

    Схема — из whitelist'а (http/https/ftp/ftps/smb/cifs), хост обязателен.
    Пустой/битый URL или чужая схема → `BOX_DOWNLOAD_INVALID_URL`. В команду
    URL всё равно уходит через `shlex.quote`, но валидация отсекает мусор до
    захода на hub.
    """
    if not isinstance(url, str) or not url.strip():
        raise SshError(
            error_code="BOX_DOWNLOAD_INVALID_URL", host=host,
            message="download_url пуст",
        )
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise SshError(
            error_code="BOX_DOWNLOAD_INVALID_URL", host=host,
            message=(
                f"download_url: недопустимая схема {scheme!r} "
                f"(ожидается одна из {sorted(_ALLOWED_SCHEMES)})"
            ),
        )
    if not parsed.netloc:
        raise SshError(
            error_code="BOX_DOWNLOAD_INVALID_URL", host=host,
            message="download_url: не указан хост",
        )
    return url.strip(), scheme


async def _download_to_tmp(ssh, host: str, url: str, scheme: str, tmp: str) -> None:
    """Скачать `url` во временный файл `tmp` на hub'е (под root).

    http/https/ftp/ftps — `curl -fSL`. smb/cifs — `smbget`, если он есть на
    хосте; иначе понятная ошибка `BOX_DOWNLOAD_SMB_UNSUPPORTED`. URL и путь
    экранируются `shlex.quote`.
    """
    q_url = shlex.quote(url)
    q_tmp = shlex.quote(tmp)
    if scheme in _HTTP_SCHEMES:
        await _run(
            ssh,
            f"curl -fSL --connect-timeout 30 -o {q_tmp} {q_url}",
            host, "BOX_DOWNLOAD_FETCH_FAILED",
            "не удалось скачать бокс (curl)", sudo=True,
        )
        return
    # smb/cifs: требуется smbget.
    rc, _out, _err = await ssh.run("command -v smbget", sudo=True)
    if rc != 0:
        raise SshError(
            error_code="BOX_DOWNLOAD_SMB_UNSUPPORTED", host=host,
            message="smb не поддержан на hub'е (нет smbget)",
        )
    await _run(
        ssh, f"smbget -q -O {q_tmp} {q_url}", host,
        "BOX_DOWNLOAD_FETCH_FAILED", "не удалось скачать бокс (smbget)",
        sudo=True,
    )


async def _import_artifact(
    ssh, host: str, tmp: str, pool_path: str, base: str, fmt: str,
) -> str:
    """Разложить скачанный артефакт в пул по формату. Вернуть целевой путь.

    tar-набор распаковывается в каталог (внутри — готовый `<box>.qcow2`), после
    чего временный файл удаляется. qcow/qcow2/raw переносится в
    `<pool>/<base>.<ext>` (`-f` перезаписывает при перекачке).
    """
    q_tmp = shlex.quote(tmp)
    q_pool = shlex.quote(pool_path)
    if fmt in _TAR_FORMATS:
        # GNU tar на распаковке сам детектит компрессию (gz/xz/plain).
        await _run(
            ssh, f"tar xf {q_tmp} -C {q_pool}", host,
            "BOX_IMPORT_UNPACK_FAILED", "не удалось распаковать бокс", sudo=True,
        )
        await ssh.run(f"rm -f {q_tmp}", sudo=True)
        return pool_path
    ext = _DISK_FORMATS[fmt]
    target = f"{pool_path}/{base}.{ext}"
    await _run(
        ssh, f"mv -f {q_tmp} {shlex.quote(target)}", host,
        "BOX_IMPORT_PLACE_FAILED", "не удалось положить образ бокса в пул",
        sudo=True,
    )
    return target


@broker.task("box.download")
async def box_download(task_id: str) -> None:
    """Скачать/импортировать бокс реестра на hub (идемпотентно).

    Что делает: заходит по управляющему ключу на hub, создаёт каталог пула
    боксов (если нет), тянет артефакт по `download_url` во временный файл и
    раскладывает его по формату — tar распаковывает в пул, qcow/raw кладёт
    файлом под именем бокса. По завершении шлёт server_service статус `ready`;
    на любой ошибке — `error` с текстом причины.

    Параметры: `task_id`. Payload — `box_id`, `box_name` (имя, под которым
    `vm.create` ищет образ в пуле), `download_url`, `format` (tar/qcow/qcow2/
    raw/…), `storage_pool_path` (опц., деф. `/vms`), плюс hub-блок адресации
    (`host`/`hub_host`, `server_id`, `is_managed`, `management_user`) и
    `target_department_id`.

    Возвращает: `{box_id, box_name, format, target_path, status}`.

    Возможные ошибки: `BOX_DOWNLOAD_INVALID_URL`, `BOX_IMPORT_UNSUPPORTED_FORMAT`,
    `BOX_DOWNLOAD_SMB_UNSUPPORTED`, `BOX_DOWNLOAD_FETCH_FAILED`,
    `BOX_IMPORT_UNPACK_FAILED`, `BOX_IMPORT_PLACE_FAILED`. На любой — best-effort
    callback `error`.
    """
    async def _impl(payload: dict) -> dict:
        box_id = payload["box_id"]
        target_dept = payload.get("target_department_id")
        host_label = str(
            payload.get("host") or payload.get("hub_host")
            or payload.get("hub_server_id") or payload.get("server_id") or "hub",
        )
        try:
            # Валидация до захода на hub, но внутри try — чтобы любой провал
            # (битый URL / неподдержанный формат / инъекция в имени) доехал до
            # server_service статус-callback'ом, а не молча.
            base = validate_name(str(payload["box_name"]), host_label, "box_name")
            pool_path = validate_path(
                payload.get("storage_pool_path", VMS_DEFAULT_POOL_PATH), host_label,
            )
            fmt = _normalize_format(payload.get("format"))
            if fmt not in _ALLOWED_FORMATS:
                raise SshError(
                    error_code="BOX_IMPORT_UNSUPPORTED_FORMAT", host=host_label,
                    message=(
                        f"формат {payload.get('format')!r} не поддержан "
                        f"(ожидается один из {sorted(_ALLOWED_FORMATS)})"
                    ),
                )
            url, scheme = _validate_download_url(
                str(payload.get("download_url") or ""), host_label,
            )

            session, host = await open_hub_session(payload)
            async with session as ssh:
                await _run(
                    ssh, f"mkdir -p {shlex.quote(pool_path)}", host,
                    "BOX_DOWNLOAD_POOL_FAILED",
                    "не удалось создать каталог пула боксов", sudo=True,
                )
                tmp = f"{pool_path}/.{base}.box-download"
                await _download_to_tmp(ssh, host, url, scheme, tmp)
                target_path = await _import_artifact(
                    ssh, host, tmp, pool_path, base, fmt,
                )
        except Exception as exc:
            error_text = getattr(exc, "error_code", type(exc).__name__)
            try:
                await server_service_client.submit_box_download_state(
                    box_id, status="error",
                    target_department_id=target_dept, error=str(error_text),
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "box.download failed-callback errored box_id=%s",
                    box_id, exc_info=True,
                )
            raise

        await server_service_client.submit_box_download_state(
            box_id, status="ready", target_department_id=target_dept,
        )
        return {
            "box_id": box_id,
            "box_name": base,
            "format": fmt,
            "target_path": target_path,
            "status": "ready",
        }

    await run_task(
        task_id,
        audit_action="box.download",
        audit_target_type="box",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_DOWNLOAD,
    )
