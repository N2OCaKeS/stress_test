"""initial secret models

Revision ID: a1f3d8c2b740
Revises:
Create Date: 2026-06-08 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision: str = "a1f3d8c2b740"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Создаёт credentials + role_acls + dept_grants + enum'ы.

    Enum'ы создаются inline через `sa.Enum(..., name=...)` в первом
    CREATE TABLE — SQLAlchemy эмитит CREATE TYPE автоматически перед
    таблицей. Повторное использование того же `name` в других таблицах
    помечено `create_type=False`, чтобы не было DuplicateObject.
    """

    op.create_table(
        "credentials",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("service", sa.String(length=64), nullable=False),
        sa.Column(
            "scope",
            sa.Enum(
                "personal",
                "department",
                "cross_department",
                name="credential_scope",
            ),
            nullable=False,
        ),
        sa.Column("owner_user_id", sa.String(length=64), nullable=True),
        sa.Column("owner_dept_id", sa.String(length=64), nullable=True),
        sa.Column("login", sa.Text(), nullable=True),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "blocked", name="credential_status"),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("blocked_reason", sa.String(length=256), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "(scope <> 'personal') OR "
            "(owner_user_id IS NOT NULL AND owner_dept_id IS NULL)",
            name="ck_credentials_personal_owner",
        ),
        sa.CheckConstraint(
            "(scope NOT IN ('department', 'cross_department')) OR "
            "(owner_dept_id IS NOT NULL AND owner_user_id IS NULL)",
            name="ck_credentials_dept_owner",
        ),
        sa.CheckConstraint(
            "length(secret_encrypted) < 8192",
            name="ck_credentials_secret_len",
        ),
        sa.CheckConstraint(
            r"secret_encrypted ~ '^v\d+\$'",
            name="ck_credentials_secret_envelope",
        ),
    )
    op.create_index(
        "ix_credentials_owner_user_scope",
        "credentials",
        ["owner_user_id", "scope"],
        unique=False,
    )
    op.create_index(
        "ix_credentials_owner_dept_scope",
        "credentials",
        ["owner_dept_id", "scope"],
        unique=False,
    )
    op.create_index(
        "ix_credentials_status", "credentials", ["status"], unique=False
    )
    # Partial UNIQUE по active-строкам. COALESCE даёт одну колонку-«owner»
    # вне зависимости от scope — личные и dept-креды попадают в одно
    # пространство имён по (owner, service, name).
    op.create_index(
        "uq_credentials_owner_service_name_active",
        "credentials",
        [sa.text("coalesce(owner_user_id, owner_dept_id)"), "service", "name"],
        unique=True,
        postgresql_where=text("status = 'active'"),
    )

    op.create_table(
        "role_acls",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("cred_id", sa.String(length=64), nullable=False),
        sa.Column("dept_id", sa.String(length=64), nullable=False),
        sa.Column("role_name", sa.String(length=64), nullable=False),
        sa.Column("can_read", sa.Boolean(), nullable=False),
        sa.Column("can_write", sa.Boolean(), nullable=False),
        sa.Column("granted_by_user_id", sa.String(length=64), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["cred_id"], ["credentials.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "cred_id", "dept_id", "role_name", name="uq_role_acl_cred_dept_role"
        ),
    )
    op.create_index(
        op.f("ix_role_acls_cred_id"), "role_acls", ["cred_id"], unique=False
    )

    op.create_table(
        "dept_grants",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("cred_id", sa.String(length=64), nullable=False),
        sa.Column("recipient_dept_id", sa.String(length=64), nullable=False),
        sa.Column("granted_by_user_id", sa.String(length=64), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["cred_id"], ["credentials.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "cred_id",
            "recipient_dept_id",
            name="uq_dept_grant_cred_recipient",
        ),
    )
    op.create_index(
        op.f("ix_dept_grants_cred_id"), "dept_grants", ["cred_id"], unique=False
    )


def downgrade() -> None:
    """Сносит таблицы в обратном порядке + enum'ы."""
    op.drop_index(op.f("ix_dept_grants_cred_id"), table_name="dept_grants")
    op.drop_table("dept_grants")

    op.drop_index(op.f("ix_role_acls_cred_id"), table_name="role_acls")
    op.drop_table("role_acls")

    op.drop_index(
        "uq_credentials_owner_service_name_active", table_name="credentials"
    )
    op.drop_index("ix_credentials_status", table_name="credentials")
    op.drop_index("ix_credentials_owner_dept_scope", table_name="credentials")
    op.drop_index("ix_credentials_owner_user_scope", table_name="credentials")
    op.drop_table("credentials")

    sa.Enum(name="credential_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="credential_scope").drop(op.get_bind(), checkfirst=True)
