"""Unit-тесты Pydantic-схем — без HTTP/БД."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.schemas.auth import IdentityContext, LoginRequest
from src.schemas.bots import BotCreate, BotRoleAssignRequest
from src.schemas.common import OkResponse
from src.schemas.groups import GroupCreate, GroupRoleAssignRequest
from src.schemas.oauth import OAuthClientCreate, OAuthTokenRequest
from src.schemas.service_roles import ServiceRoleCreate
from src.schemas.users import BanRequest, ResetPasswordRequest, UserCreate, UserUpdate


# ── UserCreate ───────────────────────────────────────────────────────────────

class TestUserCreate:
    def test_minimal_valid(self):
        m = UserCreate(username="alice123", password="Strong123abcd", department_id="dep_a")
        assert m.username == "alice123"
        assert m.platform_role is None

    def test_username_min_length(self):
        with pytest.raises(ValidationError):
            UserCreate(username="ab", password="Strong123abcd", department_id="dep_a")

    def test_username_max_length(self):
        UserCreate(username="u" * 128, password="Strong123abcd", department_id="dep_a")
        with pytest.raises(ValidationError):
            UserCreate(username="u" * 129, password="Strong123abcd", department_id="dep_a")

    def test_password_min_length(self):
        with pytest.raises(ValidationError):
            UserCreate(username="alice", password="abc", department_id="dep_a")

    def test_password_complexity_letters_and_digits(self):
        # Только буквы — fail.
        with pytest.raises(ValidationError):
            UserCreate(username="alice", password="onlyletters", department_id="dep_a")
        # Только цифры — fail.
        with pytest.raises(ValidationError):
            UserCreate(username="alice", password="12345678", department_id="dep_a")
        # 8+ символов, буквы и цифры — ok.
        UserCreate(username="alice", password="Mix123abcdef", department_id="dep_a")

    def test_email_validated(self):
        with pytest.raises(ValidationError):
            UserCreate(username="alice", password="Strong123abcd", department_id="dep_a",
                       email="not-an-email")
        m = UserCreate(username="alice", password="Strong123abcd", department_id="dep_a",
                       email="alice@example.com")
        assert m.email == "alice@example.com"

    def test_department_id_optional_in_schema(self):
        """Schema-level не требует department_id — это бизнес-валидация в user_service
        (для не-admin ролей)."""
        m = UserCreate(username="root", password="Strong123abcd")
        assert m.department_id is None


# ── UserUpdate ───────────────────────────────────────────────────────────────

class TestUserUpdate:
    def test_empty_update_valid(self):
        m = UserUpdate()
        assert m.model_dump(exclude_none=True) == {}

    def test_partial_update(self):
        m = UserUpdate(status="blocked")
        assert m.model_dump(exclude_none=True) == {"status": "blocked"}

    def test_email_validation(self):
        with pytest.raises(ValidationError):
            UserUpdate(email="bad-email")


# ── BanRequest ───────────────────────────────────────────────────────────────

class TestBanRequest:
    def test_defaults_permanent(self):
        m = BanRequest()
        assert m.ban_type == "permanent"
        assert m.expires_at is None

    def test_temporary_without_expires_at_rejected(self):
        """`ban_type=temporary` БЕЗ `expires_at` — 422.

        Раньше схема не требовала `expires_at` для temporary
        (Pydantic v2-level), и admin мог создать ban с `ban_type="temporary"`
        и `expires_at=null` — фактически permanent, но `Ban.ban_type=temporary`
        в БД. Теперь cross-field model_validator бьёт 422.
        """
        with pytest.raises(ValidationError):
            BanRequest(ban_type="temporary", reason="testing")

    def test_explicit_temporary_with_expires_at(self):
        future = datetime.now(tz=timezone.utc) + timedelta(days=365 * 5)
        m = BanRequest(ban_type="temporary", expires_at=future, reason="ddos")
        assert m.expires_at == future


# ── ResetPasswordRequest ─────────────────────────────────────────────────────

class TestResetPasswordRequest:
    def test_min_length(self):
        with pytest.raises(ValidationError):
            ResetPasswordRequest(new_password="short")

    def test_complexity_letters_and_digits(self):
        # Только цифры — fail (раньше проходило, политика была мягче).
        with pytest.raises(ValidationError):
            ResetPasswordRequest(new_password="12345678")
        # Только буквы — fail.
        with pytest.raises(ValidationError):
            ResetPasswordRequest(new_password="onlyletters")

    def test_valid_letters_and_digits(self):
        m = ResetPasswordRequest(new_password="Mix123abcdef")
        assert m.new_password == "Mix123abcdef"


# ── ServiceRoleCreate ────────────────────────────────────────────────────────

class TestServiceRoleCreate:
    def test_minimal(self):
        m = ServiceRoleCreate(role_name="custom")
        assert m.description is None

    def test_role_name_admin_currently_allowed(self):
        """Schema не запрещает name='admin' — защита `is_system` живёт в service-слое.
        Если в будущем добавим pattern-валидатор, тест надо обновить."""
        m = ServiceRoleCreate(role_name="admin")
        assert m.role_name == "admin"


# ── GroupCreate / BotCreate / OAuth* ─────────────────────────────────────────

class TestGroupCreate:
    def test_requires_department_id(self):
        with pytest.raises(ValidationError):
            GroupCreate(name="grp")

    def test_minimal_valid(self):
        m = GroupCreate(department_id="dep_x", name="grp")
        assert m.description is None


class TestBotCreate:
    def test_requires_name_and_department(self):
        with pytest.raises(ValidationError):
            BotCreate(department_id="dep_a")
        with pytest.raises(ValidationError):
            BotCreate(name="bot")

    def test_default_allowed_services_empty(self):
        m = BotCreate(name="b", department_id="dep_a")
        assert m.allowed_services == []

    def test_name_max_length(self):
        BotCreate(name="b" * 256, department_id="dep_a")
        with pytest.raises(ValidationError):
            BotCreate(name="b" * 257, department_id="dep_a")


class TestBotRoleAssignRequest:
    def test_required_fields(self):
        m = BotRoleAssignRequest(service_name="svc", roles=["reader"])
        assert m.roles == ["reader"]


class TestOAuthClientCreate:
    def test_grant_types_default(self):
        m = OAuthClientCreate(
            name="app",
            department_id="dep_a",
            redirect_uris=["https://app.example.com/cb"],
        )
        assert m.grant_types == ["authorization_code"]

    def test_empty_grant_types_rejected(self):
        """Пустой grant_types бесполезен — схема отбивает явный `[]`."""
        with pytest.raises(ValidationError):
            OAuthClientCreate(name="app", department_id="dep_a", grant_types=[])

    def test_authorization_code_without_redirect_rejected(self):
        """authorization_code-flow требует redirect_uri — без него отказ."""
        with pytest.raises(ValidationError):
            OAuthClientCreate(
                name="app",
                department_id="dep_a",
                grant_types=["authorization_code"],
                redirect_uris=[],
            )

    def test_client_credentials_without_redirect_ok(self):
        """client_credentials не требует redirect_uri — остаётся валидным."""
        m = OAuthClientCreate(
            name="app",
            department_id="dep_a",
            grant_types=["client_credentials"],
            redirect_uris=[],
        )
        assert m.grant_types == ["client_credentials"]


class TestOAuthTokenRequest:
    @pytest.mark.parametrize(
        "grant", ["authorization_code", "client_credentials", "refresh_token"]
    )
    def test_valid_grant_type_accepted(self, grant: str):
        """Pydantic Literal принимает только whitelisted значения."""
        m = OAuthTokenRequest(grant_type=grant)
        assert m.grant_type == grant

    @pytest.mark.parametrize("grant", ["password", "", "foo", "implicit"])
    def test_invalid_grant_type_rejected(self, grant: str):
        """Невалидные значения отбиваются на схеме (422 на endpoint'е)."""
        with pytest.raises(ValidationError):
            OAuthTokenRequest(grant_type=grant)

    def test_missing_grant_type_rejected(self):
        """grant_type обязательный — без него Pydantic вернёт 422."""
        with pytest.raises(ValidationError):
            OAuthTokenRequest()


class TestGroupRoleAssignRequest:
    def test_required(self):
        with pytest.raises(ValidationError):
            GroupRoleAssignRequest(service_name="svc")
        with pytest.raises(ValidationError):
            GroupRoleAssignRequest(roles=["r"])


# ── LoginRequest ─────────────────────────────────────────────────────────────

class TestLoginRequest:
    def test_required_username_password(self):
        with pytest.raises(ValidationError):
            LoginRequest(username="x")
        with pytest.raises(ValidationError):
            LoginRequest(password="x")


# ── IdentityContext ──────────────────────────────────────────────────────────

class TestIdentityContext:
    def test_default_collections(self):
        m = IdentityContext(user_id="u", username="t")
        assert m.allowed_services == []
        assert m.service_roles == {}
        assert m.is_banned is False
        assert m.department_id is None


# ── OkResponse ───────────────────────────────────────────────────────────────

class TestOkResponse:
    def test_default_ok(self):
        assert OkResponse().model_dump() == {"ok": True}
