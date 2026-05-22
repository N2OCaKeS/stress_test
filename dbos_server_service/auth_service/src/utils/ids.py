"""Генерация ID с префиксами — `usr_<hex>`, `dep_<hex>`, и т.д.

Префиксы помогают читаемости логов и распознаванию типа в audit-trail.
"""

import uuid


def _new_id(prefix: str) -> str:
    """Внутренний помощник: префикс + uuid4 hex (32 символа)."""
    return f"{prefix}{uuid.uuid4().hex}"


def user_id() -> str:
    """`usr_<hex>` — User."""
    return _new_id("usr_")


def department_id() -> str:
    """`dep_<hex>` — Department."""
    return _new_id("dep_")


def session_id() -> str:
    """`ses_<hex>` — Session (refresh-токен)."""
    return _new_id("ses_")


def pat_id() -> str:
    """`pat_<hex>` — PersonalAccessToken."""
    return _new_id("pat_")


def bot_id() -> str:
    """`bot_<hex>` — BotAccount."""
    return _new_id("bot_")


def bot_token_id() -> str:
    """`btk_<hex>` — BotToken."""
    return _new_id("btk_")


def ban_id() -> str:
    """`ban_<hex>` — Ban."""
    return _new_id("ban_")


def oauth_client_id() -> str:
    """`cli_<hex>` — OAuthClient (это и есть public client_id)."""
    return _new_id("cli_")


def oauth_code_id() -> str:
    """`oac_<hex>` — OAuthAuthorizationCode."""
    return _new_id("oac_")


def service_role_def_id() -> str:
    """`srd_<hex>` — ServiceRoleDefinition."""
    return _new_id("srd_")


def group_id() -> str:
    """`grp_<hex>` — UserGroup."""
    return _new_id("grp_")


def group_membership_id() -> str:
    """`gms_<hex>` — UserGroupMembership."""
    return _new_id("gms_")


def group_service_access_id() -> str:
    """`gsa_<hex>` — GroupServiceAccess."""
    return _new_id("gsa_")


def group_service_role_id() -> str:
    """`gsr_<hex>` — GroupServiceRole."""
    return _new_id("gsr_")


def bot_service_role_id() -> str:
    """`bsr_<hex>` — BotServiceRole."""
    return _new_id("bsr_")
