"""HR-отчёт по активности отдела (§2.7, §9.1 плана миграции).

Перенос легаси `allta_app/reports/departament_reports/libreport.py`
(`MonthlyReport`/`ReportGit`/`ReportJira`/`ReportTempo`/`ReportToConfluence`):
сводка по трём источникам (коммиты Bitbucket, комментарии Jira по спринтам
доски, часы/задачи Tempo) за календарный месяц, публикуется на Confluence
как HTML-таблица с подсветкой дней простоя.

**pandas сознательно не используется.** Легаси активно применял
`pandas.DataFrame`/`pivot_table`/`MultiIndex` для сведения — но
`testing_service` этой зависимости нигде больше не тянет, а объём данных
тривиален (десятки дней, единицы-десятки сотрудников): обычные
`dict[member_id][day]` дают тот же результат без лишней тяжёлой зависимости
в `pyproject.toml`. HTML-таблица собирается f-строками напрямую по той же
причине — рендер таблицы с двухуровневым заголовком не настолько сложен,
чтобы оправдать `beautifulsoup4` (в этом сервисе он тоже не используется).

**Частичный провал одного источника не рушит весь отчёт.** Если, например,
Tempo недоступен — отчёт всё равно публикуется (нулевые часы/задачи по всем
сотрудникам), а причина попадает в `error` результата как заметка ("tempo:
..."). Единственный источник, чей провал делает генерацию `failed` целиком —
сама публикация в Confluence (без неё отчёта попросту нет, ради чего вся
остальная работа) и отсутствие обязательной конфигурации
(`confluence_report_page_space`/`confluence_base_url`/`credential_id`).

**Bitbucket работает и без своего credential'а.** Легаси ходил в Bitbucket с
пустым паролем (`monthly_report.py:23` `PASSWORD = ''`), поэтому отсутствие
`bitbucket_credential_id` здесь не отключает источник, а переводит его в
анонимный режим. Если анонимно не пускают — причина уходит в `warnings`
заметкой "bitbucket (anonymous): ...", а не растворяется в нулях по всем
сотрудникам и сплошной подсветке простоя.

`credential_id` остаётся обязательным и используется как раньше — для
Jira-комментариев и Tempo-worklog'ов (`_resolve_jira_secret`). Публикация в
Confluence (`_resolve_confluence_secret`, C4) предпочитает отдельный
`confluence_credential_id`, если отдел его завёл; иначе падает обратно на
тот же `credential_id` — прежнее поведение с общей учёткой Jira+Confluence.
"""

from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, DepartmentActivityReportStatus, EntityType
from src.core.exceptions import AppException, DomainValidationError
from src.dependencies.auth import Identity
from src.models import DepartmentActivityReport, DepartmentIntegrationSettings, DepartmentReportMember
from src.repositories import department_activity_report as report_repo
from src.repositories import department_integration_settings as dis_repo
from src.repositories import department_report_member as member_repo
from src.repositories import department_test_settings as dts_repo
from src.services import audit_service, bitbucket_client, confluence_client, jira_report_client, permissions
from src.services import secret_client, tempo_client
from src.utils.ids import department_activity_report_id as new_report_id

logger = logging.getLogger(__name__)

# Подпись дня и порядок метрик — буквально легаси: `libreport.py:495`
# (`strftime('%Y-%m-%d--%a')`, англ. сокращение из C-локали) и `libreport.py:522`
# (`expected_metrics_order`). Отличаться здесь незачем: страницу читают те же
# люди, что читали легаси-отчёт.
_WEEKDAY_EN = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_METRIC_LABELS = ("Комментарии в Jira", "Коммиты в Bitbucket", "Часы в Tempo", "Задачи в Tempo")

_INTRO_HTML = (
    "<h1>Активность сотрудников отдела</h1>"
    "<p>Настоящий отчёт представляет собой систематизированную сводку ежедневной "
    "активности сотрудников, включающую следующие показатели:</p>"
    "<ul>"
    "<li>Количество комментариев к задачам в системе управления проектами <strong>Jira</strong>;</li>"
    "<li>Число коммитов каждого сотрудника в репозитории системы контроля версий <strong>Bitbucket</strong>.</li>"
    "<li>Суммарное количество отработанного времени за день по всем задачам <strong>Tempo</strong>.</li>"
    "<li>Задачи, на которые списано время за день, <strong>Tempo</strong>.</li>"
    "</ul>"
    "<p>Отсутствие зафиксированной активности сотрудника по всем показателям (Jira и Bitbucket) "
    'в течение дня обозначается выделением соответствующих ячеек фоновым оттенком '
    '<span style="background-color: #ffffe1; color: #fe5555;">жёлтого цвета</span>.</p>'
)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def month_business_days(period: str) -> list[date]:
    """Календарные дни `period` ("YYYY-MM") без суббот/воскресений."""
    year, month = (int(part) for part in period.split("-"))
    _, days_in_month = calendar.monthrange(year, month)
    all_days = [date(year, month, day) for day in range(1, days_in_month + 1)]
    return [d for d in all_days if d.weekday() < 5]


def _empty_metrics(members: list[DepartmentReportMember], days: list[date]) -> dict:
    return {
        member.id: {
            day.isoformat(): {"bitbucket": 0, "jira": 0, "tempo_hours": 0.0, "tempo_tasks": []}
            for day in days
        }
        for member in members
    }


def _apply_bitbucket_commits(
    metrics: dict, members: list[DepartmentReportMember], commits: list[dict], day_set: set[str],
) -> None:
    by_username = {m.bitbucket_username: m for m in members if m.bitbucket_username}
    if not by_username:
        return
    for commit in commits:
        author = (commit.get("author") or {}).get("name")
        member = by_username.get(author)
        if member is None:
            continue
        ts_ms = commit.get("authorTimestamp")
        if ts_ms is None:
            continue
        try:
            day_key = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            continue
        if day_key in day_set:
            metrics[member.id][day_key]["bitbucket"] += 1


async def _fetch_all_commits(
    settings: DepartmentIntegrationSettings, username: str | None, password: str | None,
) -> list[dict]:
    branches = await bitbucket_client.get_branches(
        base_url=settings.bitbucket_base_url, username=username, password=password,
        project_key=settings.bitbucket_project_key, repo_slug=settings.bitbucket_repo_slug,
    )
    commits: list[dict] = []
    for branch in branches:
        commits.extend(await bitbucket_client.get_commits(
            base_url=settings.bitbucket_base_url, username=username, password=password,
            project_key=settings.bitbucket_project_key, repo_slug=settings.bitbucket_repo_slug,
            branch=branch,
        ))
    return commits


async def _fetch_sprint_issues(
    settings: DepartmentIntegrationSettings, jira_token: str, period: str,
) -> list[dict]:
    year, month = (int(part) for part in period.split("-"))
    sprint_ids = await jira_report_client.get_sprint_ids(
        base_url=settings.jira_base_url, jira_token=jira_token, board_id=settings.jira_board_id,
    )
    matched: list[dict] = []
    for sprint_id in sprint_ids:
        details = await jira_report_client.get_sprint_details(
            base_url=settings.jira_base_url, jira_token=jira_token, sprint_id=sprint_id,
        )
        if not jira_report_client.sprint_matches_month(details, year, month):
            continue
        matched.extend(await jira_report_client.search_sprint_issues(
            base_url=settings.jira_base_url, jira_token=jira_token, sprint_id=sprint_id,
        ))
    return matched


async def _apply_jira_comments(
    metrics: dict, members: list[DepartmentReportMember], settings: DepartmentIntegrationSettings,
    jira_token: str, issues: list[dict], day_set: set[str], warnings: list[str],
) -> None:
    by_author = {m.jira_author_name: m for m in members if m.jira_author_name}
    if not by_author:
        return
    for issue in issues:
        issue_key = issue.get("key")
        if not issue_key:
            continue
        try:
            comments = await jira_report_client.get_issue_comments(
                base_url=settings.jira_base_url, jira_token=jira_token, issue_key=issue_key,
            )
        except AppException as exc:
            warnings.append(f"jira: comments for {issue_key} failed: {exc.message}")
            continue
        for comment in comments:
            author_name = (comment.get("author") or {}).get("displayName")
            member = by_author.get(author_name)
            if member is None:
                continue
            created = comment.get("created") or ""
            day_key = created.split("T")[0]
            if day_key in day_set:
                metrics[member.id][day_key]["jira"] += 1


def _apply_tempo_worklogs(
    metrics: dict, members: list[DepartmentReportMember], worklogs: list[dict], day_set: set[str],
) -> None:
    by_worker = {m.jira_tempo_worker_key: m for m in members if m.jira_tempo_worker_key}
    if not by_worker:
        return
    for entry in worklogs:
        member = by_worker.get(entry.get("worker"))
        if member is None:
            continue
        started = entry.get("started") or ""
        day_key = started.split(" ")[0].split("T")[0]
        if day_key not in day_set:
            continue
        seconds = entry.get("timeSpentSeconds") or 0
        metrics[member.id][day_key]["tempo_hours"] = round(
            metrics[member.id][day_key]["tempo_hours"] + seconds / 3600, 2,
        )
        task_key = (entry.get("issue") or {}).get("key")
        if task_key and task_key not in metrics[member.id][day_key]["tempo_tasks"]:
            metrics[member.id][day_key]["tempo_tasks"].append(task_key)


async def _resolve_jira_secret(settings: DepartmentIntegrationSettings) -> str:
    """Секрет `credential_id` — Jira-комментарии и Tempo-worklog'и (не тронуто C4).

    Поднимает `AppException` наружу (не best-effort) — без этого секрета
    отчёт в любом случае некуда публиковать, значит вся генерация проваливается.
    """
    if not settings.credential_id:
        raise DomainValidationError(
            error_code="ACTIVITY_REPORT_NOT_CONFIGURED",
            message="department_integration_settings.credential_id is not configured",
        )
    _login, secret = await secret_client.reveal_credential(settings.credential_id)
    if not secret:
        raise DomainValidationError(
            error_code="ACTIVITY_REPORT_NOT_CONFIGURED",
            message="reveal_credential returned an empty secret for credential_id",
        )
    return secret


async def _resolve_confluence_secret(settings: DepartmentIntegrationSettings, jira_secret: str) -> str:
    """Секрет для публикации отчёта в Confluence (C4).

    Предпочитает отдельный `confluence_credential_id`; если отдел его не
    завёл — переиспользует уже раскрытый `jira_secret` (прежняя общая учётка,
    без лишнего похода в secret_service). Провал раскрытия здесь фатален,
    как и для Jira, — без Confluence-секрета публиковать отчёт некуда.
    """
    if not settings.confluence_credential_id:
        return jira_secret
    _login, secret = await secret_client.reveal_credential(settings.confluence_credential_id)
    if not secret:
        raise DomainValidationError(
            error_code="ACTIVITY_REPORT_NOT_CONFIGURED",
            message="reveal_credential returned an empty secret for confluence_credential_id",
        )
    return secret


async def collect_activity(
    settings: DepartmentIntegrationSettings, jira_token: str, period: str,
    days: list[date], members: list[DepartmentReportMember],
) -> tuple[dict, list[str]]:
    """Сводит три источника в `{member_id: {day: {...}}}`. Возвращает `(metrics, warnings)`.

    `warnings` — человекочитаемые заметки о частичных провалах отдельных
    источников (недоступность, неполная конфигурация) — не исключения,
    вызывающий код (`generate_report`) кладёт их в `error` результата, но
    отчёт всё равно публикуется с нулями по недоступному источнику.
    """
    warnings: list[str] = []
    metrics = _empty_metrics(members, days)
    day_set = {d.isoformat() for d in days}

    if settings.bitbucket_base_url and settings.bitbucket_project_key and settings.bitbucket_repo_slug:
        # `bitbucket_credential_id` не обязателен: легаси считал коммиты с
        # пустым паролем (`monthly_report.py:23`), то есть без секрета вообще.
        # Отдел с репозиторием, открытым на чтение, получает те же цифры, а не
        # молчаливые нули у всех и сплошную подсветку простоя.
        anonymous = not settings.bitbucket_credential_id
        prefix = "bitbucket (anonymous)" if anonymous else "bitbucket"
        try:
            bb_login: str | None = None
            bb_secret: str | None = None
            if not anonymous:
                bb_login, bb_secret = await secret_client.reveal_credential(settings.bitbucket_credential_id)
            commits = await _fetch_all_commits(settings, bb_login, bb_secret)
            _apply_bitbucket_commits(metrics, members, commits, day_set)
        except AppException as exc:
            warnings.append(f"{prefix}: {exc.message}")
        except Exception as exc:  # noqa: BLE001 — источник best-effort, не должен ронять весь отчёт
            logger.warning("activity_report: bitbucket collection failed: %s", exc)
            warnings.append(f"{prefix}: {type(exc).__name__}")
    else:
        warnings.append("bitbucket: department_integration_settings incomplete")

    issues: list[dict] = []
    if settings.jira_base_url and settings.jira_board_id:
        try:
            issues = await _fetch_sprint_issues(settings, jira_token, period)
        except AppException as exc:
            warnings.append(f"jira: {exc.message}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("activity_report: jira sprint collection failed: %s", exc)
            warnings.append(f"jira: {type(exc).__name__}")
    else:
        warnings.append("jira: department_integration_settings incomplete (jira_base_url/jira_board_id)")
    if issues:
        await _apply_jira_comments(metrics, members, settings, jira_token, issues, day_set, warnings)

    if settings.tempo_team_id:
        try:
            worklogs = await tempo_client.search_worklogs(
                base_url=settings.jira_base_url, jira_token=jira_token,
                team_id=settings.tempo_team_id, month=period,
            )
            _apply_tempo_worklogs(metrics, members, worklogs, day_set)
        except AppException as exc:
            warnings.append(f"tempo: {exc.message}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("activity_report: tempo collection failed: %s", exc)
            warnings.append(f"tempo: {type(exc).__name__}")
    else:
        warnings.append("tempo: tempo_team_id is not configured")

    return metrics, warnings


def render_report_html(
    *, days: list[date], members: list[DepartmentReportMember], metrics: dict, jira_base_url: str | None,
) -> str:
    """HTML-таблица (двухуровневый заголовок сотрудник×метрика) + подсветка простоя.

    Идентична легаси по содержанию: тот же порядок метрик (`_METRIC_LABELS`),
    та же подпись дня (`YYYY-MM-DD--Fri`) и та же подсветка
    `#ffffe1`/`#fe5555` на Jira- и Bitbucket-ячейках дня, если обе равны 0.
    Ссылка на задачу вместо Jira-макроса легаси (см. module docstring
    `services/confluence_client.py`).
    """
    if not members:
        return _INTRO_HTML + "<p><em>В отделе нет активных сотрудников, включённых в отчёт.</em></p>"

    header_top = "<tr><th rowspan=\"2\"></th>" + "".join(
        f'<th colspan="4" style="text-align:center;">{_escape(m.display_name)}</th>' for m in members
    ) + "</tr>"
    header_sub = "<tr>" + "".join(
        f'<th style="text-align:center;">{label}</th>' for _m in members for label in _METRIC_LABELS
    ) + "</tr>"

    body_rows: list[str] = []
    for day in days:
        day_key = day.isoformat()
        cells = [f"<td>{day_key}--{_WEEKDAY_EN[day.weekday()]}</td>"]
        for member in members:
            cell = metrics[member.id][day_key]
            idle = cell["bitbucket"] == 0 and cell["jira"] == 0
            idle_style = (
                ' style="text-align:center; background-color: #ffffe1; color: #fe5555;"'
                if idle else ' style="text-align:center;"'
            )
            tasks_html = ", ".join(
                f'<a href="{jira_base_url}/browse/{_escape(task)}">{_escape(task)}</a>'
                if jira_base_url else _escape(task)
                for task in cell["tempo_tasks"]
            ) or "0"
            cells.append(f'<td{idle_style}>{cell["jira"]}</td>')
            cells.append(f'<td{idle_style}>{cell["bitbucket"]}</td>')
            cells.append(f'<td style="text-align:center;">{cell["tempo_hours"]:g}</td>')
            cells.append(f'<td style="text-align:center;">{tasks_html}</td>')
        body_rows.append("<tr>" + "".join(cells) + "</tr>")

    table = (
        '<table style="border-collapse: collapse;" border="1">'
        f"<thead>{header_top}{header_sub}</thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table>"
    )
    return _INTRO_HTML + table


async def _publish_page(
    settings: DepartmentIntegrationSettings, secret: str, period: str, html: str,
) -> str:
    """Найти-или-создать месячную страницу под родителем отдела, затем обновить её тело.

    Перенос легаси `ReportToConfluence.create_confluence_page`/
    `update_confluence_page` — легаси тоже сперва проверяет существование
    (`page_exists`), затем создаёт либо обновляет, никогда не создаёт дубль.
    """
    base_url = settings.confluence_base_url
    space = settings.confluence_report_page_space
    existing_id = await confluence_client.find_page_id(
        base_url=base_url, bearer_token=secret, space=space, title=period,
    )
    if existing_id is None:
        parent_id = None
        if settings.confluence_report_parent_page_title:
            parent_id = await confluence_client.find_page_id(
                base_url=base_url, bearer_token=secret, space=space,
                title=settings.confluence_report_parent_page_title,
            )
        return await confluence_client.create_page(
            base_url=base_url, bearer_token=secret, space=space, title=period,
            parent_id=parent_id, body_html=html,
        )
    version = await confluence_client.get_page_version(
        base_url=base_url, bearer_token=secret, page_id=existing_id,
    )
    await confluence_client.update_page(
        base_url=base_url, bearer_token=secret, page_id=existing_id, title=period,
        body_html=html, version=version,
    )
    return existing_id


async def _generate_report_impl(
    db: AsyncSession, department_id: str, period: str, *, generated_by: str | None, trigger: str,
) -> DepartmentActivityReport:
    """Общее тело генерации, без RBAC — вызывающая сторона решает, кто может её звать.

    `generated_by` — `identity.user_id` для ручного запуска, `None` для
    автоматического (нет пользователя-инициатора). `trigger` попадает в
    audit-детали ("manual"/"auto"), чтобы отличить кнопку от cron'а в логе.
    """
    settings = await dis_repo.get_by_department(db, department_id)
    if settings is None or not settings.confluence_report_page_space or not settings.confluence_base_url:
        audit_service.emit(
            "department_activity_report.generate",
            target_type="department_activity_report",
            status="failure", allowed=True,
            details={"department_id": department_id, "period": period, "reason": "not_configured", "trigger": trigger},
        )
        raise DomainValidationError(
            error_code="ACTIVITY_REPORT_NOT_CONFIGURED",
            message=(
                "Activity reports are not configured for this department "
                "(confluence_report_page_space/confluence_base_url missing)"
            ),
        )

    report = await report_repo.create(db, {
        "id": new_report_id(),
        "department_id": department_id,
        "period": period,
        "generated_by": generated_by,
        "status": DepartmentActivityReportStatus.GENERATING,
    })
    await db.commit()
    await db.refresh(report)

    try:
        jira_secret = await _resolve_jira_secret(settings)
        confluence_secret = await _resolve_confluence_secret(settings, jira_secret)
        members = await member_repo.list_active_by_department(db, department_id)
        days = month_business_days(period)
        metrics, warnings = await collect_activity(settings, jira_secret, period, days, members)
        html = render_report_html(days=days, members=members, metrics=metrics, jira_base_url=settings.jira_base_url)
        page_id = await _publish_page(settings, confluence_secret, period, html)

        await report_repo.update(db, report, {
            "status": DepartmentActivityReportStatus.DONE,
            "confluence_page_id": page_id,
            "error": "; ".join(warnings) if warnings else None,
        })
        await db.commit()
        await db.refresh(report)
        audit_service.emit(
            "department_activity_report.generate",
            target_id=report.id, target_type="department_activity_report",
            status="success", allowed=True,
            details={"department_id": department_id, "period": period, "warnings": warnings, "trigger": trigger},
        )
    except AppException as exc:
        logger.warning(
            "activity_report.generate failed for dept=%s period=%s: %s", department_id, period, exc.message,
        )
        await report_repo.update(db, report, {
            "status": DepartmentActivityReportStatus.FAILED, "error": exc.message[:1024],
        })
        await db.commit()
        await db.refresh(report)
        audit_service.emit(
            "department_activity_report.generate",
            target_id=report.id, target_type="department_activity_report",
            status="failure", allowed=True,
            details={"department_id": department_id, "period": period, "error_code": exc.error_code, "trigger": trigger},
        )
    return report


async def generate_report(
    db: AsyncSession, identity: Identity, department_id: str, period: str,
) -> DepartmentActivityReport:
    """Ручная генерация HR-отчёта отдела за `period` (`POST .../activity-reports/generate`).

    Заменяет легаси-паттерн "поправить MONTH в коде и перезапустить скрипт".
    RBAC — `permissions.require_department_action`: department_admin своего
    отдела ИЛИ носитель `admin` service-роли `testing_service` в этом же
    отделе, cross-department вызов отбивается ещё до чтения настроек.
    """
    await permissions.require_department_action(
        db, identity, department_id, EntityType.DEPARTMENT_ACTIVITY_REPORT, Action.CREATE,
    )
    return await _generate_report_impl(db, department_id, period, generated_by=identity.user_id, trigger="manual")


async def generate_report_auto(db: AsyncSession, department_id: str, period: str) -> DepartmentActivityReport:
    """Системная генерация из фоновой ежемесячной проверки — без RBAC.

    Вызывается только из `run_auto_generate_tick`, никогда напрямую по HTTP:
    инициатор здесь не пользователь, а cron, поэтому `require_department_action`
    не применим (ему нужен реальный `identity`) и не нужен — доступ к этой
    функции сам по себе ограничен тем, что она не выставлена ни одним роутом.
    """
    return await _generate_report_impl(db, department_id, period, generated_by=None, trigger="auto")


def previous_month_period(now: datetime) -> str:
    """`'YYYY-MM'` календарного месяца, предшествующего `now`."""
    year, month = now.year, now.month
    if month == 1:
        year, month = year - 1, 12
    else:
        month -= 1
    return f"{year:04d}-{month:02d}"


async def run_auto_generate_tick(db: AsyncSession, now_msk: datetime) -> list[str]:
    """Один проход фоновой авто-генерации (задача 9 — "1 октября → отчёт за сентябрь").

    Срабатывает только 1 числа месяца (по МСК); отчёт заводится за предыдущий
    календарный месяц для отделов с `department_test_settings.activity_report_auto_generate=True`.
    Идемпотентно: если отчёт за этот `(department_id, period)` уже заводился
    (в том числе неудачно), повторной генерации не будет — если фоновая
    проверка сработала дважды за тот же день, ничего не задублируется.
    Возвращает id отделов, для которых в этом проходе реально запустили
    генерацию (используется тестами и для лога).
    """
    if now_msk.day != 1:
        return []

    period = previous_month_period(now_msk)
    department_ids = await dts_repo.list_auto_generate_department_ids(db)
    processed: list[str] = []
    for department_id in department_ids:
        if await report_repo.exists_for_period(db, department_id, period):
            continue
        try:
            await generate_report_auto(db, department_id, period)
        except AppException as exc:
            await db.rollback()
            logger.warning(
                "activity_report auto-generate skipped dept=%s period=%s: %s",
                department_id, period, exc.message,
            )
            continue
        except Exception as exc:  # noqa: BLE001 — один сбойный отдел не должен ронять весь тик
            # `db` — общая сессия на весь тик (по одной на отдел было бы
            # избыточно); rollback обязателен, иначе транзакция остаётся
            # aborted и следующий отдел в этом же цикле упадёт на первом же
            # запросе.
            await db.rollback()
            logger.warning(
                "activity_report auto-generate unexpected error dept=%s period=%s: %s: %s",
                department_id, period, type(exc).__name__, exc,
            )
            continue
        processed.append(department_id)
    return processed


async def list_reports(
    db: AsyncSession, identity: Identity, department_id: str, *, limit: int, offset: int,
) -> tuple[list[DepartmentActivityReport], int]:
    """`GET .../activity-reports` — история генераций отдела. Тот же RBAC-гейт, что и generate."""
    await permissions.require_department_action(
        db, identity, department_id, EntityType.DEPARTMENT_ACTIVITY_REPORT, Action.VIEW,
    )
    items = await report_repo.list_by_department(db, department_id, limit=limit, offset=offset)
    total = await report_repo.count_by_department(db, department_id)
    return items, total
