from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from shutil import which

from allta_cli.utils import ui


PKG_NAME = "allta"
FTP_HOST = "10.177.103.10"
FTP_DIR = "/"
DEB_PATTERN = re.compile(r"^/?(?P<name>allta)_(?P<version>[^_]+)_(?P<arch>\w+)\.deb$")


def _sudo() -> str:
    try:
        if os.geteuid() == 0:
            return ""
    except AttributeError:
        return ""
    return "sudo " if which("sudo") else ""


def _run(cmd: str, fail: str) -> int:
    ui.cmd(cmd)
    rc = subprocess.run(cmd, shell=True, stdin=subprocess.DEVNULL).returncode
    if rc != 0:
        ui.err(f"{fail} (код {rc})")
    return rc


def _capture(cmd: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return 127, ""
    return proc.returncode, proc.stdout


def _installed_version() -> str | None:
    rc, out = _capture(["dpkg-query", "-W", "-f=${Version}", PKG_NAME])
    if rc != 0:
        return None
    value = out.strip()
    return value or None


def _compare_versions(a: str, b: str) -> int:
    """-1 if a<b, 0 if equal, 1 if a>b. Uses dpkg's native scheme."""
    for op, result in (("lt", -1), ("eq", 0), ("gt", 1)):
        try:
            rc = subprocess.run(
                ["dpkg", "--compare-versions", a, op, b],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
        except FileNotFoundError:
            return 0
        if rc == 0:
            return result
    return 0


def _list_remote_debs() -> list[tuple[str, str]]:
    from ftplib import FTP

    results: list[tuple[str, str]] = []
    with FTP(FTP_HOST, timeout=20) as ftp:
        ftp.login()
        for entry in ftp.nlst(FTP_DIR):
            base = os.path.basename(entry)
            m = DEB_PATTERN.match(base)
            if m:
                results.append((base, m.group("version")))
    return results


def _pick_latest(debs: list[tuple[str, str]]) -> tuple[str, str]:
    best_name, best_version = debs[0]
    for name, version in debs[1:]:
        if _compare_versions(version, best_version) > 0:
            best_name, best_version = name, version
    return best_name, best_version


def _download_deb(filename: str, dest: Path) -> int:
    from ftplib import FTP

    with FTP(FTP_HOST, timeout=120) as ftp:
        ftp.login()
        with open(dest, "wb") as out:
            ftp.retrbinary(f"RETR {filename}", out.write)
    return dest.stat().st_size


def upgrade_cmd(*, check: bool = False, force: bool = False) -> int:
    installed = _installed_version()
    if installed:
        ui.echo(f"Текущая версия: {installed}")
    else:
        ui.warn(f"Пакет '{PKG_NAME}' не найден в системе. Будет выполнена установка с нуля.")

    ui.step(f"Подключаюсь к ftp://{FTP_HOST}/ …")
    try:
        debs = _list_remote_debs()
    except Exception as e:
        ui.err(f"Не удалось получить список пакетов с FTP: {e}")
        return 1

    if not debs:
        ui.err(f"На ftp://{FTP_HOST}/ не найдено пакетов allta_*.deb.")
        return 1

    deb_name, remote_version = _pick_latest(debs)
    ui.echo(f"Доступная версия: {remote_version} ({deb_name})")

    if installed:
        cmp = _compare_versions(installed, remote_version)
        if cmp == 0 and not force:
            ui.ok("Установленная версия актуальна, обновление не требуется.")
            return 0
        if cmp > 0 and not force:
            ui.warn(
                f"У вас более новая версия ({installed}), чем на FTP ({remote_version}). "
                "Используйте --force, чтобы откатиться."
            )
            return 0

    if check:
        if installed and _compare_versions(installed, remote_version) < 0:
            ui.echo(f"Доступно обновление: {installed} → {remote_version}")
        return 0

    dest = Path(tempfile.gettempdir()) / deb_name
    ui.step(f"Скачиваю {deb_name} → {dest} …")
    try:
        size = _download_deb(deb_name, dest)
    except Exception as e:
        ui.err(f"Скачивание не удалось: {e}")
        return 1
    ui.ok(f"Скачано: {size / 1024 / 1024:.1f} MB")

    sudo = _sudo()
    extra = ""
    if installed and force:
        cmp_now = _compare_versions(installed, remote_version)
        if cmp_now == 0:
            extra = " --reinstall"
        elif cmp_now > 0:
            extra = " --allow-downgrades"
            ui.warn(f"Откат: {installed} → {remote_version}.")
    ui.step("Устанавливаю через apt-get (он сам заменит старую версию)…")
    rc = _run(f"{sudo}apt-get install{extra} -y {dest}", f"установка {deb_name}")
    if rc != 0:
        return rc

    try:
        dest.unlink(missing_ok=True)
    except OSError:
        pass

    new_installed = _installed_version() or "?"
    ui.ok(f"Готово: {installed or '—'} → {new_installed}")
    return 0
