"""Unit-тесты `src/dependencies/auth.py` — без HTTP, без БД.

Покрытие:
* `_extract_bearer` — варианты заголовка: пустой, без "Bearer ", lowercase
  "bearer ", два пробела, только "Bearer", "Bearer " без токена.
* `get_current_identity` — асинхронно требует валидный JWT, иначе
  AuthenticationError.
* `require_account_admin` / `require_any_admin` — фильтрация по platform_role.
"""

from datetime import timedelta

import pytest
from fastapi import Request

from src.core.exceptions import AuthenticationError, AuthorizationError
from src.core.security import create_access_token
from src.dependencies import auth as auth_dep
from src.schemas.auth import IdentityContext


def _make_request(headers: dict[str, str] | None = None) -> Request:
    """Лёгкий fastapi.Request на чистом ASGI scope (без httpx-клиента)."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/x",
        "headers": [
            (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
        ],
        "query_string": b"",
    }
    return Request(scope)


# ── _extract_bearer ──────────────────────────────────────────────────────────

class TestExtractBearer:
    def test_returns_none_when_no_header(self):
        assert auth_dep._extract_bearer(_make_request()) is None

    def test_returns_token_with_prefix(self):
        assert auth_dep._extract_bearer(_make_request({"Authorization": "Bearer abc.def.ghi"})) == "abc.def.ghi"

    def test_lowercase_bearer_extracted(self):
        """Bearer parsing is case-insensitive per RFC 7235."""
        assert auth_dep._extract_bearer(_make_request({"Authorization": "bearer abc"})) == "abc"

    def test_returns_none_for_basic(self):
        assert auth_dep._extract_bearer(_make_request({"Authorization": "Basic dXNlcjpwYXNz"})) is None

    def test_two_spaces_returns_string_with_leading_space(self):
        """`"Bearer  token"` (два пробела) — Reality: вернёт " token" (с ведущим
        пробелом). Документируем."""
        got = auth_dep._extract_bearer(_make_request({"Authorization": "Bearer  token"}))
        assert got == " token"

    def test_only_word_bearer_without_space_returns_none(self):
        assert auth_dep._extract_bearer(_make_request({"Authorization": "Bearer"})) is None

    def test_bearer_space_only_returns_empty_string(self):
        assert auth_dep._extract_bearer(_make_request({"Authorization": "Bearer "})) == ""


# ── get_current_identity ─────────────────────────────────────────────────────
#
# `get_current_identity` revalidates every JWT against the DB. Only the error
# branches that short-circuit BEFORE the DB lookup are unit-testable here. The
# happy path (valid JWT → live user → rebuilt IdentityContext) and the
# ban/role-revoke revalidate scenarios are covered by HTTP integration tests
# in `tests/auth/test_admin_guard_revalidate.py`.


class TestGetCurrentIdentity:
    async def test_missing_bearer_raises(self):
        with pytest.raises(AuthenticationError) as exc:
            await auth_dep.get_current_identity(_make_request(), db=None)
        assert exc.value.error_code == "INVALID_TOKEN"

    async def test_garbage_token_raises(self):
        req = _make_request({"Authorization": "Bearer not.a.jwt"})
        with pytest.raises(AuthenticationError):
            # DB is never touched: decode fails before any repo call.
            await auth_dep.get_current_identity(req, db=None)

    async def test_expired_token_raises(self):
        # JWT_LEEWAY_SECONDS=10 → нужно уйти ЗА пределы leeway, чтобы decode упал.
        token = create_access_token(
            {"sub": "usr_old", "username": "u"},
            expires_delta=timedelta(seconds=-30),
        )
        req = _make_request({"Authorization": f"Bearer {token}"})
        with pytest.raises(AuthenticationError):
            # Expired JWT also short-circuits before DB.
            await auth_dep.get_current_identity(req, db=None)


# ── require_* role guards ────────────────────────────────────────────────────

def _identity(platform_role: str | None) -> IdentityContext:
    return IdentityContext(user_id="u", username="t", platform_role=platform_role)


def test_identity_rejects_garbage_platform_role():
    """Произвольная строка в `platform_role` отбивается Pydantic'ом до
    того, как identity попадает в guard.

    До перевода на `PlatformRole`-enum это поле было `str | None` —
    любое значение проходило, фильтрация была только на guard'ах.
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        IdentityContext(user_id="u", username="t", platform_role="random")


class TestRequireAccountAdmin:
    def test_account_admin_passes(self):
        identity = _identity("account_admin")
        assert auth_dep.require_account_admin(identity) is identity

    @pytest.mark.parametrize("role", [None, "department_admin", "loging_admin", "loging_reader"])
    def test_other_roles_rejected(self, role):
        with pytest.raises(AuthorizationError) as exc:
            auth_dep.require_account_admin(_identity(role))
        assert exc.value.error_code == "ROLE_REQUIRED"


class TestRequireAnyAdmin:
    @pytest.mark.parametrize("role", ["account_admin", "department_admin"])
    def test_admin_roles_pass(self, role):
        identity = _identity(role)
        assert auth_dep.require_any_admin(identity) is identity

    @pytest.mark.parametrize("role", [None, "loging_admin", "loging_reader"])
    def test_non_admin_rejected(self, role):
        with pytest.raises(AuthorizationError):
            auth_dep.require_any_admin(_identity(role))
