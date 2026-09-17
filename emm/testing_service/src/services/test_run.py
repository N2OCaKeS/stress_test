"""Прогоны — fleet-wide кампании (§2.4, §6.1, §E1-E5 плана миграции).

`create_test_run()` разворачивает один запрос в независимые постановки в
очередь. Два способа собрать состав, в зависимости от того, задан ли
`test_run_stands`:

- **Явный пул** (`test_run_stands` не пуст) — прежнее поведение, вход E3
  (запуск всех тестов стенда). Для каждого `stand_id` берутся все
  `test_definitions` с `pinned_stand_id == stand_id` (тест "принадлежит"
  ровно одному стенду по конструкции §2.2/§5.5). Единственный путь, где
  допустим `debug=True`.
- **Вывод из активного состава СТП** (`test_run_stands` не задан/пуст) —
  полный прогон по РЦ: оператор выбирает только `os_version_id`, а какие
  тесты, значит какие стенды и ядра — решает активный состав СТП отдела для
  этой РЦ (`_derive_stp_entries`). Стенды и ядра не выбираются оператором,
  они выводятся из `stp_test_runs`/`stp_cells` (только `is_active=True`
  ячейки — неактивные исключены текущим составом, но сохранены как история).
  `stp_composition_id`/`stp_revision` кампании фиксируют, из какого снэпшота
  состава она собрана — последующее переключение состава СТП не должно
  незаметно переинтерпретировать уже начатую кампанию. Единственный путь,
  где допустим `full=True`.

Между постановками в очередь — никакой общей транзакции: стенд/тест без
покрытия или провал постановки одного конкретного теста не должны рушить
остальную кампанию (§5.5 "между стендами — параллельно", ошибка не глушит
очередь стенда — тот же принцип применяется и здесь на уровень выше, между
стендами кампании).

`RC` в `launch_context` — тот же `os_version_id`, что передан в запросе, без
дополнительного резолва в человекочитаемый номер релиза: `services/queue.py`
уже сегодня прокидывает `launch_context["RC"]` как есть в `os_version_id`
параметр `server_client.start_prepare_for_test()` (см. `_start_or_continue_
cycle`), то есть RC и os_version_id в этой кодовой базе — одно и то же поле
под двумя именами. Кампания просто следует уже существующему соглашению.

**Допуск по СТП решает `debug`, а не `final`.** `final` — просто
сохраняемая метка официального/релизного прогона, на постановку в очередь не
влияет. СТП-гейт (`launch_stp.require_membership`) применяется к каждому
тесту кампании безусловно, если `debug=False` — обычный/релизный прогон
всегда обязан соответствовать активной СТП, тест без членства не рушит всю
кампанию, а получает свою запись в `enqueue_errors` (тот же принцип частичных
провалов, что и у стенда без активных тестов). При `debug=True` (допустимо
только вместе с явным `test_run_stands`) гейт не вызывается вовсе, как и
проверка `readiness == READY` — весь пул стенда уходит в очередь как есть,
через тот же bypass, что `queue.py::enqueue` уже даёт одиночным debug-
запускам. Для выведенного из СТП состава (`debug` там всегда `False`) этот
гейт по конструкции всегда проходит (каждая запись и так взята из активной
ячейки СТП) — вызов не убран ради единообразия обоих путей и на случай гонки
(состав СТП поменялся между чтением и постановкой в очередь одной кампании).

`full=True` (допустимо только без `test_run_stands`) перед выводом состава
запускает `services/stp.py::generate_stp_runs(scope="full")` для этого РЦ —
состав кампании собирается уже из расширенной СТП, а не только из того, что
было сгенерировано раньше. Частичные провалы этой синхронизации (Jira/Zephyr
недоступны для конкретного стенда) не рушат создание кампании — они
попадают в отдельный список `stp_sync_errors`, не смешиваясь с
`enqueue_errors` постановки в очередь.

Режим безопасности (`MODE` в `launch_context`) больше не общий параметр
кампании — он читается с каждого теста (`test.mode`) при заведении
`TestRunEntry`, поэтому один пул стендов может законно смешивать orel- и
smolensk-тесты в одной кампании, каждый готовится под своим режимом.
Источник истины для режима записи — всегда `test.mode`, а не `StpTestRun.
mode` того прогона СТП, откуда взята ячейка: по конструкции они должны
совпадать (СТП-генерация делит тесты по их собственному режиму), но если
где-то разошлись — это рассинхронизация данных, а не повод класть в очередь
режим, которого у теста уже нет.
"""

from __future__ import annotations

import hashlib
import json
import logging
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType, StpCompositionScope, TestReadiness, TestRunStatus
from src.core.exceptions import AppException, AuthorizationError, ConflictError, DomainValidationError, NotFoundError
from src.dependencies.auth import Identity
from src.models import TestDefinition, TestRun, TestRunEntry
from src.repositories import queue_item as queue_item_repo
from src.repositories import stp_cell as stp_cell_repo
from src.repositories import stp_composition as stp_composition_repo
from src.repositories import stp_test_case as stp_test_case_repo
from src.repositories import stp_test_run as stp_test_run_repo
from src.repositories import test_definition as test_definition_repo
from src.repositories import test_run as repo
from src.repositories import test_run_entry as test_run_entry_repo
from src.repositories import test_stand as test_stand_repo
from src.schemas.test_run import TestRunPartialError, TestRunPreviewEntry
from src.services import audit_service, launch_stp, permissions, queue as queue_svc, test_run_status
from src.utils.ids import test_run_id as new_id

logger = logging.getLogger(__name__)


class _EntrySpec:
    """Одна будущая запись кампании до постановки в очередь — общий вид для обоих путей сборки состава."""

    __slots__ = ("stand_id", "kernel", "mode", "test", "stp_test_run_id")

    def __init__(self, *, stand_id: str, kernel: str, mode: str, test: TestDefinition, stp_test_run_id: str | None):
        self.stand_id = stand_id
        self.kernel = kernel
        self.mode = mode
        self.test = test
        self.stp_test_run_id = stp_test_run_id


async def _resolve_kernels(os_version_id: str, kernel: str | None) -> list[str]:
    """Явное ядро — как есть; иначе все ядра каталога ОС (карточка версии, потом
    обнаружение через `resolve_os_kernels`, если карточка их не несёт)."""
    from src.services import server_client
    if kernel:
        return [kernel]
    version = await server_client.get_os_version(os_version_id)
    kernels = list(dict.fromkeys(version.get("kernels") or []))
    if not kernels:
        kernels = await server_client.resolve_os_kernels(os_version_id)
    return kernels


async def _explicit_stand_entries(
    db: AsyncSession, *, test_run_stands: list[str], kernels: list[str],
) -> list[_EntrySpec]:
    """Прежний путь §6.1 — все тесты, закреплённые за каждым стендом явного пула."""
    tests = await test_definition_repo.list_by_pinned_stands(db, test_run_stands)
    return [
        _EntrySpec(stand_id=test.pinned_stand_id, kernel=selected_kernel, mode=test.mode, test=test, stp_test_run_id=None)
        for selected_kernel in kernels for test in tests
    ]


async def _full_scope_candidate_entries(
    db: AsyncSession, *, department_id: str, os_version_id: str, kernel: str | None,
) -> list[_EntrySpec]:
    """Гипотетический полный состав для превью `full=True` без похода в Zephyr/Confluence.

    `create_test_run(full=True)` реально вызывает `stp_svc.generate_stp_runs
    (scope="full")`, а уже потом читает получившиеся активные ячейки — это
    даёт точный состав, но пишет в БД и дёргает внешние системы, поэтому
    превью так делать не может (§E1: preview без побочных эффектов). Здесь —
    прямое приближение: все `readiness=READY` тесты отдела, закреплённые за
    каким-либо стендом, на каждом ядре каталога ОС, со своим `test.mode`.
    Список стендов/тестов может отличаться от того, что реально даст
    генерация (например, если у теста ещё нет `stp_test_case` с `zephyr_id` —
    `generate_stp_runs` заведёт его на лету, здесь эта связь не проверяется).
    """
    tests = await test_definition_repo.list_by_department_pinned(db, department_id)
    ready_tests = [t for t in tests if t.readiness == TestReadiness.READY]
    kernels = await _resolve_kernels(os_version_id, kernel)
    return [
        _EntrySpec(stand_id=t.pinned_stand_id, kernel=selected_kernel, mode=t.mode, test=t, stp_test_run_id=None)
        for selected_kernel in kernels for t in ready_tests
    ]


async def _derive_stp_entries(
    db: AsyncSession, *, department_id: str, os_version_id: str, kernel: str | None,
) -> tuple[list[_EntrySpec], list[str]]:
    """Разворачивает активный состав СТП отдела для РЦ в плоский список записей кампании.

    Источник — тот же, что у публикации СТП-матрицы (`services/stp_matrix.py`
    ::publish_stp_matrix): `stp_test_run_repo.list_by_department_and_os_version`
    + `stp_cell_repo.list_by_runs`. Здесь дополнительно фильтруется
    `is_active` (неактивные ячейки — исключённая, но сохранённая история, не
    часть текущего состава) и разрешается `stp_test_case.code → test_definition`,
    которого прогон в очередь и требует.

    `kernel`, если задан явно вызывающим, сужает состав до записей именно с
    этим ядром (override, а не фильтр каталога ОС — ядра здесь и так уже
    зафиксированы составом СТП per-запись, дополнительного резолва через
    `server_client` не нужно).

    Возвращает `(specs, candidate_stands)` — `candidate_stands` включает
    стенды всех активных ячеек, подходящих под `kernel`-фильтр, даже если по
    ним не нашлось валидной записи (например, тест-кейс без соответствующего
    `test_definition`) — нужно `create_test_run`/`preview_test_run`, чтобы
    корректно посчитать `stands_without_tests`.
    """
    runs = await stp_test_run_repo.list_by_department_and_os_version(db, department_id, os_version_id)
    if not runs:
        return [], []
    run_by_id = {r.id: r for r in runs}

    cells = await stp_cell_repo.list_by_runs(db, [r.id for r in runs])
    active_cells = [c for c in cells if c.is_active and (kernel is None or run_by_id[c.stp_test_run_id].kernel == kernel)]
    if not active_cells:
        return [], []

    case_ids = sorted({c.stp_test_case_id for c in active_cells})
    cases_by_id = {c.id: c for c in await stp_test_case_repo.list_by_ids(db, case_ids)}
    codes = sorted({case.code for case in cases_by_id.values()})
    tests_by_code = {t.code: t for t in await test_definition_repo.list_by_codes(db, codes)}

    candidate_stands = sorted({run_by_id[c.stp_test_run_id].stand_id for c in active_cells})

    specs: list[_EntrySpec] = []
    for cell in active_cells:
        run = run_by_id[cell.stp_test_run_id]
        case = cases_by_id.get(cell.stp_test_case_id)
        if case is None:
            continue
        test = tests_by_code.get(case.code)
        if test is None:
            logger.warning(
                "test_run: stp cell %s (case=%s code=%s) has no matching test_definition, skipping",
                cell.id, case.id, case.code,
            )
            continue
        if test.mode != run.mode:
            logger.warning(
                "test_run: test %s mode=%s disagrees with its stp run %s mode=%s, using test.mode",
                test.code, test.mode, run.id, run.mode,
            )
        specs.append(_EntrySpec(stand_id=run.stand_id, kernel=run.kernel, mode=test.mode, test=test, stp_test_run_id=run.id))
    return specs, candidate_stands


def _replay_result(run: TestRun, entries: list[TestRunEntry]) -> tuple[TestRun, list[str], list[TestRunPartialError]]:
    populated_stands = {entry.stand_id for entry in entries}
    stands_without_tests = [stand_id for stand_id in run.test_run_stands if stand_id not in populated_stands]
    enqueue_errors = [
        TestRunPartialError(
            stand_id=entry.stand_id, test_id=entry.test_id,
            error_code=entry.enqueue_error_code, message=entry.enqueue_error,
        )
        for entry in entries
        if entry.enqueue_error_code
    ]
    return run, stands_without_tests, enqueue_errors


async def create_test_run(
    db: AsyncSession,
    identity: Identity,
    *,
    os_version_id: str,
    kernel: str | None,
    test_run_stands: list[str] | None = None,
    final: bool = False,
    debug: bool = False,
    full: bool = False,
    request_id: str | None = None,
) -> tuple[TestRun, list[str], list[TestRunPartialError], list[dict]]:
    """Завести кампанию + поставить в очередь её состав.

    `test_run_stands` заданный явно — прежний путь §6.1 (пул выбран
    оператором), единственный, где допустим `debug=True`. Пустой/не заданный
    — состав выводится из активного состава СТП отдела для `os_version_id`
    (см. `_derive_stp_entries`), стенды и ядра оператор не выбирает,
    единственный путь, где допустим `full=True`.

    Возвращает `(test_run, stands_without_tests, enqueue_errors, stp_sync_errors)`
    — все три списка могут быть непустыми одновременно с успешно созданной
    кампанией: частичные провалы не откатывают уже поставленные в очередь
    стенды. `stp_sync_errors` заполняется только при `full=True` и содержит
    провалы самой синхронизации СТП, отдельно от `enqueue_errors`.

    `request_id` — повтор с тем же значением и тем же телом возвращает уже
    созданную кампанию без повторной постановки в очередь; с другим телом —
    `ConflictError`, как у `public_queue.py::request()`.
    """
    try:
        await permissions.require_action(db, identity, EntityType.TEST_RUN, Action.CREATE)
    except AuthorizationError:
        audit_service.emit(
            "test_run.create",
            target_type="test_run",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise

    department_id = identity.department_id
    if not department_id:
        raise DomainValidationError(
            error_code="TEST_RUN_DEPARTMENT_REQUIRED",
            message="Caller has no department_id to attribute this test run to",
        )

    requested_stands = list(dict.fromkeys(test_run_stands)) if test_run_stands else []

    if debug and not requested_stands:
        raise DomainValidationError(
            error_code="TEST_RUN_DEBUG_REQUIRES_STANDS",
            message="debug=True допустим только вместе с явным test_run_stands",
        )
    if full and requested_stands:
        raise DomainValidationError(
            error_code="TEST_RUN_FULL_REQUIRES_STP_DERIVED",
            message="full=True недопустим вместе с явным test_run_stands",
        )

    fingerprint = None
    if request_id:
        payload = {
            "os_version_id": os_version_id, "kernel": kernel,
            "test_run_stands": sorted(requested_stands), "final": final,
            "debug": debug, "full": full,
        }
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        await repo.lock_request(db, identity.user_id, request_id)
        existing = await repo.find_request(db, identity.user_id, request_id)
        if existing:
            if existing.request_fingerprint != fingerprint:
                raise ConflictError(
                    error_code="REQUEST_ID_CONFLICT",
                    message="Этот идентификатор запроса уже использован с другими параметрами",
                )
            entries = await test_run_entry_repo.list_for_run(db, existing.id)
            run, stands_without_tests, enqueue_errors = _replay_result(existing, entries)
            return run, stands_without_tests, enqueue_errors, []

    stp_composition_id: str | None = None
    stp_revision: int | None = None
    stp_sync_errors: list[dict] = []

    if requested_stands:
        kernels = await _resolve_kernels(os_version_id, kernel)
        test_run_stands = requested_stands
        composition_source = "pinned_catalog"
        entry_specs = await _explicit_stand_entries(db, test_run_stands=test_run_stands, kernels=kernels)
    else:
        if full:
            from src.services import stp as stp_svc
            _touched, stp_sync_errors = await stp_svc.generate_stp_runs(
                db, identity, os_version_id=os_version_id, mode=None, kernel=kernel,
                scope=StpCompositionScope.FULL, department_id=department_id,
            )
        entry_specs, _candidate_stands = await _derive_stp_entries(
            db, department_id=department_id, os_version_id=os_version_id, kernel=kernel,
        )
        if not entry_specs:
            raise DomainValidationError(
                error_code="STP_COMPOSITION_EMPTY",
                message="Активный состав СТП для этого РЦ пуст — запускать нечего",
            )
        test_run_stands = sorted({spec.stand_id for spec in entry_specs})
        kernels = sorted({spec.kernel for spec in entry_specs})
        composition_source = "stp_composition"
        composition_row = await stp_composition_repo.get_by_department_and_os_version(db, department_id, os_version_id)
        if composition_row is not None:
            stp_composition_id = composition_row.id
            stp_revision = composition_row.revision
    kernel = kernels[0]

    run = await repo.create(db, {
        "id": new_id(),
        "os_version_id": os_version_id,
        "kernel": kernel,
        "kernels": kernels,
        "department_id": department_id,
        "test_run_stands": list(test_run_stands),
        "status": TestRunStatus.QUEUED,
        "final": final,
        "composition_source": composition_source,
        "stp_composition_id": stp_composition_id,
        "stp_revision": stp_revision,
        "created_by": identity.user_id,
        "client_request_id": request_id,
        "request_fingerprint": fingerprint,
    })
    entries = [TestRunEntry(
        id=f"entry_{uuid4().hex}", test_run_id=run.id, stand_id=spec.stand_id,
        test_id=spec.test.id, test_code=spec.test.code, test_name=spec.test.full_name,
        kernel=spec.kernel, mode=spec.mode,
    ) for spec in entry_specs]
    db.add_all(entries)
    await db.commit()
    await db.refresh(run)

    populated_stands = {entry.stand_id for entry in entries}
    stands_without_tests = [stand_id for stand_id in test_run_stands if stand_id not in populated_stands]
    enqueue_errors: list[TestRunPartialError] = []
    for entry, spec in zip(entries, entry_specs):
        try:
            ctx = {"RC": os_version_id, "KERNEL": entry.kernel, "MODE": entry.mode}
            stp = None
            if not debug:
                stp = await launch_stp.require_membership(
                    db, entry.test_code, entry.stand_id, ctx, run_id=spec.stp_test_run_id,
                )
            await queue_svc.enqueue(
                db, identity, entry.test_id, launch_context=ctx,
                debug_mode=debug, stand_id=entry.stand_id if debug else None,
                test_run_id=run.id, test_run_entry_id=entry.id,
                stp_test_run_id=stp.id if stp else None,
            )
        except AppException as exc:
            logger.warning("test_run %s: enqueue failed for stand=%s test=%s: %s", run.id, entry.stand_id, entry.test_id, exc.message)
            entry.enqueue_error_code = exc.error_code
            entry.enqueue_error = exc.message[:2048]
            await db.commit()
            enqueue_errors.append(TestRunPartialError(
                stand_id=entry.stand_id, test_id=entry.test_id,
                error_code=exc.error_code, message=exc.message,
            ))

    new_status = await test_run_status.recompute(db, run.id, emit_audit=False)
    await db.commit()
    await db.refresh(run)

    audit_service.emit(
        "test_run.create",
        target_id=run.id, target_type="test_run",
        status="success", allowed=True,
        details={
            "department_id": department_id,
            "os_version_id": os_version_id,
            "modes": sorted({entry.mode for entry in entries}),
            "kernel": kernel,
            "stand_count": len(test_run_stands),
            "stands_without_tests": stands_without_tests,
            "enqueue_error_count": len(enqueue_errors),
            "status": new_status,
            "final": final,
            "debug": debug,
            "full": full,
            "composition_source": composition_source,
            "stp_composition_id": stp_composition_id,
            "stp_sync_error_count": len(stp_sync_errors),
        },
    )
    return run, stands_without_tests, enqueue_errors, stp_sync_errors


async def preview_test_run(
    db: AsyncSession,
    identity: Identity,
    *,
    os_version_id: str,
    kernel: str | None,
    test_run_stands: list[str] | None = None,
    debug: bool = False,
    full: bool = False,
) -> tuple[list[str], list[TestRunPreviewEntry]]:
    """Состав кампании без побочных эффектов — что будет запущено/пропущено и почему (§E4).

    Тот же выбор источника состава и те же ограничения на `debug`/`full`, что
    и у `create_test_run`. Проверяет ровно те причины пропуска, которые
    `create_test_run` умеет обрабатывать частично (readiness, активность
    стенда, СТП-членство) — без создания `test_run`/`test_run_entry` и без
    постановки в очередь. `debug=True` гасит и readiness-, и СТП-проверку в
    превью, ровно как реальный запуск (`queue.py::enqueue` тоже не смотрит на
    них при `debug_mode=True`) — активность стенда при этом по-прежнему
    проверяется, `enqueue` её не пропускает ни при каком `debug_mode`.

    `full=True` — **не вызывает** `stp_svc.generate_stp_runs`, преднамеренно:
    превью обязано быть без побочных эффектов (ни новых `stp_test_runs`, ни
    `stp_cells`, ни обращений к Zephyr/Confluence). Вместо этого состав
    строится приближённо — `_full_scope_candidate_entries` (см. её докстринг
    про то, чем это приближение может отличаться от настоящего результата
    `create_test_run(full=True)`) — и, как и `debug`, не проходит через
    СТП-проверку (сама генерация гарантирует членство постфактум).

    В отличие от `create_test_run`, пустой выведенный состав здесь не ошибка
    — превью просто вернёт пустые списки, чтобы UI мог показать "запускать
    нечего" без исключения.
    """
    await permissions.require_action(db, identity, EntityType.TEST_RUN, Action.CREATE)

    if not identity.department_id:
        raise DomainValidationError(
            error_code="TEST_RUN_DEPARTMENT_REQUIRED",
            message="Caller has no department_id to attribute this test run to",
        )
    department_id = identity.department_id

    requested_stands = list(dict.fromkeys(test_run_stands)) if test_run_stands else []

    if debug and not requested_stands:
        raise DomainValidationError(
            error_code="TEST_RUN_DEBUG_REQUIRES_STANDS",
            message="debug=True допустим только вместе с явным test_run_stands",
        )
    if full and requested_stands:
        raise DomainValidationError(
            error_code="TEST_RUN_FULL_REQUIRES_STP_DERIVED",
            message="full=True недопустим вместе с явным test_run_stands",
        )

    if requested_stands:
        kernels = await _resolve_kernels(os_version_id, kernel)
        entry_specs = await _explicit_stand_entries(db, test_run_stands=requested_stands, kernels=kernels)
        populated_stands = {spec.stand_id for spec in entry_specs}
        stands_without_tests = [stand_id for stand_id in requested_stands if stand_id not in populated_stands]
    elif full:
        entry_specs = await _full_scope_candidate_entries(
            db, department_id=department_id, os_version_id=os_version_id, kernel=kernel,
        )
        # Кандидаты собраны напрямую из готовых тестов отдела — нет отдельного
        # "состава СТП", относительно которого считать недостающие стенды.
        stands_without_tests = []
    else:
        entry_specs, candidate_stands = await _derive_stp_entries(
            db, department_id=department_id, os_version_id=os_version_id, kernel=kernel,
        )
        populated_stands = {spec.stand_id for spec in entry_specs}
        stands_without_tests = [stand_id for stand_id in candidate_stands if stand_id not in populated_stands]

    skip_stp_check = debug or full

    stands: dict[str, object] = {}
    entries: list[TestRunPreviewEntry] = []
    for spec in entry_specs:
        test = spec.test
        stand = stands.get(spec.stand_id)
        if stand is None and spec.stand_id not in stands:
            stand = await test_stand_repo.get_by_id(db, spec.stand_id)
            stands[spec.stand_id] = stand

        common = {
            "stand_id": spec.stand_id, "test_id": test.id, "test_code": test.code,
            "test_name": test.full_name, "kernel": spec.kernel, "mode": spec.mode,
        }
        if not debug and test.readiness != TestReadiness.READY:
            entries.append(TestRunPreviewEntry(
                **common, action="skip_debug_required",
                reason="Тест не в статусе «Рабочий» — обычный запуск недоступен",
            ))
            continue
        if stand is None or not stand.is_active:
            entries.append(TestRunPreviewEntry(
                **common, action="skip_stand_inactive", reason="Стенд не активен",
            ))
            continue
        if not skip_stp_check:
            ctx = {"RC": os_version_id, "KERNEL": spec.kernel, "MODE": spec.mode}
            stp = await launch_stp.find_membership(db, test.code, spec.stand_id, ctx, run_id=spec.stp_test_run_id)
            if stp is None:
                context_run = await launch_stp.find_context_run(db, spec.stand_id, ctx, run_id=spec.stp_test_run_id)
                if context_run is None:
                    entries.append(TestRunPreviewEntry(
                        **common, action="skip_stp_not_generated",
                        reason="Для этого стенда/РЦ/ядра/режима ещё не сгенерирована СТП — сначала выполните генерацию.",
                    ))
                else:
                    entries.append(TestRunPreviewEntry(
                        **common, action="skip_not_in_stp", stp_test_run_id=context_run.id,
                        reason="Тест отсутствует в активном составе СТП",
                    ))
                continue
        entries.append(TestRunPreviewEntry(**common, action="launch"))

    return stands_without_tests, entries


async def get_test_run(db: AsyncSession, identity: Identity, run_id: str) -> tuple[TestRun, list]:
    """Карточка кампании + все её дочерние queue_items (§6.1 — обзор кампании)."""
    run = await repo.get_by_id(db, run_id)
    if run is None:
        raise NotFoundError(
            error_code="TEST_RUN_NOT_FOUND",
            message="Test run not found",
        )
    permissions.require_own_department(identity, run.department_id)
    items = await queue_item_repo.list_by_test_run_id(db, run_id)
    return run, items


async def list_test_runs(
    db: AsyncSession,
    identity: Identity,
    limit: int,
    offset: int,
    *,
    department_id: str | None = None,
    status: str | None = None,
    final: bool | None = None,
) -> tuple[list[TestRun], int]:
    """List + count кампаний под фильтрами, суженными до отдела вызывающего."""
    scope = permissions.own_department_or_403(identity, department_id)
    items = await repo.list_all(
        db, limit=limit, offset=offset, department_id=scope, status=status, final=final,
    )
    total = await repo.count_all(db, department_id=scope, status=status, final=final)
    return items, total
