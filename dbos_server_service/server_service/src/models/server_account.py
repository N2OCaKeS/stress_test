"""Модель ServerAccount — OS-аккаунт с зашифрованным паролем.

Аккаунт привязывается к набору серверов через join-таблицу
`server_account_servers` (many-to-many). Пароль — общий для всех привязанных
серверов и хранится прямо на строке аккаунта (одна «личность» — один секрет).
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class ServerAccountServer(Base):
    """Связка аккаунт ↔ сервер.

    Логин аккаунта уникален в пределах сервера — нельзя завести двух
    root'ов на одной машине. Это держит инвариант, который раньше давал
    UNIQUE(server_id, login) на самой строке аккаунта; теперь он на join'е.
    """

    __tablename__ = "server_account_servers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("server_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    server_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("servers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Денормализованная копия login'а аккаунта — нужна для constraint
    # «один логин на сервер». Держится в sync с ServerAccount.login сервисным
    # слоем (login аккаунта неизменяем после создания).
    login: Mapped[str] = mapped_column(String(128), nullable=False)
    # Последняя успешная инвентаризация пользователей именно на этом сервере.
    # Хранится на связке, а не на аккаунте: один аккаунт может жить на N
    # серверах, и каждый сканируется независимо.
    last_inventory_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # True, пока пользователь реально присутствует на сервере. Инвентаризация
    # сбрасывает его в False, если аккаунт привязан, но на боксе уже нет —
    # это «дрейф», запись не удаляем молча.
    present_on_server: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("account_id", "server_id", name="uq_account_server"),
        UniqueConstraint("server_id", "login", name="uq_server_login"),
    )

    account: Mapped["ServerAccount"] = relationship(
        "ServerAccount", back_populates="server_links"
    )
    server: Mapped["Server"] = relationship(  # noqa: F821
        "Server", back_populates="account_links"
    )


class ServerAccount(Base):
    """OS-аккаунт. Пароль хранится в формате secrets_service token и общий
    на все привязанные серверы."""

    __tablename__ = "server_accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Department владельца аккаунта. Все привязанные серверы обязаны быть в
    # этом же department'е (enforce при линковке).
    department_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    login: Mapped[str] = mapped_column(String(128), nullable=False)
    # Происхождение аккаунта: `managed` — заведён через API (с паролем),
    # `discovered` — найден инвентаризацией на сервере (пароль API неизвестен,
    # password_encrypted = NULL до ручной ротации).
    source: Mapped[str] = mapped_column(String(16), default="managed", nullable=False)
    # Формат: `v<key>$<nonce>$<ciphertext>` (см. secrets_service.py).
    password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # SSH-ключ для входа под аккаунтом. Public — в открытом виде, кладётся в
    # `~/.ssh/authorized_keys` на боксе при provision'е. Private — зашифрован
    # тем же `secrets_service.encrypt()`, что и пароль, по своему AAD.
    # Оба поля NULL у managed-аккаунтов, заведённых до фичи, и у discovered —
    # на provision-вызове они заполняются автогенерацией Ed25519.
    ssh_public_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    ssh_private_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    has_sudo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    unix_groups: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    linked_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    shell: Mapped[str | None] = mapped_column(String(64), nullable=True)
    home_dir: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    server_links: Mapped[list["ServerAccountServer"]] = relationship(
        "ServerAccountServer",
        back_populates="account",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
