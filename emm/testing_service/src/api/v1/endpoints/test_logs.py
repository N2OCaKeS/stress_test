"""Чтение/экспорт логов прогонов (§2.6, §8.4 плана миграции).

Открыто любому аутентифицированному актору (`AuthenticatedIdentity`) — то же
соображение, что и у остальных read-путей сервиса (каталог глобальных
переменных, список стендов): чтение лога не секрет и не завязано на
department-scope роль. Если позже понадобится более строгий гейт — заводить
его отдельным решением, не задним числом здесь.

`WS .../log/stream` (§8.6) — живой просмотр лога прямо во время исполнения
теста, для консоли сервера в web_ui. В отличие от интерактивной SSH-консоли
`server_service` (WS-мост через Redis pub/sub к реальному PTY на другом
конце), здесь на другом конце ничего живого нет — есть только растущая строка
`test_log_blobs.content`, которую пишет `testing_worker` через `log-chunk`/
`log-segment`. Поэтому вместо pub/sub — обычный поллинг той же Postgres, к
которой сервис и так подключён: раз в `_LOG_STREAM_POLL_INTERVAL_SECONDS`
перечитываем длину текста, если выросла — шлём клиенту только новый хвост.
Как только `queue_items.state` становится терминальным — последний хвост и
штатное закрытие (1000, reason `TEST_FINISHED`).
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, Query, Response, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketDisconnect, WebSocketState

from src.core.constants import ACTIVE_QUEUE_STATES
from src.core.exceptions import AppException, AuthenticationError
from src.db.session import AsyncSessionLocal
from src.dependencies import auth as auth_deps
from src.dependencies.auth import AuthenticatedIdentity
from src.dependencies.db import get_db
from src.repositories import queue_item as queue_item_repo
from src.repositories import test_log as test_log_repo
from src.repositories import test_log_blob as test_log_blob_repo
from src.schemas.common import PaginatedResponse
from src.schemas.test_log import TestLogSegmentResponse
from src.services import test_log as svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/queue-items")

# WS close-коды. 4401/4404/4503 — application-specific (диапазон 4000-4999
# свободен по RFC 6455), мапятся на привычные HTTP-семантики. 1000 — штатное
# закрытие по завершении теста, тот же код, что и у интерактивной консоли.
_WS_CLOSE_UNAUTHENTICATED = 4401
_WS_CLOSE_NOT_FOUND = 4404
_WS_CLOSE_UNAVAILABLE = 4503
_WS_CLOSE_NORMAL = 1000

# Маркер-subprotocol живого лога — эхо-нится в accept(), если клиент его
# предложил (браузер рвёт handshake, если сервер не подтвердит ни один из
# предложенных subprotocol'ов).
_LOG_STREAM_SUBPROTOCOL = "testing-log.v1"

# Пауза между перечитываниями `test_log_blobs.content`. Мутируется тестами
# через monkeypatch — не константа модуля-уровня в горячем пути.
_LOG_STREAM_POLL_INTERVAL_SECONDS = 1.0


def _ws_bearer(websocket: WebSocket) -> str | None:
    """Достать bearer-токен из WS-handshake.

    Middleware-стек (introspect/аудит) на WebSocket не выполняется, поэтому
    делаем это здесь руками — тот же приём, что и у интерактивной SSH-консоли
    `server_service` (`api/v1/endpoints/console.py::_ws_bearer`). Браузерный
    WS API не даёт задать произвольные заголовки, поэтому клиент кладёт токен
    в `Sec-WebSocket-Protocol` как `bearer.<token>`; заголовок `Authorization`
    поддержан на случай не-браузерного клиента (тесты, curl-подобные тулзы).
    """
    auth = websocket.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):].strip() or None
    for proto in websocket.scope.get("subprotocols", []):
        if isinstance(proto, str) and proto.startswith("bearer."):
            return proto[len("bearer."):].strip() or None
    return None


async def _authenticate_ws(token: str) -> None:
    """Introspect + ban-check — WS-эквивалент `get_authenticated_identity`.

    Без department-гейта: чтение лога открыто любому аутентифицированному
    актору, как и у HTTP-соседей этого роутера (`GET .../log`), поэтому здесь
    намеренно нет проверки `SERVICE_ACCESS_DENIED`.
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


@router.get(
    "/{queue_item_id}/log",
    summary="Скачать/просмотреть текст лога прогона",
    description=(
        "Без `from`/`to` — весь текст лога, отдаётся как attachment с "
        "человекочитаемым именем файла (§8.3: "
        "`<stand>_<testname>_<os_version>_<kernel>_<date>.log`). С одним или "
        "обоими параметрами — диапазон текста по офсетам "
        "(`test_log_segments.byte_offset_*`) — для клика по сегменту в UI. "
        "Невалидный диапазон (`from>to`, за пределами длины текста) — 422."
    ),
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
        404: {"description": "TEST_LOG_NOT_FOUND — для этого queue_item лога ещё нет."},
        422: {"description": "LOG_RANGE_INVALID — невалидный from/to."},
    },
)
async def get_log(
    queue_item_id: str,
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
    from_: int | None = Query(default=None, ge=0, alias="from", description="Начало диапазона (включительно)."),
    to_: int | None = Query(default=None, ge=0, alias="to", description="Конец диапазона (исключая)."),
) -> Response:
    """Get текста лога, целиком или диапазоном. Любой аутентифицированный актор."""
    log, content = await svc.get_log_range(db, queue_item_id, from_, to_)
    filename = await svc.build_filename(db, log)
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/{queue_item_id}/log/segments",
    response_model=PaginatedResponse[TestLogSegmentResponse],
    summary="Список чекпоинтов/команд лога",
    description=(
        "Метаданные сегментов без самого текста — офсеты для перехода к "
        "диапазону через `GET .../log?from=&to=`. `status` фильтрует "
        "(например `FATAL` — сразу видно, где упало, без чтения всего лога)."
    ),
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
        404: {"description": "TEST_LOG_NOT_FOUND — для этого queue_item лога ещё нет."},
    },
)
async def list_log_segments(
    queue_item_id: str,
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
    status_filter: str | None = Query(
        default=None, alias="status", description="Фильтр по статусу сегмента: OK / CHANGED / FATAL.",
    ),
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[TestLogSegmentResponse]:
    """List сегментов лога. Любой аутентифицированный актор."""
    items, total = await svc.list_segments(db, queue_item_id, status=status_filter, limit=limit, offset=offset)
    return PaginatedResponse[TestLogSegmentResponse](
        items=[TestLogSegmentResponse.model_validate(i) for i in items],
        total=total, limit=limit, offset=offset,
    )


@router.websocket("/{queue_item_id}/log/stream")
async def stream_log(websocket: WebSocket, queue_item_id: str) -> None:
    """Живой лог теста (§8.6 плана миграции) — read-only, отдельный от SSH-консоли.

    На подключении сразу шлём уже накопленный текст (если лог ещё не заведён
    — ничего, просто ждём появления). Дальше — цикл: раз в
    `_LOG_STREAM_POLL_INTERVAL_SECONDS` перечитываем `test_log_blobs.content`,
    выросло — шлём только новый хвост text-фреймом; параллельно следим за
    `queue_items.state` — как только он терминален, шлём финальный хвост и
    закрываемся штатно (1000, `TEST_FINISHED`). Каждая итерация открывает
    свою короткоживущую сессию — иначе ORM identity map отдавала бы закэшированные
    значения вместо свежих при повторных SELECT в одной открытой транзакции.

    Не мост к живому процессу (в отличие от `console.py`) — если оператор
    одновременно держит открытой интерактивную SSH-сессию на этот же стенд,
    это два независимых представления, они друг другу не мешают.
    """
    token = _ws_bearer(websocket)
    if token is None:
        await websocket.close(code=_WS_CLOSE_UNAUTHENTICATED, reason="ACCESS_TOKEN_MISSING")
        return
    try:
        await _authenticate_ws(token)
    except AuthenticationError as exc:
        await websocket.close(code=_WS_CLOSE_UNAUTHENTICATED, reason=exc.error_code)
        return
    except AppException as exc:
        await websocket.close(code=_WS_CLOSE_UNAVAILABLE, reason=exc.error_code)
        return

    offered = websocket.scope.get("subprotocols", [])
    await websocket.accept(
        subprotocol=_LOG_STREAM_SUBPROTOCOL if _LOG_STREAM_SUBPROTOCOL in offered else None,
    )

    sent_len = 0
    try:
        while True:
            async with AsyncSessionLocal() as db:
                item = await queue_item_repo.get_by_id(db, queue_item_id)
                content = ""
                if item is not None:
                    log = await test_log_repo.get_by_queue_item_id(db, queue_item_id)
                    if log is not None:
                        blob = await test_log_blob_repo.get_by_log_id(db, log.id)
                        content = blob.content if blob else ""

            if item is None:
                await websocket.close(code=_WS_CLOSE_NOT_FOUND, reason="QUEUE_ITEM_NOT_FOUND")
                return

            if len(content) > sent_len:
                await websocket.send_text(content[sent_len:])
                sent_len = len(content)

            if item.state not in ACTIVE_QUEUE_STATES:
                await websocket.close(code=_WS_CLOSE_NORMAL, reason="TEST_FINISHED")
                return

            if await _wait_disconnect_or_timeout(websocket, _LOG_STREAM_POLL_INTERVAL_SECONDS):
                return
    except WebSocketDisconnect:
        return
    except Exception:  # noqa: BLE001 — не ронять процесс на неожиданной ошибке WS
        logger.warning("log stream ws error for queue item %s", queue_item_id, exc_info=True)
        if websocket.application_state != WebSocketState.DISCONNECTED:
            try:
                await websocket.close(code=1011)
            except Exception:  # noqa: BLE001
                pass


async def _wait_disconnect_or_timeout(websocket: WebSocket, timeout: float) -> bool:
    """True — клиент отключился за это время; False — сработал таймаут опроса.

    Читаем raw `receive()` вместо простого `asyncio.sleep`, чтобы disconnect
    клиента обрывал ожидание сразу, а не только на следующей итерации поллинга.
    """
    try:
        message = await asyncio.wait_for(websocket.receive(), timeout=timeout)
    except asyncio.TimeoutError:
        return False
    return message.get("type") == "websocket.disconnect"
