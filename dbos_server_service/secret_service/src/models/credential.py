"""Модель Credential — учётка/токен для внешнего сервиса.

Хранит пару (login, secret_encrypted) для конкретного external service.
`scope` определяет владельца: personal — user, department/cross_department — dep.

Шифрование secret_encrypted — AES-256-GCM с envelope-форматом `v<ver>$<nonce>$<ct>`
(см. README §«Шифрование»). CHECK на формат и длину живут на БД, чтобы криво
вставленную строку нельзя было получить даже в обход сервисного слоя.

`status='blocked'` ставится автоматикой lifecycle (delete_user / delete_dept /
revoke_service_access). Partial UNIQUE по active-строкам позволяет hard-delete
и пересоздать креду с тем же `(owner, service, name)` без коллизии с
заблокированной (см. README §«Lifecycle»).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


# Имена БД-enum'ов держим короткие и без префикса сервиса — таблицы и так
# живут в своей БД. Migration ссылается ровно на эти `name=...`.
CREDENTIAL_SCOPE_VALUES = ("personal", "department", "cross_department")
CREDENTIAL_STATUS_VALUES = ("active", "blocked")


class Credential(Base):
    """Учётка/токен для внешнего сервиса."""

    __tablename__ = "credentials"

    # id формата `cred_<32 hex>`. Длина 64 — общий префиксованный лимит сервисов.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    service: Mapped[str] = mapped_column(String(64), nullable=False)

    scope: Mapped[str] = mapped_column(
        Enum(*CREDENTIAL_SCOPE_VALUES, name="credential_scope"),
        nullable=False,
    )

    # Soft-FK на auth.users.id (`usr_<hex>`) / auth.departments.id (`dep_<hex>`).
    # Реального FK нет — auth_service живёт в своей БД.
    owner_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner_dept_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    login: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(
        Enum(*CREDENTIAL_STATUS_VALUES, name="credential_status"),
        nullable=False,
        server_default=text("'active'"),
    )

    # Кто завёл креду. Immutable на сервисном слое; формат `usr_…`/`bot_…`.
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    blocked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    blocked_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)

    __table_args__ = (
        # personal ⇒ только user-owner.
        CheckConstraint(
            "(scope <> 'personal') OR "
            "(owner_user_id IS NOT NULL AND owner_dept_id IS NULL)",
            name="ck_credentials_personal_owner",
        ),
        # department/cross_department ⇒ только dept-owner.
        CheckConstraint(
            "(scope NOT IN ('department', 'cross_department')) OR "
            "(owner_dept_id IS NOT NULL AND owner_user_id IS NULL)",
            name="ck_credentials_dept_owner",
        ),
        # Двукратный запас от tooling-bug'а — см. server_service.server_account.
        CheckConstraint(
            "length(secret_encrypted) < 8192",
            name="ck_credentials_secret_len",
        ),
        # Envelope-префикс `v<ver>$...` — отбивает plaintext в обход
        # secrets-сервиса.
        CheckConstraint(
            r"secret_encrypted ~ '^v\d+\$'",
            name="ck_credentials_secret_envelope",
        ),
        Index("ix_credentials_owner_user_scope", "owner_user_id", "scope"),
        Index("ix_credentials_owner_dept_scope", "owner_dept_id", "scope"),
        Index("ix_credentials_status", "status"),
        # Partial UNIQUE: имя кред уникально на пару (owner, service) только
        # среди active. Заблокированные не блокируют новый insert. Migration
        # создаёт через postgresql_where (см. initial migration).
        Index(
            "uq_credentials_owner_service_name_active",
            func.coalesce(text("owner_user_id"), text("owner_dept_id")),
            "service",
            "name",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )
