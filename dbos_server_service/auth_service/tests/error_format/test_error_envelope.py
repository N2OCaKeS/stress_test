"""AUTH-TEST-110: единый envelope-формат ошибок + X-Request-ID."""

LOGIN_URL = "/api/auth/v1/login"
ME_URL = "/api/auth/v1/me"
SERVICES_URL = "/api/auth/v1/services"
HEALTH_URL = "/api/auth/v1/health"

_ENVELOPE_KEYS = {"error", "error_code", "message", "details", "request_id", "timestamp"}


# ── Envelope shape ────────────────────────────────────────────────────────────


async def test_401_error_has_full_envelope(client):
    """401 от AuthenticationError (missing token) — все поля envelope присутствуют."""
    resp = await client.get(ME_URL)
    assert resp.status_code == 401
    body = resp.json()
    assert _ENVELOPE_KEYS.issubset(body.keys()), f"missing fields: {_ENVELOPE_KEYS - set(body.keys())}"
    assert body["error_code"] == "INVALID_TOKEN"
    assert body["request_id"]
    assert body["timestamp"]


async def test_403_error_has_full_envelope(client, user_a_token):
    """403 от AuthorizationError (regular user → admin endpoint) — все поля envelope присутствуют."""
    resp = await client.post(SERVICES_URL,
                              headers={"Authorization": f"Bearer {user_a_token}"},
                              json={"service_name": "x", "display_name": "X"})
    assert resp.status_code == 403
    body = resp.json()
    assert _ENVELOPE_KEYS.issubset(body.keys())
    assert body["error_code"] == "ROLE_REQUIRED"


async def test_401_invalid_credentials_has_full_envelope(client, account_admin):
    """401 от AppException в auth_service — единый envelope."""
    resp = await client.post(LOGIN_URL, json={"username": "t_admin", "password": "WRONG"})
    assert resp.status_code == 401
    body = resp.json()
    assert _ENVELOPE_KEYS.issubset(body.keys())
    assert body["error_code"] == "INVALID_CREDENTIALS"


# ── Pydantic validation errors ────────────────────────────────────────────────


async def test_validation_error_returns_422_with_validation_code(client):
    """Pydantic-валидация падает → 422 с error_code=VALIDATION_ERROR."""
    resp = await client.post(LOGIN_URL, json={"username": "only_user"})  # password обязателен
    assert resp.status_code == 422
    body = resp.json()
    assert _ENVELOPE_KEYS.issubset(body.keys())
    assert body["error_code"] == "VALIDATION_ERROR"
    assert body["error"] == "validation_error"
    assert "errors" in body["details"]


# ── X-Request-ID middleware ───────────────────────────────────────────────────


async def test_x_request_id_from_header_is_echoed(client):
    """Если клиент прислал X-Request-ID — он же в ответе и в envelope ошибки."""
    custom_id = "req_custom_abcdef"
    resp = await client.get(ME_URL, headers={"X-Request-ID": custom_id})
    assert resp.headers["X-Request-ID"] == custom_id
    assert resp.json()["request_id"] == custom_id


async def test_x_request_id_auto_generated_when_not_provided(client):
    """Без X-Request-ID — middleware генерирует req_<12hex> и возвращает в заголовке + envelope."""
    resp = await client.get(ME_URL)
    rid = resp.headers.get("X-Request-ID")
    assert rid and rid.startswith("req_") and len(rid) == 4 + 12
    assert resp.json()["request_id"] == rid


async def test_x_request_id_present_on_success(client):
    """X-Request-ID присутствует и в успешных ответах (не только в ошибках)."""
    resp = await client.get(HEALTH_URL)
    assert resp.status_code == 200
    assert resp.headers.get("X-Request-ID", "").startswith("req_")
