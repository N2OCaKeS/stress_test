"""Общая логика листинга пакетов для сервера и гостя ВМ.

Обе таски (`installed_packages.list` для прямого сервера и `vm.list_packages`
для гостя ВМ) выполняют один и тот же набор команд — детект пакетного менеджера
через `command -v`, листинг (`dpkg-query -W` / `rpm -qa` / apk/pacman/portage/
xbps) и разбор вывода в `[{name, version}, ...]`. Отличается только транспорт:
сервер ходит прямой SSH-сессией, ВМ — вложенным `ssh`/`sshpass` из hub-сессии.
Транспорт спрятан за `_target_runner.TargetRunner` (примитив `run`), поэтому
здесь код от него не зависит.

Поддержаны разные пакетные бэкенды (фронтенды apt/dnf/yum/zypper работают
поверх dpkg/rpm, отдельно не кодятся):

* `dpkg` — Debian/Ubuntu/Astra Linux → `dpkg-query -W`. Пустой вывод = ничего
  не подошло под pattern.
* `rpm` — RHEL/CentOS/Fedora → `rpm -qa --queryformat`.
* `apk` — Alpine → `apk info -v` (строка `<name>-<version>-r<rel>`).
* `pacman` — Arch/Manjaro → `pacman -Q` (строки `<name> <version>`).
* `portage` — Gentoo → `qlist -Iv` (строки `<category>/<name>-<version>`).
* `xbps` — Void → `xbps-query -l` (строки `ii <name>-<version>_<rev> <desc>`).

Можно запросить несколько паттернов сразу (OR-матч). dpkg/rpm принимают
несколько glob'ов позиционными аргументами; менеджеры без tool-side glob
(apk/pacman/portage/xbps) листят всё, а OR-фильтрация делается в Python
(`filter_by_patterns`). Дубли по имени схлопываются (`dedup_by_name`). Ни один
менеджер не найден — `SshError(error_code="NO_PACKAGE_MANAGER")`.

Pattern — shell glob. Перед подстановкой в команду каждый обязан пройти
`PATTERN_RE` (валидирует caller) — это defence-in-depth от break-out из
одиночных кавычек и shell-инъекции, не полагаемся на валидацию server_service.
"""

from __future__ import annotations

import fnmatch
import re

from src.clients.ssh import SshError

# Локальный allow-list символов для glob-pattern перед подстановкой в
# shell-команду. Принимаем только то, что нужно dpkg-query/rpm glob'у: буквы,
# цифры, точка, подчёркивание, дефис, плюс и сами glob-метасимволы `* ? [ ]`.
# Кавычка `'`, `$`, `;`, `\`, backtick, пробелы, перевод строки не проходят —
# это закрывает break-out из одиночных кавычек и shell-инъекцию.
PATTERN_RE = re.compile(r"^[A-Za-z0-9._\-+*?\[\]]+$")

# Явный хинт os_family → package manager, чтобы не гонять 6 проб по SSH, когда
# семейство ОС уже известно server_service'у (актуально для гостя ВМ). Всё, что
# не распознали, уходит в живой детект.
OS_FAMILY_TO_PM: dict[str, str] = {
    "apt": "dpkg", "dpkg": "dpkg", "debian": "dpkg", "astra": "dpkg",
    "dnf": "rpm", "rpm": "rpm", "yum": "rpm", "rhel": "rpm", "redos": "rpm",
}

# Порядок живого детекта пакетного менеджера через `command -v`. dpkg первый —
# основной target Astra Linux. portage детектим по `qlist` (portage-utils —
# стандарт для скриптинга на Gentoo), а не по emerge.
_PM_PROBES: tuple[tuple[str, str], ...] = (
    ("dpkg", "command -v dpkg-query"),
    ("rpm", "command -v rpm"),
    ("apk", "command -v apk"),
    ("pacman", "command -v pacman"),
    ("portage", "command -v qlist"),
    ("xbps", "command -v xbps-query"),
)

# Менеджеры без tool-side glob: листят весь набор пакетов, OR-фильтр по
# паттернам делается в Python.
_NO_TOOL_GLOB = frozenset({"apk", "pacman", "portage", "xbps"})


def resolve_patterns(payload: dict) -> list[str]:
    """Свести payload к списку glob-паттернов с дедупом и сохранением порядка.

    Приоритет: `patterns` (список) → одиночный `pattern` (back-compat) →
    `["*"]` (дефолт). Не-list `patterns` или не-str `pattern` подменяются
    дефолтом — валидацию каждого элемента делает caller через `PATTERN_RE`.
    """
    raw = payload.get("patterns")
    if isinstance(raw, list) and raw:
        source = raw
    else:
        pattern = payload.get("pattern", "*")
        source = [pattern] if pattern is not None else ["*"]
    seen: set = set()
    out: list[str] = []
    for p in source:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def build_command(package_manager: str, patterns: list[str]) -> str:
    """Собрать shell-команду листинга под выбранный package manager.

    Каждый pattern оборачивается в одинарные кавычки. Перед подстановкой они
    обязаны пройти `PATTERN_RE` (caller валидирует) — внутри не может быть
    `'`/`$`/`;` и прочих метасимволов, поэтому break-out из кавычек невозможен.

    Для dpkg/rpm несколько паттернов передаются позиционными аргументами —
    инструмент сам делает OR-матч. apk/pacman/portage/xbps не глоббят на стороне
    инструмента — листят всё, OR-фильтр по паттернам делается в Python
    (`filter_by_patterns`), поэтому pattern в shell им не подставляется.
    """
    if package_manager == "dpkg":
        joined = " ".join("'" + p + "'" for p in patterns)
        return (
            "dpkg-query -W -f='${Package} ${Version}\\n' "
            + joined
            + " 2>/dev/null"
        )
    if package_manager == "rpm":
        joined = " ".join("'" + p + "'" for p in patterns)
        return (
            "rpm -qa --queryformat '%{NAME} %{VERSION}\\n' "
            + joined
            + " 2>/dev/null"
        )
    if package_manager == "apk":
        return "apk info -v 2>/dev/null"
    if package_manager == "pacman":
        return "pacman -Q 2>/dev/null"
    if package_manager == "portage":
        return "qlist -Iv 2>/dev/null"
    if package_manager == "xbps":
        return "xbps-query -l 2>/dev/null"
    raise SshError(
        error_code="NO_PACKAGE_MANAGER",
        host="",
        message=f"unsupported package manager: {package_manager}",
    )


def parse_packages(
    stdout: str, package_manager: str = "dpkg"
) -> list[dict[str, str]]:
    """Разобрать вывод package manager'а в список `[{name, version}, ...]`.

    Для dpkg-query / rpm -qa / pacman формат строки — `<name> <version>` (две
    колонки, разделитель пробел). Для apk (`apk info -v`) формат другой — одна
    слитная строка `<name>-<version>-r<rel>`, разбирается отдельно
    (`_parse_apk_line`). portage (`qlist -Iv`) и xbps (`xbps-query -l`) тоже со
    своими парсерами.

    Один пакет может встретиться несколько раз (например, разные версии
    `linux-image-*`) — отдаём как есть, без дедупликации: caller решает, как
    показывать. Пустая / нераспознанная строка пропускается (защита от мусора).
    """
    packages: list[dict[str, str]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if package_manager == "apk":
            packages.append(_parse_apk_line(line))
            continue
        if package_manager == "portage":
            packages.append(_parse_portage_line(line))
            continue
        if package_manager == "xbps":
            parsed = _parse_xbps_line(line)
            if parsed is not None:
                packages.append(parsed)
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        name, version = parts
        packages.append({"name": name, "version": version})
    return packages


def _parse_apk_line(line: str) -> dict[str, str]:
    """Разобрать одну строку `apk info -v` в `{name, version}`.

    Формат — `<name>-<version>-r<rel>`, где имя пакета само может содержать
    дефисы (`py3-pip-23.1-r0`). Версия всегда занимает два последних
    дефис-сегмента (`<version>-r<rel>`), поэтому режем справа на 3 части. Если
    revision-сегмента нет — fallback: режем справа один раз; не делится и это —
    вся строка уходит в name с пустой version.
    """
    parts = line.rsplit("-", 2)
    if len(parts) == 3:
        name, ver, rel = parts
        return {"name": name, "version": f"{ver}-{rel}"}
    parts = line.rsplit("-", 1)
    if len(parts) == 2:
        return {"name": parts[0], "version": parts[1]}
    return {"name": line, "version": ""}


# Версия в portage-атоме начинается с цифры сразу после дефиса. Имя берём
# non-greedy, чтобы дефисы внутри имени (`libfoo-bar`) не съелись в версию.
_PORTAGE_RE = re.compile(r"^(.*?)-(\d.*)$")


def _parse_portage_line(line: str) -> dict[str, str]:
    """Разобрать одну строку `qlist -Iv` в `{name, version}`.

    Формат — `<category>/<name>-<version>`, напр. `app-shells/bash-5.2_p15`.
    Категорию отбрасываем (basename после последнего `/`), затем делим имя и
    версию по первому дефису, за которым идёт цифра. Версии нет — вся строка в
    name.
    """
    basename = line.rsplit("/", 1)[-1]
    m = _PORTAGE_RE.match(basename)
    if m:
        return {"name": m.group(1), "version": m.group(2)}
    return {"name": basename, "version": ""}


def _parse_xbps_line(line: str) -> dict[str, str] | None:
    """Разобрать одну строку `xbps-query -l` в `{name, version}`.

    Формат — `ii <name>-<version>_<rev>   <description>`: первый токен —
    состояние (`ii`), второй — `name-version_rev`, дальше описание. Берём токен
    с индексом 1 и режем по последнему дефису. Строки с менее чем двумя токенами
    пропускаем (возвращаем `None`).
    """
    tokens = line.split()
    if len(tokens) < 2:
        return None
    name, _, version = tokens[1].rpartition("-")
    if not name:
        return {"name": tokens[1], "version": ""}
    return {"name": name, "version": version}


def filter_by_patterns(
    packages: list[dict[str, str]], patterns: list[str]
) -> list[dict[str, str]]:
    """Отфильтровать пакеты по набору glob-паттернов (OR-матч) на стороне Python.

    Нужно для apk/pacman/portage/xbps, которые сами не глоббят. Пакет проходит,
    если его имя матчит ХОТЯ БЫ один паттерн. Пустой список или наличие
    `*`/пустого паттерна — без фильтра (вернуть всё). Сравнение через
    `fnmatch.fnmatchcase` — case-sensitive и не зависит от ОС воркера, что
    согласуется с tool-side глоббингом dpkg-query/rpm.
    """
    if not patterns or any(not p or p == "*" for p in patterns):
        return packages
    return [
        p for p in packages
        if any(fnmatch.fnmatchcase(p["name"], pat) for pat in patterns)
    ]


def dedup_by_name(
    packages: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Схлопнуть пакеты-дубли по имени, сохраняя порядок первого вхождения.

    При нескольких паттернах пакет может подойти под не один glob (`ssh*` и
    `*server*` оба матчат `openssh-server`) — на стороне dpkg/rpm это дало бы две
    одинаковые строки. Мульти-версионные пакеты (`linux-image-*`) различаются
    именем и не схлопываются.
    """
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for p in packages:
        if p["name"] not in seen:
            seen.add(p["name"])
            out.append(p)
    return out


async def detect_package_manager(runner) -> str:
    """Определить package manager на цели через `command -v` (по порядку).

    `command -v` возвращает rc=0 и путь, если бинарь в PATH, rc!=0 если нет —
    POSIX-стандарт, есть и в Debian, и в RHEL, и в Alpine. Порядок проверки:
    dpkg → rpm → apk → pacman → portage → xbps; берём первый найденный. Ни
    одного — `SshError(NO_PACKAGE_MANAGER)`.
    """
    for pm, probe in _PM_PROBES:
        rc, _out, _err = await runner.run(probe)
        if rc == 0:
            return pm
    raise SshError(
        error_code="NO_PACKAGE_MANAGER",
        host=runner.host,
        message=(
            "no supported package manager "
            "(dpkg-query/rpm/apk/pacman/qlist/xbps-query) on target host"
        ),
    )


async def collect_packages(
    runner, patterns: list[str], *, os_family: str = "", max_rows=None,
) -> tuple[str, list[dict[str, str]]]:
    """Снять список установленных пакетов с цели через `runner`.

    Определяет package manager (по `os_family`-хинту либо живым `command -v`),
    выполняет команду листинга, парсит вывод, для менеджеров без tool-side glob
    доводит OR-фильтр по паттернам в Python, схлопывает дубли по имени и режет по
    `max_rows`. Возвращает `(package_manager, packages)`.

    dpkg-query/rpm отдают non-zero, если ничего не подошло под pattern (stderr
    пуст) — это валидный пустой результат; rc!=0 со stderr — реальная поломка
    (битая БД пакетов) → `SshError(PACKAGE_QUERY_FAILED)`.
    """
    package_manager = OS_FAMILY_TO_PM.get(os_family or "") or (
        await detect_package_manager(runner)
    )
    cmd = build_command(package_manager, patterns)
    rc, stdout, stderr = await runner.run(cmd)
    if rc != 0:
        if stderr.strip():
            raise SshError(
                error_code="PACKAGE_QUERY_FAILED",
                host=runner.host,
                cmd_sanitized=cmd,
                returncode=rc,
                stderr=stderr.strip(),
                message=f"{package_manager} query failed",
            )
        stdout = ""
    packages = parse_packages(stdout, package_manager)
    if package_manager in _NO_TOOL_GLOB:
        packages = filter_by_patterns(packages, patterns)
    packages = dedup_by_name(packages)
    if isinstance(max_rows, int) and max_rows >= 0:
        packages = packages[:max_rows]
    return package_manager, packages
