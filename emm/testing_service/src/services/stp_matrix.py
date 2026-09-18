"""Публикация СТП-матрицы в Confluence (§D2/D3 плана миграции — эталонные отчёты).

Перенос легаси `ZefirResultTable` (`allta_app/libs/zefir.py:203-436`, вся
логика — побочный эффект конструктора): сводная HTML-таблица
версия/ядро/режим/стенд × тест-кейс → статус, с цветовой подсветкой,
публикуется как страница Confluence в трёхуровневой иерархии. Источник
данных здесь — собственная БД (`stp_test_runs`/`stp_cells`/`stp_test_cases`),
а не Jira Zephyr matrix-report API, как в легаси: EMM уже ведёт эти данные
локально (`services/stp.py`, `services/stp_status.py`), опрашивать Zephyr
за тем же самым было бы лишним обходом.

Одна страница на РЦ (`os_version_id`) на отдел — собирает ВСЕ относящиеся к
этому РЦ `stp_test_runs` отдела (любой режим/ядро/стенд), ровно как легаси
транспонирует все комбинации в одну таблицу. Отдел резолвится через
`test_stands.department_id` (у `stp_test_runs` своего department нет, см.
`models/stp_test_run.py`) — `list_by_department_and_os_version` фильтрует
join'ом.

Единственное сознательное изменение поведения (согласовано с владельцем):
пространство Confluence и заголовок grandparent-страницы иерархии — per-
department настройки (`department_integration_settings.stp_matrix_confluence_
space`/`stp_matrix_confluence_root_page_title`), не платформенный хардкод
`DEVQA`/`'Состав тестового прогона'`. Второе отличие, уже принятое ранее для
run_summary/activity_report: иерархия идемпотентна (find-or-create-or-update
на каждом уровне), легаси же требовал grandparent завести вручную заранее и
падал бы, если её нет.

**Легенда легаси не перенесена.** `zefir.py:369-370` дописывает в конец тела
буквальное содержимое `./templates/stand.html`/`./templates/times.html` —
статичные HTML-фрагменты вне класса `ZefirResultTable`, их содержимое не
разведано (см. отчёт волны D1) и здесь не воспроизводится. Открытый пункт —
см. отчёт волны.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType, StpMatrixPublicationStatus
from src.core.exceptions import AppException, AuthorizationError
from src.dependencies.auth import Identity
from src.models import StpCell, StpMatrixPublication, StpTestCase, StpTestRun
from src.repositories import department_integration_settings as dis_repo
from src.repositories import stp_cell as stp_cell_repo
from src.repositories import stp_matrix_publication as repo
from src.repositories import stp_test_case as stp_test_case_repo
from src.repositories import stp_test_run as stp_test_run_repo
from src.repositories import test_definition as test_definition_repo
from src.repositories import test_stand as test_stand_repo
from src.services import audit_service, confluence_client, permissions, secret_client, server_client
from src.utils.ids import stp_matrix_publication_id as new_id

logger = logging.getLogger(__name__)

# en → ru, тот же словарь, что легаси `zefir.py:329-335` (`StpCellStatus` в
# `core/constants.py` уже хранит en-имена, здесь только подписи для рендера).
_STATUS_LABELS_RU: dict[str, str] = {
    "not_run": "Не запускался",
    "in_progress": "Выполняется",
    "pass": "Выполнено",
    "fail": "Провалено",
}

# Фон/текст ячейки статуса — побайтово из легаси (`zefir.py:355-368`).
_STATUS_COLORS: dict[str, tuple[str, str | None]] = {
    "Выполняется": ("#fffacf", None),
    "Не запускался": ("#f8f8f8", None),
    "Выполнено": ("#dafee6", None),
    "Провалено": ("#feffa2", "#fe1313"),
}

# Подсветка режима — тот же словарь (`zefir.py:355-368`).
_MODE_COLORS: dict[str, str] = {
    "orel": "#e4f1fc",
    "smolensk": "#ffe8e8",
}


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cell_style(bg: str | None, fg: str | None = None) -> str:
    if bg is None:
        return ""
    style = f"background-color:{bg};"
    if fg:
        style += f"color:{fg};"
    return f' style="{style}"'


def _hierarchy_titles(rc_number: str) -> tuple[str | None, str]:
    """`(parent_title | None, page_title)` — перенос ветвления `zefir.py:382-422`.

    Обычный релиз (4 сегмента) или hotfix (6 сегментов, 4-й — маркер `UU`):
    страница РЦ заводится под отдельной родительской страницей
    `STRESS_stp ⬝ {release}` (release — первые три сегмента, либо для hotfix
    первые три плюс пятый, `UU` в неё не входит), сама страница называется
    буквально номером РЦ. Формат, не подпадающий ни под один случай —
    плоско, без промежуточного родителя, страница `STRESS_stp ⬝ {rc_number}`
    прямо под grandparent.

    `rc_number` — номер РЦ (`"1.8.5.46"`), не `os_version_id`: id каталога
    резолвится в версию вызывающим кодом через
    `server_client.resolve_os_version_name`.
    """
    parts = rc_number.split(".")
    if len(parts) == 6 and parts[3] == "UU":
        release = ".".join([parts[0], parts[1], parts[2], parts[4]])
        return f"STRESS_stp ⬝ {release}", rc_number
    if len(parts) == 4:
        release = ".".join(parts[:3])
        return f"STRESS_stp ⬝ {release}", rc_number
    return None, f"STRESS_stp ⬝ {rc_number}"


def render_matrix_html(
    *, rc_number: str, runs: list[StpTestRun], cases: list[StpTestCase],
    cells: list[StpCell], stand_labels: dict[str, str] | None = None,
    case_labels: dict[str, str] | None = None,
) -> str:
    """Сводная таблица версия/ядро/режим/стенд × тест-кейс → статус.

    `rc_number` — номер РЦ (`"1.8.5.46"`), не `os_version_id`: в заголовок
    таблицы и в строку «Версия» идёт человеческая версия, id каталога сюда
    попадать не должен.

    Легаси строит `pandas.DataFrame`, сортирует по (Режим, №стенда) и
    транспонирует, так что итоговые строки таблицы — исходные колонки
    (Версия/Ядро/Режим/№стенда + тест-кейсы), а столбцы — исходные строки
    (уникальные прогоны). Здесь тот же итоговый вид собирается напрямую,
    без промежуточного DataFrame.

    `stand_labels` — `test_stands.id` → человеческое имя стенда (`stand3`).
    В ячейку «№ стенда» идёт оно, а не внутренний uuid, и по нему же
    сортируются столбцы: легаси упорядочивал прогоны строкой имени стенда,
    а сортировка по uuid'у случайна и меняется от отдела к отделу. Стенд без
    алиаса печатается своим id — столбец хотя бы остаётся различимым.

    `case_labels` — `stp_test_cases.id` → короткая подпись строки
    (`test_definitions.matrix_label`, легаси `testname_columns`: "file system
    benchmark. EXT4" → "FS_EXT4"). Ровно тот же приём, что и у
    `stand_labels`: подпись подставляется в заголовок строки и по ней же
    идёт сортировка строк — легаси переименовывал колонки ДО сортировки, то
    есть упорядочивал их по сокращению, а не по полному имени. Кейс без
    подписи печатается и сортируется своим `title` — новый тест, которому
    сокращение ещё не задали, остаётся видимым, а не пустым.
    """
    if not runs:
        return f"<h1>Прогресс выполнения тестового прогона {_escape(rc_number)}</h1><p><em>Нет прогонов.</em></p>"

    labels = stand_labels or {}
    row_labels = case_labels or {}

    def _label(run: StpTestRun) -> str:
        return labels.get(run.stand_id) or run.stand_id

    def _case_label(case: StpTestCase) -> str:
        return row_labels.get(case.id) or case.title

    runs = sorted(runs, key=lambda r: (r.mode, _label(r)))

    status_by_run_and_case: dict[tuple[str, str], str] = {
        (cell.stp_test_run_id, cell.stp_test_case_id): cell.status for cell in cells
    }
    cases_sorted = sorted(cases, key=_case_label)

    def _row(label: str, values: list[str]) -> str:
        cells_html = "".join(f"<td>{v}</td>" for v in values)
        return f"<tr><th>{_escape(label)}</th>{cells_html}</tr>"

    version_row = _row("Версия", [_escape(rc_number) for _ in runs])
    kernel_row = _row("Ядро", [_escape(r.kernel) for r in runs])
    mode_cells = "".join(
        f"<td{_cell_style(_MODE_COLORS.get(r.mode))}>{_escape(r.mode)}</td>" for r in runs
    )
    mode_row = f"<tr><th>Режим</th>{mode_cells}</tr>"
    stand_row = _row("№ стенда", [_escape(_label(r)) for r in runs])

    case_rows: list[str] = []
    for case in cases_sorted:
        cells_html = []
        for run in runs:
            status = status_by_run_and_case.get((run.id, case.id))
            label = _STATUS_LABELS_RU.get(status, "") if status else ""
            bg, fg = _STATUS_COLORS.get(label, (None, None))
            cells_html.append(f"<td{_cell_style(bg, fg)}>{_escape(label)}</td>")
        case_rows.append(f"<tr><th>{_escape(_case_label(case))}</th>{''.join(cells_html)}</tr>")

    table = (
        '<table style="border-collapse:collapse;" border="1">'
        f"<tbody>{version_row}{kernel_row}{mode_row}{stand_row}{''.join(case_rows)}</tbody>"
        "</table>"
    )
    return f"<h1>Прогресс выполнения тестового прогона {_escape(rc_number)}</h1>{table}"


async def _resolve_confluence_ctx(
    db: AsyncSession, department_id: str,
) -> tuple[str, str, str, str] | None:
    """`(base_url, bearer_token, space, root_page_title)` либо `None` — не настроено.

    Свёрнуто в один "не настроено" исход (пропущенное поле, отсутствующая
    строка, провал reveal) — вызывающий код трактует это как
    `skipped_not_configured`, не как разные случаи.
    """
    settings = await dis_repo.get_by_department(db, department_id)
    if (
        settings is None
        or not settings.confluence_base_url
        or not settings.stp_matrix_confluence_space
        or not settings.stp_matrix_confluence_root_page_title
    ):
        return None
    cred_id = settings.confluence_credential_id or settings.credential_id
    if not cred_id:
        return None
    try:
        _login, secret = await secret_client.reveal_credential(cred_id)
    except AppException as exc:
        logger.warning(
            "stp_matrix: reveal_credential failed for dept=%s cred=%s: %s",
            department_id, cred_id, exc.message,
        )
        return None
    if not secret:
        return None
    return settings.confluence_base_url, secret, settings.stp_matrix_confluence_space, settings.stp_matrix_confluence_root_page_title


async def _find_or_create_page(
    *, base_url: str, bearer_token: str, space: str, title: str, parent_id: str | None, body_html: str,
) -> str:
    existing_id = await confluence_client.find_page_id(
        base_url=base_url, bearer_token=bearer_token, space=space, title=title,
    )
    if existing_id is not None:
        return existing_id
    return await confluence_client.create_page(
        base_url=base_url, bearer_token=bearer_token, space=space, title=title,
        parent_id=parent_id, body_html=body_html,
    )


async def _publish_hierarchy(
    *, base_url: str, bearer_token: str, space: str, root_title: str,
    rc_number: str, body_html: str,
) -> tuple[str, str | None]:
    """Find-or-create grandparent → (опционально) parent → страница РЦ, затем update тела страницы РЦ.

    Возвращает `(page_id, parent_id)`. Тело обновляется ТОЛЬКО у самой
    страницы РЦ — grandparent/parent создаются пустыми, если их ещё не было
    (легаси их не заполняет вовсе, это чисто структурные узлы иерархии).
    """
    grandparent_id = await _find_or_create_page(
        base_url=base_url, bearer_token=bearer_token, space=space, title=root_title,
        parent_id=None, body_html="",
    )

    parent_title, page_title = _hierarchy_titles(rc_number)
    parent_id = grandparent_id
    if parent_title is not None:
        parent_id = await _find_or_create_page(
            base_url=base_url, bearer_token=bearer_token, space=space, title=parent_title,
            parent_id=grandparent_id, body_html="",
        )

    page_id = await confluence_client.find_page_id(
        base_url=base_url, bearer_token=bearer_token, space=space, title=page_title,
    )
    if page_id is None:
        page_id = await confluence_client.create_page(
            base_url=base_url, bearer_token=bearer_token, space=space, title=page_title,
            parent_id=parent_id, body_html=body_html,
        )
    else:
        version = await confluence_client.get_page_version(
            base_url=base_url, bearer_token=bearer_token, page_id=page_id,
        )
        await confluence_client.update_page(
            base_url=base_url, bearer_token=bearer_token, page_id=page_id, title=page_title,
            body_html=body_html, version=version,
        )
    return page_id, (parent_id if parent_title is not None else None)


async def _save(
    db: AsyncSession,
    existing: StpMatrixPublication | None,
    department_id: str,
    os_version_id: str,
    *,
    status: str,
    identity: Identity,
    confluence_page_id: str | None = None,
    confluence_parent_page_id: str | None = None,
    body_snapshot: str | None = None,
    error: str | None = None,
) -> StpMatrixPublication:
    changes: dict = {
        "status": status,
        "confluence_page_id": confluence_page_id,
        "confluence_parent_page_id": confluence_parent_page_id,
        "body_snapshot": body_snapshot,
        "error": error,
    }
    if status == StpMatrixPublicationStatus.POSTED:
        changes["published_at"] = datetime.now(timezone.utc)
        changes["published_by"] = identity.user_id

    if existing is None:
        obj = await repo.create(db, {
            "id": new_id(), "department_id": department_id, "os_version_id": os_version_id, **changes,
        })
    else:
        obj = await repo.update(db, existing, changes)

    await db.commit()
    await db.refresh(obj)
    audit_service.emit(
        "stp_matrix.publish",
        target_id=obj.id, target_type="stp_matrix_publication",
        status="success" if status == StpMatrixPublicationStatus.POSTED else "failure",
        allowed=True,
        details={"department_id": department_id, "os_version_id": os_version_id, "status": status},
    )
    return obj


async def publish_stp_matrix(
    db: AsyncSession, identity: Identity, *, department_id: str, os_version_id: str,
) -> StpMatrixPublication:
    """Ручной триггер публикации сводной СТП-таблицы одного РЦ одного отдела.

    RBAC — `require_department_action` на `(stp_test_run, *, publish)`:
    department_admin своего отдела либо носитель роли с `publish` в
    testing_service этого же отдела; cross-department вызов структурно
    невозможен (см. docstring `permissions.require_department_action`).
    """
    try:
        await permissions.require_department_action(
            db, identity, department_id, EntityType.STP_TEST_RUN, Action.PUBLISH,
        )
    except AuthorizationError:
        audit_service.emit(
            "stp_matrix.publish",
            target_type="stp_matrix_publication",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "department_id": department_id, "os_version_id": os_version_id},
        )
        raise

    existing = await repo.get_by_department_and_os_version(db, department_id, os_version_id)

    ctx = await _resolve_confluence_ctx(db, department_id)
    if ctx is None:
        return await _save(
            db, existing, department_id, os_version_id,
            status=StpMatrixPublicationStatus.SKIPPED_NOT_CONFIGURED, identity=identity,
        )
    base_url, bearer_token, space, root_title = ctx

    runs = await stp_test_run_repo.list_by_department_and_os_version(db, department_id, os_version_id)
    if not runs:
        return await _save(
            db, existing, department_id, os_version_id,
            status=StpMatrixPublicationStatus.SKIPPED_NO_TEST_RUNS, identity=identity,
        )

    try:
        rc_number = await server_client.resolve_os_version_name(os_version_id)
    except AppException as exc:
        logger.warning(
            "stp_matrix.publish: cannot resolve os_version %s: %s", os_version_id, exc.message,
        )
        return await _save(
            db, existing, department_id, os_version_id,
            status=StpMatrixPublicationStatus.FAILED, identity=identity, error=exc.message[:1024],
        )

    cells = await stp_cell_repo.list_by_runs(db, [r.id for r in runs], is_active=True)
    case_ids = sorted({c.stp_test_case_id for c in cells})
    cases = await stp_test_case_repo.list_by_ids(db, case_ids)

    stands = await test_stand_repo.list_by_ids(db, sorted({r.stand_id for r in runs}))
    stand_labels = {s.id: s.legacy_token for s in stands if s.legacy_token}

    # Сокращения строк живут в каталоге тестов, а не в зеркале Zephyr:
    # `stp_test_cases.title` и так копируется из `test_definitions.full_name`
    # (`services/stp_add_test.py`), второму источнику имён взяться неоткуда.
    # Связка — по общему `code` (см. docstring `models/stp_test_case.py`).
    definitions = await test_definition_repo.list_by_codes(db, sorted({c.code for c in cases}))
    label_by_code = {d.code: d.matrix_label for d in definitions if d.matrix_label}
    case_labels = {c.id: label_by_code[c.code] for c in cases if c.code in label_by_code}

    body_html = render_matrix_html(
        rc_number=rc_number, runs=runs, cases=cases, cells=cells,
        stand_labels=stand_labels, case_labels=case_labels,
    )

    if existing is not None and existing.confluence_page_id and existing.body_snapshot == body_html:
        # Таблица не изменилась с прошлой публикации — Confluence не дёргаем.
        return await _save(
            db, existing, department_id, os_version_id,
            status=StpMatrixPublicationStatus.POSTED, identity=identity,
            confluence_page_id=existing.confluence_page_id,
            confluence_parent_page_id=existing.confluence_parent_page_id,
            body_snapshot=body_html,
        )

    try:
        page_id, parent_id = await _publish_hierarchy(
            base_url=base_url, bearer_token=bearer_token, space=space, root_title=root_title,
            rc_number=rc_number, body_html=body_html,
        )
    except AppException as exc:
        logger.warning(
            "stp_matrix.publish failed for dept=%s os_version=%s: %s",
            department_id, os_version_id, exc.message,
        )
        return await _save(
            db, existing, department_id, os_version_id,
            status=StpMatrixPublicationStatus.FAILED, identity=identity, error=exc.message[:1024],
        )

    return await _save(
        db, existing, department_id, os_version_id,
        status=StpMatrixPublicationStatus.POSTED, identity=identity,
        confluence_page_id=page_id, confluence_parent_page_id=parent_id, body_snapshot=body_html,
    )
