"""Подписанный токен графической консоли ВМ (vnc/spice) для console-прокси.

server_service токен не хранит: он самодостаточен и проверяется прокси по
общему секрету (`VM_CONSOLE_TOKEN_SECRET`). Формат — стандартный компактный
JWT (HS256) на stdlib, тот же, что читает `console_proxy/src/token.py`:

    <base64url(header)>.<base64url(payload)>.<base64url(hmac_sha256)>

payload несёт claims, которые нужны прокси-резолверу (SSH-туннель к хабу +
`virsh domdisplay`):

* `vm_id`         — ID ВМ (`vm_...`);
* `kind`          — `vnc` | `spice`;
* `hub_ip`        — IP хаба, где живёт домен и его VNC/SPICE-сокет;
* `hub_server_id` — ID хаб-сервера (`srv_...`) для fetch'а mgmt-кред в ssh-режиме;
* `ssh_port`      — SSH-порт хаба (для туннеля);
* `port`          — TCP-порт дисплея на хабе, если известен (иначе `None` →
                    прокси резолвит его через virsh);
* `domain`        — имя libvirt-домена (для `virsh domdisplay`, если порт не задан);
* `iss`, `iat`, `exp`, `jti` — стандартные.

Прокси декодирует payload, сверяет подпись (constant-time), `iss` и `exp`, затем
проксирует websocket к дисплею ВМ на хабе. Одноразовость — на стороне прокси
(короткий TTL).
"""

import base64
import hashlib
import hmac
import json
import secrets
import time

# Должен совпадать с ожидаемым `iss` прокси (VM_CONSOLE_TOKEN_ISSUER, дефолт).
_ISS = "dbos-server-service"


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _signing_input(header: dict, payload: dict) -> str:
    return (
        _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        + "."
        + _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    )


def _sign(signing_input: str, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256)
    return _b64url_encode(mac.digest())


def issue(
    *,
    secret: str,
    vm_id: str,
    kind: str,
    hub_ip: str,
    hub_server_id: str | None,
    ssh_port: int,
    port: int | None,
    domain: str | None,
    ttl_seconds: int,
) -> str:
    """Выписать консольный JWT (HS256). `kind` ∈ {vnc, spice}."""
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "vm_id": vm_id,
        "kind": kind,
        "hub_ip": hub_ip,
        "hub_server_id": hub_server_id,
        "ssh_port": ssh_port,
        "port": port,
        "domain": domain,
        "iss": _ISS,
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": secrets.token_urlsafe(12),
    }
    signing_input = _signing_input(header, payload)
    return f"{signing_input}.{_sign(signing_input, secret)}"


def verify(token: str, secret: str) -> dict | None:
    """Проверить подпись, `iss` и срок токена. Возвращает claims или None.

    Референс для тестов и симметрии с прокси — сам server_service токены не
    валидирует.
    """
    try:
        header_b64, payload_b64, sig = token.split(".")
    except ValueError:
        return None
    try:
        header = json.loads(_b64url_decode(header_b64))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(header, dict) or header.get("alg") != "HS256":
        return None
    signing_input = f"{header_b64}.{payload_b64}"
    if not hmac.compare_digest(sig, _sign(signing_input, secret)):
        return None
    try:
        claims = json.loads(_b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(claims, dict):
        return None
    if claims.get("iss") != _ISS:
        return None
    exp = claims.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        return None
    return claims
