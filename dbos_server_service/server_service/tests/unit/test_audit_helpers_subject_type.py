"""emit_denied_on_authz_error: проброс subject_type через identity.

До фикса denied-аудит не нёс информацию о типе caller'а — bot, PAT и
человеческие запросы были неразличимы в loging_service. Теперь, если
call-site пробрасывает identity, контекст-менеджер кладёт `subject_type`
в `details` (но не перетирает явное значение из `extra_details`).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.core.exceptions import AuthorizationError
from src.schemas.identity import IdentityContext
from src.services.audit_helpers import emit_denied_on_authz_error


def _identity(subject_type: str | None) -> IdentityContext:
    return IdentityContext(
        user_id="usr_test",
        username="tester",
        department_id="dep_a",
        department_name=None,
        allowed_services=["server_service"],
        service_roles={"server_service": ["admin"]},
        is_banned=False,
        platform_role=None,
        subject_type=subject_type,
    )


def _raise_authz() -> None:
    raise AuthorizationError(
        error_code="PERMISSION_DENIED", message="no",
    )


class TestEmitDeniedSubjectType:
    def test_subject_type_emitted_when_identity_passed(self):
        ident = _identity("bot")
        with patch("src.services.audit_helpers.audit_service.emit") as mock_emit:
            with pytest.raises(AuthorizationError):
                with emit_denied_on_authz_error(
                    "server.power_on",
                    target_type="server",
                    target_id="srv_123",
                    identity=ident,
                ):
                    _raise_authz()
        assert mock_emit.called
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["details"]["subject_type"] == "bot"
        assert kwargs["details"]["reason"] == "permission_denied"

    def test_subject_type_absent_without_identity(self):
        with patch("src.services.audit_helpers.audit_service.emit") as mock_emit:
            with pytest.raises(AuthorizationError):
                with emit_denied_on_authz_error(
                    "server.power_on",
                    target_type="server",
                    target_id="srv_123",
                ):
                    _raise_authz()
        kwargs = mock_emit.call_args.kwargs
        assert "subject_type" not in kwargs["details"]

    def test_subject_type_absent_when_identity_has_none(self):
        ident = _identity(None)
        with patch("src.services.audit_helpers.audit_service.emit") as mock_emit:
            with pytest.raises(AuthorizationError):
                with emit_denied_on_authz_error(
                    "server.power_on",
                    target_type="server",
                    identity=ident,
                ):
                    _raise_authz()
        kwargs = mock_emit.call_args.kwargs
        assert "subject_type" not in kwargs["details"]

    def test_extra_details_subject_type_wins_over_identity(self):
        """Явный subject_type в extra_details не перетирается identity-значением."""
        ident = _identity("user")
        with patch("src.services.audit_helpers.audit_service.emit") as mock_emit:
            with pytest.raises(AuthorizationError):
                with emit_denied_on_authz_error(
                    "server.power_on",
                    target_type="server",
                    extra_details={"subject_type": "pat"},
                    identity=ident,
                ):
                    _raise_authz()
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["details"]["subject_type"] == "pat"
