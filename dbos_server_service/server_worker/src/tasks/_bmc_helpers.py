"""Общие BMC-хелперы для task-handler'ов.

Здесь живут утилиты, которые раньше дублировались в `power.py` и
`passwords.py`: парсинг `endpoint_url` в чистый host, фабрика BMC-клиента
поверх `clients.get_bmc_client` (probe Redfish + fallback на ipmitool) и
идемпотентное закрытие BMC-клиента.

Shared circuit breaker (`services.bmc_circuit_breaker`) handler'ы дёргают
сами вокруг `get_bmc` + dispatch — `_bmc_helpers` про breaker не знает,
чтобы pytest-monkeypatch'и (подмена `_get_bmc`) не теряли проверку.

Маппинг BMC-исключений и `dispatch_*` адаптеры — рядом, в `_bmc_errors.py`.
Разделение по файлам: `_bmc_errors` отвечает за «как разобрать ошибку BMC и
как унифицировать вызовы клиента», `_bmc_helpers` — за «как достать рабочий
клиент и подключиться к нему».
"""

from __future__ import annotations

from urllib.parse import urlparse

from src.clients import get_bmc_client


def extract_bmc_host(endpoint_url: str) -> str:
    """Достать host[:port] из `endpoint_url` IPMI-controller'а.

    `endpoint_url` приходит из server_service в формате `https://10.0.0.1`,
    `https://10.0.0.1:443`, `10.0.0.1:623` либо просто `10.0.0.1`. Для IPv6
    форма с квадратными скобками: `https://[2001:db8::1]:443` либо
    `[::1]:623`.
    `clients._probe_redfish` подставляет результат в `f"{scheme}://{host}{path}"`,
    поэтому scheme нужно отрезать, а port — наоборот сохранить (включая
    стандартные 80/443/623): разные iDRAC на одном IP могут висеть на
    разных портах, и per-host circuit breaker должен видеть их раздельно.

    `urlparse(...).hostname` теряет порт и снимает квадратные скобки с IPv6 —
    используем `netloc` и срезаем `user:pass@` префикс, если он есть.
    Для строк без scheme netloc пустой, падаем на `split('/')[0]` (это уже
    host[:port]); из этого fallback'а нужно вручную отрезать userinfo —
    с bare-IPv4/IPv6 `urlparse` его не видит. Bare-bracket IPv6 без scheme
    (`[::1]` / `[::1]:443`) тоже идёт через fallback: `urlparse` кладёт это
    в `path`, а не `netloc`, поэтому split('/')[0] возвращает исходную
    строку as-is — корректное поведение для downstream подстановки в URL.

    Пустая строка → возвращаем как есть, BMC-клиент упадёт с понятной
    ошибкой connect'а.
    """
    if not endpoint_url:
        return ""
    parsed = urlparse(endpoint_url)
    netloc = parsed.netloc
    if not netloc:
        netloc = endpoint_url.split("/")[0]
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]
    return netloc


async def get_bmc(creds: dict, *, prefer: str = "redfish"):
    """Получить BMC-клиент по `creds` из server_service: Redfish с probe,
    fallback на ipmitool.

    `creds` — dict с ключами `endpoint_url`, `username`, `password` и
    опционально `kind` (`idrac`/`ilo`/`ipmi`/`redfish`, отдаётся
    `server_service_client.fetch_ipmi_credentials`). Для legacy-payload'ов
    без поля fallback — `None`, RedfishClient берёт iDRAC default.

    Circuit breaker не вызывается здесь — handler сам делает
    ``bmc_circuit_breaker.check(host)`` до этой функции и
    ``record_success``/``record_failure`` после. Это сделано чтобы
    monkeypatch'и тестов (которые подменяют именно ``_get_bmc``) не теряли
    проверку breaker'а, а заодно чтобы factor'у клиента не приходилось
    знать про breaker.
    """
    return await get_bmc_client(
        host=extract_bmc_host(creds["endpoint_url"]),
        username=creds["username"],
        password=creds["password"],
        prefer=prefer,
        kind=creds.get("kind"),
    )


async def aclose_bmc(client) -> None:
    """Закрыть BMC-клиент идемпотентно.

    У `RedfishClient` есть `aclose` (httpx.AsyncClient под капотом), у
    `IpmitoolClient` — нет (subprocess-driven, без долгоживущих сокетов).
    """
    aclose = getattr(client, "aclose", None)
    if aclose is not None:
        await aclose()
