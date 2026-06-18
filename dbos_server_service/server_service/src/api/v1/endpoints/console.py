"""Интерактивная SSH-консоль — WebSocket-мост к серверу через worker.

`WS /api/server/v1/servers/{id}/console/ws?account_id=<acc>` открывает живой
PTY-терминал на сервере под ВЫБРАННЫМ server_account'ом. Консоль НЕ требует
prepare (`is_managed` не проверяется) и НИКОГДА не ходит под управляющим
dbos-ключом: server_service резолвит логин/пароль аккаунта (как
`resolve_bootstrap_credentials` у prepare account-режима), кладёт их в Redis-
stash, а worker коннектится под аккаунтом по password-auth. server_service —
только мост: worker не имеет своего HTTP/WS-сервера, транспорт между
клиентским WebSocket'ом и удалённым shell'ом идёт через Redis pub/sub.

Протокол (для UI-клиента):

  1. Клиент открывает WS с `Authorization: Bearer <token>` (subprotocol
     `bearer.<token>` либо заголовок) и обязательным query `account_id=<acc>`.
     server_service делает introspect, проверяет RBAC `(server, console)`,
     dept-видимость сервера, затем резолвит креды аккаунта (per-account грант
     `console` ЛИБО право `(server_account, view_password)` — роль или грант;
     аккаунт привязан и виден).
  2. На отказе — WS закрывается с кодом и причиной ДО accept'а либо сразу
     после: 4401 нет токена, 4400 нет `account_id`, 4403 нет права console /
     нет права на креды аккаунта, 4404 сервер/аккаунт не найден или не
     привязан, 4409 server decommissioned / у аккаунта нет пароля, 4503 Redis
     недоступен.
  3. После accept'а server_service генерит `session_id` (`csn_<hex>`),
     стэшит креды в Redis под `dbos:console_creds:<ccd_id>`, публикует `start`
     (с `creds_stash_key`) в `console:ctl:<sid>` (worker поднимает PTY под
     аккаунтом) и ждёт `{"event":"ready"}`. На `error` — закрывает WS.
  4. Мост: текст/байты из WS → publish `console:in:<sid>`; подписка на
     `console:out:<sid>` → отправка обратно в WS. Кадры в data-каналах —
     `{"data":"<base64>"}` (base64 поверх сырых байт терминала).
  5. На disconnect WS server_service публикует `stop` в `console:ctl:<sid>`.

Аудит: `ssh_console.session_open` (на старте сессии) / `ssh_console.session_close`
(на закрытии, с reason) эмитит server_service здесь — details несут
`account_id`/`login`, чтобы trail показывал, под каким аккаунтом шла консоль.
Per-command аудит (`ssh_console.command`) делает worker — сырой ввод видит
только он.
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
    ConflictError,
    NotFoundError,
)
from src.db.session import AsyncSessionLocal
from src.dependencies import auth as auth_deps
from src.services import audit_service, permissions, worker_client
from src.services import server as server_svc
from src.services import server_account as account_svc
from src.utils.ids import console_creds_id, console_session_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/servers/{server_id}")

# WebSocket close-коды. 4400/4401/4403/4404/4409 — application-specific
# (диапазон 4000-4999 свободен по RFC 6455), мапятся на привычные HTTP-семантики.
_WS_CLOSE_BAD_REQUEST = 4400
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
    """Интерактивная SSH-консоль к серверу под выбранным server_account'ом.

    Доступ: `(server, console)` + на выбранном аккаунте либо per-account грант
    `console`, либо `(server_account, view_password)` (роль/грант). Сервер НЕ
    обязан быть prepared — консоль коннектится
    под кредами аккаунта, не под управляющим ключом. `account_id` берётся из
    query-параметра (обязателен). Department-scope сервера и аккаунта —
    `load_visible_server` / resolve бутстрап-кред.

    Связано: `server_worker/src/services/console_bridge.py` (PTY + bridge).
    """
    token = _ws_bearer(websocket)
    if token is None:
        await websocket.close(code=_WS_CLOSE_UNAUTHENTICATED, reason="ACCESS_TOKEN_MISSING")
        return

    account_id = websocket.query_params.get("account_id")
    if not account_id:
        await websocket.close(code=_WS_CLOSE_BAD_REQUEST, reason="ACCOUNT_ID_REQUIRED")
        return

    # 1. Аутентификация + RBAC console + visibility сервера + резолв кред
    # выбранного аккаунта. Любой отказ — close с понятной причиной и
    # failure/denied-аудит `ssh_console.session_open`.
    try:
        identity = await _authenticate(token)
        async with AsyncSessionLocal() as db:
            await permissions.require_action(
                db, identity, EntityType.SERVER, Action.CONSOLE,
            )
            server = await server_svc.load_visible_server(db, identity, server_id)

            # Decommissioned-gate ДО резолва кред — не дёргаем секреты, если
            # сервер всё равно не примет console-сессию.
            if server.status == ServerStatus.DECOMMISSIONED:
                audit_service.emit(
                    "ssh_console.session_open", target_id=server_id, target_type="server",
                    status="failure", allowed=True,
                    details={
                        "reason": "decommissioned",
                        "department_id": server.department_id,
                        "account_id": account_id,
                    },
                )
                await websocket.close(code=_WS_CLOSE_CONFLICT, reason="SERVER_DECOMMISSIONED")
                return

            # Account-гейт консоли: per-account грант `console` ЛИБО доступ
            # view_password (роль/грант) + аккаунт виден/привязан + есть пароль.
            # Возвращает {"login", "password", "ssh_private_key"}.
            creds = await account_svc.resolve_console_credentials(
                db, identity, account_id, server,
            )
    except AuthenticationError as exc:
        await websocket.close(code=_WS_CLOSE_UNAUTHENTICATED, reason=exc.error_code)
        return
    except AuthorizationError as exc:
        audit_service.emit(
            "ssh_console.session_open", target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "account_id": account_id},
        )
        await websocket.close(code=_WS_CLOSE_FORBIDDEN, reason=exc.error_code)
        return
    except NotFoundError as exc:
        audit_service.emit(
            "ssh_console.session_open", target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "not_found_or_cross_dept", "account_id": account_id},
        )
        await websocket.close(code=_WS_CLOSE_NOT_FOUND, reason=exc.error_code)
        return
    except ConflictError as exc:
        # Аккаунт без сохранённого пароля — консоль под ним не поднять.
        audit_service.emit(
            "ssh_console.session_open", target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "account_has_no_password", "account_id": account_id},
        )
        await websocket.close(code=_WS_CLOSE_CONFLICT, reason=exc.error_code)
        return
    except AppException as exc:
        await websocket.close(code=_WS_CLOSE_UNAVAILABLE, reason=exc.error_code)
        return

    # 2. Стэш кред аккаунта в Redis под одноразовым ключом — в pub/sub едет
    # только ссылка. Redis недоступен → 4503.
    session_id = console_session_id()
    stash_key = worker_client.console_creds_key(console_creds_id())
    try:
        await worker_client.store_console_creds(
            stash_key,
            {
                "login": creds["login"],
                "password": creds["password"],
                "ssh_private_key": creds.get("ssh_private_key"),
            },
        )
    except AppException as exc:
        await websocket.close(code=_WS_CLOSE_UNAVAILABLE, reason=exc.error_code)
        return

    # Браузер валит handshake (close 1006), если клиент предложил
    # subprotocol'ы, а сервер не подтвердил ни один из них. Клиент шлёт
    # `console.v1` (маркер) + `bearer.<token>`; токен мы уже вынули выше, а в
    # ответ эхо-ним именно `console.v1` — он всегда в списке предложенных.
    offered = websocket.scope.get("subprotocols", [])
    await websocket.accept(
        subprotocol="console.v1" if "console.v1" in offered else None,
    )
    audit_service.emit(
        "ssh_console.session_open", target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "session_id": session_id,
            "department_id": server.department_id,
            "account_id": account_id,
            "login": creds["login"],
        },
    )

    reason = "client_disconnect"
    try:
        reason = await _bridge(websocket, identity, server, session_id, stash_key, account_id)
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
        # Подчистить creds-stash (worker читает его одной операцией и сам DEL'ит;
        # этот вызов закрывает окно, если worker не успел стартовать).
        try:
            await worker_client.delete_console_creds(stash_key)
        except Exception:  # noqa: BLE001
            logger.debug("console: creds stash cleanup failed", exc_info=True)
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
                "account_id": account_id,
                "login": creds["login"],
            },
        )


async def _bridge(
    websocket: WebSocket, identity, server, session_id: str,
    creds_stash_key: str, account_id: str,
) -> str:
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
        # Старт PTY на worker'е. host / port server_service знает из server-row;
        # логин/пароль аккаунта лежат в Redis-stash, в start едет только ссылка
        # `creds_stash_key` — worker коннектится под аккаунтом по password-auth.
        await client.publish(ctl_ch, json.dumps({
            "action": "start",
            "server_id": server.id,
            "host": server.hostname,
            "ssh_port": server.ssh_port,
            "creds_stash_key": creds_stash_key,
            "account_id": account_id,
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
