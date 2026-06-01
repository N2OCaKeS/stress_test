from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from shutil import which

import click

from allta_cli.utils import ui
from allta_cli.utils.system_commands import SystemCommands


GRUB_CFG_PATH = Path("/boot/grub/grub.cfg")
GRUB_DEFAULTS_PATH = Path("/etc/default/grub")

_PKG_PREFIX = "linux-image-"
_HEADERS_PREFIX = "linux-headers-"
_MODULES_PREFIX = "linux-astra-modules-"
_KERNEL_VERSION_RE = re.compile(r"^linux-image-(\d+\.\d+\.\d+[^\s]*)$")
_EXCLUDE_SUFFIXES = ("-dbg", "-dbgsym")


@dataclass(frozen=True)
class KernelEntry:
    package: str
    version: str
    installed: bool
    running: bool


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


def _capture(cmd: list[str]) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as e:
        return 127, "", str(e)
    return proc.returncode, proc.stdout, proc.stderr


def kernel_cmd(version: str | None = None) -> int:
    if not _grub_available():
        ui.err(f"Не найден GRUB-конфиг {GRUB_CFG_PATH}. Команда поддерживает только системы с GRUB.")
        return 1

    if version:
        return _install_explicit(version)
    return _install_interactive()


def _install_explicit(version: str) -> int:
    package = _to_package(version)
    if not _apt_package_exists(package):
        ui.err(f"Пакет '{package}' не найден в apt. Проверьте имя ядра.")
        return 1

    entry = _find_entry_for_package(package) or KernelEntry(
        package=package,
        version=_to_display(package),
        installed=_is_installed(package),
        running=False,
    )
    return _install_and_set_default(entry, ask_reboot=False)


def _install_interactive() -> int:
    ui.step("Получаю список ядер (apt-cache + dpkg)…")
    entries = _collect_kernel_entries()
    if not entries:
        ui.err("Не удалось получить список ядер. Проверьте apt-кэш (apt-get update) и доступ к репозиториям.")
        return 1

    _print_entries(entries)

    raw = click.prompt("Номер ядра", type=str)
    idx = _parse_index(raw, len(entries))
    if idx is None:
        ui.err("Нужен номер из списка.")
        return 1

    entry = entries[idx - 1]
    return _install_and_set_default(entry, ask_reboot=True)


def _install_and_set_default(entry: KernelEntry, *, ask_reboot: bool) -> int:
    ui.echo("")
    ui.echo(f"Выбрано ядро: {entry.version} ({entry.package})")
    ui.echo(f"  установлено: {'да' if entry.installed else 'нет'}")
    ui.echo(f"  текущее (uname -r): {'да' if entry.running else 'нет'}")

    sudo = _sudo()

    # Кроме образа ядра ставим заголовки и astra-модули того же ядра — без них
    # не собираются модули и часть тестов ядра не запускается.
    wanted = [entry.package]
    for pkg in _companion_packages(entry.package):
        if _apt_package_exists(pkg):
            wanted.append(pkg)
        else:
            ui.warn(f"Пакет '{pkg}' не найден в apt — пропускаю.")

    to_install = [pkg for pkg in wanted if not _is_installed(pkg)]
    if to_install:
        ui.step("Установка ядра через apt-get…")
        env = "DEBIAN_FRONTEND=noninteractive"
        rc = _run(f"{sudo}{env} apt-get -y update", "apt-get update")
        if rc != 0:
            return rc
        packages = " ".join(shlex.quote(pkg) for pkg in to_install)
        rc = _run(
            f"{sudo}{env} apt-get -y install {packages}",
            f"установка пакетов: {', '.join(to_install)}",
        )
        if rc != 0:
            return rc
        ui.ok(f"Установлены пакеты: {', '.join(to_install)}.")
    else:
        ui.ok("Ядро, заголовки и модули уже установлены, пропускаю apt-get install.")

    resolved_version = _resolve_installed_kernel_version(entry.package) or entry.version
    if resolved_version != entry.version:
        ui.echo(f"Пакет {entry.package} разрешён в ядро: {resolved_version}")

    ui.step("Установка ядра по умолчанию в GRUB…")
    rc = _set_grub_default(resolved_version, sudo=sudo)
    if rc != 0:
        return rc

    ui.ok(f"Ядро {resolved_version} выставлено по умолчанию.")
    ui.warn("Для применения нового ядра необходимо перезагрузить ПК.")

    if ask_reboot:
        if _confirm_reboot():
            ui.step("Перезагрузка…")
            _run(f"{sudo}shutdown -r now", "перезагрузка")
        else:
            ui.echo("Перезагрузка отменена.")
    return 0


def _confirm_reboot() -> bool:
    prompt = "Перезагрузить ПК сейчас? [y/N]: "
    line = ""
    try:
        with open("/dev/tty", "r+", encoding="utf-8", errors="replace") as tty:
            tty.write(prompt)
            tty.flush()
            line = tty.readline()
    except OSError:
        sys.stdout.write(prompt)
        sys.stdout.flush()
        try:
            raw = sys.stdin.buffer.readline()
        except (AttributeError, ValueError):
            raw = b""
        line = raw.decode("utf-8", errors="replace")
    if not line:
        sys.stdout.write("\n")
        return False
    return line.strip().lower() == "y"


def _grub_available() -> bool:
    return GRUB_CFG_PATH.is_file() and GRUB_DEFAULTS_PATH.is_file()


def _to_package(value: str) -> str:
    name = value.strip()
    if not name:
        raise click.UsageError("Имя ядра не должно быть пустым.")
    if name.startswith(_PKG_PREFIX):
        return name
    return f"{_PKG_PREFIX}{name}"


def _to_display(package: str) -> str:
    if package.startswith(_PKG_PREFIX):
        return package[len(_PKG_PREFIX):]
    return package


def _companion_packages(image_package: str) -> list[str]:
    """По имени linux-image-<suffix> собрать заголовки и astra-модули того же
    ядра: linux-headers-<suffix> и linux-astra-modules-<suffix>. Работает и для
    мета-пакетов (suffix вида 6.6-generic), apt сам разрешит их в конкретные."""
    suffix = image_package[len(_PKG_PREFIX):] if image_package.startswith(_PKG_PREFIX) else image_package
    return [f"{_HEADERS_PREFIX}{suffix}", f"{_MODULES_PREFIX}{suffix}"]


def _apt_package_exists(package: str) -> bool:
    rc, _, _ = _capture(["apt-cache", "show", package])
    return rc == 0


def _is_installed(package: str) -> bool:
    rc, out, _ = _capture(["dpkg-query", "-W", "-f=${Status}\n", package])
    if rc != 0:
        return False
    return "install ok installed" in out


def _collect_kernel_entries() -> list[KernelEntry]:
    candidates: set[str] = set()

    rc, out, _ = _capture(["apt-cache", "search", "--names-only", "^linux-image-"])
    if rc == 0:
        for line in out.splitlines():
            pkg = line.split(" - ", 1)[0].strip()
            if _is_kernel_image_package(pkg):
                candidates.add(pkg)

    rc, out, _ = _capture(["dpkg-query", "-W", "-f=${Package}\n", "linux-image-*"])
    if rc == 0:
        for line in out.splitlines():
            pkg = line.strip()
            if _is_kernel_image_package(pkg):
                candidates.add(pkg)

    running = _running_kernel()

    entries: list[KernelEntry] = []
    for pkg in candidates:
        version = _to_display(pkg)
        entries.append(
            KernelEntry(
                package=pkg,
                version=version,
                installed=_is_installed(pkg),
                running=(version == running),
            )
        )

    entries.sort(key=lambda e: _version_sort_key(e.version), reverse=True)
    return entries


def _is_kernel_image_package(package: str) -> bool:
    if not package:
        return False
    if any(package.endswith(suffix) for suffix in _EXCLUDE_SUFFIXES):
        return False
    return bool(_KERNEL_VERSION_RE.match(package))


_VMLINUZ_OWNED_RE = re.compile(r"^/boot/vmlinuz-(\S+)$", re.MULTILINE)
_APT_DEPENDS_RE = re.compile(r"^\s*(?:Depends|PreDepends):\s*(linux-image-\S+)", re.MULTILINE)


def _package_owns_vmlinuz(package: str) -> str | None:
    rc, out, _ = _capture(["dpkg-query", "-L", package])
    if rc != 0:
        return None
    m = _VMLINUZ_OWNED_RE.search(out)
    return m.group(1) if m else None


def _resolve_installed_kernel_version(package: str) -> str | None:
    """Walk Depends until we find a package that ships /boot/vmlinuz-* and return
    the version embedded in that file's name. Works for meta packages (e.g.
    linux-image-6.6-generic → linux-image-6.6.28-1-generic → 6.6.28-1-generic)."""
    seen: set[str] = set()
    queue: list[str] = [package]
    while queue:
        pkg = queue.pop(0)
        if pkg in seen:
            continue
        seen.add(pkg)

        version = _package_owns_vmlinuz(pkg)
        if version:
            return version

        rc, out, _ = _capture(["apt-cache", "depends", pkg])
        if rc != 0:
            continue
        for dep in _APT_DEPENDS_RE.findall(out):
            if dep not in seen:
                queue.append(dep)
    return None


def _running_kernel() -> str:
    rc, out, _ = _capture(["uname", "-r"])
    return out.strip() if rc == 0 else ""


def _version_sort_key(version: str) -> tuple:
    parts = re.split(r"[.\-+]", version)
    key: list = []
    for part in parts:
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return tuple(key)


def _find_entry_for_package(package: str) -> KernelEntry | None:
    if not _is_kernel_image_package(package):
        return None
    return KernelEntry(
        package=package,
        version=_to_display(package),
        installed=_is_installed(package),
        running=(_to_display(package) == _running_kernel()),
    )


def _print_entries(entries: list[KernelEntry]) -> None:
    rows: list[list[object]] = []
    for idx, entry in enumerate(entries, start=1):
        marks: list[str] = []
        if entry.installed:
            marks.append("установлено")
        if entry.running:
            marks.append("текущее")
        rows.append([idx, entry.version, entry.package, ", ".join(marks) or "-"])
    ui.echo("")
    ui.table(headers=["#", "Версия", "Пакет", "Статус"], rows=rows)
    ui.echo("")


def _parse_index(raw: str, total: int) -> int | None:
    try:
        value = int(raw.strip())
    except ValueError:
        return None
    if 1 <= value <= total:
        return value
    return None


def _set_grub_default(version: str, *, sudo: str) -> int:
    default_id = _resolve_grub_default_id(version, sudo=sudo)
    if not default_id:
        ui.err(f"Не нашёл в {GRUB_CFG_PATH} пункт меню для ядра {version}. Запустите update-grub и повторите.")
        return 1

    try:
        original = GRUB_DEFAULTS_PATH.read_text(encoding="utf-8")
    except OSError as e:
        ui.err(f"Не удалось прочитать {GRUB_DEFAULTS_PATH}: {e}")
        return 1

    updated = _replace_grub_default(original, default_id)
    if updated == original:
        ui.echo(f"GRUB_DEFAULT уже выставлен в {default_id}.")
    else:
        rc = _write_grub_defaults(updated, sudo=sudo)
        if rc != 0:
            return rc
        ui.ok(f"В {GRUB_DEFAULTS_PATH} прописан GRUB_DEFAULT=\"{default_id}\".")

    return _run(f"{sudo}update-grub", "update-grub")


def _replace_grub_default(content: str, default_id: str) -> str:
    quoted = f'GRUB_DEFAULT="{default_id}"'
    pattern = re.compile(r"^GRUB_DEFAULT=.*$", re.MULTILINE)
    if pattern.search(content):
        return pattern.sub(lambda _m: quoted, content, count=1)
    suffix = "" if content.endswith("\n") else "\n"
    return f"{content}{suffix}{quoted}\n"


def _write_grub_defaults(content: str, *, sudo: str) -> int:
    if not sudo:
        try:
            GRUB_DEFAULTS_PATH.write_text(content, encoding="utf-8")
            return 0
        except OSError as e:
            ui.err(f"Не удалось записать {GRUB_DEFAULTS_PATH}: {e}")
            return 1

    cmd = ["sudo", "tee", str(GRUB_DEFAULTS_PATH)]
    ui.cmd(f"{sudo}tee {GRUB_DEFAULTS_PATH} < <updated>")
    proc = subprocess.run(cmd, input=content, text=True, stdout=subprocess.DEVNULL)
    if proc.returncode != 0:
        ui.err(f"Не удалось записать {GRUB_DEFAULTS_PATH} (код {proc.returncode}).")
    return proc.returncode


def _read_grub_cfg(sudo: str) -> str | None:
    try:
        return GRUB_CFG_PATH.read_text(encoding="utf-8", errors="replace")
    except PermissionError:
        pass
    except OSError:
        return None
    cmd = ["sudo", "cat", str(GRUB_CFG_PATH)] if sudo else ["cat", str(GRUB_CFG_PATH)]
    rc, out, _ = _capture(cmd)
    if rc != 0:
        return None
    return out


def _resolve_grub_default_id(version: str, *, sudo: str = "") -> str | None:
    cfg = _read_grub_cfg(sudo)
    if cfg is None:
        return None

    menuentry_re = re.compile(
        r"^\s*menuentry\s+'(?P<title>[^']*)'.*\$menuentry_id_option\s+'(?P<id>[^']+)'"
    )
    submenu_re = re.compile(
        r"^\s*submenu\s+'(?P<title>[^']*)'.*\$menuentry_id_option\s+'(?P<id>[^']+)'"
    )

    submenu_stack: list[tuple[int, str]] = []
    brace_depth = 0

    for line in cfg.splitlines():
        stripped = line.strip()
        me_m = menuentry_re.match(stripped)
        if me_m:
            title = me_m.group("title")
            entry_id = me_m.group("id")
            if _entry_matches(title, entry_id, version):
                prefix = ">".join(sid for _, sid in submenu_stack)
                return f"{prefix}>{entry_id}" if prefix else entry_id

        sub_id_pending: str | None = None
        sub_m = submenu_re.match(stripped)
        if sub_m:
            sub_id_pending = sub_m.group("id")

        for ch in line:
            if ch == "{":
                brace_depth += 1
                if sub_id_pending is not None:
                    submenu_stack.append((brace_depth, sub_id_pending))
                    sub_id_pending = None
            elif ch == "}":
                brace_depth -= 1
                while submenu_stack and submenu_stack[-1][0] > brace_depth:
                    submenu_stack.pop()

    return None


def _entry_matches(title: str, entry_id: str, version: str) -> bool:
    needle = version.strip()
    if not needle:
        return False
    return needle in title or needle in entry_id
