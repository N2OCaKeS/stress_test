"""Интерактивная SSH-консоль — WebSocket-мост к серверу через worker.

`WS /api/server/v1/servers/{id}/console/ws` открывает живой PTY-терминал на
подготовленном (`is_managed`) сервере. server_service здесь — только мост:
worker не имеет своего HTTP/WS-сервера, поэтому транспорт между клиентским
WebSocket'ом и удалённым shell'ом идёт через Redis pub/sub (решение владельца).

Протокол (для UI-клиента):

  1. Клиент открывает WS с `Authorization: Bearer <token>` (subprotocol либо
     заголовок). server_service делает introspect, проверяет RBAC
     `(server, console)`, dept-видимость и `is_managed`.
  2. На отказе — WS закрывается с кодом и причиной (`PREPARE_REQUIRED`,
     `PERMISSION_DENIED`, ...) ДО accept'а либо сразу после.
  3. После accept'а server_service генерит `session_id` (`csn_<hex>`),
     публикует `start` в `console:ctl:<sid>` (worker поднимает PTY) и ждёт
     `{"event":"ready"}`. На `error` — закрывает WS.
  4. Мост: текст/байты из WS → publish `console:in:<sid>`; подписка на
     `console:out:<sid>` → отправка обратно в WS. Кадры в data-каналах —
     `{"data":"<base64>"}` (base64 поверх сырых байт терминала).
  5. На disconnect WS server_service публикует `stop` в `console:ctl:<sid>`.

Аудит: `ssh_console.session_open` (на старте сессии) / `ssh_console.session_close`
(на закрытии, с reason) эмитит server_service здесь. Per-command аудит
(`ssh_console.command`) делает worker — сырой ввод видит только он.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from src.core.constants import Action, EntityType, ServerStatus
from src.core.exceptions import (
    AppException,
    AuthenticationError,
    AuthorizationError,
    NotFoundError,
)
from src.db.session import AsyncSessionLocal
from src.dependencies import auth as auth_deps
from src.services import audit_service, permissions, worker_client
from src.services import server as server_svc
from src.utils.ids import console_session_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/servers/{server_id}")

# WebSocket close-коды. 4401/4403/4404/4409 — application-specific (диапазон
# 4000-4999 свободен по RFC 6455), мапятся на привычные HTTP-семантики.
_WS_CLOSE_UNAUTHENTICATED = 4401
_WS_CLOSE_FORBIDDEN = 4403
_WS_CLOSE_NOT_FOUND = 4404
_WS_CLOSE_CONFLICT = 4409
_WS_CLOSE_UNAVAILABLE = 4503
_WS_CLOSE_NORMAL = 1000

# Сколько ждём `ready` от worker'а после `start`-сигнала, прежде чем сдаться.
_READY_TIMEOUT_SECONDS = 15.0


def _ws_bearer(websocket: WebSocket) -> str | None:
    """Достать bearer-токен из WS-handshake.

    Middleware-стек (`platform_admin_guard` / `attach_request_id`) на
    WebSocket не выполняется, поэтому introspect делаем здесь руками.
    Принимаем токен из `Authorization: Bearer` либо из subprotocol
    `bearer.<token>` (браузерный WS API не даёт задать произвольные
    заголовки — клиент кладёт токен в Sec-WebSocket-Protocol).
    """
    auth = websocket.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):].strip() or None
    for proto in websocket.scope.get("subprotocols", []):
        if isinstance(proto, str) and proto.startswith("bearer."):
            return proto[len("bearer."):].strip() or None
    return None


async def _authenticate(token: str):
    """Introspect + ban/service-access check. Возвращает IdentityContext.

    Зеркалит `dependencies.auth.get_current_identity`, но без Request —
    WebSocket идёт мимо HTTP-dependency-стека.
    """
    body = await auth_deps._introspect(token)
    if not body.get("active"):
        raise AuthenticationError(
            error_code="ACCESS_TOKEN_INVALID",
            message="Token is invalid, expired or revoked",
        )
    identity = auth_deps._to_identity(body)
    if identity.is_banned:
        raise AuthenticationError(error_code="USER_BANNED", message="User is banned")
    if auth_deps.SERVICE_NAME not in identity.allowed_services:
        raise AuthorizationError(
            error_code="SERVICE_ACCESS_DENIED",
            message=f"User's department has no access to {auth_deps.SERVICE_NAME}",
        )
    return identity


@router.websocket("/console/ws")
async def server_console_ws(websocket: WebSocket, server_id: str) -> None:
    """Интерактивная SSH-консоль к серверу через WebSocket + Redis-мост.

    Доступ: `(server, console)`. Сервер обязан быть `is_managed` (прошёл
    prepare) — иначе close с причиной `PREPARE_REQUIRED`. Department-scope
    проверяется через `load_visible_server`.

    Связано: `server_worker/src/services/console_bridge.py` (PTY + bridge).
    """
    token = _ws_bearer(websocket)
    if token is None:
        await websocket.close(code=_WS_CLOSE_UNAUTHENTICATED, reason="ACCESS_TOKEN_MISSING")
        return

    # 1. Аутентификация + RBAC + visibility + prepared-gate. Любой отказ —
    # close с понятной причиной и failure-аудит `ssh_console.session_open`.
    try:
        identity = await _authenticate(token)
        async with AsyncSessionLocal() as db:
            await permissions.require_action(
                db, identity, EntityType.SERVER, Action.CONSOLE,
            )
            server = await server_svc.load_visible_server(db, identity, server_id)
    except AuthenticationError as exc:
        await websocket.close(code=_WS_CLOSE_UNAUTHENTICATED, reason=exc.error_code)
        return
    except AuthorizationError as exc:
        audit_service.emit(
            "ssh_console.session_open", target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        await websocket.close(code=_WS_CLOSE_FORBIDDEN, reason=exc.error_code)
        return
    except NotFoundError:
        audit_service.emit(
            "ssh_console.session_open", target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "not_found_or_cross_dept"},
        )
        await websocket.close(code=_WS_CLOSE_NOT_FOUND, reason="SERVER_NOT_FOUND")
        return
    except AppException as exc:
        await websocket.close(code=_WS_CLOSE_UNAVAILABLE, reason=exc.error_code)
        return

    # Decommissioned — не принимает worker-операций.
    if server.status == ServerStatus.DECOMMISSIONED:
        audit_service.emit(
            "ssh_console.session_open", target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "decommissioned", "department_id": server.department_id},
        )
        await websocket.close(code=_WS_CLOSE_CONFLICT, reason="SERVER_DECOMMISSIONED")
        return

    # Prepared-gate: console ходит по управляющему ключу, до prepare заходить
    # нечем — отбиваем PREPARE_REQUIRED (как inventory dispatch'и).
    if not server.is_managed:
        audit_service.emit(
            "ssh_console.session_open", target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "prepare_required", "department_id": server.department_id},
        )
        await websocket.close(code=_WS_CLOSE_CONFLICT, reason="PREPARE_REQUIRED")
        return

    session_id = console_session_id()
    await websocket.accept(
        subprotocol="bearer" if "bearer" in websocket.scope.get("subprotocols", []) else None,
    )
    audit_service.emit(
        "ssh_console.session_open", target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "session_id": session_id,
            "department_id": server.department_id,
            "management_user": server.management_user,
        },
    )

    reason = "client_disconnect"
    try:
        reason = await _bridge(websocket, identity, server, session_id)
    except WebSocketDisconnect:
        reason = "client_disconnect"
    except Exception:  # noqa: BLE001
        logger.warning("console ws bridge error for session %s", session_id, exc_info=True)
        reason = "bridge_error"
    finally:
        # Сигнал worker'у завершить PTY — best-effort.
        try:
            await worker_client.publish_console_control(session_id, {"action": "stop"})
        except Exception:  # noqa: BLE001
            logger.debug("console: stop publish failed", exc_info=True)
        if websocket.application_state != WebSocketState.DISCONNECTED:
            try:
                await websocket.close(code=_WS_CLOSE_NORMAL)
            except Exception:  # noqa: BLE001
                pass
        audit_service.emit(
            "ssh_console.session_close", target_id=server.id, target_type="server",
            status="success", allowed=True,
            details={
                "session_id": session_id,
                "reason": reason,
                "department_id": server.department_id,
            },
        )


async def _bridge(websocket: WebSocket, identity, server, session_id: str) -> str:
    """Запустить PTY у worker'а и мостить WS ↔ Redis. Возвращает reason закрытия.

    Шаги: publish `start` → ждать `ready` на control-канале (с таймаутом) →
    параллельные помпы client→in и out→client + слежение за `closed`/`error`
    в control-канале.
    """
    client = worker_client.get_worker_redis()
    own_client = client is not worker_client._prepare_redis_client
    ctl_ch = worker_client.console_ctl_channel(session_id)
    in_ch = worker_client.console_in_channel(session_id)
    out_ch = worker_client.console_out_channel(session_id)

    pubsub = client.pubsub()
    await pubsub.subscribe(ctl_ch, out_ch)
    try:
        # Старт PTY на worker'е. management_user / host / port server_service
        # знает из server-row; пароль аккаунта в console не участвует.
        await client.publish(ctl_ch, json.dumps({
            "action": "start",
            "server_id": server.id,
            "host": server.hostname,
            "ssh_port": server.ssh_port,
            "management_user": server.management_user,
            "target_department_id": server.department_id,
            "actor_id": identity.user_id,
        }))

        ready, err = await _await_ready(pubsub, ctl_ch, out_ch)
        if not ready:
            await _safe_send_text(websocket, json.dumps({"event": "error", "error_code": err or "CONSOLE_START_FAILED"}))
            return f"start_failed:{err or 'timeout'}"

        return await _pump(websocket, pubsub, client, in_ch)
    finally:
        try:
            await pubsub.unsubscribe(ctl_ch, out_ch)
            await pubsub.aclose()
        except Exception:  # noqa: BLE001
            logger.debug("console: ws pubsub close failed", exc_info=True)
        if own_client:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass


async def _await_ready(pubsub, ctl_ch: str, out_ch: str) -> tuple[bool, str | None]:
    """Дождаться `{"event":"ready"}` (или `error`) на control-канале.

    Возврат `(ready, error_code)`. Таймаут → `(False, "timeout")`.
    """
    async def _wait() -> tuple[bool, str | None]:
        async for message in pubsub.listen():
            if message is None or message.get("type") != "message":
                continue
            channel = _as_str(message.get("channel"))
            if channel != ctl_ch:
                continue
            event = _parse_ctl(message.get("data"))
            if event is None:
                continue
            ev = event.get("event")
            if ev == "ready":
                return True, None
            if ev in ("error", "closed"):
                return False, event.get("error_code") or event.get("reason")
        return False, "stream_ended"

    try:
        return await asyncio.wait_for(_wait(), timeout=_READY_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return False, "timeout"


async def _pump(websocket: WebSocket, pubsub, client, in_ch: str) -> str:
    """Двунаправленный мост: WS→in и (out/ctl pubsub)→WS. Возвращает reason."""
    client_task = asyncio.create_task(_pump_client_to_in(websocket, client, in_ch))
    redis_task = asyncio.create_task(_pump_redis_to_ws(websocket, pubsub))
    tasks = [client_task, redis_task]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for t in done:
            if not t.cancelled() and t.exception() is None:
                res = t.result()
                if isinstance(res, str):
                    return res
        return "closed"
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _pump_client_to_in(websocket: WebSocket, client, in_ch: str) -> str:
    """Читать кадры из WS, publish в `console:in:<sid>` (base64-frame)."""
    import base64
    while True:
        try:
            message = await websocket.receive()
        except WebSocketDisconnect:
            return "client_disconnect"
        if message.get("type") == "websocket.disconnect":
            return "client_disconnect"
        raw = message.get("bytes")
        if raw is None:
            text = message.get("text")
            if text is None:
                continue
            raw = text.encode("utf-8")
        frame = json.dumps({"data": base64.b64encode(raw).decode("ascii")})
        await client.publish(in_ch, frame)


async def _pump_redis_to_ws(websocket: WebSocket, pubsub) -> str:
    """Слушать pubsub: out-кадры → WS (как bytes); ctl `closed`/`error` → стоп."""
    import base64
    async for message in pubsub.listen():
        if message is None or message.get("type") != "message":
            continue
        channel = _as_str(message.get("channel"))
        if channel and channel.startswith(worker_client.CONSOLE_CTL_CHANNEL_PREFIX):
            event = _parse_ctl(message.get("data"))
            if event and event.get("event") in ("closed", "error"):
                return event.get("reason") or event.get("error_code") or "closed"
            continue
        # out-канал: распаковать base64-frame и отправить байты клиенту.
        decoded = _decode_out_frame(message.get("data"))
        if decoded is None:
            continue
        await websocket.send_bytes(decoded)
    return "stream_ended"


def _decode_out_frame(raw) -> bytes | None:
    import base64
    try:
        obj = json.loads(_as_str(raw))
        b64 = obj.get("data")
        if not isinstance(b64, str):
            return None
        return base64.b64decode(b64)
    except Exception:  # noqa: BLE001
        return None


def _parse_ctl(raw) -> dict | None:
    try:
        obj = json.loads(_as_str(raw))
        return obj if isinstance(obj, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _as_str(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return value if isinstance(value, str) else ""


async def _safe_send_text(websocket: WebSocket, text: str) -> None:
    try:
        if websocket.application_state == WebSocketState.CONNECTED:
            await websocket.send_text(text)
    except Exception:  # noqa: BLE001
        logger.debug("console: ws send failed", exc_info=True)
