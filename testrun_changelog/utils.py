import json
import requests
import traceback

from logging_conf import testrun_logger
from errors import RepositoryNotAvailableFromAllta, PackagesNotFound

def fetch_changelog(url):
    res = requests.get(url)
    # if res.status_code != 200:
        # err_message = f"changelog НЕДОСТУПЕН:  {url}"
        # testrun_logger.critical(err_message)
        # raise ChangelogNotAvailable(err_message)
    return res.text

def fetch_packages(url):
    res = requests.get(url)
    if res.status_code != 200:
        err_message = f"url компонента НЕДОСТУПЕН: {url}"
        testrun_logger.critical(err_message)
        raise PackagesNotFound(err_message)
    return res.text
    
def fetch_repository(version):
    url_allta_with_version_ang_repos = "http://allta.devos.astralinux.ru/rest/api/get-repo-path"
    try:
        res = requests.get(url_allta_with_version_ang_repos)
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