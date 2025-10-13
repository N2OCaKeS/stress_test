from __future__ import annotations
import json

from urllib.parse import urlparse
from ftplib import FTP
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError, ContentTooShortError
from io import BytesIO
import re


from allta_cli.utils.config_api import files as fetch_file, ConfigApiError
from allta_cli.utils.auth import AuthError, TokenExpiredError, NotAuthenticatedError



def _dump_json_like(filename: str, data: dict) -> None:
    sep = "─" * 60
    print(sep)
    print(f"{filename} (начало)")
    print(sep)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    print(sep)
    print(f"{filename} (конец)")
    print(sep)


def _fetch_json_from_ftp(url: str, timeout: int = 20) -> dict:
    u = urlparse(url)
    if u.scheme != "ftp":
        raise ValueError("Ожидался ftp:// URL")

    buf = BytesIO()
    with FTP() as ftp:
        ftp.connect(host=u.hostname, port=u.port or 21, timeout=timeout)
        ftp.login()
        ftp.retrbinary(f"RETR {u.path}", buf.write)

    buf.seek(0)
    return json.loads(buf.read().decode("utf-8"))


def _fetch_json_from_http(url: str, timeout: int = 20) -> dict:
    req = Request(url, headers={"User-Agent": "python-urllib/3"})
    with urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    return json.loads(data.decode("utf-8"))

def _natural_key(s: str):
    """
    Натуральная сортировка: разбивает строку на блоки цифры/текст.
    Плюс небольшой приоритет: сначала имена, начинающиеся с буквы (как 'debian12'),
    потом чисто версионные с цифры (как '1.7.5.o').
    """
    starts_with_letter = 0 if (s and s[0].isalpha()) else 1
    parts = re.split(r'(\d+)', s)
    norm = []
    for p in parts:
        if p.isdigit():
            norm.append((0, int(p)))
        else:
            norm.append((1, p.lower()))
    return (starts_with_letter, norm)

def files_cmd(filename: str) -> int:
    """
    Получает JSON-файл по имени и ПЕЧАТАЕТ:
      - заголовок с именем файла,
      - prettified содержимое,
      - разделители начала/конца.
    Возвращает код завершения (0/1).
    """
    if not filename:
        print("Ошибка: не указано имя файла.")
        return 1

    try:
        data = fetch_file(filename)
    except (NotAuthenticatedError, TokenExpiredError, AuthError, ConfigApiError) as e:
        print(f"Ошибка: {e}")
        return 1
    
    _dump_json_like(filename=f"config/files/{filename}", data=data)
    return 0

def boxes_cmd():
    """
    FTP (анонимно) → test-box-config.json
    Печать JSON «как есть» + вывод ТОЛЬКО имён боксов из libvirt_box.
    """
    sep = "─" * 60
    boxes_url = "ftp://10.177.103.10/boxes/test-box-config.json"
    try:
        data = _fetch_json_from_ftp(boxes_url)

        libvirt = data.get("libvirt_box")
        if not libvirt:
            print("Ошибка: в конфиге не найден раздел 'libvirt_box'.")
            return 1
        names = []
        for item in libvirt:
            if isinstance(item, dict):
                names.extend(item.keys())

        if not names:
            print("Предупреждение: в 'libvirt_box' отсутствуют элементы.")
            return 0
        
        print(sep)
        for name in sorted(set(names), key=_natural_key):
            print(name)
        print(sep)


        return 0

    except (HTTPError, URLError, ContentTooShortError, TimeoutError) as e:
        print(f"Сетевая ошибка при получении boxes: {e}")
        return 1
    except json.JSONDecodeError as e:
        print(f"Некорректный JSON в boxes: {e}")
        return 1
    except Exception as e:
        print(f"Неизвестная ошибка в boxes: {e}")
        return 1
    


def releases_cmd():
    releases_url = "http://allta.devos.astralinux.ru/rest/api/get-repo-path"
    try:
        data = _fetch_json_from_http(releases_url)

        _dump_json_like("releases.json", data)

        if not isinstance(data, dict):
            print("Ошибка: некорректный формат releases (ожидался объект JSON).")
            return 1

        print("Доступные версии (как в файле):")
        for version in data.keys():
            print(f"  • {version}")
        sep = "─" * 60
        print(sep)
        

        return 0

    except (HTTPError, URLError, ContentTooShortError, TimeoutError) as e:
        print(f"Сетевая ошибка при получении releases: {e}")
        return 1
    except json.JSONDecodeError as e:
        print(f"Некорректный JSON в releases: {e}")
        return 1
    except Exception as e:
        print(f"Неизвестная ошибка в releases: {e}")
        return 1