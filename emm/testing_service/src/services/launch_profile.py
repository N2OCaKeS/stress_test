"""Профиль запуска теста.

Две части:

* **API профилей** — список/создание/новая версия. Правка никогда не
  меняет существующую версию: создаётся следующая и становится
  `current_version_id`; item, уже получивший задание, остаётся на своей
  (`queue_items.launch_profile_version_id`).
* **Сборка задания воркеру** — `render_paths` +
  `build_launch`: из версии профиля и контекста резолва claim'а получаются
  файлы для SFTP (путь, содержимое, режим, sensitive), команда запуска,
  команда остановки, `use_pty`, `redact_values`. Воркер больше не знает ни
  путей, ни `starter.sh`.

Какой профиль у теста: `test_definitions.launch_profile_id`, иначе профиль
отдела стенда с `is_default`, иначе общий (`department_id IS NULL`).
"""

from __future__ import annotations

import posixpath
import re
import shlex
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import AuthorizationError, DomainValidationError, NotFoundError
from src.dependencies.auth import Identity
from src.models import LaunchProfile, LaunchProfileVersion, TestDefinition, TestStand
from src.repositories import launch_profile as repo
from src.schemas.launch_profile import (
    LaunchProfileCreate,
    LaunchProfileUpdate,
    LaunchProfileVersionInput,
)
from src.services import audit_service, permissions, variable_resolver
from src.services.variable_resolver import ResolveContext, Resolved
from src.utils.ids import launch_profile_id, launch_profile_version_id

# `{{CODE}}` — подстановка в shell-тексте профиля (скрипт, команда остановки):
# одиночные скобки bash использует сам (`${var}`, `{ ...; }`).
SCRIPT_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")

# Отрендеренный путь на стенде: абсолютный, без пробелов и кавычек — он
# уходит и в SFTP, и в команду остановки (шаблон pgrep).
_SAFE_PATH_RE = re.compile(r"/[A-Za-z0-9_./\-]*")

# Спецсимволы ERE, которые надо экранировать в шаблоне pgrep.
_ERE_SPECIAL = set(".[]()*+?{}|^$\\")


# ── права ────────────────────────────────────────────────────────────────────

async def _require_update(db: AsyncSession, identity: Identity, department_id: str | None, action: str) -> None:
    try:
        if department_id is None:
            # Общий профиль действует на все отделы — только по матрице,
            # без bypass'а department_admin.
            await permissions.require_action(db, identity, EntityType.LAUNCH_PROFILE, Action.UPDATE)
        else:
            await permissions.require_department_action(
                db, identity, department_id, EntityType.LAUNCH_PROFILE, Action.UPDATE,
            )
    except AuthorizationError:
        audit_service.emit(
            action, target_id=department_id or "global", target_type="launch_profile",
            status="denied", allowed=False, details={"reason": "permission_denied"},
        )
        raise


async def _get_visible(db: AsyncSession, identity: Identity, profile_id: str) -> LaunchProfile:
    profile = await repo.get_by_id(db, profile_id)
    if profile is None:
        raise NotFoundError(error_code="LAUNCH_PROFILE_NOT_FOUND", message="Launch profile not found")
    if profile.department_id is not None:
        permissions.require_own_department(identity, profile.department_id)
    return profile


# ── API ──────────────────────────────────────────────────────────────────────

async def to_response(db: AsyncSession, profile: LaunchProfile) -> dict:
    version = await repo.get_version(db, profile.current_version_id) if profile.current_version_id else None
    return {
        "id": profile.id, "department_id": profile.department_id, "name": profile.name,
        "is_default": profile.is_default, "current_version": version,
        "created_at": profile.created_at, "updated_at": profile.updated_at,
    }


async def list_for(db: AsyncSession, identity: Identity, department_id: str) -> list[dict]:
    permissions.require_own_department(identity, department_id)
    return [await to_response(db, p) for p in await repo.list_visible(db, department_id)]


async def get_for(db: AsyncSession, identity: Identity, profile_id: str) -> dict:
    return await to_response(db, await _get_visible(db, identity, profile_id))


async def list_versions_for(db: AsyncSession, identity: Identity, profile_id: str) -> list[LaunchProfileVersion]:
    profile = await _get_visible(db, identity, profile_id)
    return await repo.list_versions(db, profile.id)


def _version_data(payload: LaunchProfileVersionInput) -> dict:
    data = payload.model_dump(mode="json")
    return data


async def _add_version(
    db: AsyncSession, profile: LaunchProfile, payload: LaunchProfileVersionInput, created_by: str | None,
) -> LaunchProfileVersion:
    data = _version_data(payload)
    inherit = [f for f in ("rerun_script", "extra_files") if f not in payload.model_fields_set]
    if inherit and profile.current_version_id:
        # тело без `rerun_script`/`extra_files` (клиент не знает
        # поля) — значение наследуется от текущей версии, а не стирается.
        current = await repo.get_version(db, profile.current_version_id)
        for name in inherit:
            data[name] = getattr(current, name) if current is not None else data[name]
    version = await repo.create_version(db, {
        "id": launch_profile_version_id(),
        "profile_id": profile.id,
        "version": await repo.next_version_number(db, profile.id),
        "created_by": created_by,
        **data,
    })
    profile.current_version_id = version.id
    await db.flush()
    return version


async def create(db: AsyncSession, identity: Identity, payload: LaunchProfileCreate) -> dict:
    await _require_update(db, identity, payload.department_id, "launch_profile.create")
    profile = await repo.create(db, {
        "id": launch_profile_id(),
        "department_id": payload.department_id,
        "name": payload.name,
        "is_default": payload.is_default,
        "created_by": identity.user_id,
    })
    if payload.is_default:
        await repo.clear_default(db, payload.department_id, except_id=profile.id)
    version = await _add_version(db, profile, payload.version, identity.user_id)
    await db.commit()
    await db.refresh(profile)
    audit_service.emit(
        "launch_profile.create", target_id=profile.id, target_type="launch_profile",
        status="success", allowed=True,
        details={"department_id": profile.department_id, "version_id": version.id, "is_default": profile.is_default},
    )
    return await to_response(db, profile)


async def update(db: AsyncSession, identity: Identity, profile_id: str, payload: LaunchProfileUpdate) -> dict:
    profile = await _get_visible(db, identity, profile_id)
    await _require_update(db, identity, profile.department_id, "launch_profile.update")
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"]:
        profile.name = changes["name"]
    if changes.get("is_default") is True:
        await repo.clear_default(db, profile.department_id, except_id=profile.id)
        profile.is_default = True
    elif changes.get("is_default") is False:
        profile.is_default = False
    await db.commit()
    await db.refresh(profile)
    audit_service.emit(
        "launch_profile.update", target_id=profile.id, target_type="launch_profile",
        status="success", allowed=True, details={"fields": sorted(changes)},
    )
    return await to_response(db, profile)


async def add_version(
    db: AsyncSession, identity: Identity, profile_id: str, payload: LaunchProfileVersionInput,
) -> LaunchProfileVersion:
    profile = await _get_visible(db, identity, profile_id)
    await _require_update(db, identity, profile.department_id, "launch_profile.version_created")
    version = await _add_version(db, profile, payload, identity.user_id)
    await db.commit()
    await db.refresh(version)
    audit_service.emit(
        "launch_profile.version_created", target_id=profile.id, target_type="launch_profile",
        status="success", allowed=True, details={"version_id": version.id, "version": version.version},
    )
    return version


# ── выбор версии для теста ───────────────────────────────────────────────────

async def effective_version(
    db: AsyncSession, test: TestDefinition, department_id: str | None,
) -> LaunchProfileVersion:
    """Действующая версия профиля для `test` на стенде отдела `department_id`."""
    profile = None
    if test.launch_profile_id:
        profile = await repo.get_by_id(db, test.launch_profile_id)
    if profile is None and department_id is not None:
        profile = await repo.get_default(db, department_id)
    if profile is None:
        profile = await repo.get_default(db, None)
    version = await repo.get_version(db, profile.current_version_id) if profile and profile.current_version_id else None
    if version is None:
        raise DomainValidationError(
            error_code="LAUNCH_PROFILE_NOT_CONFIGURED",
            message="No launch profile with a version is configured for this test/department",
            details={"test_id": test.id, "department_id": department_id},
        )
    return version


# ── сборка задания воркеру ────────────────────────────────────

def new_resolve_context(
    db: AsyncSession, test: TestDefinition, stand: TestStand, launch_context: dict, *, debug: bool,
    step=None, steps: list | None = None, step_index: int = 0,
) -> ResolveContext:
    """Контекст резолва задания воркеру — общий для claim, превью запуска
    и настройки стенда перед шагом.

    Отдел — стенда. Значения, которые знает только сервис
    (ветка и суффикс шага), кладутся поверх `launch_context`: постановщик
    задания их не подменит. Пути профиля, имена файлов и id item'а
    добавляет `render_paths`. `step`/`steps` — шаг многоступенчатого теста
    : суффикс берётся у шага, `STEP_INDEX`/`STEP_COUNT`/`STEP_NAME` —
    значения задания; без них — суффикс первого шага теста.
    """
    from src.services import queue_steps  # поздний импорт: queue_steps тянет очередь

    suffix = step.starter_suffix if step is not None else test.starter_suffix
    return ResolveContext(
        db=db,
        department_id=stand.department_id,
        test=test,
        stand=stand,
        launch_context={
            **launch_context,
            "TEST_BRANCH": test.category or "",
            "STARTER_SUFFIX": suffix or "",
        },
        debug=debug,
        step=step,
        locals=queue_steps.step_context(steps, step_index) if steps else {},
    )


async def render_shell(ctx: ResolveContext, text: str) -> Resolved:
    """Подставить `{{CODE}}` в shell-текст профиля."""
    parts: list[str] = []
    sensitive = False
    pos = 0
    for match in SCRIPT_PLACEHOLDER_RE.finditer(text):
        parts.append(text[pos:match.start()])
        value = await variable_resolver.resolve_code(ctx, match.group(1))
        parts.append(value.value)
        sensitive = sensitive or value.sensitive
        pos = match.end()
    parts.append(text[pos:])
    return Resolved("".join(parts), sensitive=sensitive)


def pgrep_pattern(path: str) -> str:
    """ERE для `pgrep -f`, совпадающий с `path`, но не с собственной строкой.

    Первый символ — в классе (`[/]home/u/starter\\.sh`): командная строка
    самой команды остановки содержит `[/]...`, а не `/...`, поэтому
    `pgrep -f` не находит ни её, ни `sudo`/`sh -c`, которые её запустили.
    """
    escaped = "".join(f"\\{ch}" if ch in _ERE_SPECIAL else ch for ch in path[1:])
    return f"[{path[0]}]{escaped}"


@dataclass
class RenderedPaths:
    script: str
    dates: str
    token: str
    testenv_marker: str
    command_file: str


async def render_paths(ctx: ResolveContext, version: LaunchProfileVersion, queue_item_id: str) -> RenderedPaths:
    """Пути профиля на стенде + locals/launch_context для остальных шаблонов.

    Кладёт в контекст: `QUEUE_ITEM_ID`, `STARTER_PATH`, `DATES_PATH`,
    `GIT_TOKEN_PATH`, `TESTENV_MARKER_PATH`, `COMMAND_FILE_PATH` (locals) и
    `DATES_FILE`/`GIT_TOKEN_FILE` — имена файлов, аргументы `starter.sh`
    (`$3`/`$2` легаси).
    """
    ctx.locals["QUEUE_ITEM_ID"] = queue_item_id
    rendered: dict[str, str] = {}
    for key, template in (version.paths or {}).items():
        value = (await variable_resolver.render(ctx, str(template))).value
        if not _SAFE_PATH_RE.fullmatch(value):
            raise DomainValidationError(
                error_code="LAUNCH_PROFILE_PATH_INVALID",
                message=f"Launch profile path '{key}' rendered to an unsafe or relative path: {value!r}",
                details={"key": key},
            )
        rendered[key] = value
    missing = [k for k in ("script", "dates", "token", "testenv_marker", "command_file") if k not in rendered]
    if missing:
        raise DomainValidationError(
            error_code="LAUNCH_PROFILE_PATH_INVALID",
            message=f"Launch profile is missing paths: {', '.join(missing)}",
            details={"missing": missing},
        )
    paths = RenderedPaths(**{k: rendered[k] for k in RenderedPaths.__dataclass_fields__})
    ctx.locals.update({
        "STARTER_PATH": paths.script,
        "DATES_PATH": paths.dates,
        "GIT_TOKEN_PATH": paths.token,
        "TESTENV_MARKER_PATH": paths.testenv_marker,
        "COMMAND_FILE_PATH": paths.command_file,
    })
    ctx.launch_context["DATES_FILE"] = posixpath.basename(paths.dates)
    ctx.launch_context["GIT_TOKEN_FILE"] = posixpath.basename(paths.token)
    return paths


def clone_args(clone: dict) -> str:
    """Аргументы `git clone` из настроек профиля; `$1` — ветка теста."""
    args = ['--branch "$1"']
    if clone.get("mode", "branch") == "branch":
        args.append("--single-branch")
    elif clone.get("depth"):
        args.append("--no-single-branch")
    if clone.get("depth"):
        args.append(f"--depth {int(clone['depth'])}")
    return " ".join(args)


def _masked_tokens(content: str, masked: str) -> set[str]:
    """Значения токенов dates, которые в маскированной версии стали `***`.

    Маска заменяет токен целиком (`test_command_arg.resolve_dates`), так
    что токены обеих строк идут парами; не разобралось — пусто.
    """
    try:
        plain, hidden = shlex.split(content), shlex.split(masked)
    except ValueError:
        return set()
    if len(plain) != len(hidden):
        return set()
    return {p for p, h in zip(plain, hidden) if p != h and variable_resolver.MASK in h}


@dataclass
class BuiltLaunch:
    """Задание воркеру — всё, что зависит от профиля запуска (C3)."""

    files: list[dict]
    cleanup_globs: list[str]
    launch_command: str
    launch_command_masked: str
    stop_command: str
    use_pty: bool
    redact_values: list[str] = field(default_factory=list)
    testenv_value: str = ""


async def build_launch(
    ctx: ResolveContext,
    version: LaunchProfileVersion,
    paths: RenderedPaths,
    *,
    dates_content: str,
    dates_content_masked: str,
    git_token: str,
    testenv_on: bool,
    prepare_only: bool,
    rerun: bool = False,
) -> BuiltLaunch:
    """Задание воркеру по версии профиля.

    `rerun` — шаг многоступенчатого теста с `run_mode=rerun`: по пути
    `starter.sh` кладётся `rerun_script` профиля (код уже склонирован
    первым шагом), git-токен не нужен и на стенд не пишется; команды
    запуска и остановки — те же.
    """
    if rerun and not (version.rerun_script or "").strip():
        raise DomainValidationError(
            error_code="LAUNCH_PROFILE_RERUN_NOT_CONFIGURED",
            message="The launch profile has no rerun script for run_mode=rerun steps",
            details={"launch_profile_version_id": version.id},
        )
    clone = version.clone or {}
    ctx.locals.update({
        # Строка dates целиком — для скриптов, которые берут
        # аргументы строкой, а не файлом (`ipa_run.py {dates}`); уже
        # экранирована по `dates_quoting` теста.
        "DATES_INLINE": dates_content,
        "GIT_REPO_URL": str(clone.get("repo_url") or ""),
        "GIT_CLONE_ARGS": clone_args(clone),
        "STARTER_PGREP_PATTERN": pgrep_pattern(paths.script),
        "STOP_GRACE_SECONDS": str(int(version.stop_grace_seconds)),
    })
    script = await render_shell(ctx, version.rerun_script if rerun else version.starter_script)
    dates_secret = dates_content != dates_content_masked
    if dates_secret and dates_content and dates_content in script.value:
        script = Resolved(script.value, sensitive=True)
    args = await variable_resolver.render_args(ctx, version.launch_command_template)
    launch_command = shlex.join(a.value for a in args)
    launch_command_masked = shlex.join(a.masked for a in args)
    stop_command = (await render_shell(ctx, version.stop_command_template)).value

    testenv = version.testenv or {}
    testenv_value = str(testenv.get("on_value", "on") if testenv_on else testenv.get("off_value", "off"))
    files = [
        {"path": paths.script, "content": script.value, "mode": "0755", "sensitive": script.sensitive},
        *([] if rerun else [{"path": paths.token, "content": git_token, "mode": "0600", "sensitive": True}]),
        {
            "path": paths.dates, "content": dates_content, "mode": "0644",
            "sensitive": dates_content != dates_content_masked,
        },
        {"path": paths.testenv_marker, "content": testenv_value, "mode": "0644", "sensitive": False},
    ]
    for extra in version.extra_files or []:
        path = (await variable_resolver.render(ctx, str(extra.get("path") or ""))).value
        if not _SAFE_PATH_RE.fullmatch(path):
            raise DomainValidationError(
                error_code="LAUNCH_PROFILE_PATH_INVALID",
                message=f"Launch profile extra file rendered to an unsafe or relative path: {path!r}",
                details={"key": "extra_files"},
            )
        content = await render_shell(ctx, str(extra.get("content") or ""))
        files.append({
            "path": path, "content": content.value, "mode": str(extra.get("mode") or "0644"),
            "sensitive": bool(extra.get("sensitive")) or content.sensitive
            or (dates_secret and bool(dates_content) and dates_content in content.value),
        })
    if prepare_only:
        # testenv-режим: тест не запускается — оставляем на стенде команду,
        # которой он был бы запущен (секретов в ней нет: токен и dates — файлы).
        files.append({
            "path": paths.command_file, "content": launch_command_masked + "\n",
            "mode": "0644", "sensitive": False,
        })
    cleanup_globs: list[str] = []
    if testenv.get("cleanup_other"):
        cleanup_globs.append(posixpath.join(posixpath.dirname(paths.testenv_marker), "testenv_*.conf"))

    secrets = {git_token} if git_token else set()
    secrets.update(v.value for v in ctx.resolved_values().values() if v.sensitive and v.value)
    secrets.update(_masked_tokens(dates_content, dates_content_masked))
    # Слишком короткие значения вырезать нельзя — испортят весь лог.
    secrets = {v for v in secrets if len(v) >= 4}
    return BuiltLaunch(
        files=files,
        cleanup_globs=cleanup_globs,
        launch_command=launch_command,
        launch_command_masked=launch_command_masked,
        stop_command=stop_command,
        use_pty=bool(version.use_pty),
        redact_values=sorted(secrets, key=len, reverse=True),
        testenv_value=testenv_value,
    )
