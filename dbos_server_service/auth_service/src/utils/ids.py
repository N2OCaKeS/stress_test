"""Prefixed ID generation."""

import uuid


def _new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex}"


def user_id() -> str:
    return _new_id("usr_")


def department_id() -> str:
    return _new_id("dep_")


def session_id() -> str:
    return _new_id("ses_")


def pat_id() -> str:
    return _new_id("pat_")


def bot_id() -> str:
    return _new_id("bot_")


def bot_token_id() -> str:
    return _new_id("btk_")


def ban_id() -> str:
    return _new_id("ban_")


def oauth_client_id() -> str:
    return _new_id("cli_")


def oauth_code_id() -> str:
    return _new_id("oac_")


def service_role_def_id() -> str:
    return _new_id("srd_")


def group_id() -> str:
    return _new_id("grp_")


def group_membership_id() -> str:
    return _new_id("gms_")


def group_service_access_id() -> str:
    return _new_id("gsa_")


def group_service_role_id() -> str:
    return _new_id("gsr_")
