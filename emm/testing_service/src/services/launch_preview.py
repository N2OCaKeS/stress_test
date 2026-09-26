"""Превью запуска теста: что уйдёт воркеру.

Собирает задание теми же функциями, что `queue.claim_next`, в том же
порядке и в одном контексте резолва: профиль запуска
(`launch_profile.effective_version`/`render_paths`), `dates.conf`
(`test_command_arg.resolve_dates`), git-токен (`queue.resolve_git_token`),
файлы и команды (`launch_profile.build_launch`). Своей логики сборки здесь
нет — только вызовы и маскировка результата, поэтому превью не может
разойтись с настоящим заданием.

Отличия от claim:

* item'а нет — `QUEUE_ITEM_ID` в путях профиля заменяет `PREVIEW_QUEUE_ITEM_ID`;
* ни одной записи в БД и ни одного вызова, меняющего состояние (бронь,
  `service-status`, аудит claim'а);
* этап, который не удался, не обрывает превью: ошибка уходит в `errors`,
  остальное собирается дальше (без `dates.conf` всё равно видны скрипт и
  команды);
* секреты в ответе замаскированы, открытых значений ответ не несёт.
"""

from __future__ import annotations

from dataclasses import fields

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import CommandArgKind
from src.core.exceptions import AppException
from src.dependencies.auth import Identity
from src.repositories import launch_profile as launch_profile_repo
from src.repositories import test_command_arg as command_arg_repo
from src.schemas.launch_preview import LaunchPreviewRequest
from src.services import audit_service
from src.services import launch_profile as launch_profile_svc
from src.services import provisioning_profile as provisioning_svc
from src.services import queue as queue_svc
from src.services import queue_steps
from src.services import test_definition as test_definition_svc
from src.services import test_stand as test_stand_svc
from src.services import variable_resolver
from src.services.test_command_arg import resolve_dates
from src.services.variable_resolver import MASK, Resolved

# Подставляется в пути профиля вместо id item'а (`{QUEUE_ITEM_ID}`): проходит
# проверку безопасного пути и сразу видно, что это превью.
PREVIEW_QUEUE_ITEM_ID = "qi_preview"

# Источник значений, которые знает только задание (пути профиля, id item'а,
# аргументы clone) — `ResolveContext.locals`.
CLAIM_SOURCE = "claim"
# Значение слота команды из его `override_value` (шаблон на тесте, D5).
OVERRIDE_SOURCE = "override"


def _error(stage: str, exc: AppException) -> dict:
    return {
        "stage": stage, "error_code": exc.error_code, "message": exc.message,
        "details": dict(exc.details or {}),
    }


def _mask(text: str, secrets: list[str]) -> str:
    """Вырезать секреты так же, как воркер вырезает их из лога (`redact_values`)."""
    for secret in secrets:
        text = text.replace(secret, MASK)
    return text


def _row(code: str, label: str | None, source: str, value: Resolved, position: int | None = None) -> dict:
    return {
        "code": code, "label": label, "source": source, "value": value.masked,
        "sensitive": value.sensitive, "slot_position": position,
    }


async def _slot_overrides(ctx, test_id: str, step_id: str | None) -> list[dict]:
    """Слоты команды с `override_value`: их значение — шаблон слота, а не переменная.

    Всё уже в кеше контекста (`resolve_dates` прошёл по тем же слотам),
    повторных reveal-ов нет. Слот, который не зарезолвился, уже в `errors`.
    """
    catalog = {v.id: v for v in (await ctx.catalog()).values()}
    rows = []
    slots = (
        await command_arg_repo.list_by_step(ctx.db, step_id) if step_id
        else await command_arg_repo.list_by_test(ctx.db, test_id)
    )
    for slot in slots:
        variable = catalog.get(slot.variable_id) if slot.kind == CommandArgKind.VARIABLE else None
        if variable is None or slot.override_value is None:
            continue
        try:
            value = await variable_resolver.resolve_slot_value(ctx, variable, slot.override_value)
        except AppException:
            continue
        rows.append(_row(variable.code, variable.label, OVERRIDE_SOURCE, value, slot.position))
    return rows


async def _variables(ctx, test_id: str, step_id: str | None = None) -> list[dict]:
    """Таблица «код → значение → источник» по тому, что задание реально резолвило."""
    catalog = await ctx.catalog()
    # Значения задания перекрывают каталог (`resolve_code` смотрит их первыми).
    rows = {code: _row(code, None, CLAIM_SOURCE, Resolved(value)) for code, value in ctx.locals.items()}
    for code, value in ctx.resolved_values().items():
        variable = catalog.get(code)
        rows.setdefault(code, _row(
            code, variable.label if variable else None, variable.source if variable else CLAIM_SOURCE, value,
        ))
    result = list(rows.values()) + await _slot_overrides(ctx, test_id, step_id)
    return sorted(result, key=lambda row: (row["code"], row["slot_position"] is not None, row["slot_position"] or 0))


def _files(launch: launch_profile_svc.BuiltLaunch, paths, *, dates_masked: str | None,
           dates_failed: bool, token_failed: bool) -> list[dict]:
    """Файлы задания с маской. `role` — ключ пути в профиле (`script`, `dates`, …)."""
    role_by_path = {getattr(paths, f.name): f.name for f in fields(paths)}
    result = []
    for item in launch.files:
        role = role_by_path.get(item["path"], "extra")
        if role == "dates":
            content = None if dates_failed else dates_masked
        elif role == "token" and token_failed:
            content = None
        else:
            content = _mask(item["content"], launch.redact_values)
            if item["sensitive"] and content == item["content"]:
                # Секрет короче, чем режет лог воркера (< 4 символов), или
                # файл целиком — секрет: показывать нечего.
                content = MASK
        result.append({
            "role": role, "path": item["path"], "mode": item["mode"],
            "sensitive": item["sensitive"], "content": content,
        })
    return result


async def preview(db: AsyncSession, identity: Identity, test_id: str, payload: LaunchPreviewRequest) -> dict:
    """Собрать задание воркеру для теста на стенде без постановки в очередь.

    Права — как на чтение теста и стенда: свой отдел (или платформенный
    тест), тест чужого отдела на стенд другого отдела не собирается (тот же
    инвариант, что у постановки в очередь).
    """
    test = await test_definition_svc.get_test_definition(db, identity, test_id)
    stand = await test_stand_svc.get_stand_or_404(db, payload.stand_id, identity)
    queue_svc.check_test_stand_department_match(test, stand)

    launch_context = {
        "RC": payload.os_version_id,
        "KERNEL": payload.kernel,
        "MODE": str(payload.mode or test.mode),
    }
    # Многоступенчатый тест: задание — для выбранного шага, как у
    # claim'а item'а на этом шаге (`queue_steps.current_step`).
    steps = await queue_steps.load_steps(db, test.id)
    step_index = min(payload.step_index, len(steps) - 1)
    step = steps[step_index]
    rerun = queue_steps.is_rerun(step) and not payload.testenv
    ctx = launch_profile_svc.new_resolve_context(
        db, test, stand, launch_context, debug=payload.debug, step=step, steps=steps, step_index=step_index,
    )
    result: dict = {
        "test_id": test.id, "stand_id": stand.id, "launch_context": launch_context,
        "debug": payload.debug, "testenv": payload.testenv, "errors": [],
        "step": {"index": step_index, "count": len(steps), "name": step.name or "", "run_mode": step.run_mode},
    }
    errors: list[dict] = result["errors"]

    version = paths = None
    try:
        version = await launch_profile_svc.effective_version(db, test, stand.department_id)
        profile = await launch_profile_repo.get_by_id(db, version.profile_id)
        result["launch_profile"] = {
            "profile_id": version.profile_id, "name": profile.name if profile else None,
            "version_id": version.id, "version": version.version,
        }
        paths = await launch_profile_svc.render_paths(ctx, version, PREVIEW_QUEUE_ITEM_ID)
    except AppException as exc:
        errors.append(_error("launch_profile" if version is None else "paths", exc))

    dates_content = dates_masked = None
    try:
        dates_content, dates_masked = await resolve_dates(
            db, test.id, launch_context, stand=stand, debug=payload.debug, context=ctx,
            step_id=step.id or None,
        )
        result["dates_content_masked"] = dates_masked
    except AppException as exc:
        errors.append(_error("dates", exc))

    git_token = None
    try:
        # Повторный запуск (`rerun`) не клонирует — токен ему не нужен.
        git_token = "" if rerun else await queue_svc.resolve_git_token(db, stand.department_id)
    except AppException as exc:
        errors.append(_error("git_token", exc))

    if version is not None and paths is not None:
        try:
            launch = await launch_profile_svc.build_launch(
                ctx, version, paths,
                dates_content=dates_content or "", dates_content_masked=dates_masked or "",
                git_token=git_token or "",
                # Одиночный ручной запуск (не прогон РЦ и не retry) — testenv
                # включается ровно так же, как в `claim_next`.
                testenv_on=payload.testenv, prepare_only=payload.testenv, rerun=rerun,
            )
        except AppException as exc:
            errors.append(_error("launch", exc))
        else:
            result.update({
                "files": _files(
                    launch, paths, dates_masked=dates_masked,
                    dates_failed=dates_content is None, token_failed=git_token is None,
                ),
                "launch_command_masked": launch.launch_command_masked,
                "stop_command": _mask(launch.stop_command, launch.redact_values),
                "use_pty": launch.use_pty,
                "cleanup_globs": launch.cleanup_globs,
            })
    # шаг настройки стенда — тот же резолв, что при постановке в
    # очередь (`provisioning_profile.resolve_stand_setup`), в этом же `ctx`;
    # секреты в скрипте — `***`.
    # Шаг 0 — внутри prepare-for-test, остальные — «настройка без restore»
    # перед шагом; форма одна.
    if not provisioning_svc.stand_setup_is_empty(step.stand_setup):
        try:
            setup = await provisioning_svc.resolve_stand_setup(ctx, step.stand_setup)
        except AppException as exc:
            errors.append(_error("stand_setup", exc))
        else:
            secrets = sorted(
                {v.value for v in ctx._values.values() if v.sensitive and v.value}, key=len, reverse=True,
            )
            setup["script"] = _mask(setup["script"], secrets)
            result["stand_setup"] = setup
    result["provisioning"] = await provisioning_svc.effective_values(db, test, stand.department_id)

    if "DATES_INLINE" in ctx.locals:
        # Строка dates в таблице переменных — с маской, как файл dates.conf.
        ctx.locals["DATES_INLINE"] = dates_masked or ""
    result["variables"] = await _variables(ctx, test.id, step.id or None)
    audit_service.emit(
        "test_definition.launch_preview",
        target_id=test.id, target_type="test_definition",
        status="success", allowed=True,
        details={
            "stand_id": stand.id, "os_version_id": payload.os_version_id, "kernel": payload.kernel,
            "mode": launch_context["MODE"], "debug": payload.debug, "testenv": payload.testenv,
            "step_index": step_index,
            "errors": [e["error_code"] for e in errors],
        },
    )
    return result
