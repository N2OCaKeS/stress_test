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
* `add_test_cases_to_run` — §D5: добавить тест-кейс(ы) в УЖЕ СУЩЕСТВУЮЩИЙ
  test-run (переключение состава СТП changelog→full не создаёт новый Zephyr-
  ран, а донаводит недостающие тест-кейсы в старый). Легаси-эквивалента нет
  (легаси всегда создавал ран заново). Реализовано по аналогии с
  `create_test_run`/документацией Zephyr Scale ATM REST API —
  `POST /rest/atm/1.0/testrun/{testRunKey}/testcase`, тело — список тех же
  элементов, что и `_build_body`. **Не проверено против живой Jira** — см.
  собственный docstring метода, та же оговорка, что у `update_test_result`.
* `create_test_case` — §D6: завести НОВЫЙ testcase в Zephyr (не test-run),
  используется, когда добавляемый в СТП тест ещё не имеет `stp_test_case`
  со своим `zephyr_id` (`services/stp_add_test.py`). Ни в легаси, ни в этой
  кодовой базе прежде эквивалента не было — легаси testcase'ы заводились
  вручную в Zephyr UI. `POST /rest/atm/1.0/testcase`, построено по аналогии
  с `create_test_run`/`_build_body`. **Не проверено против живой Jira** —
  тот же класс оговорки, что у `add_test_cases_to_run`/`update_test_result`.
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
* `search_test_runs`/`get_test_run` — §D8 (pull СТП из life,
  `services/stp_pull_from_life.py`): найти ранее заведённые test-run'ы в
  папке и прочитать состав+статус одного из них. Ни в легаси, ни в этой
  кодовой базе прежде такого запроса не делали — легаси только писал в
  Zephyr, никогда не читал обратно. `search_test_runs` бьёт в
  `GET /rest/atm/1.0/testrun/search?query=...` (TQL, тот же стиль поиска,
  что и `/testcase/search` в документации Zephyr Scale ATM); `get_test_run`
  — в `GET /rest/atm/1.0/testrun/{key}`. **Контракт обоих не проверен против
  живой Jira** — тот же класс оговорки, что у `add_test_cases_to_run`/
  `create_test_case`/`update_test_result`. Форма ответа особенно
  неопределённая часть: код принимает и голый JSON-массив, и обёртку
  `{"results": [...]}` (paginated-стиль, которым отвечают другие
  Atlassian-эндпоинты этого же сервиса, см. `confluence_client.py`) — какая
  из них у search-эндпоинта настоящей Jira, не проверялось.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from src.core.config import get_settings
from src.core.constants import StpCellStatus
from src.core.exceptions import NotFoundError, ServiceUnavailableError
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


_REVERSE_STATUS_MAP: dict[str, str] = {zephyr: local for local, zephyr in _STATUS_MAP.items()}


def map_status_from_zephyr(zephyr_status: str | None) -> str:
    """Обратное к `map_status` — статус Zephyr ATM → `StpCellStatus` (§D8).

    Пустое или незнакомое значение — безопасный дефолт `not_run`, а не
    исключение: чтение из Zephyr не должно падать на экзотическом статусе
    (например, "Blocked", которого нет среди четырёх локальных), лучше
    заметно занизить его до "не запускался", чем уронить весь импорт.
    """
    if not zephyr_status:
        return StpCellStatus.NOT_RUN
    return _REVERSE_STATUS_MAP.get(zephyr_status, StpCellStatus.NOT_RUN)


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


def _build_items(items: list[ZephyrRunItem], *, with_environment: bool) -> list[dict]:
    payload_items = []
    for item in items:
        entry: dict = {"testCaseKey": item.test_case_key}
        if with_environment and item.environment:
            entry["environment"] = item.environment
        if item.assigned_to_key:
            entry["assignedTo"] = item.assigned_to_key
        payload_items.append(entry)
    return payload_items


def _build_body(
    *, project_key: str, folder: str, name: str, items: list[ZephyrRunItem], with_environment: bool,
) -> dict:
    return {
        "projectKey": project_key, "folder": folder, "name": name,
        "items": _build_items(items, with_environment=with_environment),
    }


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


async def add_test_cases_to_run(
    *, base_url: str, bearer_token: str, test_run_key: str, items: list[ZephyrRunItem],
) -> None:
    """`POST /rest/atm/1.0/testrun/{testRunKey}/testcase` — добавить тест-кейс(ы) в
    уже существующий test-run (§D5: переключение состава changelog→full донаводит
    недостающие тест-кейсы в старый Zephyr-ран, вместо создания нового).

    **Контракт не проверен против живой Jira** — ни в этой кодовой базе, ни в
    легаси (`allta_app/libs/zefir.py`) для этой операции нет рабочего
    прецедента, легаси всегда создавал новый ран заново. Путь/форма тела
    построены по документации Zephyr Scale ATM REST API и по аналогии с
    `create_test_run`/`_build_body` (тот же список объектов `{testCaseKey,
    environment?, assignedTo?}`). Тот же обход "environment field not found
    on project" на 400, что и у `create_test_run` — на случай, если у этого
    эндпоинта та же квирка. Перед первым реальным использованием стоит
    свериться вручную с актуальной Jira (см. также docstring
    `update_test_result` — тот же класс оговорки).
    """
    settings = get_settings()
    url = f"{_base(base_url)}/rest/atm/1.0/testrun/{test_run_key}/testcase"
    headers = bearer_header(bearer_token)

    async def _post(with_environment: bool) -> httpx.Response:
        body = _build_items(items, with_environment=with_environment)
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

    if response.status_code >= 300:
        logger.warning(
            "zephyr: add_test_cases_to_run(%s) failed status=%s body=%s",
            test_run_key, response.status_code, response.text[:500],
        )
        raise ServiceUnavailableError(
            error_code="ZEPHYR_ADD_TEST_CASES_FAILED",
            message=f"Zephyr returned {response.status_code} adding test cases to the test run",
        )


async def create_test_case(
    *,
    base_url: str,
    bearer_token: str,
    name: str,
    folder: str,
    project_key: str = "BT",
    owner_key: str | None = None,
) -> str:
    """`POST /rest/atm/1.0/testcase` — завести новый Zephyr Scale test case, вернуть его `key`.

    §D6: заводится ровно один раз для нового `stp_test_case` — caller обязан
    сперва проверить `stp_test_case_repo.get_by_code`, чтобы не плодить дубли
    в Zephyr при повторном ручном добавлении уже связанного теста (см.
    `services/stp_add_test.py`).

    **Контракт не проверен против живой Jira.** Ни в легаси
    (`allta_app/libs/zefir.py`) для создания testcase эквивалента нет вовсе
    — легаси заводило тест-кейсы вручную в Zephyr UI, — ни в этой кодовой
    базе прежде такого вызова не было. Путь и форма тела построены по
    документации Zephyr Scale ATM REST API и по аналогии с
    `create_test_run`/`_build_body` (тот же стиль Adaptavist ATM). Перед
    первым реальным использованием стоит свериться вручную с актуальной
    Jira (см. также docstring `add_test_cases_to_run`/`update_test_result` —
    тот же класс оговорки).
    """
    settings = get_settings()
    url = f"{_base(base_url)}/rest/atm/1.0/testcase"
    headers = bearer_header(bearer_token)
    body: dict = {"projectKey": project_key, "name": name, "folder": folder}
    if owner_key:
        body["owner"] = owner_key

    async with build_client(settings.zephyr_request_timeout_seconds) as client:
        try:
            response = await client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(
                error_code="ZEPHYR_UNREACHABLE",
                message=f"Unable to reach Jira/Zephyr: {type(exc).__name__}",
            ) from exc

    if response.status_code != 201:
        logger.warning(
            "zephyr: create_test_case failed status=%s body=%s",
            response.status_code, response.text[:500],
        )
        raise ServiceUnavailableError(
            error_code="ZEPHYR_CREATE_TEST_CASE_FAILED",
            message=f"Zephyr returned {response.status_code} creating the test case",
        )

    try:
        key = response.json().get("key")
    except ValueError as exc:
        raise ServiceUnavailableError(
            error_code="ZEPHYR_ERROR",
            message="Zephyr returned a non-JSON body creating the test case",
        ) from exc
    if not key:
        raise ServiceUnavailableError(
            error_code="ZEPHYR_ERROR",
            message="Zephyr response is missing the test case key",
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


@dataclass(frozen=True)
class ZephyrTestRunSummary:
    """Один результат `search_test_runs` — сводная карточка найденного test-run'а,
    без состава (за составом+статусами — отдельный `get_test_run`)."""

    key: str
    name: str
    folder: str | None = None


@dataclass(frozen=True)
class ZephyrTestRunResultItem:
    """Один тест-кейс внутри test-run'а с его ТЕКУЩИМ статусом (`get_test_run`).

    `status` уже переведён в `StpCellStatus` через `map_status_from_zephyr` —
    вызывающему коду не нужно знать вокабуляр Zephyr.
    """

    test_case_key: str
    status: str
    environment: str | None = None
    test_case_name: str | None = None


@dataclass(frozen=True)
class ZephyrTestRunDetail:
    """Полная карточка test-run'а — `get_test_run`: имя/папка + состав с текущими статусами."""

    key: str
    name: str
    folder: str | None
    items: list[ZephyrTestRunResultItem]


async def search_test_runs(
    *, base_url: str, bearer_token: str, folder: str, project_key: str = "BT",
) -> list[ZephyrTestRunSummary]:
    """`GET /rest/atm/1.0/testrun/search?query=...` — test-run'ы в конкретной папке (§D8).

    Используется «Pull СТП из life» (`services/stp_pull_from_life.py`), чтобы
    найти ранее заведённые Zephyr test-run'ы этой РЦ — своей публикацией EMM
    или легаси-системой, или вручную — без ограничения по тому, кто их
    создал. TQL-запрос `testRun.folder = "..." AND testRun.projectKey = "..."`
    — тот же стиль, что документация Zephyr Scale ATM описывает для
    `/testcase/search`; для `/testrun/search` отдельного прецедента (ни в
    легаси, ни в этой кодовой базе) нет. **Контракт не проверен против живой
    Jira** — см. module docstring. Форма ответа принимается в двух видах:
    голый JSON-массив ИЛИ `{"results": [...]}` — какая из них настоящая,
    выяснится при первом реальном вызове.
    """
    settings = get_settings()
    query = f'testRun.folder = "{folder}" AND testRun.projectKey = "{project_key}"'
    url = f"{_base(base_url)}/rest/atm/1.0/testrun/search"
    async with build_client(settings.zephyr_request_timeout_seconds) as client:
        try:
            response = await client.get(
                url, params={"query": query}, headers=bearer_header(bearer_token),
            )
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(
                error_code="ZEPHYR_UNREACHABLE",
                message=f"Unable to reach Jira/Zephyr: {type(exc).__name__}",
            ) from exc

    if response.status_code != 200:
        logger.warning(
            "zephyr: search_test_runs(%s) failed status=%s body=%s",
            folder, response.status_code, response.text[:500],
        )
        raise ServiceUnavailableError(
            error_code="ZEPHYR_SEARCH_TEST_RUNS_FAILED",
            message=f"Zephyr returned {response.status_code} searching test runs",
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise ServiceUnavailableError(
            error_code="ZEPHYR_ERROR",
            message="Zephyr returned a non-JSON body searching test runs",
        ) from exc

    raw_items = payload if isinstance(payload, list) else (payload.get("results") or [])
    results: list[ZephyrTestRunSummary] = []
    for raw in raw_items:
        key = raw.get("key")
        if not key:
            continue
        results.append(ZephyrTestRunSummary(key=key, name=raw.get("name") or "", folder=raw.get("folder")))
    return results


async def get_test_run(
    *, base_url: str, bearer_token: str, test_run_key: str,
) -> ZephyrTestRunDetail:
    """`GET /rest/atm/1.0/testrun/{key}` — детали test-run'а: имя, папка, состав
    тест-кейсов с их ТЕКУЩИМ статусом (§D8).

    Используется после `search_test_runs`, чтобы прочитать, что именно нужно
    завести/сверить локально для одного найденного рана. **Контракт не
    проверен против живой Jira** — см. module docstring. Имя тест-кейса
    ищется в нескольких правдоподобных местах ответа (`testCaseName` на
    самом элементе либо вложенный `testCase.name`) — какое из них
    настоящее, не проверялось; при отсутствии обоих используется сам ключ.
    """
    settings = get_settings()
    url = f"{_base(base_url)}/rest/atm/1.0/testrun/{test_run_key}"
    async with build_client(settings.zephyr_request_timeout_seconds) as client:
        try:
            response = await client.get(url, headers=bearer_header(bearer_token))
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(
                error_code="ZEPHYR_UNREACHABLE",
                message=f"Unable to reach Jira/Zephyr: {type(exc).__name__}",
            ) from exc

    if response.status_code == 404:
        raise NotFoundError(
            error_code="ZEPHYR_TEST_RUN_NOT_FOUND",
            message=f"Zephyr test run {test_run_key} not found",
        )
    if response.status_code != 200:
        logger.warning(
            "zephyr: get_test_run(%s) failed status=%s body=%s",
            test_run_key, response.status_code, response.text[:500],
        )
        raise ServiceUnavailableError(
            error_code="ZEPHYR_GET_TEST_RUN_FAILED",
            message=f"Zephyr returned {response.status_code} reading the test run",
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise ServiceUnavailableError(
            error_code="ZEPHYR_ERROR",
            message="Zephyr returned a non-JSON body reading the test run",
        ) from exc

    items: list[ZephyrTestRunResultItem] = []
    for raw in body.get("items") or []:
        case_key = raw.get("testCaseKey") or (raw.get("testCase") or {}).get("key")
        if not case_key:
            continue
        name = raw.get("testCaseName") or (raw.get("testCase") or {}).get("name")
        items.append(ZephyrTestRunResultItem(
            test_case_key=case_key,
            status=map_status_from_zephyr(raw.get("status")),
            environment=raw.get("environment"),
            test_case_name=name,
        ))
    return ZephyrTestRunDetail(
        key=body.get("key") or test_run_key,
        name=body.get("name") or "",
        folder=body.get("folder"),
        items=items,
    )
