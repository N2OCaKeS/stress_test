"""IdentityContext.platform_role теперь PlatformRole enum, не сырая строка.

До: `platform_role: str | None`, magic-strings в 4+ местах.
После: `PlatformRole | None`, pydantic валидирует — typo тихо не пройдёт.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.core.constants import PlatformRole
from src.schemas.identity import IdentityContext


def test_known_platform_role_string_coerced_to_enum():
    """Строки `account_admin`/`department_admin`/`loging_admin`/`loging_reader`
    коэрсятся в enum.
    """
    for name in ("account_admin", "department_admin", "loging_admin", "loging_reader"):
        i = IdentityContext(user_id="u", username="n", platform_role=name)
        assert isinstance(i.platform_role, PlatformRole)
        assert i.platform_role == name  # equality со строкой сохраняется


def test_unknown_platform_role_rejected():
    """Опечатка в значении — pydantic 422 на валидации."""
    with pytest.raises(ValidationError):
        IdentityContext(user_id="u", username="n", platform_role="acount_admin")


def test_none_platform_role_allowed():
    """Обычный юзер — без platform-роли."""
    i = IdentityContext(user_id="u", username="n", platform_role=None)
    assert i.platform_role is None


def test_enum_equality_works_in_existing_checks():
    """Все use-case'ы сравнивают `platform_role == "account_admin"` —
    после миграции на enum equality должна работать с обеих сторон.
    """
    i = IdentityContext(user_id="u", username="n", platform_role="account_admin")
    # Со стороны кода
    assert i.platform_role == "account_admin"
    # Со стороны enum
    assert i.platform_role == PlatformRole.ACCOUNT_ADMIN
    # И наоборот
    assert PlatformRole.ACCOUNT_ADMIN == i.platform_role
