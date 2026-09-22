import logging
import re
import time
from os import getenv

import requests
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import pathlib

logger = logging.getLogger(__name__)

# Раньше креды каждого стенда читались из /home/u/ilo.py (stand3 = {...},
# stand4 = {...}), и на деле оба словаря всегда были пустыми заглушками —
# файл на сервере никогда не содержал реальных значений. Теперь номер
# стенда резолвится через emm/server_service: сначала находим сервер по
# номеру стенда в своём отделе, затем достаём его IPMI-креды через
# internal-канал сервиса.
load_dotenv(dotenv_path='/var/allta_services/config/env.allta')

SERVER_SERVICE_URL = getenv("SERVER_SERVICE_URL", "").rstrip("/")
ALLTA_APP_SERVICE_BOT_TOKEN = getenv("ALLTA_APP_SERVICE_BOT_TOKEN", "")

_HTTP_TIMEOUT = 10
_CACHE_TTL_SECONDS = 5 * 60

# stand_number(int) -> (fetched_at + ttl, creds_dict). Держим в памяти
# процесса, чтобы не дёргать server_service на каждый вызов /ilo/<stand>.
_stand_cache = {}


class IloLookupError(Exception):
    """iLO-креды для стенда не удалось получить через server_service."""


def _stand_number(stand_name):
    match = re.fullmatch(r'stand(\d+)', stand_name or '')
    if not match:
        raise IloLookupError(f"'{stand_name}' не похоже на имя стенда (ожидался вид standN)")
    return int(match.group(1))


def _fetch_stand_credentials(number):
    if not SERVER_SERVICE_URL or not ALLTA_APP_SERVICE_BOT_TOKEN:
        raise IloLookupError(
            "SERVER_SERVICE_URL/ALLTA_APP_SERVICE_BOT_TOKEN не заданы — "
            "резолв iLO-кред через emm недоступен"
        )

    auth_headers = {"Authorization": f"Bearer {ALLTA_APP_SERVICE_BOT_TOKEN}"}

    try:
        server_resp = requests.get(
            f"{SERVER_SERVICE_URL}/api/server/v1/servers/by-stand-number/{number}",
            headers=auth_headers,
            timeout=_HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise IloLookupError(f"server_service недоступен: {exc}") from exc

    if server_resp.status_code == 404:
        raise IloLookupError(f"стенд {number} не найден в emm")
    if not server_resp.ok:
        raise IloLookupError(
            f"server_service ответил {server_resp.status_code} на поиск стенда {number}"
        )

    try:
        server = server_resp.json()
    except ValueError as exc:
        raise IloLookupError(f"server_service вернул не-JSON на поиск стенда {number}") from exc

    server_id = server.get("id")
    department_id = server.get("department_id")
    if not server_id or not department_id:
        raise IloLookupError(f"в ответе server_service нет id/department_id для стенда {number}")

    internal_headers = dict(auth_headers)
    internal_headers["X-Target-Department-Id"] = str(department_id)

    try:
        creds_resp = requests.get(
            f"{SERVER_SERVICE_URL}/api/server/v1/internal/servers/{server_id}/ipmi/credentials",
            headers=internal_headers,
            timeout=_HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise IloLookupError(f"server_service (internal) недоступен: {exc}") from exc

    if creds_resp.status_code == 404:
        raise IloLookupError(f"у сервера стенда {number} нет привязанного IPMI-контроллера")
    if not creds_resp.ok:
        raise IloLookupError(
            f"server_service ответил {creds_resp.status_code} на IPMI-креды стенда {number}"
        )

    try:
        creds = creds_resp.json()
    except ValueError as exc:
        raise IloLookupError(f"server_service вернул не-JSON на IPMI-креды стенда {number}") from exc

    return {
        "url": creds.get("endpoint_url"),
        "username": creds.get("username"),
        "password": creds.get("password"),
    }


def get_stand_credentials(stand_name):
    """iLO-креды для 'standN' с кэшем на несколько минут."""
    number = _stand_number(stand_name)

    cached = _stand_cache.get(number)
    if cached is not None:
        expires_at, creds = cached
        if expires_at > time.monotonic():
            return creds

    creds = _fetch_stand_credentials(number)
    _stand_cache[number] = (time.monotonic() + _CACHE_TTL_SECONDS, creds)
    return creds


class iLOConsoleCaller:

    def __init__(self,
                 stand_number=None):
        self.stand_name = stand_number
        self.stand = get_stand_credentials(stand_number)

    def ilo_console_loader(self):
        url = self.stand['url']
        username = self.stand['username']
        password = self.stand['password']

        driver = webdriver.Firefox(service=Service(f'{pathlib.Path(__file__).parent}/drivers/geckodriver'))
        driver.get(url)
        driver.switch_to.frame("appFrame")

        if self.stand_name == 'stand3':
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, 'usernameInput'))).send_keys(username)
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, 'passwordInput'))).send_keys(password)
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, 'ID_LOGON'))).click()
        elif self.stand_name == 'stand4':
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, 'username'))).send_keys(username)
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, 'password'))).send_keys(password)
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, 'login-form__submit'))).click()

        time.sleep(8)
        #frames = driver.find_elements_by_tag_name("iframe")
        #for frame in frames:
        #    print(frame.get_attribute('id'))

        if self.stand_name == 'stand3':
            driver.switch_to.frame("frameContent")
            time.sleep(2)
            driver.switch_to.frame("iframeContent")
        elif self.stand_name == 'stand4':
            driver.switch_to.frame("iframeContent")

        html5 = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.XPATH, "//a/span[contains(text(), 'HTML5')]")))
        html5.click()
        time.sleep(5)

        #driver.close()
