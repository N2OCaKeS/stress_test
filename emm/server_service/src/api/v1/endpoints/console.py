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
     server_service делает introspect, проверяет dept-видимость сервера и
     право на консоль: ролевой `(server, console)` ЛИБО на учётке роль с
     `console`/`view_password`. Кто видит пароль учётки, тот ей и подключается.
     Аккаунт привязан и виден.
  2. На отказе — WS закрывается с кодом и причиной ДО accept'а либо сразу
     после: 4401 нет токена, 4400 нет `account_id`, 4403 нет права console /
     нет права на креды аккаунта, 4404 сервер/аккаунт не найден или не
     привязан, 4409 server decommissioned / сервер занят другим
     пользователем (`SERVER_BUSY`) / у аккаунта нет пароля / нет учётки теста
     (`TEST_CREDENTIALS_NOT_FOUND`, см. ниже), 4503 Redis недоступен.

  Пока сервер в `busy_state=testing` (идёт исполнение теста), консоль не
  блокируется — но `account_id` из query полностью игнорируется: подключение
  принудительно идёт под учёткой исполнения теста (`server_test_credentials`,
  см. `services/prepare_for_test.py`), а не под аккаунтом, который выбрал
  вызывающий. Право на консоль сервера при этом всё равно нужно — только
  серверный ролевой `(server, console)`, потому что учётку-то caller больше
  не выбирает, значит и account-level обход (`view_password` на конкретном
  аккаунте) здесь не при чём. Если тестовых кред на сервере нет — WS
  закрывается `TEST_CREDENTIALS_NOT_FOUND`, тихого фолбэка на обычный
  `account_id` нет.
  3. После accept'а server_service генерит `session_id` (`csn_<hex>`),
     стэшит креды в Redis под `dbos:console_creds:<ccd_id>`, публикует `start`
     (с `creds_stash_key`) в `console:ctl:<sid>` (worker поднимает PTY под
     аккаунтом) и ждёт `{"event":"ready"}`. На `error` — закрывает WS. Сразу
     после (и на create, и на reattach — см. п.6) клиенту уходит текстовый
     control-кадр `{"event":"session","session_id":"csn_..."}` — единственный
     JSON-текст, который когда-либо шлёт этот мост; всё остальное текстовое от
     клиента идёт как ввод терминала, всё бинарное от сервера — вывод PTY.
     Клиент обязан сохранить `session_id` (см. п.6) — это ключ к
     переподключению после разрыва.
  4. Мост: текст/байты из WS → publish `console:in:<sid>`; подписка на
     `console:out:<sid>` → отправка обратно в WS. Кадры в data-каналах —
     `{"data":"<base64>"}` (base64 поверх сырых байт терминала).

  5. Detach/reattach (grace-период). На обрыве браузерного WS сервер НЕ шлёт
     `stop` немедленно — PTY на worker'е принадлежит сессии (`session_id`), а
     не конкретному WebSocket'у. Вместо этого:
       * запись о сессии в Redis (`dbos:console_session:<sid>`, живёт по
         `CONSOLE_SESSION_REGISTRY_TTL_SECONDS`, метаданные:
         target/department/account/actor/login/credentials_source/kind +
         `token` текущего владения) получает укороченный TTL —
         `CONSOLE_REATTACH_GRACE_SECONDS` (дефолт 15 минут: переживает смену
         вкладки, сон ноутбука, короткий сетевой сбой — но не держит забытую
         сессию вечно);
       * фоновая задача либо дожидается этого таймаута (тогда публикует
         `stop` и снимает запись), либо раньше видит на control-канале, что
         worker сам снёс PTY (`idle_timeout`/`max_lifetime`) — тогда
         финализирует немедленно с настоящей причиной, не дожидаясь полного
         окна.
     Если реконнект (см. п.6) успевает перехватить `token` до истечения
     таймера — таймер, сработав, увидит несовпадение и молча выйдет: сессия
     осталась жить под новым владением.

     Grace — fallback-слой на РЕАЛЬНЫЕ обрывы (сеть моргнула, вкладку
     закрыли и переоткрыли в новой, браузер перезапустили — весь JS/registry
     на клиенте потерян, sessionStorage — единственное, что пережило).
     Обычная навигация ВНУТРИ SPA (клиент держит persistent-реестр живых
     WebSocket'ов вне React-дерева вкладки, см. `web_ui/src/lib/
     consoleSocketRegistry.ts`) grace вообще не касается — WS там не рвётся
     физически, компонент вкладки просто переподключается к уже живому
     сокету без единого сетевого запроса.

     Не всякий обрыв заслуживает grace. Два случая обходят его и останавливают
     PTY НЕМЕДЛЕННО (см. `_stop_immediately`/`_IMMEDIATE_STOP_REASONS`):
       * close-код клиента `1001` (`going away`) — реальное закрытие вкладки/
         окна или переход на другой сайт (не SPA-роутинг — тот persistent-
         реестром вообще не закрывает WS). Браузеры шлют этот код сами на
         unload — это единственный надёжный сигнал «физически всё, JS убит»,
         отличающий его от случайного сетевого обрыва (тот прилетает без кода
         или с другим).
       * close-код `4001` — наш собственный, шлёт сам клиент явно: кнопка
         «Отключить» и logout/принудительный signout на фронте закрывают ВСЕ
         живые консольные WS этим кодом (`closeAllConsoleSockets`) — здесь
         это тоже осознанное «я сюда не вернусь», а не разрыв связи.
     Бан/лок пользователя — известное ограничение: активная сессия, уже
     прошедшая grace/reattach-гейт, НЕ обрывается принудительно в момент
     бана (в системе вообще нет механизма kick'а живых WS по бану — revoke
     затрагивает только refresh-сессии/PAT, короткоживущий access-token
     догорает своим TTL). Следующая попытка reattach эту же полную
     auth/dept/role-проверку прогонит заново и упрётся в бан корректно —
     просто не мгновенно посреди уже открытой сессии.

     Ограничение: backlog вывода за время disconnect НЕ буферизуется и не
     восстанавливается. Клиент, переподключившись через grace/reattach (а не
     через persistent-реестр, где буфер — это сам xterm-Terminal, он никуда
     не делся), продолжает видеть только то, что PTY произведёт ПОСЛЕ
     реконнекта — то, что ушло в `console:out:` во время разрыва, потеряно
     (pub/sub, не очередь). Сознательный компромисс ради простоты; если это
     станет проблемой, нужен отдельный ring-buffer вывода на стороне worker'а.

  6. Reattach-протокол. Клиент, у которого есть `session_id` от прошлого
     подключения к ЭТОМУ ЖЕ (server_id/vm_id, account_id) — передаёт его вторым
     query-параметром: `?account_id=<acc>&session_id=<sid>`. server_service
     ВСЕГДА проходит полный гейт заново (auth/dept-видимость/busy-state/право
     на консоль/резолв кред аккаунта — ровно тот же путь, что и create), и
     только ПОСЛЕ этого решает, реальный ли это reattach:
       * запись `dbos:console_session:<sid>` существует;
       * её target_type/target_id/department_id/credentials_source/account_id/
         kind/actor_id совпадают с тем, что только что резолвнул гейт для
         ЭТОГО запроса (в частности — reattach доступен только тому же
         пользователю, что открыл сессию: session_id — секрет высокой
         энтропии, но cross-user reuse всё равно не нужен даже в теории);
       * worker подтвердил `pong` на `ping` по `console:ctl:<sid>` в течение
         `CONSOLE_REATTACH_PING_TIMEOUT_SECONDS` (иначе PTY уже мог умереть от
         idle/max-lifetime — запись в Redis об этом не узнала бы сама).
     Если что-то из этого не сошлось — `session_id` тихо игнорируется, и
     сервер идёт по обычному пути создания новой сессии (новый `session_id`,
     новый стэш кред, `start`). Явной ошибки клиенту не будет — ровно как если
     бы `session_id` не передавали вовсе.
  7. Single-attach (как в tmux). Одной консольной сессии — один живой
     WebSocket. Успешный reattach публикует `{"event":"takeover","token":...}`
     в `console:ctl:<sid>` ДО того как начать мостить; если на этом канале
     всё ещё сидит СТАРЫЙ WS-мост (пользователь открыл вторую вкладку не
     закрыв первую), он видит чужой `token`, прекращает помпу и закрывает
     клиентский WS кодом `4419 CONSOLE_TAKEN_OVER` — старая вкладка получает
     понятное сообщение «сессия открыта в другом месте», новая продолжает
     работу с того же PTY. Это осознанный выбор в пользу «работать из другой
     вкладки» вместо жёсткого отказа второму подключению.

Аудит: `ssh_console.session_open` (на старте сессии, включая reattach —
`details.reattach=true`) / `ssh_console.session_close` эмитит server_service.
`session_close` эмитится один раз на сессию, в момент реального прекращения
PTY (grace истёк без reattach, либо worker сам снёс PTY, либо старт вообще не
удался) — а не на каждый обрыв браузерного WS, потому что до grace-таймаута
PTY продолжает жить и переподключение — штатный сценарий, не закрытие. Эвикшн
старого WS реконнектом (`taken_over`) — вообще не событие закрытия сессии,
аудит по нему не пишется: сессией теперь владеет тот, кто её перехватил.
Per-command аудит (`ssh_console.command`) делает worker — сырой ввод видит
только он.
"""

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from src.core.config import get_settings
from src.core.constants import Action, BusyState, EntityType, ServerStatus
from src.core.exceptions import (
    AppException,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
)
from src.db.session import AsyncSessionLocal
from src.dependencies import auth as auth_deps
from src.services import audit_service, permissions, reservation, worker_client
from src.services import prepare_for_test as pft_svc
from src.services import server as server_svc
from src.services import server_account as account_svc
from src.services import vm as vm_svc
from src.utils.ids import console_creds_id, console_session_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/servers/{server_id}")
vm_router = APIRouter(prefix="/vms/{vm_id}")

# Виды консоли ВМ, которые обслуживает этот WS-мост (PTY-терминал). vnc/spice —
# графический прокси (`POST /vms/{id}/console`), сюда не приходят.
_VM_TERMINAL_KINDS = frozenset({"ssh", "serial"})

# WebSocket close-коды. 4400/4401/4403/4404/4409/4419 — application-specific
# (диапазон 4000-4999 свободен по RFC 6455), мапятся на привычные HTTP-семантики.
_WS_CLOSE_BAD_REQUEST = 4400
_WS_CLOSE_UNAUTHENTICATED = 4401
_WS_CLOSE_FORBIDDEN = 4403
_WS_CLOSE_NOT_FOUND = 4404
_WS_CLOSE_CONFLICT = 4409
_WS_CLOSE_TAKEN_OVER = 4419
_WS_CLOSE_UNAVAILABLE = 4503
_WS_CLOSE_NORMAL = 1000

# Close-коды, которые присылает КЛИЕНТ (не мы) и по которым grace-период
# однозначно бессмысленен — детали в модульном докстринге, пункт про
# «мгновенный обрыв vs SPA-навигация»:
#   * 1001 (going away) — реальное закрытие вкладки/окна или переход на
#     другой сайт (не SPA-роутинг!). Браузеры шлют его сами на unload —
#     переиспользуем как надёжный сигнал «это не временный обрыв».
#   * 4001 — наш собственный код для явного клиентского намерения оборвать
#     сессию прямо сейчас: кнопка «Отключить» и logout/бан на фронте (см.
#     `web_ui/src/lib/consoleSocketRegistry.ts`). Не путать с обычным
#     `client_disconnect` без кода — та ветка (сетевой сбой, случайный обрыв,
#     переход между страницами SPA при незавершённом переезде на persistent-
#     реестр) по-прежнему уходит в grace.
_CLIENT_CLOSE_GOING_AWAY = 1001
_CLIENT_CLOSE_INTENTIONAL = 4001
_IMMEDIATE_STOP_REASONS = frozenset({"tab_closed", "client_intentional_close"})

# Сколько ждём `ready` от worker'а после `start`-сигнала, прежде чем сдаться.
_READY_TIMEOUT_SECONDS = 15.0


def _client_disconnect_reason(code: int | None) -> str:
    """Классифицировать код закрытия, присланный КЛИЕНТОМ (не наш ответный
    close). `None`/что угодно ещё — обычный обрыв, идёт в grace как раньше."""
    if code == _CLIENT_CLOSE_GOING_AWAY:
        return "tab_closed"
    if code == _CLIENT_CLOSE_INTENTIONAL:
        return "client_intentional_close"
    return "client_disconnect"

# Живые grace-таймеры (см. `_enter_grace`) держим за сильную ссылку, иначе
# asyncio может собрать fire-and-forget задачу до её завершения.
_pending_grace_tasks: "set[asyncio.Task]" = set()


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


# ── Detach/reattach session registry (Redis) ────────────────────────────────
# `dbos:console_session:<sid>` — не транспорт (тот целиком в `worker_client`
# control/data-каналах), а метаданные владения: кто открыл сессию, под каким
# аккаунтом/департаментом/таргетом, и `token` текущего легитимного владельца.
# Токен — не session_id: session_id адресует PTY у worker'а и стабилен на всю
# жизнь сессии; token меняется при каждом (пере)подключении и служит ровно
# для двух вещей — отзыва старого live-моста (takeover) и CAS-подобной защиты
# grace-таймера от гонки с реконнектом (см. `_enter_grace`/`_try_finalize_grace`).


def _looks_like_session_id(value: str) -> bool:
    """Формат session_id, пришедшего от клиента, — тот же POSIX-набор, что
    генерит `console_session_id()` и что валидирует worker (`csn_<hex>`), но
    без жёсткой привязки к префиксу — важно не пустить в Redis-ключ мусор."""
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 64
        and all(c.isalnum() or c in "_-" for c in value)
    )


async def _read_session_registry(session_id: str) -> dict | None:
    """Best-effort GET. Недоступный Redis / незнакомый sid — просто `None`,
    как «сессии для reattach нет» — тот же тихий фолбэк, что и на невалидный
    `session_id` от клиента."""
    try:
        client = worker_client.get_worker_redis()
    except Exception:  # noqa: BLE001
        return None
    own = client is not worker_client._creds_redis_client
    try:
        raw = await client.get(worker_client.console_session_key(session_id))
    except Exception:  # noqa: BLE001
        logger.debug("console: session registry read failed", exc_info=True)
        return None
    finally:
        if own:
            await client.aclose()
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


async def _write_session_registry(session_id: str, meta: dict, *, ttl: int) -> None:
    """Best-effort SET. Реестр — удобство для reattach, не источник истины для
    самого транспорта; недоступный Redis не должен ронять живую PTY-сессию."""
    try:
        client = worker_client.get_worker_redis()
    except Exception:  # noqa: BLE001
        return
    own = client is not worker_client._creds_redis_client
    try:
        await client.set(
            worker_client.console_session_key(session_id), json.dumps(meta), ex=ttl,
        )
    except Exception:  # noqa: BLE001
        logger.debug("console: session registry write failed", exc_info=True)
    finally:
        if own:
            await client.aclose()


async def _delete_session_registry(session_id: str) -> None:
    try:
        client = worker_client.get_worker_redis()
    except Exception:  # noqa: BLE001
        return
    own = client is not worker_client._creds_redis_client
    try:
        await client.delete(worker_client.console_session_key(session_id))
    except Exception:  # noqa: BLE001
        logger.debug("console: session registry delete failed", exc_info=True)
    finally:
        if own:
            await client.aclose()


async def _ping_worker(session_id: str) -> bool:
    """Liveness-проба воркера перед reattach.

    Redis-реестр знает только то, что МЫ в него положили — если PTY умер сам
    (idle_timeout/max_lifetime) без ни одного подключённого клиента, реестр
    об этом не узнает до истечения своего TTL. `ping`/`pong` спрашивают
    напрямую владеющий session'ой worker-процесс: отвечает только тот, у кого
    сессия реально есть в `_ACTIVE_SESSIONS` (см. `console_bridge.py`).
    """
    settings = get_settings()
    try:
        client = worker_client.get_worker_redis()
    except Exception:  # noqa: BLE001
        return False
    own = client is not worker_client._creds_redis_client
    ctl_ch = worker_client.console_ctl_channel(session_id)
    try:
        pubsub = client.pubsub()
    except Exception:  # noqa: BLE001
        return False
    try:
        await pubsub.subscribe(ctl_ch)
        await client.publish(ctl_ch, json.dumps({"action": "ping"}))

        async def _wait() -> bool:
            async for message in pubsub.listen():
                if message is None or message.get("type") != "message":
                    continue
                event = _parse_ctl(message.get("data"))
                if event is None:
                    continue
                ev = event.get("event")
                if ev == "pong":
                    return True
                if ev in ("closed", "error"):
                    return False
            return False

        try:
            return await asyncio.wait_for(
                _wait(), timeout=settings.console_reattach_ping_timeout_seconds,
            )
        except asyncio.TimeoutError:
            return False
    except Exception:  # noqa: BLE001
        logger.debug("console: ping failed", exc_info=True)
        return False
    finally:
        try:
            await pubsub.unsubscribe(ctl_ch)
            await pubsub.aclose()
        except Exception:  # noqa: BLE001
            logger.debug("console: ping pubsub close failed", exc_info=True)
        if own:
            await client.aclose()


async def _try_reattach(
    websocket: WebSocket,
    *,
    target_type: str,
    target_id: str,
    department_id: str,
    account_id: str | None,
    credentials_source: str,
    actor_id: str,
    kind: str | None = None,
) -> str | None:
    """Проверить query `session_id` как кандидата на reattach.

    Возвращает `session_id`, если можно переиспользовать существующую PTY-
    сессию (запись в реестре матчит текущий, только что заново прогнанный
    гейт, и worker подтвердил pong), иначе `None` — вызывающий должен тихо
    откатиться на создание новой сессии, полностью игнорируя переданный
    `session_id` (невалидный/протухший/чужой — с точки зрения клиента разницы
    нет, ошибку это не поднимает).
    """
    requested = websocket.query_params.get("session_id")
    if not requested or not _looks_like_session_id(requested):
        return None
    meta = await _read_session_registry(requested)
    if meta is None:
        return None
    if (
        meta.get("target_type") != target_type
        or meta.get("target_id") != target_id
        or meta.get("department_id") != department_id
        or meta.get("credentials_source") != credentials_source
        or meta.get("account_id") != account_id
        or meta.get("actor_id") != actor_id
        or meta.get("kind") != kind
    ):
        return None
    if not await _ping_worker(requested):
        return None
    return requested


async def _claim_session(session_id: str, meta: dict, token: str) -> None:
    """(Пере)зарегистрировать сессию как «живую» под новым `token`.

    Вызывается и на свежем create (после `ready`), и на reattach (после
    успешного ping). Reattach безусловно перезаписывает запись новым токеном —
    это и есть механизм отзыва старого владения: старый live-мост увидит
    несовпадение токена в `takeover`-событии и сам уступит (`_pump_redis_to_ws`).
    """
    settings = get_settings()
    await _write_session_registry(
        session_id, {**meta, "token": token},
        ttl=settings.console_session_registry_ttl_seconds,
    )


async def _enter_grace(session_id: str, token: str, audit_meta: dict) -> None:
    """WS отключился, но PTY жив — сжать TTL записи до grace-окна и ждать.

    Если запись уже отсутствует или её `token` не наш — сессию кто-то успел
    перехватить (reattach) в промежутке между выходом из помпы и этим вызовом;
    в таком случае ничего не делаем, новый владелец сам распоряжается судьбой
    сессии.
    """
    settings = get_settings()
    meta = await _read_session_registry(session_id)
    if meta is None or meta.get("token") != token:
        return
    await _write_session_registry(
        session_id, meta, ttl=int(settings.console_reattach_grace_seconds),
    )
    task = asyncio.create_task(
        _grace_watch(session_id, token, audit_meta),
        name=f"console-grace-{session_id}",
    )
    _pending_grace_tasks.add(task)
    task.add_done_callback(_pending_grace_tasks.discard)


async def _try_finalize_grace(session_id: str, token: str) -> bool:
    """CAS-подобное завершение grace: удалить запись, если токен не сменился.

    `True` — мы владели сессией до конца grace-периода (или до worker-side
    close), значит именно мы отвечаем за `stop`/audit. `False` — реконнект
    успел перехватить токен раньше нас, финализация — не наша забота.
    """
    meta = await _read_session_registry(session_id)
    if meta is None or meta.get("token") != token:
        return False
    await _delete_session_registry(session_id)
    return True


async def _stop_immediately(
    session_id: str, token: str, audit_meta: dict, *, reason: str,
) -> None:
    """Снести PTY сразу, без grace — реальное закрытие вкладки (`tab_closed`,
    close-код 1001) или явный клиентский intentional-close
    (`client_intentional_close`, код 4001: кнопка «Отключить», logout/бан на
    фронте). В обоих случаях клиент точно не собирается переподключаться к
    ЭТОЙ сессии — ждать `CONSOLE_REATTACH_GRACE_SECONDS` бессмысленно.

    Переиспользует ту же CAS-проверку токена, что и истечение grace
    (`_try_finalize_grace`): маловероятная, но не невозможная гонка с уже
    случившимся reattach на этот же session_id страхуется одинаково в обоих
    путях финализации.
    """
    if not await _try_finalize_grace(session_id, token):
        return  # кто-то уже перехватил сессию — не наша забота
    try:
        await worker_client.publish_console_control(session_id, {"action": "stop"})
    except Exception:  # noqa: BLE001
        logger.debug("console: immediate stop publish failed", exc_info=True)
    audit_service.emit(
        "ssh_console.session_close",
        actor_id=audit_meta.get("actor_id"),
        target_id=audit_meta["target_id"], target_type=audit_meta["target_type"],
        status="success", allowed=True,
        department_id=audit_meta.get("department_id"),
        details={
            "session_id": session_id, "reason": reason,
            "account_id": audit_meta.get("account_id"), "login": audit_meta.get("login"),
            "credentials_source": audit_meta.get("credentials_source"),
        },
    )


async def _grace_watch(session_id: str, token: str, audit_meta: dict) -> None:
    """Ждать реконнект в течение grace-периода; иначе снести PTY.

    Параллельно слушает `console:ctl:<sid>`: если worker сам снесёт PTY раньше
    (idle_timeout/max_lifetime), закрываем сессию сразу с настоящей причиной,
    не дожидаясь полного grace-окна попусту.
    """
    settings = get_settings()
    grace_seconds = settings.console_reattach_grace_seconds
    worker_reason: str | None = None
    try:
        client = worker_client.get_worker_redis()
        own = client is not worker_client._creds_redis_client
        ctl_ch = worker_client.console_ctl_channel(session_id)
        pubsub = client.pubsub()
        try:
            await pubsub.subscribe(ctl_ch)

            async def _watch_worker_close() -> str | None:
                async for message in pubsub.listen():
                    if message is None or message.get("type") != "message":
                        continue
                    event = _parse_ctl(message.get("data"))
                    if event and event.get("event") == "closed":
                        return event.get("reason") or "worker_closed"
                return None

            try:
                worker_reason = await asyncio.wait_for(
                    _watch_worker_close(), timeout=grace_seconds,
                )
            except asyncio.TimeoutError:
                worker_reason = None
        finally:
            try:
                await pubsub.unsubscribe(ctl_ch)
                await pubsub.aclose()
            except Exception:  # noqa: BLE001
                logger.debug("console: grace watch pubsub close failed", exc_info=True)
            if own:
                await client.aclose()
    except Exception:  # noqa: BLE001
        # Redis недоступен для самого наблюдения — просто отсидим grace-окно
        # обычным sleep'ом и попробуем финализировать по истечении ниже.
        logger.debug("console: grace watch redis unavailable, falling back to sleep", exc_info=True)
        await asyncio.sleep(grace_seconds)

    if not await _try_finalize_grace(session_id, token):
        return  # реконнект успел перехватить сессию раньше нас

    reason = worker_reason or "grace_timeout"
    if worker_reason is None:
        # Worker сам не закрывался — значит PTY всё ещё жив, и это МЫ решаем
        # его прикончить по истечении grace-периода.
        try:
            await worker_client.publish_console_control(session_id, {"action": "stop"})
        except Exception:  # noqa: BLE001
            logger.debug("console: grace stop publish failed", exc_info=True)

    audit_service.emit(
        "ssh_console.session_close",
        actor_id=audit_meta.get("actor_id"),
        target_id=audit_meta["target_id"],
        target_type=audit_meta["target_type"],
        status="success", allowed=True,
        department_id=audit_meta.get("department_id"),
        details={
            "session_id": session_id,
            "reason": reason,
            "account_id": audit_meta.get("account_id"),
            "login": audit_meta.get("login"),
            "credentials_source": audit_meta.get("credentials_source"),
        },
    )


@router.websocket("/console/ws")
async def server_console_ws(websocket: WebSocket, server_id: str) -> None:
    """Интерактивная SSH-консоль к серверу под выбранным server_account'ом.

    Доступ: ролевой `(server, console)` ЛИБО на выбранном аккаунте роль с
    `console`/`view_password`. Держатель `view_password` подключается без
    отдельного console-гранта. Сервер НЕ
    обязан быть prepared — консоль коннектится
    под кредами аккаунта, не под управляющим ключом. `account_id` берётся из
    query-параметра (обязателен). Department-scope сервера и аккаунта —
    `load_visible_server` / resolve бутстрап-кред.

    Исключение — сервер в `busy_state=testing`: доступ не блокируется, но
    `account_id` игнорируется и креды резолвятся через тестовую учётку сервера
    (см. модульный докстринг выше).

    Опциональный query `session_id` — попытка reattach к уже открытой сессии
    (см. п.6/7 модульного докстринга). Гейт ниже прогоняется целиком в любом
    случае — reattach не ослабляет проверку прав.

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
    #
    # Доступ к консоли разрешён двумя путями: ролевой грант `(server, console)`
    # ИЛИ роль на учётке (`console`/`view_password`) — последнее проверяет
    # `resolve_console_credentials`. Поэтому здесь
    # серверный `console` не требуем жёстко: его отсутствие не отказ, а сигнал
    # «проверь право на учётке». Кто держит `view_password` на учётке (и так
    # видит её пароль), тот может ей и подключиться, даже без серверного console.
    try:
        identity = await _authenticate(token)
        async with AsyncSessionLocal() as db:
            has_server_console = await permissions.has_resource_action(
                db, identity, EntityType.SERVER, server_id, Action.CONSOLE,
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

            # Гейт обновления ОС: пока сервер `updating`, интерактивную консоль
            # не отдаём никому (даже владельцу/админу) — сессия посреди
            # astra-update мешала бы обновлению. Снимается callback'ом воркера.
            if server.busy_state == BusyState.UPDATING:
                audit_service.emit(
                    "ssh_console.session_open", target_id=server_id, target_type="server",
                    status="denied", allowed=False,
                    details={
                        "reason": "server_updating",
                        "department_id": server.department_id,
                        "account_id": account_id,
                    },
                )
                await websocket.close(code=_WS_CLOSE_CONFLICT, reason="SERVER_UPDATING")
                return

            # Бронь-гейт: сервер, занятый другим пользователем, консоль не
            # отдаёт никому — даже админу. Чтобы подключиться, админ сначала
            # снимает бронь (`busy_release`), сервер освобождается, дальше он
            # бронирует/подключается сам. Владелец брони заходит как обычно.
            if (
                server.busy_state == BusyState.BUSY
                and server.busy_user_id != identity.user_id
            ):
                audit_service.emit(
                    "ssh_console.session_open", target_id=server_id, target_type="server",
                    status="denied", allowed=False,
                    details={
                        "reason": "server_busy",
                        "department_id": server.department_id,
                        "account_id": account_id,
                        "busy_user_id": server.busy_user_id,
                    },
                )
                await websocket.close(code=_WS_CLOSE_CONFLICT, reason="SERVER_BUSY")
                return

            # `testing_done` — тест уже закончился, но статус ещё ждёт
            # подтверждения (`acknowledge_testing_done`); reservation.py
            # трактует эту стадию как обычную бронь (владелец/админ проходят).
            # Бронь тут всегда сервисная (`busy_user_id` пуст), поэтому
            # владельца-человека не бывает — пропускаем только админа.
            if (
                server.busy_state == BusyState.TESTING_DONE
                and server.busy_user_id != identity.user_id
                and not reservation.is_server_admin(identity, server)
            ):
                audit_service.emit(
                    "ssh_console.session_open", target_id=server_id, target_type="server",
                    status="denied", allowed=False,
                    details={
                        "reason": "server_testing_done",
                        "department_id": server.department_id,
                        "account_id": account_id,
                    },
                )
                await websocket.close(code=_WS_CLOSE_CONFLICT, reason="SERVER_TESTING_DONE")
                return

            # Account-гейт консоли: серверный `(server, console)` ИЛИ на учётке
            # `console`/`view_password` (роль/грант) + аккаунт виден/привязан +
            # есть пароль. Возвращает {"login", "password", "ssh_private_key"}.
            #
            # Исключение — сервер занят тестом (`busy_state=testing`): выбор
            # аккаунта вызывающим здесь не учитывается, консоль обязана идти
            # под учёткой самого теста, чтобы инженер мог просто посмотреть,
            # что происходит на стенде, не подбирая креды руками.
            # `account_id` из query в этом случае игнорируется полностью, а
            # account-level обход (console/view_password на конкретном
            # аккаунте) не применяется вовсе — единственная проверка права
            # остаётся серверная `has_server_console`.
            using_test_credentials = server.busy_state == BusyState.TESTING
            if using_test_credentials:
                if not has_server_console:
                    audit_service.emit(
                        "ssh_console.session_open", target_id=server_id, target_type="server",
                        status="denied", allowed=False,
                        details={
                            "reason": "permission_denied",
                            "account_id": account_id,
                            "busy_state": "testing",
                        },
                    )
                    await websocket.close(code=_WS_CLOSE_FORBIDDEN, reason="PERMISSION_DENIED")
                    return
                test_creds = await pft_svc.read_test_credentials(db, server.id)
                if test_creds is None:
                    audit_service.emit(
                        "ssh_console.session_open", target_id=server_id, target_type="server",
                        status="failure", allowed=True,
                        details={
                            "reason": "test_credentials_missing",
                            "account_id": account_id,
                            "busy_state": "testing",
                        },
                    )
                    await websocket.close(
                        code=_WS_CLOSE_CONFLICT, reason="TEST_CREDENTIALS_NOT_FOUND",
                    )
                    return
                creds = {
                    "login": test_creds["username"],
                    "password": test_creds["password"],
                    "ssh_private_key": test_creds["ssh_private_key"],
                }
            else:
                creds = await account_svc.resolve_console_credentials(
                    db, identity, account_id, server,
                    allow_via_server_console=has_server_console,
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

    # 2. Reattach-попытка (если клиент передал session_id) — гейт выше уже
    # прогнан целиком, дальше только сверка с реестром + liveness-ping. Не
    # сошлось — тихо создаём новую сессию, как будто session_id не передавали.
    credentials_source = "test" if using_test_credentials else "account"
    registry_account_id = None if using_test_credentials else account_id
    reattach_session_id = await _try_reattach(
        websocket,
        target_type="server", target_id=server.id,
        department_id=server.department_id,
        account_id=registry_account_id,
        credentials_source=credentials_source,
        actor_id=identity.user_id,
    )
    reattach = reattach_session_id is not None
    stash_key: str | None = None
    if reattach:
        session_id = reattach_session_id
    else:
        # Свежий стэш кред аккаунта в Redis под одноразовым ключом — в pub/sub
        # едет только ссылка. Redis недоступен → 4503.
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
            "credentials_source": credentials_source,
            "reattach": reattach,
        },
    )

    own_token = uuid.uuid4().hex
    registry_meta = {
        "target_type": "server", "target_id": server.id,
        "department_id": server.department_id,
        "account_id": registry_account_id,
        "login": creds["login"], "actor_id": identity.user_id,
        "credentials_source": credentials_source, "kind": None,
    }

    reason = "client_disconnect"
    try:
        reason = await _bridge(
            websocket, identity, server, session_id, stash_key, account_id,
            own_token=own_token, registry_meta=registry_meta, reattach=reattach,
        )
    except WebSocketDisconnect as exc:
        reason = _client_disconnect_reason(getattr(exc, "code", None))
    except Exception:  # noqa: BLE001
        logger.warning("console ws bridge error for session %s", session_id, exc_info=True)
        reason = "bridge_error"
    finally:
        # Подчистить creds-stash (worker читает его одной операцией и сам DEL'ит;
        # этот вызов закрывает окно, если worker не успел стартовать). На
        # reattach стэша нет вовсе — PTY уже поднят под прежними кредами.
        if stash_key is not None:
            try:
                await worker_client.delete_console_creds(stash_key)
            except Exception:  # noqa: BLE001
                logger.debug("console: creds stash cleanup failed", exc_info=True)
        if websocket.application_state != WebSocketState.DISCONNECTED:
            try:
                if reason == "taken_over":
                    await websocket.close(code=_WS_CLOSE_TAKEN_OVER, reason="CONSOLE_TAKEN_OVER")
                else:
                    await websocket.close(code=_WS_CLOSE_NORMAL)
            except Exception:  # noqa: BLE001
                pass

        if reason == "taken_over":
            # Эвикшн реконнектом — сессией теперь владеет другой WS, это не
            # закрытие сессии, аудит и Redis-реестр не трогаем.
            pass
        elif reason.startswith("start_failed"):
            # PTY так и не поднялся — нечего откладывать, закрываем сразу.
            audit_service.emit(
                "ssh_console.session_close", target_id=server.id, target_type="server",
                status="success", allowed=True,
                details={
                    "session_id": session_id, "reason": reason,
                    "department_id": server.department_id, "account_id": account_id,
                    "login": creds["login"], "credentials_source": credentials_source,
                },
            )
        elif reason in _IMMEDIATE_STOP_REASONS:
            # Реальное закрытие вкладки (1001) или явный клиентский intentional-
            # close (4001, logout/«Отключить») — клиент точно не переподключится
            # к ЭТОЙ сессии, grace бессмысленен. PTY жив (ready подтверждён) —
            # останавливаем его сейчас же, а не через 15 минут ожидания.
            await _stop_immediately(
                session_id, own_token,
                {
                    "target_id": server.id, "target_type": "server",
                    "department_id": server.department_id,
                    "account_id": account_id, "login": creds["login"],
                    "actor_id": identity.user_id,
                    "credentials_source": credentials_source,
                },
                reason=reason,
            )
        else:
            # PTY жив (ready подтверждён на create либо ping подтвердил его на
            # reattach) — не закрываем сессию сразу, даём grace-период на
            # переподключение.
            await _enter_grace(
                session_id, own_token,
                {
                    "target_id": server.id, "target_type": "server",
                    "department_id": server.department_id,
                    "account_id": account_id, "login": creds["login"],
                    "actor_id": identity.user_id,
                    "credentials_source": credentials_source,
                },
            )


@vm_router.websocket("/console/ws")
async def vm_console_ws(websocket: WebSocket, vm_id: str) -> None:
    """Интерактивная консоль ВМ под выбранным аккаунтом. Зеркало серверной.

    `WS /api/server/v1/vms/{id}/console/ws?account_id=<acc>&kind=ssh|serial`.
    Транспорт, аутентификация, close-коды и аудит — те же, что у
    `server_console_ws`, только цель — ВМ: `kind=ssh` открывает PTY в гостя
    через hub под учёткой (password-auth), `kind=serial` — `virsh console`
    домена на hub'е. Доступ — ролевой `console`/`view_password` на учётке;
    учётка привязана к ВМ (`server_account_vms`) и видима. Prepare не требуется.

    Опциональный query `session_id` — reattach, симметрично серверной консоли
    (см. модульный докстринг п.6/7); `kind` входит в сверку метаданных сессии,
    чтобы ssh- и serial-консоль одной ВМ не путались друг с другом.

    Связано: `server_worker/src/services/console_bridge.py` (hub→гость PTY).
    """
    token = _ws_bearer(websocket)
    if token is None:
        await websocket.close(code=_WS_CLOSE_UNAUTHENTICATED, reason="ACCESS_TOKEN_MISSING")
        return

    account_id = websocket.query_params.get("account_id")
    if not account_id:
        await websocket.close(code=_WS_CLOSE_BAD_REQUEST, reason="ACCOUNT_ID_REQUIRED")
        return

    kind = websocket.query_params.get("kind") or "ssh"
    if kind not in _VM_TERMINAL_KINDS:
        await websocket.close(code=_WS_CLOSE_BAD_REQUEST, reason="INVALID_CONSOLE_KIND")
        return

    try:
        identity = await _authenticate(token)
        async with AsyncSessionLocal() as db:
            vm, hub, creds = await vm_svc.resolve_vm_console_credentials(
                db, identity, vm_id, account_id,
            )
    except AuthenticationError as exc:
        await websocket.close(code=_WS_CLOSE_UNAUTHENTICATED, reason=exc.error_code)
        return
    except AuthorizationError as exc:
        audit_service.emit(
            "ssh_console.session_open", target_id=vm_id, target_type="vm",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "account_id": account_id, "kind": kind},
        )
        await websocket.close(code=_WS_CLOSE_FORBIDDEN, reason=exc.error_code)
        return
    except NotFoundError as exc:
        audit_service.emit(
            "ssh_console.session_open", target_id=vm_id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": "not_found_or_cross_dept", "account_id": account_id, "kind": kind},
        )
        await websocket.close(code=_WS_CLOSE_NOT_FOUND, reason=exc.error_code)
        return
    except ConflictError as exc:
        # ВМ занята другим (VM_RESERVED), hub недоступен или у учётки нет пароля.
        audit_service.emit(
            "ssh_console.session_open", target_id=vm_id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": exc.error_code, "account_id": account_id, "kind": kind},
        )
        await websocket.close(code=_WS_CLOSE_CONFLICT, reason=exc.error_code)
        return
    except AppException as exc:
        await websocket.close(code=_WS_CLOSE_UNAVAILABLE, reason=exc.error_code)
        return

    reattach_session_id = await _try_reattach(
        websocket,
        target_type="vm", target_id=vm.id,
        department_id=vm.department_id,
        account_id=account_id,
        credentials_source="account",
        actor_id=identity.user_id,
        kind=kind,
    )
    reattach = reattach_session_id is not None
    stash_key: str | None = None
    if reattach:
        session_id = reattach_session_id
    else:
        # Креды учётки в Redis-stash (в pub/sub едет только ссылка). Serial-консоль
        # аккаунт не использует, но стэшим одинаково — учётка выбрана в обоих видах.
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

    offered = websocket.scope.get("subprotocols", [])
    await websocket.accept(
        subprotocol="console.v1" if "console.v1" in offered else None,
    )
    audit_service.emit(
        "ssh_console.session_open", target_id=vm_id, target_type="vm",
        status="success", allowed=True,
        details={
            "session_id": session_id,
            "department_id": vm.department_id,
            "account_id": account_id,
            "login": creds["login"],
            "kind": kind,
            "reattach": reattach,
        },
    )

    own_token = uuid.uuid4().hex
    registry_meta = {
        "target_type": "vm", "target_id": vm.id,
        "department_id": vm.department_id,
        "account_id": account_id, "login": creds["login"],
        "actor_id": identity.user_id, "credentials_source": "account", "kind": kind,
    }

    start_message = None
    if not reattach:
        # start-сообщение: адресация hub'а (worker сам тянет его mgmt-ключ через
        # internal) + гость (домен/IP) + ссылка на креды учётки.
        start_message = {
            "action": "start",
            "target_type": "vm",
            "console_kind": kind,
            "vm_id": vm.id,
            "vm_domain": vm.name,
            "guest_ip": str(vm.ip_address) if vm.ip_address is not None else None,
            "creds_stash_key": stash_key,
            "account_id": account_id,
            "target_department_id": vm.department_id,
            "actor_id": identity.user_id,
            # hub-адресация для open_hub_session (SSH всегда по IP hub'а).
            "hub_server_id": hub.id,
            "server_id": hub.id,
            "host": str(hub.ip_address),
            "ssh_port": hub.ssh_port,
            "is_managed": hub.is_managed,
            "management_user": hub.management_user,
        }

    reason = "client_disconnect"
    try:
        reason = await _bridge_core(
            websocket, session_id, start_message,
            own_token=own_token, registry_meta=registry_meta,
        )
    except WebSocketDisconnect as exc:
        reason = _client_disconnect_reason(getattr(exc, "code", None))
    except Exception:  # noqa: BLE001
        logger.warning("vm console ws bridge error for session %s", session_id, exc_info=True)
        reason = "bridge_error"
    finally:
        if stash_key is not None:
            try:
                await worker_client.delete_console_creds(stash_key)
            except Exception:  # noqa: BLE001
                logger.debug("vm console: creds stash cleanup failed", exc_info=True)
        if websocket.application_state != WebSocketState.DISCONNECTED:
            try:
                if reason == "taken_over":
                    await websocket.close(code=_WS_CLOSE_TAKEN_OVER, reason="CONSOLE_TAKEN_OVER")
                else:
                    await websocket.close(code=_WS_CLOSE_NORMAL)
            except Exception:  # noqa: BLE001
                pass

        if reason == "taken_over":
            pass
        elif reason.startswith("start_failed"):
            audit_service.emit(
                "ssh_console.session_close", target_id=vm.id, target_type="vm",
                status="success", allowed=True,
                details={
                    "session_id": session_id, "reason": reason,
                    "department_id": vm.department_id, "account_id": account_id,
                    "login": creds["login"], "kind": kind,
                },
            )
        elif reason in _IMMEDIATE_STOP_REASONS:
            await _stop_immediately(
                session_id, own_token,
                {
                    "target_id": vm.id, "target_type": "vm",
                    "department_id": vm.department_id,
                    "account_id": account_id, "login": creds["login"],
                    "actor_id": identity.user_id,
                    "credentials_source": "account", "kind": kind,
                },
                reason=reason,
            )
        else:
            await _enter_grace(
                session_id, own_token,
                {
                    "target_id": vm.id, "target_type": "vm",
                    "department_id": vm.department_id,
                    "account_id": account_id, "login": creds["login"],
                    "actor_id": identity.user_id,
                    "credentials_source": "account", "kind": kind,
                },
            )


async def _bridge(
    websocket: WebSocket, identity, server, session_id: str,
    creds_stash_key: str | None, account_id: str, *,
    own_token: str, registry_meta: dict, reattach: bool,
) -> str:
    """Серверная консоль: собрать start-сообщение (create) либо переиспользовать
    существующую PTY-сессию (reattach) и уйти в общий мост."""
    start_message = None
    if not reattach:
        # Старт PTY на worker'е. host (IP, не hostname — короткие имена не
        # резолвятся из пода) / port server_service знает из server-row; логин/пароль
        # аккаунта лежат в Redis-stash, в start едет только ссылка `creds_stash_key`
        # — worker коннектится под аккаунтом по password-auth.
        start_message = {
            "action": "start",
            "server_id": server.id,
            "host": str(server.ip_address),
            "ssh_port": server.ssh_port,
            "creds_stash_key": creds_stash_key,
            "account_id": account_id,
            "target_department_id": server.department_id,
            "actor_id": identity.user_id,
        }
    return await _bridge_core(
        websocket, session_id, start_message,
        own_token=own_token, registry_meta=registry_meta,
    )


async def _bridge_core(
    websocket: WebSocket, session_id: str, start_message: dict | None,
    *, own_token: str, registry_meta: dict,
) -> str:
    """Поднять PTY у worker'а (create) либо переподключиться (reattach) и
    мостить WS ↔ Redis. Возвращает reason закрытия.

    `start_message is None` — reattach: PTY уже поднят и его liveness только
    что подтверждена pong'ом (`_try_reattach`), поэтому вместо `start`+`ready`
    публикуем `takeover` (отзывает старый живой мост на этом же session_id,
    если он есть — см. `_pump_redis_to_ws`) и сразу уходим в помпы. Иначе
    (create) — как раньше: `start` → ждать `ready` → помпы. В обоих случаях
    по входу в помпы сессия регистрируется в Redis (`_claim_session`) под
    `own_token` — это и есть точка, после которой сессия официально «живая» и
    доступна для будущего reattach/takeover.
    """
    client = worker_client.get_worker_redis()
    own_client = client is not worker_client._creds_redis_client
    ctl_ch = worker_client.console_ctl_channel(session_id)
    in_ch = worker_client.console_in_channel(session_id)
    out_ch = worker_client.console_out_channel(session_id)

    pubsub = client.pubsub()
    await pubsub.subscribe(ctl_ch, out_ch)
    try:
        if start_message is not None:
            await client.publish(ctl_ch, json.dumps(start_message))
            ready, err = await _await_ready(pubsub, ctl_ch, out_ch)
            if not ready:
                await _safe_send_text(websocket, json.dumps({"event": "error", "error_code": err or "CONSOLE_START_FAILED"}))
                return f"start_failed:{err or 'timeout'}"
        else:
            await client.publish(ctl_ch, json.dumps({"event": "takeover", "token": own_token}))

        await _claim_session(session_id, registry_meta, own_token)
        await _safe_send_text(websocket, json.dumps({"event": "session", "session_id": session_id}))
        return await _pump(websocket, pubsub, client, in_ch, own_token)
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


async def _pump(websocket: WebSocket, pubsub, client, in_ch: str, own_token: str) -> str:
    """Двунаправленный мост: WS→in и (out/ctl pubsub)→WS. Возвращает reason."""
    client_task = asyncio.create_task(_pump_client_to_in(websocket, client, in_ch))
    redis_task = asyncio.create_task(_pump_redis_to_ws(websocket, pubsub, own_token))
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
    """Читать кадры из WS, publish в `console:in:<sid>` (base64-frame).

    Возврат на disconnect классифицирован по close-коду клиента
    (`_client_disconnect_reason`) — caller решает по нему, входить в
    grace-период или останавливать PTY немедленно.
    """
    import base64
    while True:
        try:
            message = await websocket.receive()
        except WebSocketDisconnect as exc:
            return _client_disconnect_reason(getattr(exc, "code", None))
        if message.get("type") == "websocket.disconnect":
            return _client_disconnect_reason(message.get("code"))
        raw = message.get("bytes")
        if raw is None:
            text = message.get("text")
            if text is None:
                continue
            raw = text.encode("utf-8")
        frame = json.dumps({"data": base64.b64encode(raw).decode("ascii")})
        await client.publish(in_ch, frame)


async def _pump_redis_to_ws(websocket: WebSocket, pubsub, own_token: str) -> str:
    """Слушать pubsub: out-кадры → WS (как bytes); ctl `closed`/`error`/чужой
    `takeover` → стоп."""
    async for message in pubsub.listen():
        if message is None or message.get("type") != "message":
            continue
        channel = _as_str(message.get("channel"))
        if channel and channel.startswith(worker_client.CONSOLE_CTL_CHANNEL_PREFIX):
            event = _parse_ctl(message.get("data"))
            if event:
                ev = event.get("event")
                if ev in ("closed", "error"):
                    return event.get("reason") or event.get("error_code") or "closed"
                if ev == "takeover" and event.get("token") != own_token:
                    # Реконнект в другой вкладке/окне перехватил сессию —
                    # уступаем без публикации stop'а, PTY продолжает жить под
                    # новым владением.
                    return "taken_over"
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
