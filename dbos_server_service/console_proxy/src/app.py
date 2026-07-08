"""aiohttp-приложение console_proxy.

Один публичный префикс (`/vm-console` по умолчанию, за ingress). На пути
`{prefix}/{kind}/{vm_id}` живут сразу две вещи:

* обычный GET (без Upgrade) отдаёт HTML-обёртку с self-hosted noVNC/spice-html5;
* GET с `Upgrade: websocket` — это и есть мост: токен проверяется, резолвится
  TCP-таргет консоли на хабе, байты гоняются в обе стороны.

Токен браузер предъявляет query-параметром `?token=<jwt>` (так его передаёт
noVNC/websockify) либо WS-subprotocol'ом `bearer.<jwt>`. Статика раздаётся из
`{prefix}/assets/` того же origin — CSP разрешает только `self`, CDN запрещён.
"""

from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

from src import token as token_mod
from src.bridge import pump
from src.config import Settings, get_settings
from src.internal_client import InternalClient
from src.resolver import ResolveError, Resolver, build_resolver

log = logging.getLogger("console_proxy")

# Соответствие close-кодов токен-ошибок (по образцу серверной консоли web_ui).
_TOKEN_CLOSE = {
    "MISSING_TOKEN": (4401, "MISSING_TOKEN"),
    "MALFORMED": (4401, "MALFORMED_TOKEN"),
    "BAD_ALG": (4401, "BAD_TOKEN"),
    "INVALID_SIGNATURE": (4401, "INVALID_SIGNATURE"),
    "BAD_ISSUER": (4401, "BAD_ISSUER"),
    "NOT_YET_VALID": (4401, "NOT_YET_VALID"),
    "EXPIRED": (4401, "TOKEN_EXPIRED"),
    "BAD_KIND": (4400, "BAD_KIND"),
    "SERVER_MISCONFIGURED": (4503, "SERVER_MISCONFIGURED"),
}
_RESOLVE_CLOSE = {
    "NO_PORT": (4400, "NO_PORT"),
    "NO_DOMAIN": (4400, "NO_DOMAIN"),
    "NO_HUB_SERVER": (4400, "NO_HUB_SERVER"),
    "TARGET_UNREACHABLE": (4502, "TARGET_UNREACHABLE"),
    "SSH_CONNECT_FAILED": (4502, "HUB_SSH_FAILED"),
    "TUNNEL_FAILED": (4502, "TUNNEL_FAILED"),
    "DISPLAY_NOT_FOUND": (4404, "DISPLAY_NOT_FOUND"),
}


def _extract_token(request: web.Request) -> str:
    tok = request.query.get("token")
    if tok:
        return tok
    proto = request.headers.get("Sec-WebSocket-Protocol", "")
    for part in (p.strip() for p in proto.split(",")):
        if part.startswith("bearer."):
            return part[len("bearer.") :]
    return ""


def _wants_websocket(request: web.Request) -> bool:
    return request.headers.get("Upgrade", "").lower() == "websocket"


async def health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def ready(request: web.Request) -> web.Response:
    settings: Settings = request.app["settings"]
    if not settings.console_token_secret:
        return web.json_response({"status": "unconfigured"}, status=503)
    return web.json_response({"status": "ready"})


async def console(request: web.Request) -> web.StreamResponse:
    kind = request.match_info["kind"]
    vm_id = request.match_info["vm_id"]
    if kind not in token_mod.VALID_KINDS:
        raise web.HTTPNotFound()
    if _wants_websocket(request):
        return await _console_ws(request, kind, vm_id)
    return _console_page(request, kind, vm_id)


def _console_page(request: web.Request, kind: str, vm_id: str) -> web.Response:
    settings: Settings = request.app["settings"]
    template = "spice.html" if kind == "spice" else "vnc.html"
    path = Path(settings.static_dir) / template
    try:
        html = path.read_text(encoding="utf-8")
    except OSError:
        raise web.HTTPNotFound()
    html = (
        html.replace("__VM_ID__", vm_id)
        .replace("__KIND__", kind)
        .replace("__PREFIX__", settings.path_prefix)
    )
    resp = web.Response(text=html, content_type="text/html")
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
        "frame-ancestors 'self'"
    )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


async def _console_ws(request: web.Request, kind: str, vm_id: str) -> web.WebSocketResponse:
    settings: Settings = request.app["settings"]
    resolver: Resolver = request.app["resolver"]

    # noVNC согласует subprotocol 'binary'; отвечаем тем же, чтобы клиент не рвал.
    ws = web.WebSocketResponse(protocols=("binary",), max_msg_size=0)
    await ws.prepare(request)

    raw = _extract_token(request)
    try:
        claims = token_mod.verify(
            raw,
            secret=settings.console_token_secret,
            issuer=settings.console_token_issuer,
            leeway=settings.console_token_leeway_seconds,
        )
    except token_mod.TokenError as exc:
        code, reason = _TOKEN_CLOSE.get(exc.code, (4401, "UNAUTHORIZED"))
        await ws.close(code=code, message=reason.encode())
        return ws

    if claims.kind != kind or claims.vm_id != vm_id:
        # Токен выписан на другую ВМ/тип, чем запрошенный путь.
        await ws.close(code=4403, message=b"TOKEN_MISMATCH")
        return ws

    try:
        target = await resolver.open(claims)
    except ResolveError as exc:
        code, reason = _RESOLVE_CLOSE.get(exc.code, (4502, "RESOLVE_FAILED"))
        log.warning("console target resolve failed vm=%s: %s", vm_id, exc)
        await ws.close(code=code, message=reason.encode())
        return ws

    log.info("console session open vm=%s kind=%s hub=%s", vm_id, kind, claims.hub_ip)
    try:
        await pump(ws, target, idle_timeout=settings.idle_timeout_seconds)
    except Exception as exc:  # noqa: BLE001 — сессия не должна ронять воркер
        log.warning("console session error vm=%s: %s", vm_id, exc)
    finally:
        if not ws.closed:
            await ws.close()
        log.info("console session closed vm=%s", vm_id)
    return ws


def create_app(settings: Settings | None = None) -> web.Application:
    settings = settings or get_settings()
    app = web.Application()
    app["settings"] = settings
    app["internal_client"] = InternalClient(
        base_url=settings.server_service_base_url,
        api_key=settings.server_service_api_key,
        api_key_name=settings.server_service_api_key_name,
        timeout=settings.internal_request_timeout,
    )
    app["resolver"] = build_resolver(settings, app["internal_client"])

    prefix = settings.path_prefix.rstrip("/")
    app.router.add_get("/healthz", health)
    app.router.add_get("/readyz", ready)
    app.router.add_get(f"{prefix}/{{kind}}/{{vm_id}}", console)

    static_dir = Path(settings.static_dir)
    assets = static_dir / "assets"
    if assets.is_dir():
        app.router.add_static(f"{prefix}/assets", str(assets), show_index=False)
    return app
