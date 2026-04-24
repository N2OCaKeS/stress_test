"""Shared enums and constants."""

from enum import StrEnum


class UserStatus(StrEnum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    BANNED = "banned"


class PlatformRole(StrEnum):
    """Auth-level roles managed exclusively by auth_service."""
    ACCOUNT_ADMIN = "account_admin"
    DEPARTMENT_ADMIN = "department_admin"


class ServiceRole(StrEnum):
    """Per-service roles applied by all application services."""
    GUEST = "guest"
    READER = "reader"
    OPERATOR = "operator"
    ADMIN = "admin"


class BanType(StrEnum):
    TEMPORARY = "temporary"
    PERMANENT = "permanent"


class SubjectType(StrEnum):
    USER = "user"
    BOT = "bot"


class BotStatus(StrEnum):
    ACTIVE = "active"
    BLOCKED = "blocked"


# Token prefixes — raw value shown once, only hash stored in DB
PAT_PREFIX = "dbos_pat_"
BOT_TOKEN_PREFIX = "dbos_bot_"
