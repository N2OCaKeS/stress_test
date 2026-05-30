"""Безопасное извлечение IP клиента из FastAPI Request.

Канонический источник для копи-паста между сервисами. Эталон — то, что
лежит в `auth_service/src/services/audit_context.py` (там самый
проработанный allow-list с поддержкой CIDR и left-most non-trusted).

Где сейчас дублируется этот код:
    auth_service/src/services/audit_context.py    — extract_client_ip + хелперы
    server_service/src/services/audit_context.py  — то же самое почти 1:1
    auth_service/src/main.py                       — вызывается из rate-limit key
    server_service/src/main.py                     — вызывается из rate-limit key

Зачем нужен модуль:

Наивный `request.headers["X-Forwarded-For"]` — spoofing vector. Любой
клиент может подставить произвольный IP, и audit-log запишет атакующего как
кого угодно, а rate-limit-by-IP обойдётся в один заголовок.

Поэтому: доверять `X-Forwarded-For` / `X-Real-IP` можно ТОЛЬКО если запрос
пришёл с IP из `trusted_proxy_ips` allow-list (явный список доверенных
прокси/балансировщиков, который оператор задаёт в конфиге). Иначе берём
прямой `request.client.host` — он подделать через заголовки нельзя.

В `X-Forwarded-For: A, B, C` (client, proxy1, proxy2) ищем самый левый IP,
который НЕ является доверенным proxy — это и есть оригинальный клиент.

Использование:

    from sdk_local.extract_client_ip import extract_client_ip
    ip = extract_client_ip(request, settings.trusted_proxy_ips)

Параметр `trusted_proxy_ips` — список IP/CIDR строк. Если пуст — заголовкам
не доверяем, всегда возвращаем `request.client.host`.
"""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request


def _is_trusted_proxy(client_ip: str, trusted: list[str]) -> bool:
    """True, если `client_ip` входит в allow-list `trusted`.

    Поддерживается точное совпадение и CIDR (`10.0.0.0/8`).
    """
    if not trusted or not client_ip:
        return False
    try:
        ip_obj = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for entry in trusted:
        entry = (entry or "").strip()
        if not entry:
            continue
        if "/" in entry:
            try:
                network = ipaddress.ip_network(entry, strict=False)
            except ValueError:
                continue
            if ip_obj in network:
                return True
        else:
            try:
                if ip_obj == ipaddress.ip_address(entry):
                    return True
            except ValueError:
                continue
    return False


def _leftmost_non_trusted(xff: str, trusted: list[str]) -> str | None:
    """Из цепочки `X-Forwarded-For: A, B, C` берём самый левый IP, который НЕ
    является доверенным proxy.

    Это и есть оригинальный клиент (proxy'и стоят правее в цепочке).
    Невалидные IP пропускаем — продолжаем по списку.
    """
    for raw in xff.split(","):
        ip = raw.strip()
        if not ip:
            continue
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            continue
        if not _is_trusted_proxy(ip, trusted):
            return ip
    return None


def extract_client_ip(
    request: "Request",
    trusted_proxy_ips: list[str] | None = None,
) -> str | None:
    """Безопасно достаёт IP клиента с учётом X-Forwarded-For + allow-list.

    Логика:
      1. Берём прямой `request.client.host`.
      2. Если он НЕ входит в `trusted_proxy_ips` → заголовкам доверять нельзя,
         возвращаем прямой client.host (или None, если его тоже нет).
      3. Если входит → парсим `X-Forwarded-For` (left-most non-trusted IP).
      4. Если XFF пуст или невалиден — fallback на `X-Real-IP`.
      5. Иначе возвращаем прямой `request.client.host`.

    `trusted_proxy_ips=None` или пустой список → ведём себя как если бы
    proxy'ев не было: возвращаем direct client.host без чтения заголовков.
    """
    trusted = list(trusted_proxy_ips or [])

    direct_ip: str | None = None
    if request.client and request.client.host:
        direct_ip = request.client.host

    if not direct_ip or not _is_trusted_proxy(direct_ip, trusted):
        return direct_ip

    xff = request.headers.get("X-Forwarded-For")
    if xff:
        original = _leftmost_non_trusted(xff, trusted)
        if original:
            return original

    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        real_ip = real_ip.strip()
        try:
            ipaddress.ip_address(real_ip)
            return real_ip
        except ValueError:
            pass

    return direct_ip
