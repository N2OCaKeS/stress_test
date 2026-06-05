"""auth db hardening: FK indexes, enum CHECK constraints, sessions expires_at index

Revision ID: g4h5i6j7k8l9
Revises: d1c2b3a4f5e6
Create Date: 2026-06-05 00:00:00.000000

Additive only — safe to rollback. Дополняет схему недостающими b-tree
индексами по FK-колонкам (cascade-delete без них даёт full-scan),
defense-in-depth CHECK-ами по enum-as-VARCHAR полям и partial-индексом
под session sweep-query.

"""
from typing import Sequence, Union

from alembic import op

revision: str = "g4h5i6j7k8l9"
down_revision: Union[str, None] = "d1c2b3a4f5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Допустимые значения для CHECK-ов. Зеркалят соответствующие enum'ы в
# `core.constants` и hardcoded строки в `models/department_docker_registry.py`,
# `models/personal_access_token.py` docstring и RFC 7636 PKCE method'ы.
# Любое расширение enum'а требует новой миграции на ALTER CHECK.
_USER_STATUS = ("active", "blocked", "banned")
_BAN_TYPE = ("temporary", "permanent")
_BOT_STATUS = ("active", "disabled")
_PULL_POLICY = ("all", "restricted")
_REVOKED_REASON = ("ban", "user", "expired", "admin_reset")
_PKCE_METHOD = ("S256", "plain")


def _values(items: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in items)


def upgrade() -> None:
    # ── FK indexes по service_name (нужны для cascade-delete platform_services)
    op.create_index(
        "ix_department_service_access_service_name",
        "department_service_access",
        ["service_name"],
    )
    op.create_index(
        "ix_user_service_roles_service_name",
        "user_service_roles",
        ["service_name"],
    )
    op.create_index(
        "ix_group_service_roles_service_name",
        "group_service_roles",
        ["service_name"],
    )
    op.create_index(
        "ix_group_service_access_service_name",
        "group_service_access",
        ["service_name"],
    )
    op.create_index(
        "ix_bot_service_roles_service_name",
        "bot_service_roles",
        ["service_name"],
    )

    # ── FK index по user_id (cascade-delete users → oauth_authorization_codes)
    op.create_index(
        "ix_oauth_authorization_codes_user_id",
        "oauth_authorization_codes",
        ["user_id"],
    )

    # ── Partial index под sessions.sweep_expired (is_active = true)
    op.create_index(
        "ix_sessions_expires_at",
        "sessions",
        ["expires_at"],
        postgresql_where="is_active = true",
    )

    # ── CHECK constraints по enum-as-VARCHAR
    op.create_check_constraint(
        "ck_users_status",
        "users",
        f"status IN ({_values(_USER_STATUS)})",
    )
    op.create_check_constraint(
        "ck_bans_ban_type",
        "bans",
        f"ban_type IN ({_values(_BAN_TYPE)})",
    )
    op.create_check_constraint(
        "ck_bot_accounts_status",
        "bot_accounts",
        f"status IN ({_values(_BOT_STATUS)})",
    )
    op.create_check_constraint(
        "ck_department_docker_registry_pull_policy",
        "department_docker_registry",
        f"pull_policy IN ({_values(_PULL_POLICY)})",
    )
    op.create_check_constraint(
        "ck_personal_access_tokens_revoked_reason",
        "personal_access_tokens",
        f"revoked_reason IS NULL OR revoked_reason IN ({_values(_REVOKED_REASON)})",
    )
    op.create_check_constraint(
        "ck_oauth_authorization_codes_code_challenge_method",
        "oauth_authorization_codes",
        f"code_challenge_method IS NULL OR code_challenge_method IN ({_values(_PKCE_METHOD)})",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_oauth_authorization_codes_code_challenge_method",
        "oauth_authorization_codes",
        type_="check",
    )
    op.drop_constraint(
        "ck_personal_access_tokens_revoked_reason",
        "personal_access_tokens",
        type_="check",
    )
    op.drop_constraint(
        "ck_department_docker_registry_pull_policy",
        "department_docker_registry",
        type_="check",
    )
    op.drop_constraint("ck_bot_accounts_status", "bot_accounts", type_="check")
    op.drop_constraint("ck_bans_ban_type", "bans", type_="check")
    op.drop_constraint("ck_users_status", "users", type_="check")

    op.drop_index("ix_sessions_expires_at", table_name="sessions")

    op.drop_index(
        "ix_oauth_authorization_codes_user_id",
        table_name="oauth_authorization_codes",
    )
    op.drop_index(
        "ix_bot_service_roles_service_name", table_name="bot_service_roles"
    )
    op.drop_index(
        "ix_group_service_access_service_name", table_name="group_service_access"
    )
    op.drop_index(
        "ix_group_service_roles_service_name", table_name="group_service_roles"
    )
    op.drop_index(
        "ix_user_service_roles_service_name", table_name="user_service_roles"
    )
    op.drop_index(
        "ix_department_service_access_service_name",
        table_name="department_service_access",
    )
