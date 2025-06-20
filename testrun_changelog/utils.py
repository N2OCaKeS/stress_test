import json
import socket
import requests
import traceback
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

from logging_conf import testrun_logger
from errors import RepositoryNotAvailableFromAllta, PackagesNotFound, ChangelogNotAvailable

# Принудительно используем IPv4 и отключаем DNS-кэш
socket.getaddrinfo = lambda *args: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (args[0], args[1]))]
socket._GLOBAL_DNS_CACHE = {}

def _get_retry_session() -> requests.Session:
    """Создает сессию requests с настройками Retry."""
    retry_strategy = Retry(
        total=60,
        backoff_factor=60,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.trust_env = False 
    return session

def fetch_changelog(url):
    try:
        session = _get_retry_session()
        res = session.get(url,  headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "*/*"
        })
        res.raise_for_status()
        return res.text
    except requests.exceptions.RequestException as e:
        testrun_logger.critical(f"Changelog недоступен: {url}. Ошибка: {e}")
        raise ChangelogNotAvailable(f"Ошибка при загрузке changelog: {e}")

def fetch_packages(url):
    session = _get_retry_session()
    try:
        res = session.get(url,  headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "*/*"
        })
        res.raise_for_status()
        return res.text
    except requests.exceptions.RequestException as e:
        err_message = f"URL компонент недоступен: {url}. Ошибка: {e}"
        testrun_logger.critical(err_message)
        raise PackagesNotFound(err_message)
    
def fetch_repository(version):
    url_allta_with_version_ang_repos = "http://allta.devos.astralinux.ru/rest/api/get-repo-path"
    session = _get_retry_session()
    headers = {
        "Host": "allta.devos.astralinux.ru",
        "User-Agent": "Mozilla/5.0",
        "Accept": "*/*"
    }
    try:
        # res = requests.get(url_allta_with_version_ang_repos)
        res = session.get(url_allta_with_version_ang_repos, headers=headers)
        res.raise_for_status()
        info_version_repo = json.loads(res.text)
    except requests.exceptions.RequestException as e:
        testrun_logger.critical(f"Ошибка при получении данных с {url_allta_with_version_ang_repos}: {e}")
        raise RepositoryNotAvailableFromAllta(f"Ошибка при получении данных с {url_allta_with_version_ang_repos}: {e}")
    except json.decoder.JSONDecodeError:
        testrun_logger.critical(f"Ошибка при получении данных с {url_allta_with_version_ang_repos}")
        raise RepositoryNotAvailableFromAllta(f"Ошибка при получении данных с {url_allta_with_version_ang_repos}")
    
    # print(info_version_repo)
    return info_version_repo[version]

def filter_repository(repos):
    new_lst_repos = []
    for repo in repos:
        if "extended" in repo:
            testrun_logger.info(f"ИСКЛЮЧЕНО: {repo}")
            continue
        new_lst_repos.append(repo)
    return new_lst_repos


class UtilGetTraceback:
    @staticmethod
    def get_traceback(e):
        # full_traceback = traceback.format_exception(etype=type(e), value=e, tb=e.__traceback__)
        full_traceback = traceback.format_exception(type(e), e, e.__traceback__)
        # for line in full_traceback:
        #     print(line, end="")
        return full_traceback