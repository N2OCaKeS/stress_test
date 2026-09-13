"""Генерация СТП-прогонов (§1, §5, §6.1 плана миграции).

`generate_stp_runs()`:

1. Берёт все тесты отдела, закреплённые за каким-либо стендом
   (`test_definitions.pinned_stand_id`).
2. Прогоняет их через changelog-фильтр (`_filter_by_changelog`, §1/§7): на
   `final=true` или `rc` вида `X.Y.Z.1` без `UU` — фильтр не применяется
   (полный набор); иначе тест проходит, если его `changelog_component` пуст
   (безопасный дефолт) или входит в список изменившихся компонентов из
   changelog-сервиса. Недоступность changelog-сервиса — тоже безопасный
   дефолт (полный набор, см. `changelog_service.fetch_changed_components`).
3. Группирует отфильтрованные тесты по `pinned_stand_id` — тот же принцип,
   что и легаси-группировка топиков по `(mode, stand)`, но не хардкодом, а
   реальной моделью.
4. На каждый стенд: сопоставляет тесты с `stp_test_cases` по `code`,
   отбрасывая тесты без карточки СТП или без `zephyr_id` (нечего создавать в
   Zephyr), резолвит Jira-креды отдела и создаёт один Zephyr test-run на
   стенд со всеми его тест-кейсами.

Провал одного стенда (нет интеграционных настроек, reveal не прошёл, Zephyr
недоступен) не должен рушить остальные — собирается в `errors`, тем же
принципом, что и `services/test_run.py` (частичные ошибки постановки в
очередь).
"""

from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType, StpCellStatus
from src.core.exceptions import AppException, AuthorizationError, NotFoundError
from src.dependencies.auth import Identity
from src.models import StpTestRun, TestDefinition
from src.repositories import department_integration_settings as dis_repo
from src.repositories import stp_cell as stp_cell_repo
from src.repositories import stp_test_case as stp_test_case_repo
from src.repositories import stp_test_run as stp_test_run_repo
from src.repositories import test_definition as test_definition_repo
from src.services import audit_service, changelog_service, permissions, secret_client, zephyr_client
from src.services.zephyr_client import ZephyrRunItem
from src.utils.ids import stp_cell_id as new_cell_id
from src.utils.ids import stp_test_run_id as new_run_id

logger = logging.getLogger(__name__)


def _is_full_scope(rc: str, final: bool) -> bool:
    """§1: `final=true`, или `rc` заканчивается на `.1` и не содержит `UU` — без фильтра."""
    if final:
        return True
    return rc.endswith(".1") and "UU" not in rc


async def _filter_by_changelog(
    db: AsyncSession, tests: list[TestDefinition], rc: str, final: bool,
) -> list[TestDefinition]:
    if _is_full_scope(rc, final):
        return tests
    changed = await changelog_service.fetch_changed_components(db, rc)
    if changed is None:
        # changelog-сервис недоступен/не настроен — безопасный дефолт: полный набор.
        return tests
    changed_set = set(changed)
    return [t for t in tests if not t.changelog_component or t.changelog_component in changed_set]


def _derive_release(rc: str) -> str:
    """Первые два сегмента RC (`1.8.5.46` → `1.8`) — родительская папка Zephyr.

    Легаси хранит `release`/`rc` как отдельные параметры (`allta_back.py`), в
    testing_service своего отдельного понятия "release" нет — RC и
    `os_version_id` уже отождествлены (см. `services/test_run.py`), поэтому
    "release" здесь — лучшее доступное приближение, не отдельное поле БД.
    """
    parts = rc.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else rc


async def _resolve_jira_bearer(db: AsyncSession, department_id: str) -> tuple[str, str] | None:
    """`(jira_base_url, bearer_token)` для отдела, либо `None` — не настроено/недоступно.

    `None` — вызывающий код обязан трактовать это как частичный провал этого
    стенда/отдела, не как исключение, рушащее весь `/stp/generate`. Помимо
    отсутствующих `department_integration_settings`, сюда же попадает и 403 от
    `secret_client.reveal_credential` — легитимный исход, если department_admin
    отдела ещё не завёл интеграционную credential со scope=service (см. её
    docstring).
    """
    settings = await dis_repo.get_by_department(db, department_id)
    if settings is None or not settings.credential_id or not settings.jira_base_url:
        return None
    try:
        _login, secret = await secret_client.reveal_credential(settings.credential_id)
    except AppException as exc:
        logger.warning(
            "stp.generate: reveal_credential failed for dept=%s cred=%s: %s",
            department_id, settings.credential_id, exc.message,
        )
        return None
    if not secret:
        return None
    return settings.jira_base_url, secret


async def generate_stp_runs(
    db: AsyncSession,
    identity: Identity,
    *,
    os_version_id: str,
    mode: str | None,
    kernel: str | None,
    final: bool,
    department_id: str,
) -> tuple[list[StpTestRun], list[dict]]:
    """Сгенерировать СТП-прогоны для всех подходящих (стенд, набор тест-кейсов) пар отдела.

    Возвращает `(created_runs, errors)` — `errors` это список
    `{"stand_id", "error_code", "message"}`, стенды без единого пригодного
    тест-кейса молча пропускаются (не ошибка — например, тесты есть, но ни
    один ещё не заведён в каталоге СТП).
    """
    try:
        await permissions.require_action(db, identity, EntityType.STP_TEST_RUN, Action.CREATE)
    except AuthorizationError:
        audit_service.emit(
            "stp_test_run.generate",
            target_type="stp_test_run",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise

    if kernel is None or mode is None:
        from src.services import server_client
        kernels = [kernel] if kernel else await server_client.resolve_os_kernels(os_version_id)
        modes = [mode] if mode else ["orel", "smolensk"]
        all_runs, all_errors = [], []
        for selected_kernel in kernels:
            for selected_mode in modes:
                runs, errors = await generate_stp_runs(db, identity, os_version_id=os_version_id,
                    kernel=selected_kernel, mode=selected_mode, final=final, department_id=department_id)
                all_runs.extend(runs)
                all_errors.extend(errors)
        return all_runs, all_errors

    tests = await test_definition_repo.list_by_department_pinned(db, department_id)
    filtered = await _filter_by_changelog(db, tests, os_version_id, final)

    by_stand: dict[str, list[TestDefinition]] = defaultdict(list)
    for test in filtered:
        by_stand[test.pinned_stand_id].append(test)

    release = _derive_release(os_version_id)
    created: list[StpTestRun] = []
    errors: list[dict] = []

    for stand_id, stand_tests in by_stand.items():
        codes = [t.code for t in stand_tests]
        cases_by_code = {c.code: c for c in await stp_test_case_repo.list_by_codes(db, codes)}
        pairs = [
            (cases_by_code[t.code], t) for t in stand_tests
            if t.code in cases_by_code and cases_by_code[t.code].zephyr_id
        ]
        if not pairs:
            logger.info(
                "stp.generate: stand %s has no zephyr-linked stp_test_cases, skipping", stand_id,
            )
            continue

        jira_ctx = await _resolve_jira_bearer(db, department_id)
        if jira_ctx is None:
            errors.append({
                "stand_id": stand_id,
                "error_code": "JIRA_INTEGRATION_NOT_AVAILABLE",
                "message": "department_integration_settings not configured or credential reveal failed",
            })
            continue
        base_url, bearer_token = jira_ctx

        items = []
        for case, test in pairs:
            assignee_key = None
            if test.owner:
                assignee_key = await zephyr_client.resolve_user_key(
                    base_url=base_url, bearer_token=bearer_token, username=test.owner,
                )
            items.append(ZephyrRunItem(
                test_case_key=case.zephyr_id, environment=kernel, assigned_to_key=assignee_key,
            ))

        folder = f"/stress_test/{release}/{os_version_id}"
        name = f"{os_version_id}_{mode}_{kernel}_{stand_id}"
        try:
            zephyr_test_run_key = await zephyr_client.create_test_run(
                base_url=base_url, bearer_token=bearer_token, folder=folder, name=name, items=items,
            )
        except AppException as exc:
            errors.append({
                "stand_id": stand_id, "error_code": exc.error_code, "message": exc.message,
            })
            continue

        run = await stp_test_run_repo.create(db, {
            "id": new_run_id(),
            "os_version_id": os_version_id,
            "mode": mode,
            "kernel": kernel,
            "stand_id": stand_id,
            "zephyr_test_run_key": zephyr_test_run_key,
            "zephyr_folder_path": folder,
        })
        for case, _test in pairs:
            await stp_cell_repo.create(db, {
                "id": new_cell_id(),
                "stp_test_case_id": case.id,
                "stp_test_run_id": run.id,
                "status": StpCellStatus.NOT_RUN,
            })
        await db.commit()
        await db.refresh(run)
        created.append(run)

    audit_service.emit(
        "stp_test_run.generate",
        target_type="stp_test_run",
        status="success", allowed=True,
        details={
            "department_id": department_id,
            "os_version_id": os_version_id,
            "mode": mode,
            "kernel": kernel,
            "final": final,
            "created_count": len(created),
            "error_count": len(errors),
        },
    )
    return created, errors


async def list_stp_test_runs(
    db: AsyncSession, limit: int, offset: int, *,
    stand_id: str | None = None, os_version_id: str | None = None,
) -> tuple[list[StpTestRun], int]:
    items = await stp_test_run_repo.list_all(
        db, limit=limit, offset=offset, stand_id=stand_id, os_version_id=os_version_id,
    )
    total = await stp_test_run_repo.count_all(db, stand_id=stand_id, os_version_id=os_version_id)
    return items, total


async def get_stp_test_run(db: AsyncSession, run_id: str):
    run = await stp_test_run_repo.get_by_id(db, run_id)
    if run is None:
        raise NotFoundError(error_code="STP_TEST_RUN_NOT_FOUND", message="Stp test run not found")
    cells = await stp_cell_repo.list_by_run(db, run_id)
    return run, cells
