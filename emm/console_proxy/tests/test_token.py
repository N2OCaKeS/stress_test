from __future__ import annotations

import time

import pytest

from src import token as tok

SECRET = "test-console-secret-at-least-32-chars-long"
ISS = "dbos-server-service"


def _claims(**over):
    base = {
        "vm_id": "vm_abc",
        "kind": "vnc",
        "hub_ip": "10.0.0.5",
        "hub_server_id": "srv_1",
        "port": 5901,
        "exp": int(time.time()) + 120,
        "jti": "vmc_xyz",
    }
    base.update(over)
    return base


def test_sign_verify_roundtrip():
    t = tok.sign(_claims(), SECRET, issuer=ISS)
    c = tok.verify(t, secret=SECRET, issuer=ISS)
    assert c.vm_id == "vm_abc"
    assert c.kind == "vnc"
    assert c.hub_ip == "10.0.0.5"
    assert c.port == 5901
    assert c.hub_server_id == "srv_1"
    assert c.ssh_port == 22


def test_display_and_domain_optional():
    t = tok.sign(_claims(port=None, display=3, domain="my-vm"), SECRET, issuer=ISS)
    c = tok.verify(t, secret=SECRET, issuer=ISS)
    assert c.port is None
    assert c.display == 3
    assert c.domain == "my-vm"


def test_expired():
    t = tok.sign(_claims(exp=int(time.time()) - 100), SECRET, issuer=ISS)
    with pytest.raises(tok.TokenError) as e:
        tok.verify(t, secret=SECRET, issuer=ISS, leeway=0)
    assert e.value.code == "EXPIRED"


def test_bad_signature():
    t = tok.sign(_claims(), SECRET, issuer=ISS)
    with pytest.raises(tok.TokenError) as e:
        tok.verify(t, secret="another-secret-entirely-different-key", issuer=ISS)
    assert e.value.code == "INVALID_SIGNATURE"


def test_tampered_payload_fails_signature():
    t = tok.sign(_claims(port=5901), SECRET, issuer=ISS)
    head, payload, sig = t.split(".")
    # Пересобрать другой payload со старой подписью — подпись не сойдётся.
    forged = tok.sign(_claims(port=1), SECRET, issuer=ISS).split(".")[1]
    with pytest.raises(tok.TokenError) as e:
        tok.verify(f"{head}.{forged}.{sig}", secret=SECRET, issuer=ISS)
    assert e.value.code == "INVALID_SIGNATURE"


def test_bad_issuer():
    t = tok.sign(_claims(), SECRET, issuer="evil")
    with pytest.raises(tok.TokenError) as e:
        tok.verify(t, secret=SECRET, issuer=ISS)
    assert e.value.code == "BAD_ISSUER"


def test_bad_kind():
    t = tok.sign(_claims(kind="ftp"), SECRET, issuer=ISS)
    with pytest.raises(tok.TokenError) as e:
        tok.verify(t, secret=SECRET, issuer=ISS)
    assert e.value.code == "BAD_KIND"


def test_malformed():
    with pytest.raises(tok.TokenError) as e:
        tok.verify("not-a-jwt", secret=SECRET, issuer=ISS)
    assert e.value.code == "MALFORMED"


def test_missing_token():
    with pytest.raises(tok.TokenError) as e:
        tok.verify("", secret=SECRET, issuer=ISS)
    assert e.value.code == "MISSING_TOKEN"


def test_missing_secret_is_misconfig():
    t = tok.sign(_claims(), SECRET, issuer=ISS)
    with pytest.raises(tok.TokenError) as e:
        tok.verify(t, secret="", issuer=ISS)
    assert e.value.code == "SERVER_MISCONFIGURED"


def test_alg_none_rejected():
    import base64
    import json

    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "none", "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps(_claims()).encode()
    ).rstrip(b"=").decode()
    with pytest.raises(tok.TokenError) as e:
        tok.verify(f"{header}.{payload}.", secret=SECRET, issuer=ISS)
    assert e.value.code in ("BAD_ALG", "INVALID_SIGNATURE", "MALFORMED")
