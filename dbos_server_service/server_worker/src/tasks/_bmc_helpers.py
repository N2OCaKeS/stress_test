"""Общие BMC-хелперы для task-handler'ов.

Здесь живут утилиты, которые раньше дублировались в `power.py` и
`passwords.py`: парсинг `endpoint_url` в чистый host, фабрика BMC-клиента
поверх `clients.get_bmc_client` (probe Redfish + fallback на ipmitool) и
идемпотентное закрытие BMC-клиента.

Маппинг BMC-исключений и `dispatch_*` адаптеры — рядом, в `_bmc_errors.py`.
Разделение по файлам: `_bmc_errors` отвечает за «как разобрать ошибку BMC и
как унифицировать вызовы клиента», `_bmc_helpers` — за «как достать рабочий
клиент и подключиться к нему».
"""

from __future__ import annotations

from urllib.parse import urlparse

from src.clients import get_bmc_client


def extract_bmc_host(endpoint_url: str) -> str:
    """Достать host/IP из `endpoint_url` IPMI-controller'а.

    `endpoint_url` приходит из server_service в формате `https://10.0.0.1`
    либо просто `10.0.0.1`. `clients._probe_redfish` строит URL как
    `https://{host}/redfish/v1/`, и если host уже содержит scheme — получим
    `https://https://...` (404 на probe → fallback на ipmitool с такой же
    битой строкой → connect-error). Отрезаем scheme заранее.

    Пустая строка / невалидный URL → возвращаем как есть, BMC-клиент упадёт
    с понятной ошибкой connect'а.
    """
    if not endpoint_url:
        return ""
    parsed = urlparse(endpoint_url)
    return parsed.hostname or endpoint_url.split("/")[0]


async def get_bmc(creds: dict, *, prefer: str = "redfish"):
    """Получить BMC-клиент по `creds` из server_service: Redfish с probe,
    fallback на ipmitool.

    `creds` — dict с ключами `endpoint_url`, `username`, `password` и
    опционально `bmc_vendor` (`idrac`/`ilo`/`ipmi_generic`, отдаётся
    `server_service_client.fetch_ipmi_credentials`). Для legacy-payload'ов
    без поля fallback — `None`, RedfishClient берёт iDRAC default.
    """
    return await get_bmc_client(
        host=extract_bmc_host(creds["endpoint_url"]),
        username=creds["username"],
        password=creds["password"],
        prefer=prefer,
        bmc_vendor=creds.get("bmc_vendor"),
    )


async def aclose_bmc(client) -> None:
    """Закрыть BMC-клиент идемпотентно.

    У `RedfishClient` есть `aclose` (httpx.AsyncClient под капотом), у
    `IpmitoolClient` — нет (subprocess-driven, без долгоживущих сокетов).
    """
    aclose = getattr(client, "aclose", None)
    if aclose is not None:
        await aclose()
