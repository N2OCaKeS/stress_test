"""Проверка консольного токена.

Токен — это компактный JWT (HS256), который подписывает server_service тем же
секретом, что задан прокси в `VM_CONSOLE_TOKEN_SECRET`. Формат стандартный
(`base64url(header).base64url(payload).base64url(hmac)`), поэтому на стороне
server_service его можно собрать хоть stdlib, хоть PyJWT — лишь бы alg=HS256 и
секрет совпадал.

Полезная нагрузка (claims):

* `vm_id`        — ID ВМ (`vm_...`), нужен только для логов/аудита прокси;
* `kind`         — `vnc` | `spice` | `serial`;
* `hub_ip`       — IP хаба, где живёт домен и его VNC/SPICE-сокет;
* `hub_server_id`— ID хаб-сервера (`srv_...`) для fetch'а mgmt-кред в ssh-режиме;
* `port`         — TCP-порт консоли на хабе, если server_service его уже знает
                   (иначе `None` → прокси резолвит через virsh);
* `display`      — номер VNC-дисплея (альтернатива порту), опционально;
* `ssh_port`     — SSH-порт хаба (дефолт 22);
* `iss`, `iat`, `exp`, `jti` — стандартные.

Сам `hub_ip`/`port` в браузер не отдаются — они спрятаны в подписанном токене,
браузер видит только сам токен и публичный ws-URL прокси.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

VALID_KINDS = frozenset({"vnc", "spice", "serial"})


class TokenError(Exception):
    """Токен не прошёл проверку. `code` — стабильный машинный код для close-кадра."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ConsoleClaims:
    vm_id: str
    kind: str
    hub_ip: str
    hub_server_id: str | None
    department_id: str | None
    domain: str | None
    port: int | None
    display: int | None
    ssh_port: int
    jti: str | None
    exp: int


def _b64url_decode(segment: str) -> bytes:
    pad = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + pad)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign(payload: dict, secret: str, *, issuer: str = "dbos-server-service") -> str:
    """Собрать консольный JWT. Используется тестами и как эталон для server_service."""
    header = {"alg": "HS256", "typ": "JWT"}
    body = dict(payload)
    body.setdefault("iss", issuer)
    body.setdefault("iat", int(time.time()))
    signing_input = (
        _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url_encode(json.dumps(body, separators=(",", ":")).encode())
    )
    mac = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return signing_input + "." + _b64url_encode(mac)


def verify(
    token: str,
    *,
    secret: str,
    issuer: str,
    leeway: int = 10,
    now: int | None = None,
) -> ConsoleClaims:
    """Проверить подпись, срок и структуру токена. Бросает `TokenError`."""
    if not secret:
        raise TokenError("SERVER_MISCONFIGURED", "console token secret is not configured")
    if not token:
        raise TokenError("MISSING_TOKEN", "token is empty")

    parts = token.split(".")
    if len(parts) != 3:
        raise TokenError("MALFORMED", "token is not a compact JWS")
    header_b64, payload_b64, sig_b64 = parts

    try:
        header = json.loads(_b64url_decode(header_b64))
    except (ValueError, json.JSONDecodeError) as exc:
        raise TokenError("MALFORMED", "bad header") from exc
    if header.get("alg") != "HS256":
        raise TokenError("BAD_ALG", "only HS256 is accepted")

    signing_input = f"{header_b64}.{payload_b64}".encode()
    expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    try:
        given = _b64url_decode(sig_b64)
    except ValueError as exc:
        raise TokenError("MALFORMED", "bad signature encoding") from exc
    if not hmac.compare_digest(expected, given):
        raise TokenError("INVALID_SIGNATURE", "signature mismatch")

    try:
        claims = json.loads(_b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError) as exc:
        raise TokenError("MALFORMED", "bad payload") from exc

    if claims.get("iss") != issuer:
        raise TokenError("BAD_ISSUER", "unexpected issuer")

    ts = int(now if now is not None else time.time())
    exp = claims.get("exp")
    if not isinstance(exp, int):
        raise TokenError("MALFORMED", "exp missing")
    if ts > exp + leeway:
        raise TokenError("EXPIRED", "token expired")
    iat = claims.get("iat")
    if isinstance(iat, int) and iat > ts + leeway:
        raise TokenError("NOT_YET_VALID", "token issued in the future")

    kind = claims.get("kind")
    if kind not in VALID_KINDS:
        raise TokenError("BAD_KIND", f"kind must be one of {sorted(VALID_KINDS)}")

    hub_ip = claims.get("hub_ip")
    if not hub_ip or not isinstance(hub_ip, str):
        raise TokenError("MALFORMED", "hub_ip missing")

    vm_id = claims.get("vm_id")
    if not vm_id or not isinstance(vm_id, str):
        raise TokenError("MALFORMED", "vm_id missing")

    port = claims.get("port")
    if port is not None and not isinstance(port, int):
        raise TokenError("MALFORMED", "port must be int or null")
    display = claims.get("display")
    if display is not None and not isinstance(display, int):
        raise TokenError("MALFORMED", "display must be int or null")

    return ConsoleClaims(
        vm_id=vm_id,
        kind=kind,
        hub_ip=hub_ip,
        hub_server_id=claims.get("hub_server_id"),
        department_id=claims.get("department_id"),
        domain=claims.get("domain"),
        port=port,
        display=display,
        ssh_port=int(claims.get("ssh_port") or 22),
        jti=claims.get("jti"),
        exp=exp,
    )
