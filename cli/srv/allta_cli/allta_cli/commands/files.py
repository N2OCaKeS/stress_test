from __future__ import annotations
import json
import re
from io import BytesIO
from urllib.parse import urlparse
from ftplib import FTP
import requests
from urllib.error import URLError, HTTPError, ContentTooShortError

from allta_cli.utils import ui
from allta_cli.utils.config_api import files as fetch_file, ConfigApiError, ConfigApiFileNotFound
from allta_cli.utils.auth import AuthError, TokenExpiredError, NotAuthenticatedError
from allta_cli.utils.http_fallback import request_with_http_fallback


def _dump_json_like(filename: str, data: dict) -> None:
    ui.header(f"{filename} (начало)")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    ui.footer(f"{filename} (конец)")


def _print_not_found_payload(payload: object) -> None:
    """
    Печатает тело ответа при 404 от config-API.
    Предпочтительно показывает 'info' как таблицу.
    """
    if not isinstance(payload, dict):
        ui.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    detail = payload.get("detail")
    if isinstance(detail, str) and detail.strip():
        ui.err(detail.strip())
    else:
        ui.err("Файл не найден (HTTP 404).")

    info = payload.get("info")
    if not isinstance(info, list) or not info:
        return

    items = [x for x in info if isinstance(x, dict)]
    if not items:
        return

    preferred_keys = ["filename", "description", "uploaded_by"]
    keys: list[str] = []
    for k in preferred_keys:
        if any(k in it for it in items):
            keys.append(k)
    other_keys = sorted({k for it in items for k in it.keys()} - set(keys))
    keys.extend(other_keys)

    label = {
        "filename": "Filename",
        "description": "Description",
        "uploaded_by": "Uploaded By",
    }
    headers = [label.get(k, k) for k in keys]
    rows = [[it.get(k, "") for k in keys] for it in items]

    ui.echo("Доступные файлы:")
    ui.table(headers=headers, rows=rows)


def _fetch_json_from_ftp(url: str, timeout: int = 20) -> dict:
    u = urlparse(url)
    if u.scheme != "ftp":
        raise ValueError("Ожидался ftp:// URL")

    # mypy/pyright: hostname и path могут быть None -> валидируем
    host = u.hostname
    if not host:
        raise ValueError("FTP URL не содержит host.")
    path = u.path or ""
    if not path or path == "/":
        raise ValueError("FTP URL не содержит путь к файлу.")

    buf = BytesIO()
    with FTP() as ftp:
        # host: str, port: int
        ftp.connect(host=host, port=u.port or 21, timeout=timeout)
        ftp.login()  # anonymous
        ftp.retrbinary(f"RETR {path}", buf.write)

    buf.seek(0)
    return json.loads(buf.read().decode("utf-8"))


def _fetch_json_from_http(url: str, timeout: int = 20) -> dict:
    resp = request_with_http_fallback(
        "GET",
        url,
        headers={"User-Agent": "python-urllib/3"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return json.loads(resp.content.decode("utf-8"))


def _natural_key(s: str):
    """
    Натуральная сортировка: разбивает строку на блоки цифры/текст.
    Приоритет: сначала имена, начинающиеся с буквы, затем — начинающиеся с цифры.
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
    Получает JSON-файл по имени и печатает его с разделителями.
    Возвращает код завершения (0/1).
    """
    if not filename:
        ui.err("Не указано имя файла.")
        return 1

    try:
        data = fetch_file(filename)
    except ConfigApiFileNotFound as e:
        _print_not_found_payload(e.payload)
        return 1
    except (NotAuthenticatedError, TokenExpiredError, AuthError, ConfigApiError) as e:
        ui.err(f"{e}")
        return 1

    _dump_json_like(filename=f"config/files/{filename}", data=data)
    return 0


def boxes_cmd() -> int:
    """
    FTP (анонимно) → test-box-config.json
    Вывод ТОЛЬКО имён боксов из libvirt_box в едином стиле.
    """
    boxes_url = "ftp://10.177.103.10/boxes/test-box-config.json"
    with ui.section("BOXES (libvirt_box)"):
        try:
            data = _fetch_json_from_ftp(boxes_url)

            libvirt = data.get("libvirt_box")
            if not libvirt:
                ui.err("В конфиге не найден раздел 'libvirt_box'.")
                return 1

            names: list[str] = []
            for item in libvirt:
                if isinstance(item, dict):
                    names.extend(item.keys())

            if not names:
                ui.warn("В 'libvirt_box' отсутствуют элементы.")
                return 0

            for name in sorted(set(names), key=_natural_key):
                print(name)
            return 0

        except (HTTPError, URLError, ContentTooShortError, TimeoutError) as e:
            ui.err(f"Сетевая ошибка при получении boxes: {e}")
            return 1
        except json.JSONDecodeError as e:
            ui.err(f"Некорректный JSON в boxes: {e}")
            return 1
        except Exception as e:
            ui.err(f"Неизвестная ошибка в boxes: {e}")
            return 1


def releases_cmd() -> int:
    releases_url = "https://allta.devos.astralinux.ru/rest/api/get-repo-path"
    with ui.section("RELEASES"):
        try:
            data = _fetch_json_from_http(releases_url)

            _dump_json_like("releases.json", data)

            if not isinstance(data, dict):
                ui.err("Некорректный формат releases (ожидался объект JSON).")
                return 1

            ui.echo("Доступные версии (как в файле):")
            for version in data.keys():
                print(f"  • {version}")

            return 0

        except (requests.RequestException, HTTPError, URLError, ContentTooShortError, TimeoutError) as e:
            ui.err(f"Сетевая ошибка при получении releases: {e}")
            return 1
        except json.JSONDecodeError as e:
            ui.err(f"Некорректный JSON в releases: {e}")
            return 1
        except Exception as e:
            ui.err(f"Неизвестная ошибка в releases: {e}")
            return 1
