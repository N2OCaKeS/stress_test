"""Тест: PKCE-plain для confidential клиентов эмитит audit-event.

До фикса `logger.warning` на `code_challenge_method=plain` уходил
только в локальный лог контейнера — SIEM не видел. После фикса параллельно
эмитится `oauth.pkce_plain_used` (WARNING), чтобы попадало в loging_service
и можно было отслеживать неправильно сконфигурённые интеграции.
"""

CLIENTS_URL = "/api/auth/v1/oauth2/clients"
AUTHORIZE_URL = "/api/auth/v1/oauth2/authorize"


async def _make_confidential_client(client, admin_token, dept_id, *, name):
    resp = await client.post(
        CLIENTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "department_id": dept_id,
            "name": name,
            "grant_types": ["authorization_code"],
            "redirect_uris": ["https://app.example.com/cb"],
            "allowed_scopes": [],
            "is_public": False,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_confidential_pkce_plain_emits_audit(
    client, admin_token, user_a_token, dept_a, monkeypatch,
):
    cc = await _make_confidential_client(
        client, admin_token, dept_a.id, name="conf_pkce_plain_audit",
    )

    captured: list[dict] = []
    from src.services import audit_service as _audit
    real_emit = _audit.emit

    def _spy(action, *args, **kwargs):
        if action == "oauth.pkce_plain_used":
            captured.append({"action": action, "args": args, "kwargs": kwargs})
        return real_emit(action, *args, **kwargs)

    monkeypatch.setattr(_audit, "emit", _spy)

    resp = await client.get(
        AUTHORIZE_URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        params={
            "client_id": cc["client_id"],
            "redirect_uri": "https://app.example.com/cb",
            "response_type": "code",
            "code_challenge": "plain-challenge-from-legacy-client",
            "code_challenge_method": "plain",
        },
        follow_redirects=False,
    )
    # `plain` для confidential — back-compat, 302 с кодом.
    assert resp.status_code == 302, resp.text

    assert len(captured) == 1, f"ожидался ровно один audit, got {captured}"
    details = captured[0]["kwargs"]["details"]
    assert details["client_id"] == cc["client_id"]
    assert "user_id" in details


async def test_confidential_pkce_s256_does_not_emit_audit(
    client, admin_token, user_a_token, dept_a, monkeypatch,
):
    """S256 для confidential клиента не должен эмитить pkce_plain_used."""
    import base64
    import hashlib

    cc = await _make_confidential_client(
        client, admin_token, dept_a.id, name="conf_pkce_s256_quiet",
    )

    captured: list[dict] = []
    from src.services import audit_service as _audit
    real_emit = _audit.emit

    def _spy(action, *args, **kwargs):
        if action == "oauth.pkce_plain_used":
            captured.append({"action": action})
        return real_emit(action, *args, **kwargs)

    monkeypatch.setattr(_audit, "emit", _spy)

    verifier = "valid-verifier-with-enough-length-zzzzzzz1234567890ab"
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    resp = await client.get(
        AUTHORIZE_URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        params={
            "client_id": cc["client_id"],
            "redirect_uri": "https://app.example.com/cb",
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text
    assert captured == []
