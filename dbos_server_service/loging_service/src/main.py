"""Application entrypoint loging_service'а."""

import asyncio
import logging
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

from src.api.router import api_router
from src.core.config import get_settings
from src.core.constants import ADVISORY_LOCKS
from src.core.exceptions import AppException
from src.core.https_guard import HTTPSRequiredMiddleware
from src.core.limiter import limiter
from src.core.logging import configure_logging
from src.dependencies import auth as auth_deps
from src.services.audit_outbox import (
    AuditEnvelope,
    AuditOutbox,
    _resolve_actor_type,
    make_envelope,
    write_envelope_to_db,
)


# Self-audit outbox: middleware `audit_access` пишет события через
# `_audit_outbox.push_nowait(...)`, drain-loop в lifespan'е выгребает их
# батчами под одним pooled-коннектом. До этого каждый http.* event запускал
# собственный `asyncio.to_thread(_emit_audit)`, который открывал свой
# `SessionLocal()` — под всплеском admin/reader-трафика пул упирался в cap.
#
# Сам инстанс создаётся в `_build_audit_outbox()` лениво (per-application),
# чтобы тесты могли подменить session_factory / writer через monkeypatch до
# первого push'а.
_audit_outbox: "AuditOutbox | None" = None


# Strict-numeric content-length: `int()` принимает `+1`, `_`-сепараторы,
# окружающие пробелы и юникодные digits — всё это потенциальные smuggling
# поверхности на стеке без h11 0.16 (или прокси, нормализующего header).
# RFC 9110 разрешает только ASCII `0-9`. Pattern явно `[0-9]+` (а не `\d+`):
# без флага re.ASCII шаблон `\d` в Python матчит весь Unicode-class Nd,
# включая Devanagari `१२३` и арабские `٠١٢` — `int()` их тоже принимает,
# и в результате malformed Content-Length прошёл бы guard. fullmatch
# гарантирует, что после strip'а строка состоит из этих цифр и ничего больше.
_CONTENT_LENGTH_RE = re.compile(r"[0-9]+")


# SOURCE OF TRUTH: dbos_server_service/sdk/security_headers.py
# DUPE: keep in sync with auth_service/loging_service/server_service security_headers.py
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Базовые security headers (HSTS опционально, X-Frame-Options, CSP).

    HSTS включается только при `SECURITY_HSTS_ENABLED=true` — за http-фронтом
    он сломает rebound. Зеркалит auth_service.
    """

    def __init__(self, app, *, hsts_enabled: bool):
        super().__init__(app)
        self._hsts_enabled = hsts_enabled

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )
        # CSP для JSON-API минимальный — disallow всё лишнее. Swagger UI
        # на /docs (dev-only) тянет свои inline-скрипты, но `frame-ancestors
        # 'none'` парный с X-Frame-Options прикрывает clickjacking.
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'",
        )
        # Permissions-Policy: JSON-API без UI, зануляем sensor-API на случай,
        # если когда-нибудь появится браузерный клиент.
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=(), payment=(), usb=()",
        )
        if self._hsts_enabled:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=63072000; includeSubDomains",
            )
        return response


_REQUEST_ID_SAFE_CHARS = re.compile(r"[^A-Za-z0-9_.\-]")


def _sanitize_request_id(raw: str | None) -> str:
    """Возвращает безопасный `request_id`: либо санитизированный header,
    либо свежесгенерированный `req_<hex>`.

    Порядок шагов — cap → strip CR/LF/NUL → drop out-of-charset → strip → cap.
    Charset синхронизирован со схемным `_REQUEST_ID_PATTERN`
    (`[A-Za-z0-9_.\\-]{1,64}`); без этого envelope-payload c символами вроде
    `;` или `(` проходил middleware-reflection, но падал на drain'е при
    `EventCreate(...)` валидации и терялся как self-audit-failure.

    Вынесено как top-level helper, чтобы тот же id мог поднять и body-size
    middleware (он outermost, до attach_request_id, иначе 413/400 envelope
    остаётся без `X-Request-ID` для корреляции).
    """
    if raw is not None:
        sanitized = (
            raw[:256]
            .replace("\r", "")
            .replace("\n", "")
            .replace("\x00", "")
        )
        sanitized = _REQUEST_ID_SAFE_CHARS.sub("", sanitized).strip()[:64]
        if sanitized:
            return sanitized
    return f"req_{uuid.uuid4().hex[:12]}"


def create_application() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.app_log_level)

    # `staging` приравнивается к `production`: prod-guard'ы (TLS-enforcement,
    # отказ от persistAuthorization в Swagger UI, валидация SERVICE_API_KEY)
    # обязаны срабатывать и на стенде — модель угроз одинаковая.
    _is_prod = settings.app_env in ("production", "staging")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Долгоживущие ресурсы: pooled introspect-клиент + retention-цикл.

        Startup:
          * Подпуливаем один `httpx.AsyncClient` для `_fetch_identity` →
            auth_service. Bounded limits защищают от slowloris-amplification,
            когда N параллельных reader-запросов иначе открывали бы N свежих
            TCP+TLS handshake'ов (как в `server_service`). Скипаем, если
            `AUTH_SERVICE_URL` не задан (early-dev конфиги); `_fetch_identity`
            тогда поднимет `AUTH_SERVICE_NOT_CONFIGURED` на первом обращении.
          * Стартуем retention-cleanup daemon-thread (раньше висел на
            `@app.on_event("startup")`, теперь свёрнут в lifespan, чтобы
            убрать FastAPI DeprecationWarning). Под `RETENTION_LOOP_ENABLED=False`
            пропускается — нужно тестам, у которых module-level `SessionLocal`
            всё равно смотрит мимо TEST_DATABASE_URL.

        Shutdown:
          * Закрываем pooled-клиент (`await aclose()`). Retention-тред —
            daemon, умирает вместе с процессом.
        """
        # `settings` снаружи — снимок на момент `create_application()`. Тесты
        # дёргают `monkeypatch.setenv(...)` + `get_settings.cache_clear()`
        # между импортом модуля и стартом lifespan'а, поэтому здесь
        # перечитываем актуальный `get_settings()`. Без этого тестовые
        # переопределения (`AUTH_SERVICE_URL`, `RETENTION_LOOP_ENABLED`)
        # игнорируются.
        live_settings = get_settings()
        url = live_settings.auth_service_url
        if url:
            base = url.rstrip("/")
            timeout = httpx.Timeout(
                live_settings.introspect_timeout_seconds,
                connect=live_settings.introspect_connect_timeout_seconds,
            )
            # `verify`: bool, либо путь к PEM-bundle для self-signed CA.
            # bundle переопределяет bool=True (verify по bundle вместо
            # системного store); при verify=False bundle игнорируется и
            # проверка выключена полностью.
            verify_param: bool | str = live_settings.introspect_tls_verify
            if verify_param and live_settings.introspect_tls_ca_bundle:
                verify_param = live_settings.introspect_tls_ca_bundle
            auth_deps._introspect_client = httpx.AsyncClient(
                base_url=base,
                timeout=timeout,
                limits=httpx.Limits(
                    max_connections=live_settings.introspect_pool_max_connections,
                    max_keepalive_connections=live_settings.introspect_pool_max_keepalive,
                ),
                verify=verify_param,
            )
            # Pooled клиент для `POST /token` swagger-логина. Лимиты скромнее
            # introspect'а — логины редкие, а одна полу-висящая connection
            # не должна забирать pool-slot у hot-path introspect'а. Бюджет
            # делим с introspect'ом — параметры аутентификации (Argon2id
            # + lockout) живут за тем же auth_service, отдельный SLA не нужен.
            auth_deps._token_proxy_client = httpx.AsyncClient(
                base_url=base,
                timeout=httpx.Timeout(
                    live_settings.introspect_timeout_seconds,
                    connect=live_settings.introspect_connect_timeout_seconds,
                ),
                limits=httpx.Limits(
                    max_connections=live_settings.token_proxy_pool_max_connections,
                    max_keepalive_connections=live_settings.token_proxy_pool_max_keepalive,
                ),
                verify=verify_param,
            )
        # Rate-limit storage backend: фиксируем в логах + WARNING для prod
        # на memory://. SlowAPI хранит счётчики в этом backend'е; per-pod
        # memory:// под multi-replica раздаёт фактически N × лимит.
        storage_uri = live_settings.rate_limit_storage_uri or "memory://"
        logger.info("rate_limit_storage: %s", storage_uri)
        if storage_uri == "memory://" and live_settings.app_env in (
            "production", "staging"
        ):
            logger.warning(
                "rate_limit_storage=memory:// in %s: ingest/query window "
                "expands per-replica. Set RATE_LIMIT_STORAGE_URI=redis://...",
                live_settings.app_env,
            )

        # Порядок старта: сначала self-audit outbox, потом retention-thread.
        # Retention эмитит `logging.retention_sweep` через outbox; если
        # outbox упадёт на старте, retention-thread не должен оставаться
        # живым с полу-инициализированным окружением (writes пойдут в
        # sync-fallback с `SessionLocal()` на каждое событие).
        # `_build_audit_outbox + start()` обёрнуты в try/except: если
        # они упадут — retention НЕ стартуем, lifespan продолжит yield
        # с already-broken outbox-каналом, alert-сигналом будет CRITICAL
        # из `_audit_drain_task_done_callback` либо self-audit-failure
        # counter в health-probe.
        global _audit_outbox
        outbox_ready = False
        if live_settings.audit_outbox_enabled:
            try:
                _audit_outbox = _build_audit_outbox(live_settings)
                _audit_outbox.start(asyncio.get_running_loop())
                outbox_ready = True
                # Drain-task создаётся внутри `start()` через `loop.create_task`,
                # без callback. Если drain упадёт по unhandled exception (см.
                # `_drain_loop` generic-except), task завершится тихо: `qsize`
                # начнёт расти, `push_nowait` уйдёт в sync-fallback (новая
                # `SessionLocal()` на каждое событие), `dropped_after_stop_total`
                # пробудит SIEM только после `stop()`. Регистрируем CRITICAL-лог
                # на unexpected exit, чтобы оператор увидел инцидент сразу.
                drain_task = _audit_outbox._drain_task
                if drain_task is not None:
                    drain_task.add_done_callback(_audit_drain_task_done_callback)
            except Exception as exc:
                logger.critical(
                    "audit outbox failed to start: %s — retention thread NOT spawned",
                    exc,
                    exc_info=True,
                )
                _audit_outbox = None
        else:
            outbox_ready = True
        if live_settings.retention_loop_enabled and outbox_ready:
            # Retention-цикл — daemon thread, как и старый on_event hook.
            t = threading.Thread(target=_retention_loop_supervised, daemon=True)
            t.start()
        try:
            yield
        finally:
            # Graceful drain self-audit outbox'а с бюджетом
            # `audit_drain_timeout_seconds`. Что не успело — теряется и
            # логируется как ERROR (см. `_drain_remaining`).
            # K8s-инвариант: `terminationGracePeriodSeconds` в
            # `k8s/deployment.yaml` должен покрывать
            # `AUDIT_DRAIN_TIMEOUT_SECONDS` + buffer на aclose клиентов
            # + retention-thread join. При повышении drain-таймаута
            # синхронно поднять grace-period в манифесте.
            outbox = _audit_outbox
            if outbox is not None:
                drain_timeout = live_settings.audit_drain_timeout_seconds
                try:
                    await outbox.stop(timeout=drain_timeout)
                except Exception as exc:
                    logger.error(
                        "audit outbox shutdown failed: %s", exc, exc_info=True
                    )
                _audit_outbox = None
            # Каждый `aclose` под своим try/except: если первый клиент упал
            # на shutdown'е (например, transport уже зарезетили), второй всё
            # равно должен получить шанс закрыться. Без try-обёртки exception
            # уносил бы управление наверх с уже None'нутым handle'ом — FD-leak
            # до GC.
            client = auth_deps._introspect_client
            auth_deps._introspect_client = None
            if client is not None:
                try:
                    await client.aclose()
                except Exception as exc:
                    logger.error(
                        "introspect http client aclose failed: %s",
                        exc,
                        exc_info=True,
                    )
            token_client = auth_deps._token_proxy_client
            auth_deps._token_proxy_client = None
            if token_client is not None:
                try:
                    await token_client.aclose()
                except Exception as exc:
                    logger.error(
                        "token proxy http client aclose failed: %s",
                        exc,
                        exc_info=True,
                    )

    # `persistAuthorization` держит SERVICE_API_KEY / Bearer-токен в
    # localStorage браузера между перезагрузками /docs. В development/test
    # окружениях это тоже нежелательно — общий браузер на shared dev-стенде
    # сольёт токен следующему пользователю. Включаем только под `app_env=local`,
    # где разработчик сидит за своей машиной и /docs у него один на host.
    _swagger_params = (
        {"persistAuthorization": True} if settings.app_env == "local" else {}
    )
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        # В проде Swagger/OpenAPI скрыты: persistAuthorization кладёт
        # SERVICE_API_KEY в localStorage браузера, а сам OpenAPI раскрывает
        # схемы и error-codes. Зеркалит auth_service.
        docs_url=None if _is_prod else "/docs",
        redoc_url=None if _is_prod else "/redoc",
        openapi_url=None if _is_prod else "/openapi.json",
        debug=settings.app_debug,
        swagger_ui_parameters=_swagger_params,
        lifespan=lifespan,
    )

    # Per-IP rate-limit wiring (slowapi, decorator-only mode):
    #   * `app.state.limiter` держит slowapi-машинерию introspectable
    #     (admin-эндпоинты, тесты) и совпадает с официальным wiring-паттерном.
    #   * Сам limit-check живёт на `@limiter.limit` декораторе в
    #     `endpoints/events.py` — slowapi `Limiter._check_request_limit`
    #     вызывает `limiter.hit()` ровно один раз на запрос.
    #   * НАМЕРЕННО не регистрируем `SlowAPIMiddleware` здесь:
    #     middleware + декоратор вместе double-count'ят hits (middleware не
    #     ставит `request.state._rate_limiting_complete` перед делегацией
    #     в route, так что обёртка декоратора пере-проверяет тот же лимит
    #     и инкрементит counter второй раз — подтверждено трассировкой
    #     `slowapi/middleware.py:43` vs `extension.py:730`).
    #   * `RateLimitExceeded`, поднятый декоратором, ловится
    #     `@app.exception_handler(RateLimitExceeded)` ниже.
    app.state.limiter = limiter

    _HEALTH_PATHS = {"/api/logging/v1/health", "/api/logging/v1/ready"}

    # ВАЖНО про порядок Starlette `@app.middleware("http")` — LIFO:
    # последний зарегистрированный = outermost. Чтобы получить стек
    # outer → inner: `limit_body_size → audit_access → attach_request_id → route`,
    # регистрируем в обратном порядке:
    #   1. `attach_request_id`  — innermost (видит response от route);
    #   2. `audit_access`       — посередине (эмитит audit на 4xx/5xx);
    #   3. `limit_body_size`    — outermost (рубит 413 первым).
    #
    # Зачем body-limit outermost:
    #   * 413-ответ возвращается ДО `audit_access` → нет audit-emit'а на
    #     отбитом трафике (зеркалит 429-amplification фикс в `server_service`);
    #   * `Content-Length` проверяется ДО того, как ASGI начнёт читать body
    #     в память — единственный момент, когда можно дёшево отбить
    #     многомегабайтный запрос. Если пропустить дальше, body будет
    #     прочитан полностью (включая pydantic-парсинг) и pool/память уже
    #     расходованы.
    #   * Rate-limit на `POST /events` — slowapi-декоратор внутри route, то
    #     есть ещё глубже attach_request_id → 413 от body-limit срабатывает
    #     и до rate-limit'а тоже (что и должно быть: лишний incoming-counter
    #     тратится только на правильно сформированные запросы под лимитом).

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        # Санитизация incoming `X-Request-ID` — defence-in-depth от
        # response-splitting. `EventCreate.request_id` валидирует тот же
        # charset, но middleware видит header на КАЖДОМ запросе (health,
        # token, admin), не только на десериализованных `EventCreate`.
        # Cheap path: убираем CR/LF/NUL (splittable header-терминаторы),
        # cap до 64 символов от сырых байтов, потом strip — чтобы padding
        # из тысячи пробелов не съедал budget. Пустое после strip'а →
        # автогенерируемый id.
        # Если body-size middleware уже вычислил request_id для возможного
        # 413/400 ответа, переиспользуем тот же id — иначе id в логах и в
        # X-Request-ID response-header'е разъедутся для пограничных кейсов.
        existing = getattr(request.state, "request_id", None)
        if existing:
            request_id = existing
        else:
            request_id = _sanitize_request_id(request.headers.get("X-Request-ID"))
            request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    # Эндпоинты, где аудит не нужен (служебные / рекурсивные).
    _SKIP_AUDIT_PATHS = {
        "/api/logging/v1/health",
        "/api/logging/v1/ready",
        "/api/logging/v1/token",
    }
    # POST на эти префиксы — входящие от сервисов, не аудитируем (избегаем
    # рекурсии: события приёма событий).
    _INGEST_PREFIXES = (
        "/api/logging/v1/events",
        "/api/logging/v1/services/",  # POST /services/{svc}/events
    )
    # PUT/DELETE retention эндпоинт сам пишет `logging.retention_write`
    # (single source) — middleware на success дублировал бы запись.
    # Ошибки (401/403/4xx/5xx) всё равно эмитятся как `http.*` ниже.
    _RETENTION_PATH = "/api/logging/v1/retention"
    # POST/PATCH/DELETE на `/rules*` эндпоинт сам эмитит `logging_rule.*`
    # через `rules.py::_audit`. Без этого скипа middleware на success
    # дополнительно писал бы `logging.rules_write`, и SOC видел бы дубль
    # на каждое admin-action (один CRITICAL `logging_rule.update` + один
    # WARNING `logging.rules_write`). 4xx/5xx по-прежнему уходят через
    # `http.*` ветку ниже — auth-фейлы на admin-эндпоинте нельзя скипать.
    _RULES_PATH_PREFIX = "/api/logging/v1/rules"

    @app.middleware("http")
    async def audit_access(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        status_code = response.status_code

        # Скипаем служебные пути.
        if path in _SKIP_AUDIT_PATHS:
            return response

        # POST от сервисов (ingest событий, регистрация событий): успешный
        # приём НЕ аудитируем — это осознанный anti-amplification (loging не
        # должен писать audit-событие на каждое принятое событие, иначе
        # рекурсия/усиление). Но auth-провалы (401/403) и rate-limit
        # отбивы (429) на этих путях пропускать нельзя: иначе перебор
        # SERVICE_API_KEY или flood утёкшим ключом не оставляет следа ни в
        # журнале, ни у SOC. 429 идёт через ту же ветку, что и 401/403 —
        # как http.client_error (см. mapping ниже), петли не создаёт.
        if request.method == "POST" and path.startswith(_INGEST_PREFIXES):
            if status_code not in (401, 403, 429):
                return response

        # Successful retention writes покрываются endpoint-уровневым self-audit'ом;
        # middleware-эмиссия дала бы дубль `logging.retention_write` в SIEM.
        if (
            request.method in ("PUT", "DELETE")
            and path == _RETENTION_PATH
            and status_code < 400
        ):
            return response

        # Successful rule CRUD покрывается endpoint-уровневым `logging_rule.*`
        # self-audit'ом (`rules.py::_audit`). Скипаем middleware-эмиссию,
        # чтобы не было дубля `logging.rules_write` + `logging_rule.<verb>`
        # на одно admin-action. 4xx/5xx идут через `http.*` (см. ниже) —
        # auth-фейлы на admin-эндпоинте остаются видимыми.
        if (
            request.method in ("POST", "PATCH", "DELETE")
            and path.startswith(_RULES_PATH_PREFIX)
            and status_code < 400
        ):
            return response

        identity = getattr(request.state, "auth_identity", None)
        actor_id = identity.get("user_id") if identity else None
        username = identity.get("username") if identity else None
        # `actor_type` пробрасывание: `_fetch_identity` кладёт `subject_type`
        # от auth_service introspect ("user"/"bot"/"oauth_client") на
        # identity-dict. Без этого M2M caller'ы (PAT/bot/oauth_client)
        # писались бы в audit как "user" — fake user-активность для SOC.
        actor_type = identity.get("actor_type") if identity else None
        request_id = getattr(request.state, "request_id", None)
        details: dict = {"method": request.method, "path": path, "status_code": status_code}
        if identity and identity.get("platform_role"):
            details["platform_role"] = identity["platform_role"]

        if status_code in (401, 403):
            action, emit_status, allowed = "http.access_denied", "denied", False
        elif status_code >= 500:
            action, emit_status, allowed = "http.server_error", "failure", False
        elif status_code >= 400:
            action, emit_status, allowed = "http.client_error", "failure", False
        else:
            action, emit_status, allowed = _action_for_path(request.method, path), "success", True

        # IP клиента кладём для auth-провалов (401/403) и rate-limit отбивов
        # (429), но не для штатных success — IP на каждое чтение раздул бы
        # audit_events и не даёт сигнала ни SOC, ни SIEM. На 401/403/429 IP
        # критичен: одно failed-attempt в потоке успехов от одного источника —
        # перебор `SERVICE_API_KEY` или flood. `get_remote_address` возвращает
        # `request.client.host` (без честного парсинга X-Forwarded-For — это
        # на стороне reverse-proxy), но как минимум разделяет «локальный
        # docker-compose сосед» от внешнего сканера.
        if status_code in (401, 403, 429):
            client_ip = get_remote_address(request)
            if client_ip:
                details["client_ip"] = client_ip

        envelope = make_envelope(
            action=action,
            actor_id=actor_id,
            actor_type=actor_type,
            username=username,
            emit_status=emit_status,
            allowed=allowed,
            request_id=request_id,
            details=details,
        )
        outbox = _audit_outbox
        if outbox is not None:
            outbox.push_nowait(envelope)
        else:
            # Lifespan не подняли (тесты с `audit_outbox_enabled=False`) —
            # fallback в синхронный writer на отдельном треде, как раньше.
            # Без drain'а это редкий путь, не hot-path.
            await asyncio.to_thread(_emit_audit_envelope, envelope)
        return response

    @app.middleware("http")
    async def limit_body_size(request: Request, call_next):
        """Отбивает мутирующие запросы с body больше `max_request_body_bytes`.

        DoS-фикс (нет лимита размера body + pool=30 → DoS):
        `EventCreate._details_size` кэпает только под-dict `details` в 64 KiB
        ПОСЛЕ того, как pydantic уже распарсил весь body. Один атакующий
        с `SERVICE_API_KEY` может прислать 100 MB body — ASGI читает его в
        память до того, как стартует любой валидатор, и 30 параллельных
        uploads × 100 MB ≈ 3 GB RSS плюс pgsql-pool насыщается ещё до
        того, как один client упрётся в per-IP rate-limit slowapi
        (default 100/min).

        Стратегия:
        * Cheap path — доверяем `Content-Length`, если есть. Стандарт-compliant
          HTTP/1.1 и HTTP/2 клиенты всегда шлют его на fixed-size body; ASGI
          передаёт header без изменений. Если declared length больше cap'а —
          возвращаем 413 сразу, не трогая body.
        * Если `Content-Length` нет (chunked transfer-encoding,
          `Transfer-Encoding: chunked`) — оборачиваем ASGI `receive`-колбэк
          и считаем байты по мере приёма chunk'ов. Как только сумма
          превышает `max_size`, отдаём 413 и не пропускаем дальше. Это
          стримовая проверка: память не раздувается, downstream обработчик
          не вызывается на overflow.

        Оба пути сверяются с тем же `max_request_body_bytes` — атакующий
        не может выиграть, перейдя на chunked transfer-encoding. Контракт
        зафиксирован тестами `tests/test_body_size_limit.py`
        (`TestBodySizeMiddleware` — CL-путь, `TestChunkedOverflow` —
        стримовый путь без CL).

        Граничное условие — строгое `received > max_size`: ровно-в-лимит
        body пропускается, off-by-one не съест последний легитимный байт.

        Проверяем только `POST/PUT/PATCH` — `GET/HEAD/DELETE/OPTIONS`
        body для audit-ingest не несут.
        """
        if request.method in ("POST", "PUT", "PATCH"):
            # Body-size middleware outermost — attach_request_id ещё не
            # выставил `request.state.request_id`. Вычисляем id здесь, чтобы
            # 413/400 envelope нёс `X-Request-ID` для correlation с логами и
            # чтобы downstream middleware/route переиспользовали тот же id.
            request_id = _sanitize_request_id(request.headers.get("X-Request-ID"))
            request.state.request_id = request_id
            settings = get_settings()
            max_size = settings.max_request_body_bytes
            cl = request.headers.get("content-length")
            if cl is not None:
                # `int(cl)` принимал `+50000000`, `1_000_000`, окружающие
                # пробелы и `int("-1")` (последнее ловилось отдельным
                # негативным гардом). На стеке без h11 0.16 — или за
                # прокси, которое нормализует header перед передачей —
                # `+` и пробелы могли вернуться в downstream-парсер уже
                # как валидное число, открывая body-smuggling. Strict
                # fullmatch `\d+` после strip'а покрывает все варианты:
                # отрицательные, плюс, underscores, whitespace, юникодные
                # digits — всё отбивается единым 400.
                stripped = cl.strip()
                if not _CONTENT_LENGTH_RE.fullmatch(stripped):
                    return JSONResponse(
                        status_code=400,
                        content={
                            "error": "bad_request",
                            "error_code": "INVALID_CONTENT_LENGTH",
                            "message": "Content-Length header is malformed",
                            "details": {},
                            "request_id": request_id,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                        headers={"X-Request-ID": request_id},
                    )
                declared = int(stripped)
                if declared > max_size:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "error": "payload_too_large",
                            "error_code": "PAYLOAD_TOO_LARGE",
                            "message": (
                                f"Request body exceeds the {max_size}-byte limit"
                            ),
                            "details": {
                                "max_bytes": max_size,
                                "declared_bytes": declared,
                            },
                            "request_id": request_id,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                        headers={"X-Request-ID": request_id},
                    )
            else:
                # Chunked / Transfer-Encoding: считаем байты по потоку до
                # `max_size + 1`. Без этого атакующий с HTTP/1.1 chunked-
                # кодированием обходит cheap path по Content-Length и шлёт
                # сколько угодно body — единственный hard-cap дальше это
                # 64 KiB на `details`, но `EventCreate` его видит уже после
                # ASGI'еного буфера.
                #
                # Раньше middleware подменял receive lazy-обёрткой и принимал
                # решение об overflow ПОСЛЕ возврата route'а. На truncate'нутом
                # body route отвечал 422 (pydantic не парсит пустую строку),
                # и middleware перетирал этот 422 на 413. Проблема: легитимный
                # 4xx, который шёл бы по pre-body-path (auth fail / rate-limit /
                # invalid Content-Type), точно так же терялся под 413 — caller
                # вместо реальной причины получал «слишком большой запрос».
                #
                # Сейчас читаем стрим в буфер в самом middleware и решаем ДО
                # route'а: overflow → 413 сразу, без route call'а; в норме
                # подкладываем буфер обратно через receive-replay, чтобы
                # route видел нормальный http.request-стрим.
                buffered: list[bytes] = []
                received = 0
                overflow = False
                original_receive = request.receive
                while True:
                    message = await original_receive()
                    if message["type"] != "http.request":
                        # http.disconnect и прочее — выходим, тело уже не дочитаем.
                        break
                    body = message.get("body", b"")
                    if body:
                        received += len(body)
                        if received > max_size:
                            overflow = True
                            break
                        buffered.append(body)
                    if not message.get("more_body", False):
                        break
                if overflow:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "error": "payload_too_large",
                            "error_code": "PAYLOAD_TOO_LARGE",
                            "message": (
                                f"Request body exceeds the {max_size}-byte limit"
                            ),
                            "details": {
                                "max_bytes": max_size,
                                "received_bytes": received,
                            },
                            "request_id": request_id,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                        headers={"X-Request-ID": request_id},
                    )

                # Стрим уместился — replay'им буфер одним сообщением вниз
                # по стеку. Делать ровно один http.request-message достаточно:
                # Starlette просто конкатенирует `body` до `more_body=False`.
                replay = [{
                    "type": "http.request",
                    "body": b"".join(buffered),
                    "more_body": False,
                }]

                async def replay_receive():
                    if replay:
                        return replay.pop(0)
                    return {"type": "http.request", "body": b"", "more_body": False}

                # `request._receive` — Starlette-private attr, alt-name'а
                # для подмены receive-callable в публичном API сейчас нет.
                # Upstream-смена имени станет тихим breakage'ом: pinned
                # `starlette>=` в `pyproject.toml` фиксирует версию,
                # `test_body_size_limit.py` проверит regression на 413.
                request._receive = replay_receive  # type: ignore[attr-defined]
                return await call_next(request)
        return await call_next(request)

    # SecurityHeadersMiddleware регистрируем последним → outermost слой.
    # Так заголовки попадают на КАЖДЫЙ ответ, включая 413 от
    # `limit_body_size` и 429 от rate-limit, а не только на route-ответы.
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts_enabled=settings.security_hsts_enabled,
    )

    # HTTPS-guard ставим ПОСЛЕ SecurityHeadersMiddleware → становится самым
    # outermost'ом. Cleartext-запрос в prod/staging отбивается 403 ДО body-limit,
    # rate-limit, audit_access — нет audit-amplification на http-флуд, нет
    # расхода introspect-pool на запросы, которые мы и так отвергнем. В
    # local/dev/test middleware пропускает всё (uvicorn в devcontainer на http,
    # тесты через ASGI без TLS).
    app.add_middleware(
        HTTPSRequiredMiddleware,
        app_env=settings.app_env,
    )

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "error": _http_status_to_category(exc.http_status),
                "error_code": exc.error_code,
                "message": exc.message,
                "details": exc.details,
                "request_id": getattr(request.state, "request_id", None),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_exception_handler(request: Request, exc: RateLimitExceeded):
        # Держим стандартный error envelope (как у AppException), чтобы
        # клиенты диспатчили по `error_code`, а не парсили plain-text body
        # дефолтного slowapi. `exc.detail` несёт лимит, который сработал
        # (например, "100 per 1 minute").
        return JSONResponse(
            status_code=429,
            content={
                "error": "too_many_requests",
                "error_code": "RATE_LIMIT_EXCEEDED",
                "message": f"Ingest rate limit exceeded: {exc.detail}",
                "details": {"limit": str(exc.detail)},
                "request_id": getattr(request.state, "request_id", None),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            headers={"Retry-After": "60"},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "error_code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": {"errors": jsonable_encoder(exc.errors())},
                "request_id": getattr(request.state, "request_id", None),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    app.include_router(api_router, prefix="/api")

    # NB: `start_retention_cleanup` раньше был `@app.on_event("startup")`
    # хуком здесь; свёрнут в `lifespan` выше, чтобы убрать
    # DeprecationWarning и держать порядок startup'а (introspect-pool
    # первый, потом daemon-thread) явным в одном месте.

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)

        # Заменяем auto-сгенерированный HTTPBearer на наши именованные схемы.
        schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
        schemes.pop("HTTPBearer", None)
        schemes["ServiceAPIKey"] = {
            "type": "http",
            "scheme": "bearer",
            "description": "Вставь SERVICE_API_KEY как есть (service-to-service auth)",
        }
        schemes["OAuth2Password"] = {
            "type": "oauth2",
            "description": "Логин по LOGGING_ADMIN_USERNAME + LOGGING_ADMIN_PASSWORD",
            "flows": {
                "password": {
                    "tokenUrl": "/api/logging/v1/token",
                    "scopes": {},
                }
            },
        }

        # Подменяем endpoint-level HTTPBearer-ссылки, чтобы принимались оба flow'а.
        _AUTH = [{"ServiceAPIKey": []}, {"OAuth2Password": []}]
        for path_item in schema.get("paths", {}).values():
            for operation in path_item.values():
                if isinstance(operation, dict) and "security" in operation:
                    operation["security"] = _AUTH

        schema["security"] = _AUTH
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
    return app


# Backward-compat alias: `tests/test_hardening.py` импортирует константу
# именем `_RETENTION_ADVISORY_LOCK_KEY`. Сам ключ живёт в
# `core.constants.ADVISORY_LOCKS["retention_sweep"]`.
_RETENTION_ADVISORY_LOCK_KEY: int = ADVISORY_LOCKS["retention_sweep"]


def _build_retention_sweep_details(
    *,
    deleted: int,
    snapshot: list,
    run_date_msk: str,
    cutoff_at: datetime | None = None,
) -> dict:
    """Собирает `details` для `logging.retention_sweep` self-audit.

    `snapshot` — список активных `RetentionPolicy`-объектов, снятый ДО
    `apply_active` (иначе concurrent admin-DELETE даст пустой snapshot при
    ненулевом deleted_count). Под filter-режим политик может быть несколько
    с разными `retain_days` — кладём массив + min/max, чтобы SOC видел
    честный набор, а не одну «представительскую» политику.

    `cutoff_at` — UTC-момент, на который sweep зафиксировал «now» при
    вычислении `timestamp < now - retain_days`. На 50M-таблице chunked
    DELETE идёт часами; без этого поля SOC не может восстановить точное
    окно, под которое попали удалённые row'ы (run_date_msk даёт только
    дату MSK). Для backward-compat поле опциональное — старые caller'ы
    без `cutoff_at` получают audit-запись без этого ключа.
    """
    policies_details = [
        {
            "id": p.id,
            "retain_days": p.retain_days,
            "severity": p.severity,
            "service": p.service,
        }
        for p in snapshot
    ]
    details: dict = {
        "deleted_count": int(deleted),
        "run_date_msk": run_date_msk,
        "policies": policies_details,
    }
    if cutoff_at is not None:
        details["cutoff_at"] = cutoff_at.isoformat()
    if snapshot:
        retain_values = [p.retain_days for p in snapshot]
        details["min_retain_days"] = min(retain_values)
        details["max_retain_days"] = max(retain_values)
    return details


_RETENTION_TICK_INTERVAL_SECONDS = 60.0

# Watchdog: last monotonic-timestamp успешного tick'а `_retention_loop`.
# `_retention_loop_supervised` обновляет это значение на каждой итерации;
# `/ready` сравнивает с `time.monotonic()` и кидает 503, если отставание
# больше `_RETENTION_WATCHDOG_TTL_SECONDS`. None — loop ещё не успел
# отработать первый tick (legitimate startup window).
_retention_last_tick_monotonic: float | None = None

# Отдельный watchdog для последнего УСПЕШНОГО sweep'а `apply_active`.
# Минутный tick фиксирует `_retention_last_tick_monotonic` независимо от
# того, прошёл ли sweep — если sweep в 00:00 MSK падает каждый день, а
# daemon-thread продолжает спать-просыпаться, `/ready` оставался бы зелёным.
# `_retention_last_successful_sweep_monotonic` обновляется только после
# успешного возврата `apply_active`; `/ready` сверяет его с двумя
# retention-tick-интервалами и кидает 503, если sweep тихо не идёт.
# None — sweep ещё ни разу не прошёл в этом процессе (legitimate startup
# window до первой 00:00 MSK granicy).
_retention_last_successful_sweep_monotonic: float | None = None
_retention_watchdog_lock = threading.Lock()

# TTL: сколько секунд между успешными tick'ами считается приемлемым.
# Тик минутный (см. `_RETENTION_TICK_INTERVAL_SECONDS`), берём 5× запас на
# сам sweep (apply_active под нагрузкой может занять минуты). Если loop
# не дошёл до следующего tick'а за это окно — он либо умер, либо завис.
_RETENTION_WATCHDOG_TTL_SECONDS = 5 * 60.0

# TTL для успешного sweep'а: каждый retention-tick минутный, sweep идёт
# раз в сутки в 00:00 MSK. Если sweep тихо падает каждый день, минутный
# `_retention_last_tick_monotonic` всё равно обновляется и `/ready`
# остаётся зелёным. Этот watchdog ловит именно «sweep'а нет», а не
# «loop встал». Дефолт — 2 × retention_interval_seconds, но не короче
# 25 часов, чтобы успеть переждать одну пропущенную 00:00 MSK границу
# без флапа readiness'а в долгоиграющий sweep.
_RETENTION_SWEEP_WATCHDOG_TTL_SECONDS = max(
    2 * _RETENTION_TICK_INTERVAL_SECONDS,
    25 * 3600.0,
)


def _next_retention_boundary_from(now_monotonic: float, *, interval: float = _RETENTION_TICK_INTERVAL_SECONDS) -> float:
    """Возвращает абсолютный monotonic-timestamp ближайшего будущего tick'а.

    Привязка идёт к wallclock-минуте (`time.time()` floor + interval), чтобы
    тики не дрейфовали относительно реального времени — иначе при медленном
    sweep'е, который сожрал больше interval'а, инкремент `next_run += interval`
    наматывает отставание, и мы проскакиваем нужные минутные слоты.

    Перевод обратно в monotonic делается через дельту wall→mono в момент
    вычисления — это корректно даже если системные часы прыгнули (NTP-step):
    monotonic-база не зависит от прыжков wallclock'а, а смещение фиксируется
    сейчас, не в момент старта процесса.
    """
    wall_now = time.time()
    # floor wall_now до interval-секунд + interval = следующая граница.
    boundary_wall = (wall_now // interval) * interval + interval
    delta_to_boundary = boundary_wall - wall_now
    return now_monotonic + delta_to_boundary


def _retention_loop() -> None:
    """Фоновый тред: применяет retention-политику раз в сутки в 00:00 MSK (UTC+3).

    Тик минутный. `next_run` всегда пересчитывается от абсолютной wallclock-границы
    через `_next_retention_boundary_from` — drift не накапливается, а если sweep
    занял дольше interval'а, мы перескакиваем пропущенные слоты, а не
    отрабатываем их катящимся залпом.

    Под multi-replica каждый instance тикает независимо. Чтобы DELETE не
    шёл из N replica'ов одновременно (DELETE race + WAL amplification),
    apply-проход обёрнут в `pg_try_advisory_lock` — только держатель
    lock'а делает работу, остальные тихо пропускают.
    """
    from datetime import date
    from zoneinfo import ZoneInfo
    from sqlalchemy import text
    global _retention_last_tick_monotonic, _retention_last_successful_sweep_monotonic
    _MSK = ZoneInfo("Europe/Moscow")
    last_run: date | None = None
    interval = _RETENTION_TICK_INTERVAL_SECONDS

    # Стартовый tick — ближайшая wallclock-минута, не «сейчас + 60s».
    next_run = _next_retention_boundary_from(time.monotonic(), interval=interval)

    while True:
        # Sleep до запланированной границы. Сам tick (`if hour == 0`) идёт
        # после сна — так дительность работы внутри слота легко замерить.
        now_mono = time.monotonic()
        sleep_seconds = next_run - now_mono
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

        slot_start = time.monotonic()
        now_msk = datetime.now(_MSK)
        today = now_msk.date()

        if last_run != today and now_msk.hour == 0:
            try:
                from src.db.session import SessionLocal
                from src.repositories.retention_policies import apply_active
                db = SessionLocal()
                try:
                    locked = db.execute(
                        text("SELECT pg_try_advisory_lock(:k)"),
                        {"k": _RETENTION_ADVISORY_LOCK_KEY},
                    ).scalar()
                    if not locked:
                        # Lock держит другая replica — она и сделает cleanup.
                        # Мы всё равно фиксируем last_run, чтобы не ретраить
                        # в каждый последующий минутный tick этого часа.
                        logger.info(
                            "Retention cleanup [%s MSK]: skipped, advisory lock held by another replica",
                            today,
                        )
                        last_run = today
                        # Watchdog: для каждой replica successful_sweep —
                        # локальный маркер «sweep отработал в этом окне», и
                        # «другая replica взяла лок» — это легитимный успех
                        # текущей итерации. Без этого non-holder replica'и
                        # навсегда показывали бы stale successful_sweep и
                        # отдавали бы 503 на `/ready` через TTL.
                        with _retention_watchdog_lock:
                            _retention_last_successful_sweep_monotonic = time.monotonic()
                    else:
                        # Снимаем snapshot активных политик ДО sweep'а: иначе
                        # concurrent admin-DELETE между apply_active и сбором
                        # деталей даст deleted_count>0 при пустом списке политик.
                        #
                        # ВНИМАНИЕ: фактически это ДВА независимых SELECT'а —
                        # этот `list_active` и второй внутри `apply_active`
                        # (`retention_policies.py:195`). Audit-details попадает
                        # один snapshot, sweep DELETE'ит по другому. Инвариант
                        # «snapshot == policies-sweep'а» держится только потому,
                        # что _retention_loop сидит под `pg_try_advisory_lock`
                        # (см. acquire выше), а PUT /retention этого лока НЕ
                        # берёт — значит admin-PUT в принципе НЕ пересекается
                        # с этим SELECT-окном (контракт: PUT приходит между
                        # tick'ами, а внутри tick'а advisory-lock держится до
                        # release под `finally` ниже). Если когда-нибудь под
                        # лок поставят и PUT, между двумя SELECT'ами появится
                        # окно, в котором audit врёт. Передавать snapshot в
                        # `apply_active(db, policies=snapshot)` пока не нужно:
                        # советующий лок снимает гонку, а лишний parameter
                        # размывает контракт «apply_active сам тянет активные».
                        from src.repositories.retention_policies import list_active
                        snapshot = list_active(db)
                        try:
                            # Chunk size берём из live settings — оператор
                            # может крутить `RETENTION_CHUNK_SIZE` под профиль
                            # БД (WAL-amplification vs длительность транзакции)
                            # без перевыкатки кода.
                            chunk_size = get_settings().retention_chunk_size
                            # cutoff_at фиксируется ровно тем же UTC-моментом,
                            # который apply_active использует для предиката
                            # `timestamp < now - retain_days`. audit-details
                            # `retention_sweep` потом получит этот же ts —
                            # SOC сможет точно реконструировать окно даже
                            # если sweep шёл часами.
                            cutoff_at = datetime.now(timezone.utc)
                            deleted = apply_active(
                                db,
                                chunk_size=chunk_size,
                                now=cutoff_at,
                            )
                            # Сразу после apply_active фиксируем «sweep прошёл».
                            # Это до self-audit'а сознательно: если audit упадёт
                            # (БД проблемная, ошибка сериализации), сам sweep
                            # уже сделал работу — watchdog не должен из-за этого
                            # 503'ить. Если apply_active кинул — управление
                            # улетает в outer `except Exception`, marker не
                            # обновляется, и watchdog поймает stall через
                            # `_RETENTION_SWEEP_WATCHDOG_TTL_SECONDS`.
                            with _retention_watchdog_lock:
                                _retention_last_successful_sweep_monotonic = time.monotonic()
                            logger.info(
                                "Retention cleanup [%s MSK]: deleted %d events",
                                today, deleted,
                            )
                            # Self-audit факта sweep'а: compliance требует
                            # запись «событие удалено». Под filter-режим
                            # активных политик может быть несколько с разными
                            # retain_days — пишем массив + min/max, чтобы SOC
                            # видел честную картину, а не одну «представительскую».
                            try:
                                from src.schemas.events import EventCreate
                                from src.services.event_service import record_admin_action
                                details = _build_retention_sweep_details(
                                    deleted=deleted,
                                    snapshot=snapshot,
                                    run_date_msk=today.isoformat(),
                                    cutoff_at=cutoff_at,
                                )
                                record_admin_action(
                                    db,
                                    EventCreate(
                                        timestamp=datetime.now(timezone.utc),
                                        service="loging_service",
                                        action="logging.retention_sweep",
                                        actor_id=None,
                                        actor_type="service",
                                        username="loging_service",
                                        status="success",
                                        allowed=True,
                                        severity=None,
                                        details=details,
                                    ),
                                )
                            except Exception as audit_exc:
                                # Sweep сам прошёл успешно — теряем только audit.
                                # Логируем, но не валим last_run.
                                logger.error(
                                    "retention sweep self-audit failed: %s",
                                    audit_exc,
                                    exc_info=True,
                                )
                            last_run = today
                        finally:
                            # `last_run = today` выставляется в success-ветке
                            # try-блока (после apply_active + self-audit) или
                            # в `except audit_exc` (sweep прошёл, audit упал —
                            # реран в тот же день не нужен). Если же сам
                            # apply_active кинет — управление улетит во
                            # внешний `except Exception` (line ~837), last_run
                            # не сдвинется, и следующий tick повторит sweep —
                            # это by-design.
                            # unlock и commit вынесены сюда, чтобы упавший
                            # commit не маскировал сам факт, что sweep
                            # отработал: apply_active коммитит per-chunk сам,
                            # record_admin_action — под `commit=True`, так
                            # что финальный db.commit() чистит хвост пустой
                            # транзакции и упасть может только на disconnect'е.
                            try:
                                db.execute(
                                    text("SELECT pg_advisory_unlock(:k)"),
                                    {"k": _RETENTION_ADVISORY_LOCK_KEY},
                                )
                            except Exception as unlock_exc:
                                logger.error(
                                    "retention sweep advisory_unlock failed: %s",
                                    unlock_exc,
                                )
                            try:
                                # `db.commit()` под зависший pgsql может
                                # ждать до `tcp_keepalive_intvl × probes`
                                # (>2 минут default), блокируя daemon-thread
                                # и сдвигая следующий tick. Ограничиваем
                                # commit-RTT отдельным `statement_timeout`
                                # на эту транзакцию: apply_active уже
                                # коммитил per-chunk, финальный commit
                                # чистит пустой хвост и не должен висеть.
                                try:
                                    db.execute(
                                        text("SET LOCAL statement_timeout = 2000")
                                    )
                                except Exception:
                                    pass
                                db.commit()
                            except Exception as commit_exc:
                                logger.error(
                                    "retention sweep trailing commit failed: %s",
                                    commit_exc,
                                )
                finally:
                    db.close()
            except Exception as exc:
                logger.error("Retention cleanup failed: %s", exc)

        # Если tick (включая sweep) сожрал больше interval'а — WARN. next_run
        # пересчитывается от абсолютной wallclock-границы относительно
        # текущего момента: пропущенные слоты тихо скипаются, drift не копится.
        tick_duration = time.monotonic() - slot_start
        if tick_duration > interval:
            logger.warning(
                "retention sweep took %.1fs (interval=%d)",
                tick_duration, int(interval),
            )

        # Watchdog: фиксируем факт «прошёл tick» (даже если sweep'а в этот
        # слот не было — sleep+slot-проверка отработали штатно).
        # `/ready` смотрит на эту метку, чтобы поймать зависший / упавший
        # daemon-thread до того, как пользователь увидит молчаливый stall.
        with _retention_watchdog_lock:
            _retention_last_tick_monotonic = time.monotonic()

        next_run = _next_retention_boundary_from(time.monotonic(), interval=interval)


def _retention_loop_supervised() -> None:
    """Внешний wrapper над `_retention_loop` с restart-петлёй.

    Голый `_retention_loop` ловит exception только вокруг session-block
    внутри try'а; всё, что вылетит из `time.sleep`/`datetime`/`zoneinfo`
    (NTP-step, OS interruption, OOM), убьёт daemon-thread тихо.
    Watchdog в `/ready` это поймает (last-tick TTL), но трафик уже
    мог гнаться через мёртвый retention-цикл.

    Здесь оборачиваем loop в while-True + try/except: на любую неожиданную
    смерть логируем CRITICAL и стартуем заново через короткую паузу.
    `_retention_loop` сам не возвращает управление в норме, поэтому штатный
    выход из вложенной функции тоже трактуется как аномалия.
    """
    backoff_seconds = 5.0
    while True:
        try:
            _retention_loop()
            logger.critical(
                "retention loop exited normally — daemon should run forever; "
                "restarting after %.0fs",
                backoff_seconds,
            )
        except Exception as exc:
            logger.critical(
                "retention loop crashed: %s — restarting after %.0fs",
                exc,
                backoff_seconds,
                exc_info=True,
            )
        try:
            time.sleep(backoff_seconds)
        except Exception as sleep_exc:
            # `time.sleep` редко падает (KeyboardInterrupt / signal-handler с
            # raise). Раньше тут стоял `return` — daemon-thread мгновенно
            # умирал, retention sweep останавливался, `/ready` ловил это
            # только через TTL. Логируем и крутим цикл дальше: следующий
            # `_retention_loop()` сразу полезет в sleep_until_boundary
            # и переждёт минуту. Если sleep падает каждый раз (broken signal
            # handler), цикл будет писать CRITICAL в лог — диагностируемо.
            logger.critical(
                "retention loop backoff sleep failed: %s — continuing",
                sleep_exc,
                exc_info=True,
            )
            continue


def _audit_drain_task_done_callback(task: "asyncio.Task") -> None:
    """Callback на завершение `_drain_task` audit outbox'а.

    Штатное завершение происходит только через `stop()` (cancel → выход
    из drain loop'а). Любой неожиданный exit — это потерянный hot-path:
    `push_nowait` начнёт уходить в sync-fallback, `qsize()` будет расти,
    SOC увидит инцидент только по `dropped_after_stop_total` после shutdown'а.
    Логируем CRITICAL, чтобы оператор поймал момент смерти task'а сразу.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.critical(
            "audit outbox drain task died with exception: %s",
            exc,
            exc_info=exc,
        )
    else:
        # `_drain_loop` вышел сам по себе (без exception), что возможно
        # только через `_stopping=True` — но callback вызывается и при
        # неожиданном return из-за breaking changes в loop'е. Помечаем как
        # WARNING (нет exception → возможно legitimate stop).
        logger.warning(
            "audit outbox drain task exited without exception (expected only on stop)"
        )


def get_retention_last_tick_monotonic() -> float | None:
    """Снапшот последнего успешного tick'а `_retention_loop`. Для `/ready` и тестов."""
    with _retention_watchdog_lock:
        return _retention_last_tick_monotonic


def get_retention_last_successful_sweep_monotonic() -> float | None:
    """Снапшот monotonic-времени последнего успешного sweep'а `apply_active`.

    None — sweep ещё не прошёл в этом процессе. `/ready` тогда не блокирует
    pod (startup window до первой 00:00 MSK границы). После первого
    успешного sweep'а отстававание сравнивается с
    `_RETENTION_SWEEP_WATCHDOG_TTL_SECONDS` (см. `health.py::ready`).
    """
    with _retention_watchdog_lock:
        return _retention_last_successful_sweep_monotonic


def _action_for_path(method: str, path: str) -> str:
    """Возвращает имя action для успешного обращения к admin-эндпоинту loging_service.

    Раньше работало через `in` substring-matching, где порядок проверок
    (`/events` ДО `/services`) был единственным, что отличало
    `GET /services/{svc}/events` (чтение events) от `GET /services` (листинг
    реестра). Любой будущий путь вида `/retention/events_archive` или
    `/services/{svc}/rules` сломал бы атрибуцию без шума — middleware
    послал бы в SIEM не тот action.

    Здесь идём через segment walk: режем path на компоненты и берём первый
    под `/v1/` как resource. Хвостовой сегмент `events` под `/services/{svc}/`
    ведёт в отдельный action `logging.service_events_browsed` — это чтение
    каталога зарегистрированных action'ов сервиса, не audit-журнала; SOC
    не должен путать «листинг описания сервиса» с «чтение событий».
    """
    # Хвостовой `/` и пустые сегменты не должны мешать lookup'у:
    # path вида `/api/logging/v1/rules/` после split'а даёт пустую финальную
    # компоненту; фильтруем сразу.
    segments = [s for s in path.split("/") if s]

    # `/services/{svc}/events` — каталог зарегистрированных action'ов
    # сервиса (`endpoints/services.py::list_service_events`), не audit-журнал.
    # Отдельный action, чтобы SOC-фильтр по `logging.events_queried` ловил
    # только чтения journal'а.
    if (
        len(segments) >= 2
        and segments[-1] == "events"
        and len(segments) >= 4
        and segments[-3] == "services"
    ):
        return "logging.service_events_browsed"

    # Голое `/events` (или любое другое окончание на `events`, не под
    # `/services/{svc}/`) — чтение audit-журнала.
    if segments and segments[-1] == "events":
        return "logging.events_queried"

    # Ищем resource'ный сегмент: первый после `v1`. На посторонних путях
    # (`/api/logging/v1/health` или вовсе вне `/v1/`) fallback на admin_access.
    resource = None
    for i, seg in enumerate(segments):
        if seg == "v1" and i + 1 < len(segments):
            resource = segments[i + 1]
            break

    if resource == "rules":
        return "logging.rules_read" if method == "GET" else "logging.rules_write"
    if resource == "events":
        return "logging.events_queried"
    if resource == "services":
        return "logging.services_read"
    if resource == "retention":
        # PUT/DELETE retention'а пишет self-audit прямо в endpoint'е
        # (`logging.retention_write` через `retention.py::_audit`), а
        # успех на write-методах из middleware скипается (см. `audit_access`).
        # Сюда дойдёт только GET → read; write-ветка оставлена на случай,
        # если функцию когда-то позовут вне `audit_access` (defence-in-depth).
        return "logging.retention_read" if method == "GET" else "logging.retention_write"
    return "logging.admin_access"


# Defence-in-depth: counter тихих self-audit ошибок. Без него silent failure
# маскирует регрессии (БД упала, schema mismatch, AppException от
# `record_admin_action`).
#
# Hot-path инкремента — `audit_outbox._write_batch_locked` (исполняется в
# ThreadPoolExecutor через `asyncio.to_thread` из `_drain_loop`), плюс
# fallback из `_emit_audit_envelope` под graceful-shutdown. Несколько
# воркеров инкрементят счётчик параллельно из разных threads; CPython GIL
# не делает `+=` атомарным (read-modify-write на байткоде из трёх инструкций),
# под нагрузкой получаем lost-increment'ы — самые informative цифры теряются
# как раз когда сервис горит. Заводим threading.Lock и делаем приватный
# setter, чтобы все апдейты шли через него.
self_audit_failures_total = 0
_self_audit_failures_lock = threading.Lock()
# Timestamp последнего инкремента — для SIEM-правил «failures за последние N
# минут». Без него scrape'у пришлось бы держать diff'ы между опросами, а
# редкие одиночные failure'ы маскировались бы шагом scrape-интервала.
# UTC, под тем же lock'ом, что и `self_audit_failures_total`.
_self_audit_last_failure_at: datetime | None = None


def _bump_self_audit_failures() -> int:
    """Атомарно увеличить счётчик self-audit ошибок, вернуть новое значение."""
    global self_audit_failures_total, _self_audit_last_failure_at
    with _self_audit_failures_lock:
        self_audit_failures_total += 1
        _self_audit_last_failure_at = datetime.now(timezone.utc)
        return self_audit_failures_total


def get_self_audit_failures_total() -> int:
    """Прочитать счётчик под тем же lock'ом, что и инкремент.

    Прямое чтение `self_audit_failures_total` из-под GIL атомарно для int,
    но это implicit invariant. Внешним наблюдателям (тесты, DLQ-инспекции)
    рекомендуется идти через этот хелпер.
    """
    with _self_audit_failures_lock:
        return self_audit_failures_total


def get_self_audit_last_failure_at() -> datetime | None:
    """Прочитать UTC-timestamp последнего инкремента под тем же lock'ом.

    `None` до первого инкремента или после перезапуска процесса.
    Полезно SIEM-правилу «не было ли failure'ов в окно [now-5min; now]».
    """
    with _self_audit_failures_lock:
        return _self_audit_last_failure_at


def _build_audit_outbox(settings) -> AuditOutbox:
    """Собирает `AuditOutbox` с инжектированной session_factory и writer'ом.

    Session factory разрешается лениво (через `_session_factory_default`),
    чтобы тесты, патчающие `src.db.session.SessionLocal` через monkeypatch,
    видели свой `SessionLocal` на момент drain'а, а не снапшот из импорта.

    Writer оборачивает default-`write_envelope_to_db` в self-audit-failure
    обвязку: исключения проходят через `_bump_self_audit_failures` —
    инвариант «counter растёт ровно на каждое потерянное событие» сохраняется.
    """
    return AuditOutbox(
        max_size=settings.audit_outbox_max_size,
        batch_size=settings.audit_outbox_batch_size,
        poll_interval_seconds=settings.audit_outbox_poll_interval_seconds,
        session_factory=_session_factory_default,
        writer=write_envelope_to_db,
        bump_failure=_bump_self_audit_failures,
    )


def _session_factory_default():
    """Поздний резолв `SessionLocal` — учитывает monkeypatch'и в тестах."""
    from src.db import session as session_module
    return session_module.SessionLocal()


def _emit_audit_envelope(envelope: AuditEnvelope) -> None:
    """Sync-обёртка для записи одного envelope'а вне drain'а (fallback).

    Используется, когда outbox не поднят (тесты без lifespan'а): открывает
    собственную сессию, пишет и закрывает. По семантике совпадает со старым
    `_emit_audit`.
    """
    db = _session_factory_default()
    try:
        try:
            write_envelope_to_db(db, envelope)
            commit = getattr(db, "commit", None)
            if callable(commit):
                commit()
        except Exception as exc:
            # Тестовые fake-сессии (`_BoomSession`/`_NoopSession`) могут не
            # реализовывать rollback/close — гвардим через getattr, чтобы
            # инварианты self-audit failure counter / log error работали.
            rollback = getattr(db, "rollback", None)
            if callable(rollback):
                try:
                    rollback()
                except Exception:
                    pass
            _bump_self_audit_failures()
            logger.error("self-audit failed: %s", exc, exc_info=True)
    finally:
        close = getattr(db, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def _emit_audit(
    action: str,
    actor_id: str | None,
    actor_type: str | None,
    username: str | None,
    emit_status: str,
    allowed: bool,
    request_id: str | None,
    details: dict,
) -> None:
    """Пишет событие loging_service напрямую в БД — всегда минуя SUPPRESS-правила.

    Production hot-path сейчас идёт через `_emit_audit_envelope` после того,
    как `audit_access` middleware кладёт событие в outbox или (без outbox'а)
    в `to_thread(_emit_audit_envelope, envelope)`. Этот helper нужен тестам
    `test_middleware.py` / `test_batch2.py` / `test_p4_cleanups.py` — они
    проверяют инварианты self-audit (счётчик ошибок, defence-in-depth обход
    SUPPRESS, actor_type fallback) с positional-аргументной сигнатурой, и
    переписывать их одной волной слишком шумно. Поэтому функция просто
    собирает envelope через тот же `make_envelope` и вызывает
    `_emit_audit_envelope` — DRY со sync-fallback'ом outbox'а.

    `actor_type` пробрасывается из identity (через `_fetch_identity` —
    маппинг от auth_service introspect `subject_type`). Whitelist-резолв
    делает `_resolve_actor_type` внутри `write_envelope_to_db` — здесь
    дополнительной обработки не нужно.
    """
    envelope = make_envelope(
        action=action,
        actor_id=actor_id,
        actor_type=actor_type,
        username=username,
        emit_status=emit_status,
        allowed=allowed,
        request_id=request_id,
        details=details,
    )
    _emit_audit_envelope(envelope)


def _http_status_to_category(status: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        422: "validation_error",
        429: "too_many_requests",
        503: "service_unavailable",
    }.get(status, "internal_error")


app = create_application()
