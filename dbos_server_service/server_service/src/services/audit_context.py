# SOURCE OF TRUTH: dbos_server_service/sdk/extract_client_ip.py
# DUPE: keep `extract_client_ip` / `_is_trusted_proxy` / `_leftmost_non_trusted`
#       in sync with sdk/extract_client_ip.py и auth_service/src/services/audit_context.py.
"""Per-request audit-контекст — наполняется middleware'ом, читается `audit_service.emit`.

Позволяет любому вызову `emit(...)` автоматически подхватить:
  actor_id, username, department_id, request_id, ip_address, user_agent —
без передачи их в каждом вызове.

contextvar изолирован по asyncio-таске — FastAPI обрабатывает каждый запрос
в своей таске, так что одновременные запросы не пересекаются. Для sync-кода
(фоновые потоки в `asyncio.to_thread`) контекст наследуется автоматически.

Также содержит helper `extract_client_ip(request)` — безопасную замену
наивного парсинга `X-Forwarded-For`. Заголовку доверяем только если
`request.client.host` входит в `Settings.trusted_proxy_ips`; иначе
берём прямой client.host. Без allow-list любой мог бы поставить
`X-Forwarded-For: 1.2.3.4` и подделать IP в audit-log / обойти
rate-limit-by-IP. Симметрично с `auth_service/src/services/audit_context.py`.
"""

from __future__ import annotations

import ipaddress
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request


@dataclass
class AuditContext:
    """Контейнер per-request данных, попадающих в audit-payload."""

    actor_id: str | None = None
    username: str | None = None
    department_id: str | None = None
    request_id: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    # Тип субъекта — зеркалит `IdentityContext.subject_type`. Выставляется
    # в `get_current_identity` после introspect; `audit_service.emit`
    # подхватывает как actor_type, если caller явно не задал. Значения:
    # "user" / "bot" / "pat" / "oauth_client" / None (анонимный middleware).
    subject_type: str | None = None
    # Можно расширить произвольными ключами, которые попадут в details по умолчанию
    extra: dict = field(default_factory=dict)


_current: ContextVar[AuditContext | None] = ContextVar("audit_context", default=None)


def set_context(ctx: AuditContext) -> object:
    """Выставить контекст текущего запроса. Возвращает token для последующего reset()."""
    return _current.set(ctx)


def reset_context(token: object) -> None:
    """Сбросить контекст по token'у, полученному из set_context()."""
    _current.reset(token)  # type: ignore[arg-type]


def get_context() -> AuditContext:
    """Вернуть текущий контекст (пустой, если не задан)."""
    ctx = _current.get()
    return ctx if ctx is not None else AuditContext()


def update_context(**fields) -> None:
    """Дописать поля в текущий контекст. Создаёт новый, если не было."""
    ctx = _current.get()
    if ctx is None:
        ctx = AuditContext()
        _current.set(ctx)
    for k, v in fields.items():
        if hasattr(ctx, k) and v is not None:
            setattr(ctx, k, v)
        elif v is not None:
            ctx.extra[k] = v


# ── Безопасное извлечение IP клиента ──────────────────────────────────────────


def _is_trusted_proxy(client_ip: str, trusted: list[str]) -> bool:
    """True, если `client_ip` входит в allow-list `trusted` (точное совпадение или CIDR)."""
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
    """Из цепочки `X-Forwarded-For: A, B, C` (client, proxy1, proxy2) берём самый левый IP,
    который НЕ является доверенным proxy (left-most non-trusted)."""
    for raw in xff.split(","):
        ip = raw.strip()
        if not ip:
            continue
        # Невалидные IP пропускаем — следующий кандидат
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            continue
        if not _is_trusted_proxy(ip, trusted):
            return ip
    return None


def extract_client_ip(
    request: Request,
    trusted_proxy_ips: list[str] | None = None,
) -> str | None:
    """Безопасно достаёт IP клиента с учётом X-Forwarded-For + allow-list.

    Логика:
      1. Прямой `request.client.host`.
      2. Если он НЕ входит в `trusted_proxy_ips` → доверять заголовкам нельзя,
         возвращаем прямой client.host (или None).
      3. Если входит → парсим `X-Forwarded-For` (left-most non-trusted IP).
         При отсутствии XFF — fallback на `X-Real-IP`.
      4. Иначе возвращаем прямой `request.client.host`.

    `trusted_proxy_ips=None` (default) → читаем из `Settings.trusted_proxy_ips`.
    """
    if trusted_proxy_ips is None:
        # Lazy import — config может тянуть тяжёлые зависимости.
        from src.core.config import get_settings
        trusted_proxy_ips = list(get_settings().trusted_proxy_ips or [])

    direct_ip: str | None = None
    if request.client and request.client.host:
        direct_ip = request.client.host

    # Без allow-list или direct_ip вне него → заголовкам НЕ доверяем.
    if not direct_ip or not _is_trusted_proxy(direct_ip, trusted_proxy_ips):
        return direct_ip

    # direct_ip — доверенный proxy: можно парсить forwarded-headers.
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        original = _leftmost_non_trusted(xff, trusted_proxy_ips)
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
