"""Outermost middleware: буфер audit-outbox'а на время запроса.

`audit_service.emit()` не пишет в БД сам — он складывает готовый payload
в буфер текущего запроса. Этот middleware буфер открывает и в finally
сбрасывает в таблицу `audit_outbox` одним INSERT'ом.

Почему чистый ASGI, а не `BaseHTTPMiddleware`: он должен стоять снаружи
всех остальных, включая `AuditAccessMiddleware`, который эмитит http.*
уже ПОСЛЕ возврата из `call_next`. `BaseHTTPMiddleware` гоняет нижний
слой в отдельной task'е, и contextvar, выставленный внутри неё, наружу
не виден — поэтому scope ставится здесь, до всей цепочки. Сам объект
буфера мутабельный, так что append'ы из вложенных task'ов видны снаружи.
"""

from __future__ import annotations

from src.services import audit_outbox


class AuditOutboxScopeMiddleware:
    """Открыть буфер audit-событий на запрос, по завершении — записать."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        token = audit_outbox.begin_scope()
        try:
            await self.app(scope, receive, send)
        finally:
            # flush не бросает наружу (см. `audit_outbox.persist`) — ответ
            # пользователю от проблем с аудитом не зависит.
            await audit_outbox.flush_scope(token)
