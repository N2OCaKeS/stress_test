"""
Легковесный клиент для API управления тестами Zefir/Jira.

Возможности:
- Получать список прогонов (test runs) в дереве папок.
- Получать элементы прогона и находить id результата тест-кейса по именам.
- Обновлять статус результата теста.
- Загружать данные отчёта matrix по произвольному TQL-фильтру.

Все средовые детали (хосты, авторизация, id проектов/папок, маппинг статусов,
запросы) передаются параметрами, поэтому клиент подходит для любых тестов без
жёсткой привязки к проекту или публикации в Confluence.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import pandas as pd
import requests
from urllib.parse import quote


DEFAULT_STATUS_MAP: Dict[str, int] = {
    "pass": 91,
    "fail": 92,
    "progress": 90,
    "not_executed": 89,
}
JIRA_HOST = "jira.astralinux.ru"


class ZefirClient:
    """
    Минимальный клиент Zefir/Jira для работы с прогонами и результатами.

    Предоставляет методы для поиска прогонов, элементов прогонов, обновления
    статусов тестов и получения matrix-отчётов.
    """

    def __init__(
        self,
        basic_auth_header: str,
        project_id: int,
        *,
        session: Optional[requests.Session] = None,
        logger: Optional[logging.Logger] = None,
        status_map: Optional[Dict[str, int]] = None,
        default_user_key: Optional[str] = None,
        default_folder_tree_id: Optional[int] = None,
        user_agent: str = "Mozilla/5.0",
    ):
        """
        Args:
            basic_auth_header: Значение заголовка Authorization (например "Basic XXX").
            project_id: ID проекта Jira для запросов прогонов.
            session: Необязательный requests.Session для переиспользования соединений.
            logger: Необязательный логгер; если не задан, используется модульный.
            status_map: Необязательная мапа названия статуса -> id.
            default_user_key: Необязательный user key для обновления статусов.
            default_folder_tree_id: Необязательный id дерева папок по умолчанию.
            user_agent: Значение User-Agent.
        Returns:
            None
        """
        self._base_url = f"https://{JIRA_HOST}"
        self._project_id = project_id
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "User-Agent": user_agent,
                "authority": JIRA_HOST,
                "Authorization": basic_auth_header,
                "Accept": "application/json, text/plain, */*",
            }
        )
        self._logger = logger or logging.getLogger(__name__)
        self._status_map = status_map or DEFAULT_STATUS_MAP
        self._default_user_key = default_user_key
        self._default_folder_tree_id = default_folder_tree_id

    # ------------------------------------------------------------------ helpers
    def _get(self, url: str, **kwargs: Any) -> requests.Response:
        """
        Выполняет GET-запрос с логированием кода ответа.

        Args:
            url (str): Полный URL запроса.
            **kwargs: Дополнительные параметры для `requests.Session.get`.

        Returns:
            requests.Response: Объект ответа.

        Raises:
            requests.HTTPError: При неуспешном статус-коде.
        """
        resp = self._session.get(url, **kwargs)
        self._logger.debug("GET %s -> %s", url, resp.status_code)
        resp.raise_for_status()
        return resp

    def _put(self, url: str, **kwargs: Any) -> requests.Response:
        """
        Выполняет PUT-запрос с логированием кода ответа.

        Args:
            url (str): Полный URL запроса.
            **kwargs: Дополнительные параметры для `requests.Session.put`.

        Returns:
            requests.Response: Объект ответа.

        Raises:
            requests.HTTPError: При неуспешном статус-коде.
        """
        resp = self._session.put(url, **kwargs)
        self._logger.debug("PUT %s -> %s", url, resp.status_code)
        resp.raise_for_status()
        return resp

    # ------------------------------------------------------------------ listing
    def list_test_runs(
        self,
        *,
        folder_tree_id: Optional[int] = None,
        query: Optional[str] = None,
        max_results: int = 400,
        start_at: int = 0,
        archived: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Получает список прогонов (test cycles) по фильтру дерева или кастомному запросу.

        Args:
            folder_tree_id (int | None): ID дерева папок для фильтрации.
            query (str | None): Кастомный запрос, если нужен.
            max_results (int): Лимит результатов.
            start_at (int): Смещение для пагинации.
            archived (bool): Включать ли архивные прогоны.

        Returns:
            list[dict[str, Any]]: Сырые объекты прогонов из API.
        """
        folder_id = folder_tree_id or self._default_folder_tree_id
        if folder_id is None and query is None:
            raise ValueError("Нужно указать folder_tree_id или передать кастомный query")

        query_str = (
            query
            or f"testRun.projectId IN ({self._project_id}) AND "
            f"testRun.folderTreeId IN ({folder_id}) ORDER BY testRun.name ASC"
        )
        url = (
            f"{self._base_url}/rest/tests/1.0/testrun/search?"
            f"fields=id,key,name,folderId,iterationId,projectVersionId,environmentId,"
            f"userKeys,environmentIds,plannedStartDate,plannedEndDate,executionTime,"
            f"estimatedTime,testResultStatuses,testCaseCount,issueCount,"
            f"status(id,name,i18nKey,color),customFieldValues,createdOn,createdBy,"
            f"updatedOn,updatedBy,owner&query={quote(query_str)}"
            f"&maxResults={max_results}&startAt={start_at}&archived={str(archived).lower()}"
        )
        resp = self._get(url)
        data = resp.json()
        return data.get("results", [])

    def list_test_run_items(self, test_run_id: int) -> List[Dict[str, Any]]:
        """
        Returns элементы прогона (тест-кейсы) по id прогона.

        Args:
            test_run_id (int): Идентификатор прогона.

        Returns:
            list[dict[str, Any]]: Элементы прогона с данными последнего результата.
        """
        url = (
            f"{self._base_url}/rest/tests/1.0/testrun/{test_run_id}"
            "/testrunitems?fields=id,index,issueCount,$lastTestResult"
        )
        resp = self._get(url)
        payload = resp.json()
        return payload.get("testRunItems", [])

    # ------------------------------------------------------------------ helpers for statuses
    def _status_to_id(self, status: int | str) -> int:
        """
        Приводит статус к числовому id.

        Args:
            status (int | str): Имя статуса или его числовой код.

        Returns:
            int: Числовой код статуса.

        Raises:
            ValueError: Если статус неизвестен.
        """
        if isinstance(status, int):
            return status
        try:
            return self._status_map[status]
        except KeyError as exc:
            raise ValueError(f"Неизвестный статус: {status}") from exc

    def _execution_date(self, execution_date: Optional[datetime]) -> str:
        """
        Returns дату выполнения в ISO-формате (UTC).

        Args:
            execution_date (datetime | None): Явная дата; если None, берётся текущая.

        Returns:
            str: Дата в формате ISO с таймзоной.
        """
        if execution_date is None:
            execution_date = datetime.now(timezone.utc)
        if execution_date.tzinfo is None:
            execution_date = execution_date.replace(tzinfo=timezone.utc)
        return execution_date.isoformat()

    # ------------------------------------------------------------------ finders
    def find_test_case_result_id(
        self,
        *,
        test_cycle_name: str,
        test_case_name: str,
        folder_tree_id: Optional[int] = None,
        query: Optional[str] = None,
    ) -> Optional[int]:
        """
        Находит id результата теста по имени прогона и тест-кейса.
        Returns None, если не найден.

        Args:
            test_cycle_name (str): Имя прогона.
            test_case_name (str): Имя тест-кейса.
            folder_tree_id (int | None): ID дерева папок, если нужен фильтр.
            query (str | None): Альтернативный кастомный запрос.

        Returns:
            int | None: Идентификатор результата теста или None.
        """
        runs = self.list_test_runs(folder_tree_id=folder_tree_id, query=query)
        for run in runs:
            if run.get("name") != test_cycle_name:
                continue
            run_items = self.list_test_run_items(run["id"])
            for item in run_items:
                last_res = item.get("$lastTestResult") or {}
                case = last_res.get("testCase") or {}
                if case.get("name") == test_case_name:
                    return last_res.get("id")
        return None

    # ------------------------------------------------------------------ updates
    def set_test_result_by_id(
        self,
        *,
        test_result_id: int,
        status: int | str,
        user_key: Optional[str] = None,
        execution_date: Optional[datetime] = None,
    ) -> requests.Response:
        """
        Обновляет статус результата теста по id.

        Args:
            test_result_id (int): Идентификатор результата теста.
            status (int | str): Код или имя статуса.
            user_key (str | None): User key, если не задан по умолчанию.
            execution_date (datetime | None): Дата выполнения (UTC, по умолчанию сейчас).

        Returns:
            requests.Response: Ответ API.

        Raises:
            ValueError: Если не указан user_key.
        """
        status_id = self._status_to_id(status)
        user = user_key or self._default_user_key
        if not user:
            raise ValueError("Необходимо указать user_key")

        payload = [
            {
                "id": test_result_id,
                "testResultStatusId": status_id,
                "userKey": user,
                "executionDate": self._execution_date(execution_date),
            }
        ]
        url = f"{self._base_url}/rest/tests/1.0/testresult"
        return self._put(url, json=payload)

    def set_test_result(
        self,
        *,
        test_cycle_name: str,
        test_case_name: str,
        status: int | str,
        user_key: Optional[str] = None,
        folder_tree_id: Optional[int] = None,
        query: Optional[str] = None,
        execution_date: Optional[datetime] = None,
    ) -> requests.Response:
        """
        Ищет id результата по именам прогона/кейса и обновляет его статус.

        Args:
            test_cycle_name (str): Имя прогона.
            test_case_name (str): Имя тест-кейса.
            status (int | str): Код или имя статуса.
            user_key (str | None): User key, если не задан по умолчанию.
            folder_tree_id (int | None): ID дерева папок, если нужен фильтр.
            query (str | None): Альтернативный кастомный запрос.
            execution_date (datetime | None): Дата выполнения (UTC, по умолчанию сейчас).

        Returns:
            requests.Response: Ответ API.

        Raises:
            ValueError: Если тест-кейс не найден.
        """
        result_id = self.find_test_case_result_id(
            test_cycle_name=test_cycle_name,
            test_case_name=test_case_name,
            folder_tree_id=folder_tree_id,
            query=query,
        )
        if result_id is None:
            raise ValueError(
                f"Тест-кейс '{test_case_name}' в прогоне '{test_cycle_name}' не найден"
            )
        return self.set_test_result_by_id(
            test_result_id=result_id,
            status=status,
            user_key=user_key,
            execution_date=execution_date,
        )

    # ------------------------------------------------------------------ matrix report
    def fetch_matrix(
        self,
        *,
        tql: str,
        epic_jql: str = "",
        jql: str = "",
        display_unit: str = "COUNT",
        scorecard_option: str = "EXECUTION_RESULTS",
        project_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Получает данные отчёта matrix по произвольному TQL-фильтру.

        Аргументы:
            tql: Строка фильтра TQL.
            epic_jql: Необязательный epic JQL.
            jql: Необязательный JQL.
            display_unit: Единица отображения (например COUNT).
            scorecard_option: Опция scorecard для matrix.
            project_id: Переопределение id проекта для запроса.

        Returns:
            dict[str, Any]: Сырые данные matrix-отчёта.
        """
        pid = project_id or self._project_id
        url = (
            f"{self._base_url}/rest/tests/1.0/reports/testresults/matrix/testrun?"
            f"displayUnit={display_unit}&epicJQL={epic_jql}&jql={jql}&period=MONTH&"
            f"projectId={pid}&scorecardOption={scorecard_option}&tql={quote(tql)}&"
            "traceabilityCustomTreeDisplayOption=CONDENSED&"
            "traceabilityMatrixOption=COVERAGE_TEST_CASES&"
            "traceabilityReportOption=COVERAGE_TEST_CASES&"
            "traceabilityTreeOption=COVERAGE_TEST_CASES"
        )
        resp = self._get(url)
        return resp.json()

    # ------------------------------------------------------------------ dataframe helper
    def matrix_to_dataframe(
        self,
        matrix: Sequence[Dict[str, Any]],
        *,
        run_name_parser: Optional[Callable[[str], Sequence[str]]] = None,
        status_transform: Optional[Callable[[str], str]] = None,
    ) -> pd.DataFrame:
        """
        Конвертирует данные matrix в DataFrame, похожий на сводную таблицу.

        Аргументы:
            matrix: Элементы matrix, как возвращает fetch_matrix().
            run_name_parser: Функция, разбивающая имя прогона на атрибуты
                (по умолчанию делит по подчёркиванию и пробелам).
            status_transform: Необязательная функция трансформации статуса.

        Returns:
            pandas.DataFrame: Таблица с атрибутами прогона, именем теста и статусом.
        """
        def default_run_parser(name: str) -> Sequence[str]:
            return name.replace("_", " ").split()

        parse_run = run_name_parser or default_run_parser
        transform_status = status_transform or (lambda x: x)

        records: List[Tuple[Tuple[str, ...], str, str]] = []
        for entry in matrix:
            test_case_name = entry.get("testCase", {}).get("name", "")
            for test_run_entry in entry.get("testRuns", []):
                run_name = test_run_entry.get("testRun", {}).get("name", "")
                status_key = (test_run_entry.get("status") or {}).get("i18nKey", "")
                status_value = status_key.split(".")[-1] if status_key else ""
                records.append((tuple(parse_run(run_name)), test_case_name, transform_status(status_value)))

        if not records:
            return pd.DataFrame()

        run_attrs_len = len(records[0][0])
        columns = [f"run_attr_{i}" for i in range(run_attrs_len)]
        rows = []
        for run_parts, case_name, status in records:
            row = dict(zip(columns, run_parts))
            row["test_case"] = case_name
            row["status"] = status
            rows.append(row)

        df = pd.DataFrame(rows)
        return df


# ----------------------------------------------------------------------
# Совместимые универсальные обёртки в стиле старого zefir.py

class ZefirStatusAPI:
    """
    Совместимый слой над ZefirClient для поиска кейса и обновления статуса
    без привязки к конкретному проекту/тесту.
    """

    def __init__(
        self,
        *,
        folder_tree_id: Optional[int],
        basic_auth: str,
        test_cycle_name: str,
        test_case_name: str,
        project_id: int,
        user_key: Optional[str] = None,
    ):
        self._test_cycle_name = test_cycle_name
        self._test_case_name = test_case_name
        self._client = ZefirClient(
            basic_auth_header=basic_auth,
            project_id=project_id,
            default_user_key=user_key,
        )
        self._folder_tree_id = folder_tree_id

    def test_case_dates(self, test_cycle_id: int) -> Tuple[List[int], List[str], List[int]]:
        """
        Получает для прогона список тест-кейсов с их последними результатами.

        Args:
            test_cycle_id: Идентификатор прогона.

        Returns:
            Кортеж списков: (id результатов, имена тест-кейсов, id статусов).
        """
        items = self._client.list_test_run_items(test_cycle_id)
        case_ids = [it["$lastTestResult"]["id"] for it in items if "$lastTestResult" in it]
        case_names = [it["$lastTestResult"]["testCase"]["name"] for it in items if "$lastTestResult" in it]
        statuses = [it["$lastTestResult"]["testResultStatusId"] for it in items if "$lastTestResult" in it]
        return case_ids, case_names, statuses

    def dates_test_cycle(self) -> List[Dict[str, Any]]:
        """
        Возвращает прогоны из указанного дерева папок с вложенными тест-кейсами.

        Returns:
            Список словарей с полями test_cycle_id, test_cycle_name, test_case_id, test_case_name, test_case_result_status.
        """
        runs = self._client.list_test_runs(folder_tree_id=self._folder_tree_id)
        test_cycles: List[Dict[str, Any]] = []
        for run in runs:
            run_id = run.get("id")
            if run_id is None:
                continue
            case_ids, case_names, statuses = self.test_case_dates(run_id)
            test_cycles.append(
                {
                    "test_cycle_id": run_id,
                    "test_cycle_name": run.get("name", ""),
                    "test_case_id": case_ids,
                    "test_case_name": case_names,
                    "test_case_result_status": statuses,
                }
            )
        return test_cycles

    def finder(self) -> int:
        """
        Ищет id результата по имени прогона/кейса.

        Returns:
            int: Идентификатор результата тест-кейса.

        Raises:
            ValueError: если кейс не найден.
        """
        result_id = self._client.find_test_case_result_id(
            test_cycle_name=self._test_cycle_name,
            test_case_name=self._test_case_name,
            folder_tree_id=self._folder_tree_id,
        )
        if result_id is None:
            raise ValueError("Тест-кейс не найден")
        return result_id

    def upload_status(self, result_status: int | str):
        """
        Обновляет статус результата теста.

        Args:
            result_status: Код или текст статуса (pass/fail/progress/число).
        """
        return self._client.set_test_result(
            test_cycle_name=self._test_cycle_name,
            test_case_name=self._test_case_name,
            status=result_status,
            folder_tree_id=self._folder_tree_id,
        )


class ZefirResultTable:
    """
    Универсальный сборщик matrix-отчёта без жёсткой привязки к конкретному тесту.

    Позволяет получить сырые данные matrix по TQL и преобразовать их в DataFrame.
    """

    def __init__(
        self,
        *,
        basic_auth: str,
        project_id: int,
        folder_filter: Optional[str] = None,
    ):
        self._client = ZefirClient(
            basic_auth_header=basic_auth,
            project_id=project_id,
        )
        self.folder_filter = folder_filter

    def fetch(self) -> Dict[str, Any]:
        """
        Возвращает raw matrix-данные по TQL фильтру.

        Returns:
            dict: Ответ API matrix-report.
        """
        tql = (
            f"testResult.projectId IN ({self._client._project_id})"
            + (f" AND testRun.folderName IN ({self.folder_filter})" if self.folder_filter else "")
        )
        return self._client.fetch_matrix(tql=tql)

    def to_dataframe(self, matrix: Optional[Sequence[Dict[str, Any]]] = None) -> pd.DataFrame:
        """
        Конвертирует matrix-ответ в DataFrame.

        Args:
            matrix: Необязательный заранее полученный matrix-ответ (list или dict с key 'results').

        Returns:
            pandas.DataFrame: Таблица с атрибутами прогона, именем теста и статусом.
        """
        data = matrix if matrix is not None else self.fetch()
        matrix_entries: Sequence[Dict[str, Any]]
        if isinstance(data, list):
            matrix_entries = data
        elif isinstance(data, dict):
            matrix_entries = data.get("results", [])
        else:
            matrix_entries = []
        return self._client.matrix_to_dataframe(matrix_entries)


class UploaderZC:
    """
    Совместимый интерфейс для установки статуса прогона/кейса.

    Лёгкая обёртка над ZefirStatusAPI для вызова upload_test_cycle_status.
    """

    def __init__(
        self,
        *,
        folder_tree_id: Optional[int],
        test_cycle_name: str,
        test_case_name: str,
        basic_auth: str,
        project_id: int,
        user_key: Optional[str] = None,
    ):
        self._status_api = ZefirStatusAPI(
            folder_tree_id=folder_tree_id,
            basic_auth=basic_auth,
            test_cycle_name=test_cycle_name,
            test_case_name=test_case_name,
            project_id=project_id,
            user_key=user_key,
        )

    def upload_test_cycle_status(self, zefir_status: int | str):
        """
        Устанавливает статус результата теста (pass/fail/progress/код).
        """
        self._status_api.upload_status(zefir_status)
