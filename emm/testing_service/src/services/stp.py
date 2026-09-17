"""Генерация/переключение состава СТП-прогонов (§1, §5, §6.1, §D4/D5 плана миграции).

`generate_stp_runs()` управляет составом Zephyr test-run'ов отдела для одной
РЦ:

1. Берёт все тесты отдела, закреплённые за каким-либо стендом
   (`test_definitions.pinned_stand_id`).
2. Вычисляет целевой набор по явному `scope` (`StpCompositionScope`,
   `changelog`/`full`) — параметр, который задаёт вызывающий (кнопки «По
   changelog»/«Полный набор», §D5), НЕ угадывается по виду строки версии.
   `changelog` фильтрует через `_filter_by_changelog` (§1/§7): тест проходит,
   если его `changelog_component` пуст (безопасный дефолт) или входит в
   список изменившихся компонентов из changelog-сервиса; недоступность
   changelog-сервиса — тоже безопасный дефолт (полный набор).
3. Группирует тесты по `pinned_stand_id`, резолвит Jira-креды отдела.
4. На каждый стенд — либо заводит новый Zephyr test-run (первый вызов для
   этой пары стенд/РЦ/режим/ядро), либо РЕКОНЦИЛИРУЕТ уже существующий:
   тесты, вновь попавшие в объём и не имевшие ячейки — создаются локально и
   добавляются тест-кейсом в СУЩЕСТВУЮЩИЙ Zephyr-ран (`zephyr_client.
   add_test_cases_to_run`, НЕ новый ран); тесты, ранее исключённые и
   возвращающиеся — просто `is_active=True` без потери статуса; тесты,
   выпадающие из объёма — `is_active=False`, ячейка и её история остаются.
   Повтор с тем же `scope` — идемпотентный no-op относительно Zephyr и
   локальных данных (полезен, чтобы досоздать прогоны для новых стендов).
5. `stp_compositions` (одна строка на `(department_id, os_version_id)`)
   несёт текущий `scope` + `revision`; `revision` растёт только когда
   `scope` РЕАЛЬНО меняется (changelog↔full), не на каждый вызов.

Провал одного стенда (нет интеграционных настроек, reveal не прошёл, Zephyr
недоступен) не должен рушить остальные — собирается в `errors`, тем же
принципом, что и `services/test_run.py` (частичные ошибки постановки в
очередь).
"""

from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType, StpCellStatus, StpCompositionScope, TestReadiness
from src.core.exceptions import AppException, AuthorizationError, DomainValidationError, NotFoundError
from src.dependencies.auth import Identity
from src.models import StpComposition, StpTestRun, TestDefinition
from src.repositories import department_integration_settings as dis_repo
from src.repositories import stp_cell as stp_cell_repo
from src.repositories import stp_composition as stp_composition_repo
from src.repositories import stp_test_case as stp_test_case_repo
from src.repositories import stp_test_run as stp_test_run_repo
from src.repositories import test_definition as test_definition_repo
from src.repositories import test_stand as test_stand_repo
from src.services import audit_service, changelog_service, permissions, secret_client, server_client, zephyr_client
from src.services.zephyr_client import ZephyrRunItem
from src.utils.ids import stp_cell_id as new_cell_id
from src.utils.ids import stp_composition_id as new_composition_id
from src.utils.ids import stp_test_run_id as new_run_id

logger = logging.getLogger(__name__)


def _is_full_scope(scope: str) -> bool:
    """`scope == "full"` — явный параметр (§D4), не выводится из RC."""
    return scope == StpCompositionScope.FULL


def _validate_scope(scope: str) -> None:
    if scope not in set(StpCompositionScope):
        raise DomainValidationError(
            error_code="STP_COMPOSITION_SCOPE_INVALID",
            message="scope must be one of: " + ", ".join(sorted(StpCompositionScope)),
        )


async def _filter_by_changelog(
    db: AsyncSession, tests: list[TestDefinition], rc: str, scope: str,
) -> list[TestDefinition]:
    if _is_full_scope(scope):
        return tests
    changed = await changelog_service.fetch_changed_components(db, rc)
    if changed is None:
        # changelog-сервис недоступен/не настроен — безопасный дефолт: полный набор.
        return tests
    changed_set = set(changed)
    return [t for t in tests if not t.changelog_component or t.changelog_component in changed_set]


def _derive_release(rc: str, *, is_urgent_update: bool = False) -> str:
    """Родительская папка Zephyr по номеру РЦ: `1.8.5.46` → `1.8.5`.

    Правило легаси (`liballta.py::TestrunManager.create_test_run`): обычный
    релиз из 4 сегментов → первые ТРИ сегмента, хотфикс из 6 сегментов с
    маркером `UU` на четвёртом месте → первые ПЯТЬ (`1.7.3.UU.1.2` →
    `1.7.3.UU.1`). Дальше `zefir.py` складывает из этого
    `/stress_test/{release}/{rc}`, поэтому глубина здесь определяет, в какую
    ветку дерева папок Jira садится ран — легаси-раны того же РЦ лежат именно
    там.

    `is_urgent_update` — структурный флаг `os_versions.is_urgent_update`
    (сервер знает, хотфикс это или нет, а не только по виду строки — легаси
    хранил только строку и парсинг был единственным сигналом; emm флаг есть,
    используем его как ворота: без него 6-сегментная строка, случайно не
    являющаяся хотфиксом, не свернёт в укороченный формат). Сравнение с `UU`
    регистронезависимое — владелец может ввести версию как `uu`.

    Формат, не подпадающий ни под один случай (в легаси такого не было, там
    `release` просто оставался неинициализированным), сводим к первым трём
    сегментам — тот же консервативный дефолт, что у
    `run_summary.render_titles`.
    """
    parts = rc.split(".")
    if is_urgent_update and len(parts) == 6 and parts[3].upper() == "UU":
        return ".".join(parts[:5])
    return ".".join(parts[:3]) if len(parts) >= 3 else rc


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


async def _resolve_composition(
    db: AsyncSession, *, department_id: str, os_version_id: str, scope: str, identity: Identity,
) -> StpComposition:
    """Upsert `stp_compositions` — revision растёт только при реальной смене `scope`."""
    existing = await stp_composition_repo.get_by_department_and_os_version(db, department_id, os_version_id)
    if existing is None:
        composition = await stp_composition_repo.create(db, {
            "id": new_composition_id(),
            "department_id": department_id,
            "os_version_id": os_version_id,
            "scope": scope,
            "revision": 1,
            "updated_by": identity.user_id,
        })
    elif existing.scope != scope:
        composition = await stp_composition_repo.update(db, existing, {
            "scope": scope, "revision": existing.revision + 1, "updated_by": identity.user_id,
        })
    else:
        composition = existing
    await db.commit()
    await db.refresh(composition)
    return composition


async def get_stp_composition_effective(
    db: AsyncSession, identity: Identity, department_id: str, os_version_id: str,
) -> dict:
    """Эффективный состав пары `(department, РЦ)` — пустой дефолт, если строки ещё нет
    (состав ни разу не генерировался), тот же паттерн, что и
    `department_integration_settings.get_effective`."""
    permissions.require_own_department(identity, department_id)
    row = await stp_composition_repo.get_by_department_and_os_version(db, department_id, os_version_id)
    if row is None:
        return {
            "id": None, "department_id": department_id, "os_version_id": os_version_id,
            "scope": None, "revision": 0, "updated_at": None, "updated_by": None,
        }
    return {
        "id": row.id, "department_id": row.department_id, "os_version_id": row.os_version_id,
        "scope": row.scope, "revision": row.revision,
        "updated_at": row.updated_at, "updated_by": row.updated_by,
    }


async def _create_stand_run(
    db: AsyncSession, *,
    department_id: str, os_version_id: str, rc_number: str, mode: str, kernel: str, stand_id: str,
    release: str, stand_token: str,
    pairs: list[tuple],
    target_codes: set[str],
) -> tuple[StpTestRun | None, dict | None]:
    """Первый вызов для этой пары `(stand, os_version, mode, kernel)` — заводит Zephyr
    test-run + локальный прогон/ячейки для тестов, попавших в `target_codes`."""
    target_pairs = [(case, t) for case, t in pairs if t.code in target_codes]
    if not target_pairs:
        return None, None

    jira_ctx = await _resolve_jira_bearer(db, department_id)
    if jira_ctx is None:
        return None, {
            "stand_id": stand_id,
            "error_code": "JIRA_INTEGRATION_NOT_AVAILABLE",
            "message": "department_integration_settings not configured or credential reveal failed",
        }
    base_url, bearer_token = jira_ctx

    items = []
    for case, test in target_pairs:
        assignee_key = None
        if test.owner:
            assignee_key = await zephyr_client.resolve_user_key(
                base_url=base_url, bearer_token=bearer_token, username=test.owner,
            )
        items.append(ZephyrRunItem(
            test_case_key=case.zephyr_id, environment=kernel, assigned_to_key=assignee_key,
        ))

    folder = f"/stress_test/{release}/{rc_number}"
    # Первый сегмент имени — человеческий номер РЦ (не внутренний
    # os_version_id), как у легаси; четвёртый — человеческое имя стенда
    # (`stand3`), по нему же `stp_pull_from_life` разбирает чужие раны обратно.
    name = f"{rc_number}_{mode}_{kernel}_{stand_token}"
    try:
        zephyr_test_run_key = await zephyr_client.create_test_run(
            base_url=base_url, bearer_token=bearer_token, folder=folder, name=name, items=items,
        )
    except AppException as exc:
        return None, {"stand_id": stand_id, "error_code": exc.error_code, "message": exc.message}

    run = await stp_test_run_repo.create(db, {
        "id": new_run_id(),
        "os_version_id": os_version_id,
        "mode": mode,
        "kernel": kernel,
        "stand_id": stand_id,
        "zephyr_test_run_key": zephyr_test_run_key,
        "zephyr_folder_path": folder,
    })
    for case, _test in target_pairs:
        await stp_cell_repo.create(db, {
            "id": new_cell_id(),
            "stp_test_case_id": case.id,
            "stp_test_run_id": run.id,
            "status": StpCellStatus.NOT_RUN,
            "is_active": True,
        })
    await db.commit()
    await db.refresh(run)
    return run, None


async def _reconcile_existing_run(
    db: AsyncSession, *,
    department_id: str, run: StpTestRun, pairs: list[tuple], target_codes: set[str],
) -> tuple[StpTestRun, dict | None]:
    """Повторный вызов для стенда, у которого уже есть Zephyr test-run: свести
    текущие ячейки к `target_codes` без создания нового рана.

    Активация/деактивация уже существующих ячеек — чисто локальные операции
    (не трогают Zephyr, не трогают `status`/`queue_item_id`/`updated_by`).
    Добавление НОВЫХ ячеек требует и локальной записи, и добавления
    тест-кейса в существующий Zephyr-ран — если это не удаётся, активация/
    деактивация уже применённых изменений не откатывается (тот же принцип
    частичных ошибок, что и у создания рана впервые).
    """
    existing_cells = await stp_cell_repo.list_by_run(db, run.id)
    cells_by_case_id = {c.stp_test_case_id: c for c in existing_cells}

    to_activate = []
    to_deactivate = []
    to_create: list[tuple] = []

    for case, test in pairs:
        in_target = test.code in target_codes
        cell = cells_by_case_id.get(case.id)
        if cell is None:
            if in_target:
                to_create.append((case, test))
        elif in_target and not cell.is_active:
            to_activate.append(cell)
        elif not in_target and cell.is_active:
            to_deactivate.append(cell)

    for cell in to_activate:
        await stp_cell_repo.update(db, cell, {"is_active": True})
    for cell in to_deactivate:
        await stp_cell_repo.update(db, cell, {"is_active": False})

    error: dict | None = None
    if to_create:
        if not run.zephyr_test_run_key:
            error = {
                "stand_id": run.stand_id,
                "error_code": "ZEPHYR_TEST_RUN_KEY_MISSING",
                "message": "stp_test_run has no zephyr_test_run_key, cannot add test cases",
            }
        else:
            jira_ctx = await _resolve_jira_bearer(db, department_id)
            if jira_ctx is None:
                error = {
                    "stand_id": run.stand_id,
                    "error_code": "JIRA_INTEGRATION_NOT_AVAILABLE",
                    "message": "department_integration_settings not configured or credential reveal failed",
                }
            else:
                base_url, bearer_token = jira_ctx
                items = []
                for case, test in to_create:
                    assignee_key = None
                    if test.owner:
                        assignee_key = await zephyr_client.resolve_user_key(
                            base_url=base_url, bearer_token=bearer_token, username=test.owner,
                        )
                    items.append(ZephyrRunItem(
                        test_case_key=case.zephyr_id, environment=run.kernel, assigned_to_key=assignee_key,
                    ))
                try:
                    await zephyr_client.add_test_cases_to_run(
                        base_url=base_url, bearer_token=bearer_token,
                        test_run_key=run.zephyr_test_run_key, items=items,
                    )
                except AppException as exc:
                    error = {"stand_id": run.stand_id, "error_code": exc.error_code, "message": exc.message}
                else:
                    for case, _test in to_create:
                        await stp_cell_repo.create(db, {
                            "id": new_cell_id(),
                            "stp_test_case_id": case.id,
                            "stp_test_run_id": run.id,
                            "status": StpCellStatus.NOT_RUN,
                            "is_active": True,
                        })

    await db.commit()
    await db.refresh(run)
    return run, error


async def _reconcile_stand_runs(
    db: AsyncSession, *, os_version_id: str, rc_number: str, is_urgent_update: bool, mode: str,
    kernel: str, scope: str, department_id: str,
) -> tuple[list[StpTestRun], list[dict]]:
    tests = await test_definition_repo.list_by_department_pinned(db, department_id)
    # Полный набор — это все тесты со статусом «Рабочий» и СВОИМ режимом,
    # совпадающим с режимом этого прогона, а не буквально весь каталог отдела:
    # «На проверке»/«Неисправен»/«В разработке» не входят в СТП, как и в
    # обычный (не debug) запуск, а тест чужого режима не может оказаться в
    # чужом стенд-прогоне (§ prepare-for-test, mode — фиксированное свойство
    # теста). `tests` ниже остаётся НЕотфильтрованным — по нему строятся
    # кандидаты (`by_stand`/`pairs`), чтобы уже заведённая ячейка теста,
    # ставшего нерабочим или сменившего режим, корректно деактивировалась
    # через обычный путь reconcile, а не просто пропадала из рассмотрения.
    ready_tests = [t for t in tests if t.readiness == TestReadiness.READY and t.mode == mode]
    target_tests = await _filter_by_changelog(db, ready_tests, os_version_id, scope)

    target_codes_by_stand: dict[str, set[str]] = defaultdict(set)
    for t in target_tests:
        target_codes_by_stand[t.pinned_stand_id].add(t.code)

    by_stand: dict[str, list[TestDefinition]] = defaultdict(list)
    for t in tests:
        by_stand[t.pinned_stand_id].append(t)

    release = _derive_release(rc_number, is_urgent_update=is_urgent_update)
    stand_tokens = {
        s.id: (s.legacy_token or s.id)
        for s in await test_stand_repo.list_by_ids(db, list(by_stand.keys()))
    }
    touched: list[StpTestRun] = []
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

        target_codes = target_codes_by_stand.get(stand_id, set())
        existing_run = await stp_test_run_repo.find_latest_for_context(
            db, stand_id=stand_id, os_version_id=os_version_id, mode=mode, kernel=kernel,
        )

        if existing_run is None:
            run, error = await _create_stand_run(
                db, department_id=department_id, os_version_id=os_version_id, rc_number=rc_number,
                mode=mode, kernel=kernel, stand_id=stand_id, release=release,
                stand_token=stand_tokens.get(stand_id, stand_id),
                pairs=pairs, target_codes=target_codes,
            )
            if error is not None:
                errors.append(error)
            if run is not None:
                touched.append(run)
            continue

        run, error = await _reconcile_existing_run(
            db, department_id=department_id, run=existing_run, pairs=pairs, target_codes=target_codes,
        )
        if error is not None:
            errors.append(error)
        touched.append(run)

    return touched, errors


async def generate_stp_runs(
    db: AsyncSession,
    identity: Identity,
    *,
    os_version_id: str,
    mode: str | None,
    kernel: str | None,
    scope: str,
    department_id: str,
) -> tuple[list[StpTestRun], list[dict]]:
    """Сгенерировать/переключить состав СТП-прогонов отдела для одной РЦ (§D4/D5).

    `scope` — явный `changelog`/`full`, задаётся вызывающим (кнопки UI), не
    выводится из RC. Возвращает `(test_runs, errors)` — `test_runs` несёт как
    вновь созданные, так и уже существующие (реконциленные) прогоны, `errors`
    — список `{"stand_id", "error_code", "message"}` частичных провалов;
    стенды без единого пригодного тест-кейса молча пропускаются.
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

    _validate_scope(scope)

    composition = await _resolve_composition(
        db, department_id=department_id, os_version_id=os_version_id, scope=scope, identity=identity,
    )

    if kernel is None or mode is None:
        kernels = [kernel] if kernel else await server_client.resolve_os_kernels(os_version_id)
        modes = [mode] if mode else ["orel", "smolensk"]
    else:
        kernels = [kernel]
        modes = [mode]

    # Резолвится один раз на вызов (не на каждую пару режим/ядро) — Jira видит
    # человеческую версию РЦ, не внутренний id каталога ОС.
    os_version_info = await server_client.resolve_os_version_info(os_version_id)
    rc_number = os_version_info.name

    all_runs: list[StpTestRun] = []
    all_errors: list[dict] = []
    for selected_kernel in kernels:
        for selected_mode in modes:
            runs, errors = await _reconcile_stand_runs(
                db, os_version_id=os_version_id, rc_number=rc_number,
                is_urgent_update=os_version_info.is_urgent_update, mode=selected_mode,
                kernel=selected_kernel, scope=scope, department_id=department_id,
            )
            all_runs.extend(runs)
            all_errors.extend(errors)

    audit_service.emit(
        "stp_test_run.generate",
        target_type="stp_test_run",
        status="success", allowed=True,
        details={
            "department_id": department_id,
            "os_version_id": os_version_id,
            "mode": mode,
            "kernel": kernel,
            "scope": scope,
            "composition_revision": composition.revision,
            "touched_run_count": len(all_runs),
            "error_count": len(all_errors),
        },
    )
    return all_runs, all_errors


async def list_stp_test_runs(
    db: AsyncSession, identity: Identity, limit: int, offset: int, *,
    stand_id: str | None = None, os_version_id: str | None = None,
) -> tuple[list[StpTestRun], int]:
    """Прогоны своего отдела — фильтр по отделу стенда (своего у прогона нет)."""
    scope = permissions.own_department_or_403(identity, None)
    items = await stp_test_run_repo.list_all(
        db, limit=limit, offset=offset,
        stand_id=stand_id, os_version_id=os_version_id, department_id=scope,
    )
    total = await stp_test_run_repo.count_all(
        db, stand_id=stand_id, os_version_id=os_version_id, department_id=scope,
    )
    return items, total


async def get_stp_test_run(db: AsyncSession, identity: Identity, run_id: str):
    run = await stp_test_run_repo.get_by_id(db, run_id)
    if run is None:
        raise NotFoundError(error_code="STP_TEST_RUN_NOT_FOUND", message="Stp test run not found")
    # `stand_id` — NOT NULL FK с RESTRICT, стенд обязан быть; если его всё же
    # нет, отдел не восстановить — прогон считаем невидимым.
    stand = await test_stand_repo.get_by_id(db, run.stand_id)
    if stand is None:
        raise NotFoundError(error_code="STP_TEST_RUN_NOT_FOUND", message="Stp test run not found")
    permissions.require_own_department(identity, stand.department_id)
    cells = await stp_cell_repo.list_by_run(db, run_id)
    return run, cells
