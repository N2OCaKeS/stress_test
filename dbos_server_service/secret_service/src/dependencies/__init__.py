"""FastAPI dependencies для secret_service.

* ``get_db`` — async DB-сессия на один request.
* ``get_identity`` — resolve identity вызывающего через introspect в auth_service.
"""

from src.dependencies.auth import (
    CurrentIdentity,
    Identity,
    get_identity,
    require_account_admin,
    require_dept_admin_for,
    require_service_admin,
    require_user_context,
)
from src.dependencies.db import get_db

__all__ = [
    "CurrentIdentity",
    "Identity",
    "get_db",
    "get_identity",
    "require_account_admin",
    "require_dept_admin_for",
    "require_service_admin",
    "require_user_context",
]
