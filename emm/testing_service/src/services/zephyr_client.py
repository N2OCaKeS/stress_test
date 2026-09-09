"""Клиент Jira Zephyr Scale ATM REST API (§6.1, §6.2 плана миграции).

Три операции, все принимают `base_url`/`bearer_token` параметрами — резолв
кред (department_integration_settings + secret_client.reveal_credential)
остаётся зоной ответственности вызывающего сервиса (`services/stp.py`,
`services/stp_status.py`), этот модуль ничего не знает про departments.

* `resolve_user_key` — легаси `ZefirTestRun.creater()`
  (`allta_app/libs/zefir.py`): `GET {jira_url}/rest/api/2/user?username=...`
  → `response.json()["key"]`. Используется, чтобы проставить `assignedTo` в
  элементах тест-рана.
* `create_test_run` — легаси `ZefirTestRun.create_test_run()`:
  `POST {jira_url}/rest/atm/1.0/testrun/`, `projectKey` захардкожен `"BT"`
  (реальный, актуальный ключ проекта — воспроизведено буквально). На 400 с
  текстом `"was not found for field environment on project"` — повтор БЕЗ
  поля `environment` в items (легаси именно так это обходит).
* `update_test_result` — **НЕ переносит легаси-путь** (`ZefirStatusAPI.
  upload_status`, `rest/tests/1.0`, поиск тест-цикла по имени внутри
  `folder_tree_id` — хрупкий текстовый матчинг, дублирующий более новый API).
  Вместо этого — тот же `rest/atm/1.0`-стиль, что и создание, эндпоинт
  `POST /rest/atm/1.0/testrun/{testRunKey}/testcase/{testCaseKey}/testresult`
  с телом `{"status": "Pass"|"Fail"|"In Progress"}`. **Не проверено против
  живой Jira** — тестовое покрытие только мокает HTTP, реализация построена
  по аналогии со схемой создания (тот же стиль Adaptavist ATM REST API) и по
  документации Zephyr Scale ATM, не по работающему легаси-коду (в легаси для
  этого эндпоинта не нашлось эквивалента на `rest/atm/1.0`). Перед реальным
  end-to-end использованием стоит свериться вручную с актуальной Jira.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from src.core.config import get_settings
from src.core.constants import StpCellStatus
from src.core.exceptions import ServiceUnavailableError
from src.core.http import bearer_header

logger = logging.getLogger("testing_service.zephyr_client")

_ENVIRONMENT_NOT_FOUND_HINT = "was not found for field environment on project"

_STATUS_MAP: dict[str, str] = {
    StpCellStatus.NOT_RUN: "Not Executed",
    StpCellStatus.IN_PROGRESS: "In Progress",
    StpCellStatus.PASSED: "Pass",
    StpCellStatus.FAIL: "Fail",
}


@dataclass(frozen=True)
class ZephyrRunItem:
    """Один элемент тела `POST /rest/atm/1.0/testrun/` — один тест-кейс прогона."""

    test_case_key: str
    environment: str | None = None
    assigned_to_key: str | None = None


def build_client(timeout: float) -> httpx.AsyncClient:
    """Клиент под один вызов. Отдельная функция — точка подмены в тестах."""
    return httpx.AsyncClient(timeout=timeout)


def map_status(status: str) -> str:
    """`StpCellStatus` → строка статуса Zephyr ATM."""
    return _STATUS_MAP.get(status, "Not Executed")


def _base(base_url: str) -> str:
    return base_url.rstrip("/")


async def resolve_user_key(*, base_url: str, bearer_token: str, username: str) -> str | None:
    """`GET {base_url}/rest/api/2/user?username=...` → `key`, либо `None` (не найден/сбой).

    Best-effort: caller не должен ронять весь прогон, если резолв ассайни не
    удался — элемент тест-рана просто уйдёт без `assignedTo`.
    """
    settings = get_settings()
    async with build_client(settings.zephyr_request_timeout_seconds) as client:
        try:
            response = await client.get(
                f"{_base(base_url)}/rest/api/2/user",
                params={"username": username},
                headers=bearer_header(bearer_token),
            )
        except httpx.HTTPError as exc:
            logger.warning("zephyr: resolve_user_key(%s) unreachable: %s", username, exc)
            return None
    if response.status_code != 200:
        logger.warning(
            "zephyr: resolve_user_key(%s) returned %s", username, response.status_code,
        )
        return None
    try:
        return response.json().get("key")
    except ValueError:
        return None


def _build_body(
    *, project_key: str, folder: str, name: str, items: list[ZephyrRunItem], with_environment: bool,
) -> dict:
    payload_items = []
    for item in items:
        entry: dict = {"testCaseKey": item.test_case_key}
        if with_environment and item.environment:
            entry["environment"] = item.environment
        if item.assigned_to_key:
            entry["assignedTo"] = item.assigned_to_key
        payload_items.append(entry)
    return {"projectKey": project_key, "folder": folder, "name": name, "items": payload_items}


async def create_test_run(
    *,
    base_url: str,
    bearer_token: str,
    folder: str,
    name: str,
    items: list[ZephyrRunItem],
    project_key: str = "BT",
) -> str:
    """`POST /rest/atm/1.0/testrun/` — создать тест-ран, вернуть `zephyr_test_run_key`.

    На 400 с намёком на "environment field not found on project" — один
    повтор без поля `environment` в items (тот же обход, что легаси
    `ZefirTestRun.create_test_run`).
    """
    settings = get_settings()
    url = f"{_base(base_url)}/rest/atm/1.0/testrun/"
    headers = bearer_header(bearer_token)

    async def _post(with_environment: bool) -> httpx.Response:
        body = _build_body(
            project_key=project_key, folder=folder, name=name, items=items,
            with_environment=with_environment,
        )
        async with build_client(settings.zephyr_request_timeout_seconds) as client:
            return await client.post(url, json=body, headers=headers)

    try:
        response = await _post(with_environment=True)
    except httpx.HTTPError as exc:
        raise ServiceUnavailableError(
            error_code="ZEPHYR_UNREACHABLE",
            message=f"Unable to reach Jira/Zephyr: {type(exc).__name__}",
        ) from exc

    if response.status_code == 400 and _ENVIRONMENT_NOT_FOUND_HINT in response.text:
        try:
            response = await _post(with_environment=False)
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(
                error_code="ZEPHYR_UNREACHABLE",
                message=f"Unable to reach Jira/Zephyr: {type(exc).__name__}",
            ) from exc

    if response.status_code != 201:
        logger.warning(
            "zephyr: create_test_run failed status=%s body=%s",
            response.status_code, response.text[:500],
        )
        raise ServiceUnavailableError(
            error_code="ZEPHYR_CREATE_TEST_RUN_FAILED",
            message=f"Zephyr returned {response.status_code} creating the test run",
        )

    try:
        key = response.json().get("key")
    except ValueError as exc:
        raise ServiceUnavailableError(
            error_code="ZEPHYR_ERROR",
            message="Zephyr returned a non-JSON body creating the test run",
        ) from exc
    if not key:
        raise ServiceUnavailableError(
            error_code="ZEPHYR_ERROR",
            message="Zephyr response is missing the test run key",
        )
    return key


async def update_test_result(
    *, base_url: str, bearer_token: str, test_run_key: str, test_case_key: str, status: str,
) -> None:
    """`POST /rest/atm/1.0/testrun/{key}/testcase/{key}/testresult` — обновить статус.

    См. module docstring — не проверено против живой Jira.
    """
    settings = get_settings()
    url = (
        f"{_base(base_url)}/rest/atm/1.0/testrun/{test_run_key}"
        f"/testcase/{test_case_key}/testresult"
    )
    body = {"status": map_status(status)}
    async with build_client(settings.zephyr_request_timeout_seconds) as client:
        try:
            response = await client.post(url, json=body, headers=bearer_header(bearer_token))
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(
                error_code="ZEPHYR_UNREACHABLE",
                message=f"Unable to reach Jira/Zephyr: {type(exc).__name__}",
            ) from exc
    if response.status_code >= 300:
        logger.warning(
            "zephyr: update_test_result(%s/%s) failed status=%s",
            test_run_key, test_case_key, response.status_code,
        )
        raise ServiceUnavailableError(
            error_code="ZEPHYR_UPDATE_RESULT_FAILED",
            message=f"Zephyr returned {response.status_code} updating the test result",
        )
