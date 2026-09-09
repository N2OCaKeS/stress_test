"""Per-request audit-контекст — выставляется middleware'ом, читается `audit_service.emit`.

Изолирован по asyncio-таске через `contextvars`. FastAPI обрабатывает каждый
запрос в своей таске, так что одновременные запросы не пересекаются.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass
class AuditContext:
    """Контейнер per-request данных, попадающих в audit-payload."""

    actor_id: str | None = None
    username: str | None = None
    department_id: str | None = None
    department_name: str | None = None
    request_id: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    # "user" / "bot" / "pat" / "oauth_client" / "service" / None
    subject_type: str | None = None
    extra: dict = field(default_factory=dict)


_current: ContextVar[AuditContext | None] = ContextVar("audit_context", default=None)


def set_context(ctx: AuditContext) -> object:
    """Выставить контекст текущего запроса. Возвращает token для reset_context()."""
    return _current.set(ctx)


def reset_context(token: object) -> None:
    """Сбросить контекст по token'у из set_context()."""
    _current.reset(token)  # type: ignore[arg-type]


def get_context() -> AuditContext:
    """Текущий контекст; пустой, если не задан."""
    ctx = _current.get()
    return ctx if ctx is not None else AuditContext()


def update_context(**fields) -> None:
    """Дописать поля в текущий контекст. None'ы пропускаются (не перетирают)."""
    ctx = _current.get()
    if ctx is None:
        ctx = AuditContext()
        _current.set(ctx)
    for k, v in fields.items():
        if v is None:
            continue
        if hasattr(ctx, k):
            setattr(ctx, k, v)
        else:
            ctx.extra[k] = v
