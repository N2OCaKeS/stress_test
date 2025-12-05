"""
Универсальные классы для работы с Jira/Zefir по образцу ``example_zephir.py``:
поиск прогонов/кейсов, обновление статуса результата и сбор matrix-отчётов без
автопубликации в Confluence.
"""

from __future__ import annotations

from collections import defaultdict
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd
import requests
from atlassian import Confluence
from numpy import where
from urllib.parse import quote

# Базовые настройки и статусы Zefir
JIRA_HOST = "jira.astralinux.ru"
PROJECT_ID = "11200"
DEFAULT_USER_KEY = "JIRAUSER38882"
DEFAULT_STATUS_MAP: Dict[str, int] = {
    "pass": 91,
    "fail": 92,
    "progress": 90,
    "not_executed": 89,
}
TESTNAME_COLUMNS_URL = "http://allta.devos.astralinux.ru/rest/api/get-testname-columns"
TIMES_URL = "http://allta.devos.astralinux.ru/rest/api/get-times"
STAND_URL = "http://allta.devos.astralinux.ru/rest/api/get-stand"


def _build_session(basic_auth: str) -> requests.Session:
    """
    Создаёт ``requests.Session`` с базовыми заголовками.

    Args:
        basic_auth: Значение заголовка Authorization (Basic XXX).

    Returns:
        requests.Session: Сессия с преднастроенными заголовками.
    """
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0",
            "authority": JIRA_HOST,
            "Authorization": basic_auth,
            "Accept": "application/json, text/plain, */*",
        }
    )
    return session


def _status_to_id(status: int | str) -> int:
    """
    Преобразует статус в числовой id.

    Args:
        status: Число статуса или строка из DEFAULT_STATUS_MAP.

    Returns:
        int: Числовое значение статуса.

    Raises:
        ValueError: Если строковый статус неизвестен.
    """
    if isinstance(status, int):
        return status
    try:
        return DEFAULT_STATUS_MAP[status]
    except KeyError as exc:
        raise ValueError(f"Неизвестный статус: {status}") from exc


def _execution_date(dt: Optional[datetime] = None) -> str:
    """
    Возвращает дату исполнения в ISO-формате UTC.

    Args:
        dt: Необязательная дата; если None, берётся текущее время UTC.

    Returns:
        str: ISO-строка с таймзоной.
    """
    dt = dt or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


class ZefirStatusAPI:
    """
    Клиент для получения прогонов/кейсов и смены статуса результата теста по
    имени прогона и тест-кейса.
    """

    def __init__(
        self,
        folder_tree_id: Optional[int] = None,
        basic_auth: Optional[str] = None,
        test_cycle_name: Optional[str] = None,
        test_case_name: Optional[str] = None,
    ):
        """
        Инициализирует клиент для поиска кейса и обновления статуса.

        Args:
            folder_tree_id: ID дерева папок с прогонами.
            basic_auth: Заголовок Authorization (Basic XXX).
            test_cycle_name: Имя прогона.
            test_case_name: Имя тест-кейса.

        Raises:
            ValueError: Если не передан basic_auth.
        """
        logging.basicConfig(
            filename="zefir.log",
            filemode="a",
            level=logging.INFO,
            format="%(levelname)s: t:%(created)f th:%(thread)d ps:%(process)d <%(name)s> | %(message)s",
        )
        self._logger = logging.getLogger(__name__)
        self._folder_tree_id = folder_tree_id
        self._test_cycle_name = test_cycle_name
        self._test_case_name = test_case_name

        if not basic_auth:
            raise ValueError("Необходимо передать basic_auth")
        self._session = _build_session(basic_auth)

    # ------------------------------------------------------------------ HTTP helpers
    def _get(self, url: str, **kwargs: Any) -> requests.Response:
        """
        Выполняет GET-запрос.

        Args:
            url: Полный URL.
            **kwargs: Дополнительные параметры requests.get.

        Returns:
            requests.Response: Ответ с проверкой статуса.
        """
        resp = self._session.get(url, **kwargs)
        self._logger.debug("GET %s -> %s", url, resp.status_code)
        resp.raise_for_status()
        return resp

    def _put(self, url: str, **kwargs: Any) -> requests.Response:
        """
        Выполняет PUT-запрос.

        Args:
            url: Полный URL.
            **kwargs: Дополнительные параметры requests.put.

        Returns:
            requests.Response: Ответ с проверкой статуса.
        """
        resp = self._session.put(url, **kwargs)
        self._logger.debug("PUT %s -> %s", url, resp.status_code)
        resp.raise_for_status()
        return resp

    # ------------------------------------------------------------------ API methods
    def test_case_dates(self, test_cycle_id: int) -> Tuple[List[int], List[str], List[int]]:
        """
        Получает id, имена и статусы кейсов указанного прогона.

        Args:
            test_cycle_id: ID прогона.

        Returns:
            tuple: (list id результатов, list имён кейсов, list id статусов).
        """
        url = (
            f"https://{JIRA_HOST}/rest/tests/1.0/testrun/{test_cycle_id}"
            "/testrunitems?fields=id,index,issueCount,$lastTestResult"
        )
        response = self._get(url).json()
        items = response.get("testRunItems", [])

        case_ids: List[int] = []
        case_names: List[str] = []
        case_statuses: List[int] = []

        for item in items:
            last_result = item.get("$lastTestResult") or {}
            result_id = last_result.get("id")
            result_status = last_result.get("testResultStatusId")
            if result_id is None or result_status is None:
                continue
            case_ids.append(int(result_id))
            case_names.append((last_result.get("testCase") or {}).get("name", ""))
            case_statuses.append(int(result_status))

        self._logger.info(case_ids)
        self._logger.info(case_names)
        self._logger.info(case_statuses)
        self._logger.info("------" * 30)
        return case_ids, case_names, case_statuses

    def dates_test_cycle(self) -> List[Dict[str, Any]]:
        """
        Получает прогоны в дереве папок и вложенные тест-кейсы.

        Returns:
            list[dict]: Список словарей с ключами test_cycle_id/name и списками кейсов/статусов.

        Raises:
            ValueError: Если не задан folder_tree_id.
        """
        if self._folder_tree_id is None:
            raise ValueError("Необходимо указать folder_tree_id")

        query = (
            f"testRun.projectId IN ({PROJECT_ID}) AND "
            f"testRun.folderTreeId IN ({self._folder_tree_id}) ORDER BY testRun.name ASC"
        )
        url = (
            f"https://{JIRA_HOST}/rest/tests/1.0/testrun/search?"
            f"fields=id,key,name,folderId,iterationId,projectVersionId,environmentId,userKeys,"
            f"environmentIds,plannedStartDate,plannedEndDate,executionTime,estimatedTime,"
            f"testResultStatuses,testCaseCount,issueCount,status(id,name,i18nKey,color),"
            f"customFieldValues,createdOn,createdBy,updatedOn,updatedBy,owner&query={quote(query)}"
            "&maxResults=400&startAt=0&archived=false"
        )
        response = self._get(url).json()
        self._logger.info(response)
        self._logger.info("------" * 30)

        test_cycles: List[Dict[str, Any]] = []
        for result in response.get("results", []):
            run_id = result.get("id")
            if run_id is None:
                continue
            case_ids, case_names, statuses = self.test_case_dates(run_id)
            test_cycles.append(
                {
                    "test_cycle_id": run_id,
                    "test_cycle_name": result.get("name", ""),
                    "test_case_id": case_ids,
                    "test_case_name": case_names,
                    "test_case_result_status": statuses,
                }
            )
        return test_cycles

    def finder(self) -> int:
        """
        Находит id результата по имени прогона и кейса.

        Returns:
            int: Идентификатор результата тест-кейса.

        Raises:
            ValueError: Если кейс не найден.
        """
        cycles = self.dates_test_cycle()
        for cycle in cycles:
            if cycle.get("test_cycle_name") != self._test_cycle_name:
                continue
            try:
                index_tc_name = cycle["test_case_name"].index(self._test_case_name)
            except ValueError:
                continue
            self._logger.info(cycle["test_case_id"][index_tc_name])
            self._logger.info("------" * 30)
            return cycle["test_case_id"][index_tc_name]
        raise ValueError("Тест-кейс не найден")

    def upload_status(self, result_status: int | str) -> requests.Response:
        """
        Обновляет статус результата теста.

        Args:
            result_status: Код статуса или строка из списка
                - pass -> 91
                - fail -> 92
                - progress -> 90
                - not_executed -> 89

        Returns:
            requests.Response: Ответ API.

        Raises:
            ValueError: Если не задан userKey или кейс не найден.
        """
        status_id = _status_to_id(result_status)
        result_id = self.finder()
        user_key = DEFAULT_USER_KEY
        if not user_key:
            raise ValueError("Необходимо указать userKey через переменную окружения ZEFIR_USER_KEY")

        payload = [
            {
                "id": result_id,
                "testResultStatusId": status_id,
                "userKey": user_key,
                "executionDate": _execution_date(),
            }
        ]
        self._logger.info(payload)
        self._logger.info("------" * 30)

        url = f"https://{JIRA_HOST}/rest/tests/1.0/testresult"
        return self._put(url, json=payload)


class ZefirResultTable:
    """
    Обёртка для получения matrix-отчёта Zefir и преобразования его в
    ``pandas.DataFrame`` без публикации куда-либо.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        basic_auth: Optional[str] = None,
        username: Optional[str] = None,
        test_cycle_version: Optional[str] = None,
        confluence_url: Optional[str] = None,
    ):
        """
        Формирует таблицу результатов и обновляет страницу Confluence по образцу example_zefir.py.

        Args:
            token: Токен для Confluence.
            basic_auth: Заголовок Authorization (Basic XXX) для Jira.
            username: Пользователь Confluence.
            test_cycle_version: Версия прогона для формирования фильтра folderName.
            confluence_url: Базовый URL Confluence.

        Raises:
            ValueError: Если отсутствуют обязательные параметры.
        """
        if not basic_auth:
            raise ValueError("Необходимо передать basic_auth")
        if not token or not username or not confluence_url:
            raise ValueError("Необходимо передать token, username и confluence_url")

        self.__token = token
        self.__username = username
        self.__basic = basic_auth
        self.__pt_version = test_cycle_version or ""
        self.__confluence_url = (
            confluence_url if confluence_url.startswith("http") else f"https://{confluence_url}"
        )

        self._session = _build_session(basic_auth)
        self._test_cycle_version = test_cycle_version

        matrix = self.fetch()
        entries = matrix.get("results", []) if isinstance(matrix, dict) else list(matrix)
        if not entries:
            return

        def dates(entry: Dict[str, Any]) -> List[Any]:
            test_runs = entry.get("testRuns", [])
            names = [tr.get("testRun", {}).get("name", "").replace("_", " ") for tr in test_runs]
            split_name = [name.split(" ") for name in names]
            test_case_name = entry.get("testCase", {}).get("name", "")
            status = []
            for tr in test_runs:
                key = (tr.get("status") or {}).get("i18nKey", "")
                status.append(key.split(".")[-1] if key else "")
            return [v for i in range(len(split_name)) for v in (split_name[i], test_case_name, status[i])]

        dates_list_raw = [v for entry in entries for v in dates(entry)]
        if not dates_list_raw:
            return
        dates_list = sorted([dates_list_raw[x : x + 3] for x in range(0, len(dates_list_raw), 3)])

        data = defaultdict(list)
        data["Версия"] = [dates_list[0][0][0]]
        data["Ядро"] = [dates_list[0][0][2] if len(dates_list[0][0]) > 2 else ""]
        data["Режим"] = [dates_list[0][0][1] if len(dates_list[0][0]) > 1 else ""]
        data["№ стенда"] = [dates_list[0][0][3] if len(dates_list[0][0]) > 3 else ""]
        self.new_tab = pd.DataFrame(data=data)

        def add_columns_rows(iter_index: int):
            if (
                [dates_list[iter_index][0][0]] == list(data.values())[0]
                and [dates_list[iter_index][0][2]] == list(data.values())[1]
                and [dates_list[iter_index][0][1]] == list(data.values())[2]
                and [dates_list[iter_index][0][3]] == list(data.values())[3]
            ):
                if dates_list[iter_index][1] in self.new_tab.columns:
                    self.new_tab.at[self.new_tab.index[-1], dates_list[iter_index][1]] = dates_list[iter_index][2]
                else:
                    self.new_tab.insert(loc=len(self.new_tab.columns), column=dates_list[iter_index][1], value="")
                    self.new_tab.at[self.new_tab.index[-1], dates_list[iter_index][1]] = dates_list[iter_index][2]
            else:
                data["Версия"] = [dates_list[iter_index][0][0]]
                data["Ядро"] = [dates_list[iter_index][0][2] if len(dates_list[iter_index][0]) > 2 else ""]
                data["Режим"] = [dates_list[iter_index][0][1] if len(dates_list[iter_index][0]) > 1 else ""]
                data["№ стенда"] = [dates_list[iter_index][0][3] if len(dates_list[iter_index][0]) > 3 else ""]
                self.new_tab = pd.concat([self.new_tab, pd.DataFrame(data)], ignore_index=True)
                if dates_list[iter_index][1] in self.new_tab.columns:
                    self.new_tab.at[self.new_tab.index[-1], dates_list[iter_index][1]] = dates_list[iter_index][2]
                else:
                    self.new_tab.insert(loc=len(self.new_tab.columns), column=dates_list[iter_index][1], value="")
                    self.new_tab.at[self.new_tab.index[-1], dates_list[iter_index][1]] = dates_list[iter_index][2]

        [add_columns_rows(item) for item in range(0, len(dates_list))]

        self.new_tab.fillna("", inplace=True)
        columns = ["Версия", "Ядро", "Режим", "№ стенда"]
        for col in columns:
            self.new_tab[col] = self.new_tab[col].astype(str).str.replace(r"\[|\]|'", "", regex=True)

        try:
            response_columns = requests.get(TESTNAME_COLUMNS_URL, timeout=10)
            testname_columns = response_columns.json() if response_columns.status_code == 200 else {}
        except Exception:
            testname_columns = {}
        for k, v in testname_columns.items():
            self.new_tab.rename(columns={k: v}, inplace=True)
        for name in self.new_tab.columns:
            self.new_tab[name] = where(self.new_tab[name] == "NOT_EXECUTED", "Не запускался", self.new_tab[name])
            self.new_tab[name] = where(self.new_tab[name] == "IN_PROGRESS", "Выполняется", self.new_tab[name])
            self.new_tab[name] = where(self.new_tab[name] == "PASS", "Выполнено", self.new_tab[name])
            self.new_tab[name] = where(self.new_tab[name] == "FAIL", "Провалено", self.new_tab[name])

        self.new_tab = self.new_tab.sort_values(by=["Режим", "№ стенда"], ascending=[True, True])
        self.new_tab = self.new_tab[[x for x in self.new_tab if x not in self.new_tab.columns[4:].sort_values()]
            + [x for x in self.new_tab.columns[4:].sort_values() if x in self.new_tab]]
        self.new_tab = self.new_tab.T

        res_path = Path("res.html")
        result_path = Path("result.html")
        templates_dir = Path("templates")
        templates_dir.mkdir(parents=True, exist_ok=True)
        self.new_tab.to_html(res_path, header=False)

        html_string = '<p><h3 style="font-family: Century Gothic, sans-serif;"><b>{text}</b></h3></p>'
        try:
            response_times = requests.get(TIMES_URL, timeout=10)
            if response_times.status_code == 200:
                (templates_dir / "times.html").write_bytes(response_times.content)
            else:
                (templates_dir / "times.html").write_text(
                    html_string.format(text=f"Failed to get file from {TIMES_URL}: {response_times.status_code}"),
                    encoding="utf-8",
                )
        except Exception as exc:
            (templates_dir / "times.html").write_text(html_string.format(text=str(exc)), encoding="utf-8")

        try:
            response_stand = requests.get(STAND_URL, timeout=10)
            if response_stand.status_code == 200:
                (templates_dir / "stand.html").write_bytes(response_stand.content)
            else:
                (templates_dir / "stand.html").write_text(
                    html_string.format(text=f"Failed to get file from {STAND_URL}: {response_stand.status_code}"),
                    encoding="utf-8",
                )
        except Exception as exc:
            (templates_dir / "stand.html").write_text(html_string.format(text=str(exc)), encoding="utf-8")

        html_table = res_path.read_text(encoding="utf-8").splitlines(keepends=True)
        stand_html = (templates_dir / "stand.html").read_text(encoding="utf-8")
        times_html = (templates_dir / "times.html").read_text(encoding="utf-8")

        def write_html(string: str):
            with result_path.open("a", encoding="utf-8") as w:
                w.write(string)

        write_html(f"<h1>Прогресс выполнения тестового прогона {dates_list[0][0][0]}</h1>")
        for string in html_table:
            if string.strip() == "<td>Выполняется</td>":
                write_html(string.replace(string, '      <td style="background-color:#fffacf;">Выполняется</td>\n'))
            elif string.strip() == "<td>Не запускался</td>":
                write_html(string.replace(string, '      <td style="background-color:#f8f8f8;">Не запускался</td>\n'))
            elif string.strip() == "<td>Выполнено</td>":
                write_html(string.replace(string, '      <td style="background-color:#dafee6;">Выполнено</td>\n'))
            elif string.strip() == "<td>Провалено</td>":
                write_html(string.replace(string, '      <td style="background-color:#feffa2; color:#fe1313;">Провалено</td>\n'))
            elif string.strip() == "<td>orel</td>":
                write_html(string.replace(string, '      <td style="background-color:#e4f1fc;">orel</td>\n'))
            elif string.strip() == "<td>smolensk</td>":
                write_html(string.replace(string, '      <td style="background-color:#ffe8e8;">smolensk</td>\n'))
            else:
                write_html(string)
        write_html(stand_html)
        write_html(times_html)

        confluence = Confluence(
            url=self.__confluence_url,
            username=self.__username,
            token=self.__token,
        )

        table = result_path.read_text(encoding="utf-8")
        check_len_version = self.__pt_version.split(".")

        def upload_page(space: str, title: str, name_page: str, body: str):
            if len(check_len_version) == 4 and check_len_version[3] != "UU":
                release_version = ".".join(check_len_version[:3])
                rc_version = self.__pt_version
                if not confluence.page_exists(space=space, title=f"STRESS_stp ⬝ {release_version}"):
                    parent_id = confluence.get_page_id(space=space, title=title)
                    confluence.create_page(space=space, parent_id=parent_id, title=f"STRESS_stp ⬝ {release_version}", body="")

                if not confluence.page_exists(space=space, title=rc_version):
                    parent_id = confluence.get_page_id(space=space, title=f"STRESS_stp ⬝ {release_version}")
                    confluence.create_page(space=space, parent_id=parent_id, title=rc_version, body=body)
                else:
                    page_id = confluence.get_page_id(space=space, title=rc_version)
                    confluence.update_page(page_id=page_id, title=rc_version, body=body)

            elif len(check_len_version) == 6 and check_len_version[3] == "UU":
                release_version = ".".join(check_len_version[:5])
                rc_version = self.__pt_version
                if not confluence.page_exists(space=space, title=f"STRESS_stp ⬝ {release_version}"):
                    parent_id = confluence.get_page_id(space=space, title=title)
                    confluence.create_page(space=space, parent_id=parent_id, title=f"STRESS_stp ⬝ {release_version}", body="")

                if not confluence.page_exists(space=space, title=rc_version):
                    parent_id = confluence.get_page_id(space=space, title=f"STRESS_stp ⬝ {release_version}")
                    confluence.create_page(space=space, parent_id=parent_id, title=rc_version, body=body)
                else:
                    page_id = confluence.get_page_id(space=space, title=rc_version)
                    confluence.update_page(page_id=page_id, title=rc_version, body=body)

            else:
                if not confluence.page_exists(space=space, title=name_page):
                    parent_id = confluence.get_page_id(space=space, title=title)
                    confluence.create_page(space=space, parent_id=parent_id, title=name_page, body=body)
                else:
                    page_id = confluence.get_page_id(space=space, title=name_page)
                    confluence.update_page(page_id=page_id, title=name_page, body=body)

        upload_page("DEVQA", "Состав тестового прогона", f"STRESS_stp ⬝ {self.__pt_version}", table)

        res_path.unlink(missing_ok=True)
        result_path.unlink(missing_ok=True)

    def _get(self, url: str, **kwargs: Any) -> requests.Response:
        """
        Выполняет GET-запрос с проверкой статуса.

        Args:
            url: Полный URL.
            **kwargs: Дополнительные параметры requests.get.

        Returns:
            requests.Response: Ответ API.
        """
        resp = self._session.get(url, **kwargs)
        logging.getLogger(__name__).debug("GET %s -> %s", url, resp.status_code)
        resp.raise_for_status()
        return resp

    def _folder_filter(self) -> str:
        """
        Формирует фильтр folderName для matrix-запроса.

        Returns:
            str: Значение фильтра folderName.
        """
        version = self._test_cycle_version
        if not version:
            return "'/stress_test/**'"

        parts = version.split(".")
        if len(parts) == 4 and parts[3] != "UU":
            release_version = ".".join(parts[:3])
            rc_version = version
            return f"'%2Fstress_test%2F{release_version}%2F{rc_version}%2F**'"
        if len(parts) == 6 and parts[3] == "UU":
            release_version = ".".join(parts[:5])
            rc_version = version
            return f"'%2Fstress_test%2F{release_version}%2F{rc_version}%2F**'"
        return f"'%2Fstress_test','%2Fstress_test%2F{version}'"

    def fetch(self) -> Dict[str, Any]:
        """
        Получает сырые matrix-данные.

        Returns:
            dict: Ответ API reports/matrix.
        """
        folder_filter = self._folder_filter()
        tql = (
            f"testResult.projectId IN ({PROJECT_ID}) AND "
            f"testRun.folderName IN ({folder_filter})"
        )
        url = (
            f"https://{JIRA_HOST}/rest/tests/1.0/reports/testresults/matrix/testrun?"
            f"displayUnit=COUNT&epicJQL=&jql=&period=MONTH&projectId={PROJECT_ID}"
            f"&scorecardOption=EXECUTION_RESULTS&tql={quote(tql)}&"
            "traceabilityCustomTreeDisplayOption=CONDENSED&"
            "traceabilityMatrixOption=COVERAGE_TEST_CASES&"
            "traceabilityReportOption=COVERAGE_TEST_CASES&"
            "traceabilityTreeOption=COVERAGE_TEST_CASES"
        )
        response = self._get(url)
        return response.json()

    def to_dataframe(self, matrix: Optional[Sequence[Dict[str, Any]]] = None) -> pd.DataFrame:
        """
        Преобразует matrix-ответ в DataFrame.

        Args:
            matrix: Необязательный список/словарь matrix-ответа; если None, вызывается fetch().

        Returns:
            pandas.DataFrame: Таблица с атрибутами прогона, именем теста и статусом.
        """
        data = matrix if matrix is not None else self.fetch()
        if isinstance(data, dict):
            entries = data.get("results", [])
        else:
            entries = list(data)

        records: List[Tuple[Tuple[str, ...], str, str]] = []
        for entry in entries:
            test_case_name = entry.get("testCase", {}).get("name", "")
            for test_run_entry in entry.get("testRuns", []):
                run_name = test_run_entry.get("testRun", {}).get("name", "")
                status_key = (test_run_entry.get("status") or {}).get("i18nKey", "")
                status_value = status_key.split(".")[-1] if status_key else ""
                run_parts = tuple(run_name.replace("_", " ").split())
                records.append((run_parts, test_case_name, status_value))

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

        return pd.DataFrame(rows)


class UploaderZC:
    """
    Совместимая обёртка из ``example_zephir.py``: обновляет статус результата
    теста через ``ZefirStatusAPI``.
    """

    def __init__(
        self,
        folder_tree_id: Optional[int] = None,
        test_cycle_name: Optional[str] = None,
        test_case_name: Optional[str] = None,
        basic_auth: Optional[str] = None,
    ):
        """
        Инициализирует обёртку для обновления статуса теста.

        Args:
            folder_tree_id: ID дерева папок.
            test_cycle_name: Имя прогона.
            test_case_name: Имя тест-кейса.
            basic_auth: Заголовок Authorization (Basic XXX).
        """
        self.FTI = folder_tree_id
        self.TCYC = test_cycle_name
        self.TCAS = test_case_name
        self.BA = basic_auth

    def test_cycle_status_changer(self, status: int | str) -> int:
        """
        Обновляет статус результата теста (pass/fail/progress/код).

        Args:
            status: Код статуса или строковое имя:
                - pass -> 91
                - fail -> 92
                - progress -> 90
                - not_executed -> 89

        Returns:
            int: 0 при успешном обновлении.
        """
        zefir = ZefirStatusAPI(
            folder_tree_id=self.FTI,
            test_cycle_name=self.TCYC,
            test_case_name=self.TCAS,
            basic_auth=self.BA,
        )
        zefir.upload_status(status)
        return 0

    def upload_test_cycle_status(self, zefir_status: int | str):
        """
        Унифицированный метод для обновления статуса теста.

        Args:
            zefir_status: Код статуса или строковое имя:
                - pass -> 91
                - fail -> 92
                - progress -> 90
                - not_executed -> 89

        Returns:
            int: Результат вызова ``test_cycle_status_changer``.
        """
        return self.test_cycle_status_changer(zefir_status)
