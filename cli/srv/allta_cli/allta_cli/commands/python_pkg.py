from __future__ import annotations

import shlex
import tempfile
from glob import glob
from pathlib import Path
from shutil import which, rmtree

from allta_cli.utils import ui
from allta_cli.utils.system_commands import SystemCommands
from allta_cli.utils.config import DEVPI_URL, DEVPI_INDEX
from allta_cli.utils.http_fallback import prefer_https_url, http_fallback_url
from allta_cli.utils.config_api import (
    ConfigApiError,
    ServiceCredentialNotFound,
    get_service_credential,
)
from allta_cli.utils.auth import AuthError, NotAuthenticatedError, TokenExpiredError
from allta_cli.utils.runtime_env import system_ld_library_path_scope

DEVPI_CREDENTIAL_NAME = "devpi_root"


def _index_url(base: str) -> str:
    return f"{base.rstrip('/')}/{DEVPI_INDEX}"


def _run(cmd: str, fail: str) -> int:
    ui.cmd(cmd)
    with system_ld_library_path_scope():
        rc = SystemCommands.cmd_with_returncode(command=cmd)
    if rc != 0:
        ui.err(f"{fail} (код {rc})")
    return rc


def _run_capture(cmd: str) -> tuple[int, str]:
    ui.cmd(cmd)
    with system_ld_library_path_scope():
        rc, out = SystemCommands.check_output_command_with_returncode(command=cmd)
    return rc, out


def _pip_bin() -> str | None:
    for name in ("pip3", "pip"):
        found = which(name)
        if found:
            return found
    return None


def _resolve_requirements(path: str | None) -> Path | None:
    """Находит requirements-файл: явный путь, либо поиск в текущем каталоге."""
    if path:
        target = Path(path).expanduser()
        if not target.is_file():
            ui.err(f"Файл требований не найден: {target}")
            return None
        return target

    candidates = sorted(glob("requirements*.txt"))
    if not candidates:
        return None
    for c in candidates:
        if c == "requirements.txt":
            return Path(c)
    return Path(candidates[0])


def _read_requirements(req_file: Path) -> list[str]:
    specs: list[str] = []
    for line in req_file.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or text.startswith("-"):
            continue
        specs.append(text)
    return specs


def _spec_name(spec: str) -> str:
    """Достаёт имя пакета из спецификации (requests==2.31.0 → requests)."""
    name = spec.strip()
    for sep in ("===", "==", ">=", "<=", "~=", "!=", ">", "<", "[", ";", " "):
        idx = name.find(sep)
        if idx != -1:
            name = name[:idx]
    return name.strip().lower().replace("_", "-")


def _devpi_credential() -> tuple[str, str] | None:
    try:
        cred = get_service_credential(DEVPI_CREDENTIAL_NAME)
    except ServiceCredentialNotFound:
        ui.err(
            f"В config-сервисе нет credential '{DEVPI_CREDENTIAL_NAME}'. "
            "Заведите его: allta creds upsert."
        )
        return None
    except (ConfigApiError, NotAuthenticatedError, TokenExpiredError, AuthError) as e:
        ui.err(f"Не удалось получить devpi credential: {e}")
        return None
    return cred["username"], cred["password"]


def _fetch_text(url: str) -> tuple[int, str]:
    """Тянет содержимое URL: curl, если он есть, иначе через python+urllib."""
    curl = which("curl")
    if curl:
        return _run_capture(f"{shlex.quote(curl)} -ksS {shlex.quote(url)}")

    script = (
        "import urllib.request,ssl;"
        "c=ssl.create_default_context();"
        "c.check_hostname=False;c.verify_mode=ssl.CERT_NONE;"
        f"print(urllib.request.urlopen({url!r},context=c).read().decode())"
    )
    return _run_capture(f"python3 -c {shlex.quote(script)}")


def _index_package_names() -> set[str] | None:
    """Имена пакетов, уже лежащих в индексе devpi (через simple-индекс).

    Возвращает множество нормализованных имён или None, если индекс недоступен.
    """
    import re

    simple = prefer_https_url(f"{_index_url(DEVPI_URL)}/+simple/")
    urls = [simple]
    http_simple = http_fallback_url(simple)
    if http_simple:
        urls.append(http_simple)

    for url in urls:
        rc, out = _fetch_text(url)
        if rc != 0 or not out:
            continue
        names: set[str] = set()
        for m in re.findall(r">\s*([A-Za-z0-9][A-Za-z0-9._-]+)\s*<", out):
            names.add(m.strip().lower().replace("_", "-"))
        if names:
            return names
    return None


def _devpi_upload(distdir: Path, username: str, password: str) -> int:
    devpi = which("devpi")
    if not devpi:
        ui.err("Не найден devpi-client (devpi). Установите его в окружение.")
        return 1

    index = _index_url(DEVPI_URL)

    rc = _run(f"{shlex.quote(devpi)} use --set-cfg {shlex.quote(index)}", "devpi use")
    if rc != 0:
        return rc
    rc = _run(
        f"{shlex.quote(devpi)} login {shlex.quote(username)} --password {shlex.quote(password)}",
        "devpi login",
    )
    if rc != 0:
        return rc
    rc = _run(f"{shlex.quote(devpi)} use {shlex.quote(DEVPI_INDEX)}", "devpi use index")
    if rc != 0:
        return rc

    distfiles = sorted(
        p for p in distdir.iterdir()
        if p.suffix in {".whl"} or p.name.endswith((".tar.gz", ".tar.bz2", ".zip"))
    )
    if not distfiles:
        ui.warn("В wheelhouse нет дистрибутивов для загрузки.")
        return 0

    failed = 0
    for dist in distfiles:
        rc = _run(
            f"{shlex.quote(devpi)} upload {shlex.quote(str(dist))}",
            f"загрузка {dist.name}",
        )
        if rc != 0:
            failed += 1
    if failed:
        ui.err(f"Не удалось загрузить дистрибутивов: {failed}.")
        return 1
    ui.ok(f"Загружено дистрибутивов в {DEVPI_INDEX}: {len(distfiles)}.")
    return 0


def _download_wheelhouse(specs: list[str], wheelhouse: Path) -> int:
    pip = _pip_bin()
    if not pip:
        ui.err("Не найден pip (pip3/pip). Сначала установите Python: allta python")
        return 1

    quoted = " ".join(shlex.quote(s) for s in specs)
    cmd = (
        f"{shlex.quote(pip)} download {quoted} "
        f"--dest {shlex.quote(str(wheelhouse))}"
    )
    return _run(cmd, "скачивание пакетов (pip download)")


def python_pkg(
    *,
    path: str | None = None,
    file: str | None = None,
    packages: tuple[str, ...] = (),
) -> int:
    """
    Находит пакеты, которых ещё нет в devpi-индексе (root/release), и докладывает их
    туда вместе с транзитивными зависимостями — чтобы внутренняя сеть пережила обрыв
    интернета.

    Источники пакетов:
      - явные имена (PACKAGE...),
      - файл требований (--file или позиционный PATH),
      - либо requirements*.txt из текущего каталога (по умолчанию).
    """
    with ui.section("PYTHON-PKG (start)", "PYTHON-PKG (end)"):
        requested: list[str] = list(packages)

        req_file = None
        if file:
            req_file = _resolve_requirements(file)
            if req_file is None:
                return 1
        elif path:
            # позиционный PATH может быть как файлом требований, так и просто его не существует
            maybe = Path(path).expanduser()
            if maybe.is_file():
                req_file = maybe
        if req_file is None and not requested:
            req_file = _resolve_requirements(None)

        if req_file is not None:
            ui.ok(f"Файл требований: {req_file}")
            requested.extend(_read_requirements(req_file))

        if not requested:
            ui.err(
                "Нечего обрабатывать: не указаны пакеты и не найден requirements*.txt "
                "в текущем каталоге."
            )
            return 1

        # дедуп по имени, сохраняя первую спецификацию (с версией)
        seen: dict[str, str] = {}
        for spec in requested:
            seen.setdefault(_spec_name(spec), spec)

        ui.step("Сверка с индексом devpi…")
        present = _index_package_names()
        if present is None:
            ui.warn("Не удалось прочитать индекс devpi — обрабатываю все запрошенные пакеты.")
            missing = list(seen.items())
        else:
            missing = [(name, spec) for name, spec in seen.items() if name not in present]

        if not missing:
            ui.ok("Все запрошенные пакеты уже есть в индексе. Нечего добавлять.")
            return 0

        ui.echo("Будут добавлены (с транзитивными зависимостями):")
        for name, spec in missing:
            ui.echo(f"  · {spec}")

        cred = _devpi_credential()
        if cred is None:
            return 1
        username, password = cred

        wheelhouse = Path(tempfile.mkdtemp(prefix="allta-wheelhouse-"))
        try:
            rc = _download_wheelhouse([spec for _, spec in missing], wheelhouse)
            if rc != 0:
                return rc
            return _devpi_upload(wheelhouse, username, password)
        finally:
            rmtree(wheelhouse, ignore_errors=True)
