from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import click

from allta_cli.utils import ui
from allta_cli.utils import auth as auth_utils
from allta_cli.utils.lazy import requests
from allta_cli.utils.auth import AuthError, NotAuthenticatedError, TokenExpiredError
from allta_cli.utils.config import (
    JIRA_AUTH_MODE,
    JIRA_BASE_URL,
    JIRA_BOARD_ID,
    JIRA_EPIC_LINK_FIELD,
    JIRA_ISSUE_TYPE_ID,
    JIRA_ISSUE_TYPE_NAME,
    JIRA_PRIORITY_ID,
    JIRA_PROJECT_KEY,
    JIRA_SERVICE_USERS,
    JIRA_STORY_POINTS_FIELD,
)
from allta_cli.utils.config_api import ConfigApiError, get_token_credential


SERVICE_COMPONENT = "НТ. Sprint backlog"
TESTCASE_COMPONENTS = ("НТ. Sprint backlog", "НТ. main Backlog")
TESTCASE_COMPONENT_ALIASES = {
    "НТ. main Backlog": "Нагрузочное тестирование",
}


class JiraError(RuntimeError):
    pass


@dataclass(frozen=True)
class JiraTaskSpec:
    summary: str
    component: str
    assignee: str
    story_points: float | int | None = None
    epic_key: str | None = None
    priority_id: str | None = None


@dataclass(frozen=True)
class JiraCredentials:
    username: str
    token: str


class JiraClient:
    def __init__(self, *, base_url: str, username: str, token: str, auth_mode: str, board_id: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username.strip()
        self.token = token.strip()
        self.auth_mode = auth_mode.strip().lower()
        self.board_id = board_id
        if self.auth_mode not in {"bearer", "basic"}:
            raise JiraError("JIRA_AUTH_MODE должен быть 'bearer' или 'basic'.")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.auth_mode == "bearer":
            token = self.token
            if not token.lower().startswith("bearer "):
                token = f"Bearer {token}"
            headers["Authorization"] = token
        return headers

    def _auth(self) -> tuple[str, str] | None:
        if self.auth_mode == "basic":
            return self.username, self.token
        return None

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> requests.Response:
        url = f"{self.base_url}{path}"
        ui.http(f"{method.upper()} {url}")
        try:
            resp = requests.request(
                method,
                url,
                headers=self._headers(),
                auth=self._auth(),
                json=json,
                timeout=timeout,
            )
        except requests.RequestException as e:
            raise JiraError(f"Не удалось выполнить запрос к Jira: {e}") from e
        if resp.status_code >= 400:
            raise JiraError(_format_jira_error(resp))
        return resp

    def create_issue(self, spec: JiraTaskSpec) -> str:
        payload = _issue_payload(spec)
        data = self._json(self._request("POST", "/rest/api/2/issue", json=payload), context="создание задачи")
        issue_key = _issue_key(data)
        if spec.story_points is not None:
            self.update_story_points(issue_key, spec.story_points)
        return issue_key

    def update_story_points(self, issue_key: str, story_points: float | int) -> None:
        if self.board_id:
            self._request(
                "PUT",
                f"/rest/agile/1.0/issue/{issue_key}/estimation?boardId={self.board_id}",
                json={"value": str(story_points)},
            )
        else:
            self._request("PUT", f"/rest/api/2/issue/{issue_key}", json={"fields": {JIRA_STORY_POINTS_FIELD: story_points}})

    @staticmethod
    def _json(resp: requests.Response, *, context: str) -> Any:
        try:
            return resp.json()
        except ValueError as e:
            raise JiraError(f"Jira вернула не-JSON при операции: {context}.") from e


def service_cmd(*, sprint: int | None = None, assignee: str | None = None, dry_run: bool = False) -> int:
    sprint_number = sprint if sprint is not None else click.prompt("Номер спринта", type=int)
    if sprint_number <= 0:
        ui.err("Номер спринта должен быть положительным числом.")
        return 1

    creds = _jira_credentials()
    users = _load_jira_users()
    default_assignee = creds.username if creds.username in users else (users[0] if users else "")

    if assignee:
        chosen = _resolve_assignee(assignee, users)
    else:
        chosen = _prompt_assignee("Исполнитель для service-задач", users, default=default_assignee)

    specs = build_service_tasks(sprint_number=sprint_number, assignee=chosen)
    return _run_task_batch(specs, dry_run=dry_run, creds=creds)


def testcase_cmd(
    *,
    epic: str | None = None,
    name: str | None = None,
    component: str | None = None,
    component_per_task: bool = False,
    assignee: str | None = None,
    assignee_per_task: bool = False,
    dry_run: bool = False,
) -> int:
    epic_key = _normalize_epic_key(epic or click.prompt("Код эпика, можно только цифры после DEVQA-", type=str))
    task_name = (name or click.prompt("Название задачи", type=str)).strip()
    if not task_name:
        ui.err("Название задачи не должно быть пустым.")
        return 1

    creds = _jira_credentials()
    users = _load_jira_users()
    specs = build_testcase_tasks(
        task_name=task_name,
        epic_key=epic_key,
        component=component,
        component_per_task=component_per_task,
        assignee=assignee,
        assignee_per_task=assignee_per_task,
        users=users,
        default_assignee=creds.username if creds.username in users else (users[0] if users else None),
    )
    return _run_task_batch(specs, dry_run=dry_run, creds=creds)


def _load_jira_users() -> list[str]:
    """Возвращает список логинов из auth API с отфильтрованными служебными аккаунтами,
    отсортированный по login. На ошибку API падает с JiraError."""
    try:
        users = auth_utils.list_users(verbose=False)
    except (AuthError, NotAuthenticatedError, TokenExpiredError) as e:
        raise JiraError(f"Не удалось получить список пользователей из auth API: {e}") from e
    logins: list[str] = []
    for u in users:
        login = str(u.get("login") or "").strip()
        if not login or login in JIRA_SERVICE_USERS:
            continue
        logins.append(login)
    logins.sort(key=str.lower)
    if not logins:
        raise JiraError("Список пользователей пуст после фильтрации служебных аккаунтов.")
    return logins


def build_service_tasks(*, sprint_number: int, assignee: str) -> list[JiraTaskSpec]:
    return [
        JiraTaskSpec(f"Спринт_{sprint_number}. Daily", SERVICE_COMPONENT, assignee, 2, priority_id=JIRA_PRIORITY_ID),
        JiraTaskSpec(f"Спринт_{sprint_number - 1}. Review", SERVICE_COMPONENT, assignee, 1, priority_id=JIRA_PRIORITY_ID),
        JiraTaskSpec(f"Спринт_{sprint_number + 1} Планирование", SERVICE_COMPONENT, assignee, 3, priority_id=JIRA_PRIORITY_ID),
        JiraTaskSpec(f"Спринт_{sprint_number - 1} Ретроспектива", SERVICE_COMPONENT, assignee, 1, priority_id=JIRA_PRIORITY_ID),
    ]


def build_testcase_tasks(
    *,
    task_name: str,
    epic_key: str,
    component: str | None,
    component_per_task: bool,
    assignee: str | None,
    assignee_per_task: bool,
    users: list[str],
    default_assignee: str | None = None,
) -> list[JiraTaskSpec]:
    all_summaries: list[tuple[str, float | int | None]] = [
        (f"НТ. {task_name}. Подготовка окружения.", None),
        (f"НТ. {task_name}. Нагрузочный скрипт.", None),
        (f"НТ. {task_name}. Обработка результатов.", None),
        (f"НТ. {task_name}. Выкладка результатов на Life.", 2),
        (f"НТ. {task_name}. Написать README + выложить описание на Life.", 2),
        (f"НТ. {task_name}. Добавить тесткейс в BT", 0.5),
        (f"НТ. {task_name}. Добавить в статистику", None),
        (f"НТ. {task_name}. Добавить в allta", 2),
        (f"НТ. {task_name}. Итоговое тестирование.", 2),
    ]

    summaries_and_points = _prompt_task_selection(all_summaries)

    if component and component_per_task:
        raise click.UsageError("Нельзя одновременно использовать --component и --component-per-task.")
    if component:
        component_mode = "all"
        default_component = _resolve_component(component)
    elif component_per_task:
        component_mode = "per_task"
        default_component = None
    else:
        component_mode = _prompt_mode("Компоненты", "Назначить один компонент всем задачам", "Настроить компонент точечно")
        default_component = _prompt_component("Компонент для всех задач") if component_mode == "all" else None

    chosen_assignee = _resolve_assignee(assignee, users) if assignee and not assignee_per_task else None
    if assignee and assignee_per_task:
        raise click.UsageError("Нельзя одновременно использовать --assignee и --assignee-per-task.")
    if assignee_per_task:
        assignee_mode = "per_task"
    elif assignee:
        assignee_mode = "all"
    else:
        assignee_mode = _prompt_mode("Исполнители", "Назначить все задачи одному пользователю", "Настроить исполнителей точечно")
        if assignee_mode == "all":
            chosen_assignee = _prompt_assignee("Исполнитель для всех задач", users, default=default_assignee)

    per_task = component_mode == "per_task" or assignee_mode == "per_task"
    both_per_task = component_mode == "per_task" and assignee_mode == "per_task"
    total = len(summaries_and_points)

    specs: list[JiraTaskSpec] = []
    for idx, (summary, story_points) in enumerate(summaries_and_points, start=1):
        if per_task:
            _print_task_header(idx, summary, total)

        if component_mode == "per_task":
            task_component = _prompt_component("Компонент")
        else:
            task_component = default_component

        if both_per_task:
            _print_minor_sep()

        if assignee_mode == "per_task":
            task_assignee = _prompt_assignee("Исполнитель", users, default=default_assignee)
        else:
            task_assignee = chosen_assignee

        specs.append(
            JiraTaskSpec(
                summary=summary,
                component=task_component or SERVICE_COMPONENT,
                assignee=task_assignee or (users[0] if users else ""),
                story_points=story_points,
                epic_key=epic_key,
            )
        )
    return specs


def _run_task_batch(specs: list[JiraTaskSpec], *, dry_run: bool, creds: JiraCredentials) -> int:
    _print_preview(specs)
    if dry_run:
        ui.ok("Dry-run: задачи не создавались.")
        return 0

    if not click.confirm(f"Создать задач: {len(specs)}?", default=True):
        ui.warn("Создание отменено.")
        return 0

    try:
        client = JiraClient(
            base_url=JIRA_BASE_URL,
            username=creds.username,
            token=creds.token,
            auth_mode=JIRA_AUTH_MODE,
            board_id=JIRA_BOARD_ID,
        )
        rows: list[list[object]] = []
        for spec in specs:
            key = client.create_issue(spec)
            rows.append([key, spec.summary])
            ui.ok(f"Создана задача {key}: {spec.summary}")
    except JiraError as e:
        ui.err(f"Ошибка Jira: {e}")
        return 1

    ui.table(headers=["Issue", "Summary"], rows=rows)
    return 0


def _issue_payload(spec: JiraTaskSpec) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "project": {"key": JIRA_PROJECT_KEY},
        "summary": spec.summary,
        "issuetype": {"id": JIRA_ISSUE_TYPE_ID} if JIRA_ISSUE_TYPE_ID else {"name": JIRA_ISSUE_TYPE_NAME},
        "components": [{"name": _jira_component(spec.component)}],
        "assignee": {"name": spec.assignee},
    }
    if spec.priority_id:
        fields["priority"] = {"id": spec.priority_id}
    if spec.epic_key:
        fields[JIRA_EPIC_LINK_FIELD] = spec.epic_key
    return {"fields": fields}


def _issue_key(data: Any) -> str:
    if isinstance(data, dict) and isinstance(data.get("key"), str) and data["key"].strip():
        return data["key"].strip()
    raise JiraError("Jira вернула ответ без ключа созданной задачи.")


def _format_jira_error(resp: requests.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        detail = (resp.text or "").strip()
        return f"HTTP {resp.status_code}: {detail or resp.reason}"

    parts: list[str] = []
    if isinstance(data, dict):
        errors = data.get("errors")
        messages = data.get("errorMessages")
        if isinstance(messages, list):
            parts.extend(str(item) for item in messages)
        if isinstance(errors, dict):
            parts.extend(f"{key}: {value}" for key, value in errors.items())
    return f"HTTP {resp.status_code}: {'; '.join(parts) if parts else data}"


def _jira_credentials() -> JiraCredentials:
    return JiraCredentials(
        username=_credential_from_allta_token("username"),
        token=_credential_from_allta_token("jira_token"),
    )


def _credential_from_allta_token(token_key: str) -> str:
    try:
        return get_token_credential(token_key)["token"]
    except (ConfigApiError, NotAuthenticatedError, TokenExpiredError, AuthError) as e:
        raise JiraError(f"Не удалось получить Jira credential '{token_key}': {e}") from e


def _normalize_epic_key(value: str) -> str:
    text = value.strip().upper()
    if not text:
        raise click.UsageError("Код эпика не должен быть пустым.")
    if text.isdigit():
        return f"{JIRA_PROJECT_KEY}-{text}"
    if "-" not in text:
        return f"{JIRA_PROJECT_KEY}-{text}"
    return text


def _resolve_component(value: str) -> str:
    normalized = value.strip()
    if normalized in {"1", "sprint", "Sprint", "НТ. Sprint backlog"}:
        return TESTCASE_COMPONENTS[0]
    if normalized in {"2", "main", "Main", "load", "Load", "НТ. main Backlog", "Нагрузочное тестирование"}:
        return TESTCASE_COMPONENTS[1]
    if normalized in TESTCASE_COMPONENTS:
        return normalized
    raise click.UsageError("Компонент должен быть 1/2 или одним из поддержанных названий.")


def _prompt_component(label: str) -> str:
    ui.echo(f"{label}: 1) {TESTCASE_COMPONENTS[0]}  2) {TESTCASE_COMPONENTS[1]}")
    return _resolve_component(click.prompt("Выбор", type=click.Choice(["1", "2"]), default="1", show_default=True))


def _resolve_assignee(value: str, users: list[str]) -> str:
    if not users:
        raise click.UsageError("Список пользователей пуст — некого назначить исполнителем.")
    normalized = value.strip()
    if not normalized:
        raise click.UsageError("Исполнитель не должен быть пустым.")
    if normalized.isdigit():
        idx = int(normalized)
        if 1 <= idx <= len(users):
            return users[idx - 1]
        raise click.UsageError(f"Номер исполнителя должен быть от 1 до {len(users)}.")
    lookup = {u.lower(): u for u in users}
    real = lookup.get(normalized.lower())
    if real:
        return real
    allowed = ", ".join(users)
    raise click.UsageError(f"Неизвестный исполнитель '{normalized}'. Доступны: {allowed}.")


_SEP_TASK = "─" * 44
_SEP_MINOR = "  · · · · ·"


def _prompt_task_selection(
    summaries_and_points: list[tuple[str, float | int | None]],
) -> list[tuple[str, float | int | None]]:
    ui.echo("")
    ui.echo("Задачи:")
    for i, (summary, sp) in enumerate(summaries_and_points, start=1):
        sp_str = f" [{sp} SP]" if sp is not None else ""
        ui.echo(f"  {i}) {summary}{sp_str}")
    ui.echo("")

    mode = _prompt_mode("Создать", "Все задачи", "Выбрать задачи")
    if mode == "all":
        return list(summaries_and_points)

    raw = click.prompt("Номера задач через пробел или запятую (например: 1 3 5)", type=str)
    indices: list[int] = []
    for part in raw.replace(",", " ").split():
        try:
            n = int(part.strip())
            if 1 <= n <= len(summaries_and_points) and n not in indices:
                indices.append(n)
        except ValueError:
            pass

    if not indices:
        raise click.UsageError("Не выбрано ни одной задачи.")

    indices.sort()
    return [summaries_and_points[i - 1] for i in indices]


def _print_task_header(idx: int, summary: str, total: int) -> None:
    ui.echo("")
    ui.echo(_SEP_TASK)
    ui.echo(f"  Задача {idx}/{total}: {summary}")
    ui.echo(_SEP_TASK)


def _print_minor_sep() -> None:
    ui.echo(_SEP_MINOR)


def _prompt_mode(title: str, all_label: str, per_task_label: str) -> str:
    ui.echo(f"{title}: 1) {all_label}  2) {per_task_label}")
    choice = click.prompt("Выбор", type=click.Choice(["1", "2"]), default="1", show_default=True)
    return "all" if choice == "1" else "per_task"


def _prompt_assignee(label: str, users: list[str], *, default: str | None = None) -> str:
    if not users:
        raise click.UsageError("Список пользователей пуст — некого назначить исполнителем.")
    ui.echo(label + ":")
    for i, login in enumerate(users, start=1):
        marker = "  ← по умолчанию" if default and login == default else ""
        ui.echo(f"  {i}) {login}{marker}")
    default_value: str
    if default and default in users:
        default_value = str(users.index(default) + 1)
    else:
        default_value = "1"
    raw = click.prompt("Выбор (номер или login)", type=str, default=default_value, show_default=True)
    return _resolve_assignee(raw, users)


def _print_preview(specs: list[JiraTaskSpec]) -> None:
    rows: list[list[object]] = []
    for idx, spec in enumerate(specs, start=1):
        rows.append([
            idx,
            spec.summary,
            _display_component(spec.component),
            spec.assignee,
            spec.story_points if spec.story_points is not None else "",
            spec.epic_key or "",
        ])
    ui.table(headers=["#", "Summary", "Component", "Assignee", "SP", "Epic"], rows=rows)


def _jira_component(value: str) -> str:
    return TESTCASE_COMPONENT_ALIASES.get(value, value)


def _display_component(value: str) -> str:
    for display_name, jira_name in TESTCASE_COMPONENT_ALIASES.items():
        if value == jira_name:
            return display_name
    return value
