from __future__ import annotations

import os
import re
import shlex
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from shutil import rmtree, which
from urllib.parse import urljoin, urlparse

from allta_cli.utils import ui
from allta_cli.utils.config import DEVPI_SOURCE_INDEX_URL, DEVPI_URL
from allta_cli.utils.http_fallback import http_fallback_url
from allta_cli.utils.lazy import requests
from allta_cli.utils.config_api import (
    ConfigApiError,
    ServiceCredentialNotFound,
    get_service_credential,
)
from allta_cli.utils.auth import AuthError, NotAuthenticatedError, TokenExpiredError, current_login, load_token
from allta_cli.utils.runtime_env import system_ld_library_path_scope


DEFAULT_DEVPI_INDEX = "root/pypi"
DEFAULT_CREDENTIAL_NAMES = ("devpi_allta", "devpi_root")
DIST_SUFFIXES = (".whl", ".tar.gz", ".zip", ".tar.bz2")
EXACT_SPEC_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+(?:\[[^\]]+\])?)\s*(?:===|==)\s*([^;\s]+)")
NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)")
OPERATOR_VALUE_RE = re.compile(r"^(===|==|>=|<=|~=|!=|>|<|=)(.+)$")
SPEC_OPERATORS = {"===", "==", ">=", "<=", "~=", "!=", ">", "<", "="}


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attrs_dict = dict(attrs)
        self._href = attrs_dict.get("href")
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        self.links.append((self._href, "".join(self._text).strip()))
        self._href = None
        self._text = []


def _normalize_repo(repo: str) -> str:
    index = repo.strip("/")
    if not index:
        index = DEFAULT_DEVPI_INDEX
    if "/" not in index:
        return f"root/{index}"
    return index


def _default_devpi_index() -> str:
    return _normalize_repo(
        os.getenv("ALLTA_DEVPI_PYPI_INDEX")
        or os.getenv("DEVPI_PYPI_INDEX")
        or os.getenv("ALLTA_DEVPI_INDEX")
        or os.getenv("DEVPI_INDEX")
        or DEFAULT_DEVPI_INDEX
    )


def _devpi_index(repo: str | None = None) -> str:
    return _normalize_repo(repo or _default_devpi_index())


def _index_url(repo: str | None = None) -> str:
    return f"{DEVPI_URL.rstrip('/')}/{_devpi_index(repo)}"


def _simple_url(repo: str | None = None, package: str | None = None) -> str:
    base = f"{_index_url(repo)}/+simple/"
    if package:
        return urljoin(base, f"{_pep503_name(package)}/")
    return base


def _run(args: list[str], fail: str, *, hide_password: bool = False) -> int:
    shown = list(args)
    if hide_password:
        for i, arg in enumerate(shown):
            if arg == "--password" and i + 1 < len(shown):
                shown[i + 1] = "<hidden>"
    ui.cmd(" ".join(shlex.quote(part) for part in shown))
    with system_ld_library_path_scope():
        rc = subprocess.run(args).returncode
    if rc != 0:
        ui.err(f"{fail} (код {rc})")
    return rc


def _pip_bin() -> str | None:
    for name in ("pip3", "pip"):
        found = which(name)
        if found:
            return found
    return None


def _devpi_bin() -> str | None:
    return which("devpi")


def _strip_inline_comment(line: str) -> str:
    return re.sub(r"\s+#.*$", "", line).strip()


def _normalize_spec(spec: str) -> str:
    normalized = " ".join(str(spec).strip().split())
    normalized = re.sub(r"\s*(===|==|>=|<=|~=|!=|>|<|=)\s*", r"\1", normalized)
    normalized = re.sub(r"(?<![<>=!~])=(?![=])", "==", normalized)
    normalized = re.sub(r"\s*,\s*", ",", normalized)
    return normalized


def _specs_from_cli_tokens(tokens: tuple[str, ...]) -> list[str]:
    specs: list[str] = []
    i = 0
    while i < len(tokens):
        spec = str(tokens[i]).strip()
        i += 1
        if not spec:
            continue

        added_operator = False
        while i < len(tokens):
            token = str(tokens[i]).strip()
            if i + 1 < len(tokens) and token in SPEC_OPERATORS:
                operator = "==" if token == "=" else token
                version = str(tokens[i + 1]).strip()
                i += 2
            else:
                match = OPERATOR_VALUE_RE.match(token)
                if not match:
                    break
                operator = "==" if match.group(1) == "=" else match.group(1)
                version = match.group(2).strip()
                i += 1
            if not version:
                continue
            prefix = "," if added_operator else ""
            spec = f"{spec}{prefix}{operator}{version}"
            added_operator = True
        specs.append(_normalize_spec(spec))
    return specs


def _read_req_lines(path: str) -> list[str]:
    req = Path(path).expanduser()
    if not req.is_file():
        raise FileNotFoundError(str(req))

    specs: list[str] = []
    for line in req.read_text(encoding="utf-8").splitlines():
        text = _strip_inline_comment(line)
        if not text or text.startswith("#") or text.startswith("-"):
            continue
        specs.append(text)
    return specs


def _collect_specs(packages: tuple[str, ...], req_files: tuple[str, ...]) -> list[str]:
    specs: list[str] = []
    for req_file in req_files:
        try:
            specs.extend(_read_req_lines(req_file))
        except FileNotFoundError:
            ui.err(f"Файл требований не найден: {Path(req_file).expanduser()}")
            raise
    specs.extend(_specs_from_cli_tokens(packages))

    result: list[str] = []
    seen: set[str] = set()
    for spec in specs:
        normalized = _normalize_spec(spec)
        if not normalized:
            continue
        key = normalized.lower().replace("_", "-")
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def _package_name(spec: str) -> str:
    match = NAME_RE.match(spec)
    return (match.group(1) if match else spec).lower().replace("_", "-")


def _is_allta(spec: str) -> bool:
    return _package_name(spec) == "allta"


def _session_credentials() -> tuple[str, str] | None:
    try:
        return current_login(), load_token(verbose=False)
    except (AuthError, NotAuthenticatedError, TokenExpiredError):
        return None


def _session_credential_source() -> str | None:
    try:
        login = current_login()
        load_token(verbose=False)
    except (AuthError, NotAuthenticatedError, TokenExpiredError):
        return None
    return f"allta login token ({login})"


def _env_credentials() -> tuple[str, str] | None:
    env_user = os.getenv("ALLTA_DEVPI_USERNAME") or os.getenv("DEVPI_UPLOAD_USER") or os.getenv("DEVPI_LOCAL_UPLOAD_USER")
    env_password = (
        os.getenv("ALLTA_DEVPI_PASSWORD")
        or os.getenv("DEVPI_UPLOAD_PASSWORD")
        or os.getenv("DEVPI_LOCAL_UPLOAD_PASSWORD")
    )
    if env_user and env_password:
        return env_user, env_password
    return None


def _env_credential_source() -> str | None:
    env_user = os.getenv("ALLTA_DEVPI_USERNAME") or os.getenv("DEVPI_UPLOAD_USER") or os.getenv("DEVPI_LOCAL_UPLOAD_USER")
    env_password = (
        os.getenv("ALLTA_DEVPI_PASSWORD")
        or os.getenv("DEVPI_UPLOAD_PASSWORD")
        or os.getenv("DEVPI_LOCAL_UPLOAD_PASSWORD")
    )
    if env_user and env_password:
        return f"env ({env_user})"
    return None


def _service_credentials(names: tuple[str, ...] | None = None) -> tuple[str, str] | None:
    ordered_names = list(names or ())
    ordered_names.append(os.getenv("ALLTA_DEVPI_CREDENTIAL") or "")
    ordered_names.extend(DEFAULT_CREDENTIAL_NAMES)
    seen: set[str] = set()
    for name in [item.strip() for item in ordered_names if item.strip()]:
        if name in seen:
            continue
        seen.add(name)
        try:
            cred = get_service_credential(name)
        except ServiceCredentialNotFound:
            continue
        except (ConfigApiError, NotAuthenticatedError, TokenExpiredError, AuthError) as exc:
            ui.err(f"Не удалось получить devpi credential '{name}': {exc}")
            return None
        return cred["username"], cred["password"]

    return None


def _service_credential_source(names: tuple[str, ...] | None = None) -> str | None:
    ordered_names = list(names or ())
    ordered_names.append(os.getenv("ALLTA_DEVPI_CREDENTIAL") or "")
    ordered_names.extend(DEFAULT_CREDENTIAL_NAMES)
    seen: set[str] = set()
    for name in [item.strip() for item in ordered_names if item.strip()]:
        if name in seen:
            continue
        seen.add(name)
        try:
            cred = get_service_credential(name)
        except ServiceCredentialNotFound:
            continue
        except (ConfigApiError, NotAuthenticatedError, TokenExpiredError, AuthError):
            return f"config-service {name} unavailable"
        return f"config-service {name} ({cred['username']})"
    return None


def _devpi_credentials(repo: str) -> tuple[str, str] | None:
    if _devpi_index(repo) == "root/pypi":
        credentials = _service_credentials(("devpi_allta",)) or _session_credentials() or _env_credentials()
    else:
        credentials = _session_credentials() or _service_credentials() or _env_credentials()
    if credentials is None:
        ui.err(
            "Не найдены devpi-креды. Выполните allta login, задайте "
            "credential 'devpi_allta' или ALLTA_DEVPI_USERNAME/ALLTA_DEVPI_PASSWORD."
        )
    return credentials


def _credential_source(repo: str) -> str:
    if _devpi_index(repo) == "root/pypi":
        source = (
            _service_credential_source(("devpi_allta",))
            or _session_credential_source()
            or _env_credential_source()
        )
    else:
        source = (
            _session_credential_source()
            or _service_credential_source()
            or _env_credential_source()
        )
    return source or "not found"


def _devpi_login(username: str, password: str, repo: str | None = None) -> int:
    devpi = _devpi_bin()
    if not devpi:
        ui.err("Не найден devpi-client (devpi).")
        return 1

    rc = _run([devpi, "use", "--set-cfg", _index_url(repo)], "devpi use")
    if rc != 0:
        return rc
    return _run([devpi, "login", username, "--password", password], "devpi login", hide_password=True)


def _download_one(spec: str, wheelhouse: Path, *, no_deps: bool) -> int:
    pip = _pip_bin()
    if not pip:
        ui.err("Не найден pip (pip3/pip). Сначала установите Python: allta python")
        return 1

    cmd = [pip, "download", "--index-url", DEVPI_SOURCE_INDEX_URL, spec, "--dest", str(wheelhouse)]
    if no_deps:
        cmd.append("--no-deps")
    return _run(cmd, f"pip download {spec}")


def _download_from_repo(specs: list[str], dest: Path, repo: str, *, no_deps: bool) -> int:
    pip = _pip_bin()
    if not pip:
        ui.err("Не найден pip (pip3/pip). Сначала установите Python: allta python")
        return 1

    dest.mkdir(parents=True, exist_ok=True)
    cmd = [pip, "download", "--index-url", _simple_url(repo), "--dest", str(dest)]
    if no_deps:
        cmd.append("--no-deps")
    cmd.extend(specs)
    return _run(cmd, "pip download из devpi")


def _distfiles(wheelhouse: Path) -> list[Path]:
    return sorted(
        item
        for item in wheelhouse.iterdir()
        if item.is_file() and item.name.endswith(DIST_SUFFIXES)
    )


def _pep503_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _links_from_html(html: str) -> list[tuple[str, str]]:
    parser = LinkParser()
    parser.feed(html)
    return parser.links


def _http_get_text(url: str) -> tuple[int, str]:
    urls = [url]
    fallback = http_fallback_url(url)
    if fallback:
        urls.append(fallback)
    last_status = 0
    last_text = ""
    for candidate in urls:
        try:
            response = requests.get(candidate, timeout=10, verify=False)
        except requests.RequestException as exc:
            last_text = str(exc)
            continue
        last_status = response.status_code
        last_text = response.text
        if response.status_code == 200:
            return response.status_code, response.text
        if response.status_code == 404:
            return response.status_code, response.text
    return last_status, last_text


def _repo_exists(repo: str) -> bool:
    status, _ = _http_get_text(_index_url(repo))
    return status == 200


def _ensure_repo(repo: str) -> bool:
    if _repo_exists(repo):
        return True
    ui.err(f"Repo не найден: {repo} ({_index_url(repo)})")
    return False


def list_repos() -> int:
    status, text = _http_get_text(f"{DEVPI_URL.rstrip('/')}/")
    if status != 200:
        ui.err(f"Не удалось прочитать список repo ({DEVPI_URL}) HTTP {status}.")
        return 1

    repos = sorted(
        {
            label.strip()
            for _, label in _links_from_html(text)
            if "/" in label.strip() and not label.strip().startswith("+")
        }
    )
    if not repos:
        ui.warn("Repo не найдены.")
        return 0

    rows = []
    for repo in repos:
        names = _project_names(repo)
        rows.append([repo, len(names) if names is not None else "-"])
    ui.table(["Repo", "Packages"], rows)
    return 0


def _project_names(repo: str) -> list[str] | None:
    status, text = _http_get_text(_simple_url(repo))
    if status != 200:
        ui.err(f"Не удалось прочитать {_devpi_index(repo)}/+simple/ (HTTP {status}).")
        return None
    names = sorted({_pep503_name(label or href.rstrip("/").split("/")[-1]) for href, label in _links_from_html(text)})
    return [name for name in names if name]


def _filename_from_link(href: str, label: str) -> str:
    raw = label or urlparse(href).path.rsplit("/", 1)[-1]
    return raw.split("#", 1)[0]


def _version_from_dist(filename: str, project: str) -> str | None:
    name = _pep503_name(project)
    base = filename
    for suffix in (".tar.gz", ".tar.bz2", ".zip", ".whl"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    normalized = _pep503_name(base)
    prefix = f"{name}-"
    if not normalized.startswith(prefix):
        return None
    rest = base[len(project) + 1 :] if base.lower().startswith(project.lower() + "-") else base[len(prefix) :]
    if filename.endswith(".whl"):
        return rest.split("-", 1)[0]
    return rest


def _package_files(repo: str, package: str) -> list[dict[str, str]] | None:
    status, text = _http_get_text(_simple_url(repo, package))
    if status == 404:
        return []
    if status != 200:
        ui.err(f"Не удалось прочитать пакет {package} в {_devpi_index(repo)} (HTTP {status}).")
        return None

    files: list[dict[str, str]] = []
    for href, label in _links_from_html(text):
        filename = _filename_from_link(href, label)
        version = _version_from_dist(filename, package) or "-"
        files.append({"filename": filename, "version": version, "url": urljoin(_simple_url(repo, package), href)})
    return files


def _package_versions(repo: str, package: str) -> list[str] | None:
    files = _package_files(repo, package)
    if files is None:
        return None
    return _sort_versions({item["version"] for item in files if item["version"] != "-"})


def _matching_versions(spec: str, versions: list[str]) -> list[str]:
    try:
        from packaging.requirements import Requirement
        from packaging.version import InvalidVersion, Version
    except Exception:
        return versions

    try:
        requirement = Requirement(spec)
    except Exception:
        return versions
    if not requirement.specifier:
        return versions

    matched: list[str] = []
    for version in versions:
        try:
            parsed = Version(version)
        except InvalidVersion:
            continue
        if parsed in requirement.specifier:
            matched.append(version)
    return matched


def _sort_versions(versions) -> list[str]:
    try:
        from packaging.version import InvalidVersion, Version
    except Exception:
        return sorted(versions)

    def key(value: str):
        try:
            return (0, Version(value))
        except InvalidVersion:
            return (1, value)

    return sorted(versions, key=key)


def _format_versions(versions: list[str]) -> str:
    if not versions:
        return "-"
    if len(versions) <= 6:
        return ", ".join(versions)
    head = ", ".join(versions[:2])
    tail = ", ".join(versions[-3:])
    return f"{head}, ..., {tail}"


def load_packages(
    packages: tuple[str, ...],
    req_files: tuple[str, ...],
    *,
    repo: str | None = None,
    no_deps: bool = False,
) -> int:
    target_repo = _devpi_index(repo)
    if not _ensure_repo(target_repo):
        return 1
    try:
        specs = _collect_specs(packages, req_files)
    except FileNotFoundError:
        return 1

    if not specs:
        ui.err("Укажите пакет или requirements-файл: allta devpi load requests==2.32.3")
        return 1

    allta_specs = [spec for spec in specs if _is_allta(spec)]
    if allta_specs:
        ui.err("Пакет allta не загружается в root/pypi: он публикуется в root/release.")
        return 1

    credentials = _devpi_credentials(target_repo)
    if credentials is None:
        return 1

    wheelhouse = Path(tempfile.mkdtemp(prefix="allta-devpi-wheelhouse-"))
    try:
        failures = 0
        for spec in specs:
            ui.step(f"Скачиваю {spec}")
            if _download_one(spec, wheelhouse, no_deps=no_deps) != 0:
                failures += 1
        if failures:
            ui.err(f"Не удалось скачать spec-строк: {failures}. Upload отменён.")
            return 1

        files = _distfiles(wheelhouse)
        if not files:
            ui.warn("pip download не сохранил дистрибутивы. Загружать нечего.")
            return 0

        username, password = credentials
        if _devpi_login(username, password, target_repo) != 0:
            return 1

        devpi = _devpi_bin()
        assert devpi is not None
        failed_uploads = 0
        for dist in files:
            if _run([devpi, "upload", str(dist)], f"devpi upload {dist.name}") != 0:
                failed_uploads += 1

        if failed_uploads:
            ui.err(f"Не удалось загрузить дистрибутивов: {failed_uploads}.")
            return 1
        ui.ok(f"Загружено дистрибутивов в {target_repo}: {len(files)}.")
        return 0
    finally:
        rmtree(wheelhouse, ignore_errors=True)


def _remove_target(spec: str) -> str | None:
    exact = EXACT_SPEC_RE.match(spec)
    if exact:
        return f"{exact.group(1)}=={exact.group(2)}"
    if re.match(r"^\s*[A-Za-z0-9_.-]+\s*$", spec):
        return spec.strip()
    return None


def remove_packages(
    packages: tuple[str, ...],
    req_files: tuple[str, ...],
    *,
    repo: str | None = None,
    yes: bool = False,
) -> int:
    target_repo = _devpi_index(repo)
    if not _ensure_repo(target_repo):
        return 1
    try:
        specs = _collect_specs(packages, req_files)
    except FileNotFoundError:
        return 1

    targets: list[str] = []
    skipped: list[str] = []
    for spec in specs:
        target = _remove_target(spec)
        if target is None:
            skipped.append(spec)
        else:
            targets.append(target)

    if skipped:
        for spec in skipped:
            ui.warn(f"Пропускаю range/сложный spec для remove: {spec}")

    if not targets:
        ui.err("Нет целей для удаления. Используйте имя пакета или точный pin package==version.")
        return 1

    credentials = _devpi_credentials(target_repo)
    if credentials is None:
        return 1

    username, password = credentials
    if _devpi_login(username, password, target_repo) != 0:
        return 1

    devpi = _devpi_bin()
    if not devpi:
        ui.err("Не найден devpi-client (devpi).")
        return 1

    failures = 0
    for target in targets:
        cmd = [devpi, "remove"]
        if yes:
            cmd.append("-y")
        cmd.append(target)
        if _run(cmd, f"devpi remove {target}") != 0:
            failures += 1

    if failures:
        ui.err(f"Не удалось удалить целей: {failures}.")
        return 1
    ui.ok(f"Удалено целей из {target_repo}: {len(targets)}.")
    return 0


def list_packages(values: tuple[str, ...], req_files: tuple[str, ...], *, repo: str | None = None) -> int:
    target_repo = _devpi_index(repo)
    if not _ensure_repo(target_repo):
        return 1

    try:
        specs = _collect_specs(values, req_files)
    except FileNotFoundError:
        return 1

    if not specs:
        names = _project_names(target_repo)
        if names is None:
            return 1
        rows: list[list[object]] = []
        for name in names:
            versions = _package_versions(target_repo, name)
            if versions is None:
                return 1
            rows.append([name, len(versions), versions[-1] if versions else "-"])
        if rows:
            ui.table(["Package", "Versions", "Latest"], rows)
        else:
            ui.warn(f"В {target_repo} нет пакетов.")
        return 0

    rows = []
    failed = 0
    for spec in specs:
        name = _package_name(spec)
        versions = _package_versions(target_repo, name)
        if versions is None:
            failed += 1
            continue
        matched = _matching_versions(spec, versions)
        if not versions:
            status = "нет пакета"
        elif matched:
            status = "ok"
        else:
            status = "нет подходящей версии"
        rows.append([spec, status, _format_versions(matched or versions)])
    ui.table(["Spec", "Status", "Versions"], rows)
    return 1 if failed else 0


def check_package(values: tuple[str, ...], *, repo: str | None = None) -> int:
    target_repo = _devpi_index(repo)
    if not _ensure_repo(target_repo):
        return 1
    rest = values
    if len(rest) != 1:
        ui.err("Использование: allta devpi check --repo root/pypi package")
        return 1

    package = _package_name(rest[0])
    files = _package_files(target_repo, package)
    if files is None:
        return 1
    if not files:
        ui.warn(f"Пакет {package} не найден в {target_repo}.")
        return 1

    ui.echo(f"Repo: {_index_url(target_repo)}")
    ui.echo(f"Package: {package}")
    versions = _sort_versions({item["version"] for item in files if item["version"] != "-"})
    ui.echo(f"Versions: {', '.join(versions) or '-'}")
    rows = [[item["version"], item["filename"], item["url"]] for item in files]
    ui.table(["Version", "File", "URL"], rows)
    return 0


def download_packages(
    values: tuple[str, ...],
    *,
    repo: str | None = None,
    dest: str = ".",
    no_deps: bool = False,
) -> int:
    target_repo = _devpi_index(repo)
    if not _ensure_repo(target_repo):
        return 1
    rest = values
    specs = _specs_from_cli_tokens(rest)
    if not specs:
        ui.err("Использование: allta devpi download --repo root/pypi package")
        return 1
    return _download_from_repo(specs, Path(dest).expanduser(), target_repo, no_deps=no_deps)


def install_packages(
    values: tuple[str, ...],
    req_files: tuple[str, ...],
    *,
    repo: str | None = None,
    python: str | None = None,
) -> int:
    target_repo = _devpi_index(repo)
    if not _ensure_repo(target_repo):
        return 1
    if not python:
        ui.err("Использование: allta devpi install --repo root/pypi --python /usr/bin/python3 package")
        return 1

    try:
        specs = _collect_specs(values, req_files)
    except FileNotFoundError:
        return 1
    if not specs:
        ui.err("Укажите пакет или -r requirements.txt для установки.")
        return 1

    cmd = [python, "-m", "pip", "install", "--index-url", _simple_url(target_repo)]
    cmd.extend(specs)
    return _run(cmd, "pip install из devpi")


def debug_config(*, repo: str | None = None) -> int:
    target_repo = _devpi_index(repo)
    url_source = "ALLTA_DEVPI_URL" if os.getenv("ALLTA_DEVPI_URL") else "DEVPI_URL" if os.getenv("DEVPI_URL") else "default"
    rows = [
        ["DEVPI_URL", DEVPI_URL],
        ["DEVPI_URL source", url_source],
        ["DEVPI_SOURCE_INDEX_URL", DEVPI_SOURCE_INDEX_URL],
        ["Repo", target_repo],
        ["Index URL", _index_url(target_repo)],
        ["Simple URL", _simple_url(target_repo)],
        ["Credential source", _credential_source(target_repo)],
    ]
    ui.table(["Field", "Value"], rows)
    return 0
