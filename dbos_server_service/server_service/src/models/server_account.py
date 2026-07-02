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

    # PK с префиксом `acs_<hex>` — внутренний idempotency-toolkit для
    # join-row, не показывается в API. В отличие от `dispatch_outbox`
    # (raw UUID — там нет user-facing endpoint'а и читателей PK), здесь
    # префиксованная схема симметрична остальным `server_account.id`/`server.id`
    # для grep'а в логах и audit-trail.
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
    # password_encrypted = NULL до ручной ротации). CHECK на БД —
    # ck_server_accounts_source.
    source: Mapped[str] = mapped_column(String(16), default="managed", nullable=False)
    # Envelope формат AES-256-GCM: `v<key_ver>$<base64-nonce>$<base64-ct+tag>`
    # (см. secrets_service.py). Для пароля до 256 байт plaintext ~400 символов;
    # для приватного SSH-ключа (Ed25519 ~120 байт, RSA-4096 ~3 КБ) — до ~4 КБ.
    # На БД лежит CHECK length(...) < 8192 — двукратный запас от tooling-bug'а.
    password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Прежний пароль на время переходного периода ротации. Когда пароль меняют
    # через rotate_password, текущий ciphertext переезжает сюда (тем же envelope
    # и AAD, что password_encrypted — AAD привязан к id строки, не к колонке).
    # Это даёт оператору подключаться и старым, и новым паролем, пока новый не
    # раскатан на все привязанные серверы. Обнуляется, когда переходный период
    # закончен — первый успешный provision-callback снимает
    # credentials_pending_apply и заодно зануляет previous.
    previous_password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    previous_password_rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # SSH-ключ для входа под аккаунтом. Public — в открытом виде, кладётся в
    # `~/.ssh/authorized_keys` на боксе при provision'е. Private — зашифрован
    # тем же `secrets_service.encrypt()`, что и пароль, по своему AAD; на тот
    # же envelope распространяется length-cap (CHECK < 8192).
    # Оба поля NULL у managed-аккаунтов, заведённых до фичи, и у discovered —
    # на provision-вызове они заполняются автогенерацией Ed25519.
    ssh_public_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    ssh_private_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Прежний приватный ключ на время переходного периода ротации ssh-ключа.
    # При rotate_ssh_key текущий ciphertext переезжает сюда (тем же envelope и
    # AAD, что ssh_private_key_encrypted — AAD привязан к id строки, не к
    # колонке), даёт оператору скачать старый ключ, пока новый не раскатан на
    # серверы. По образцу previous_password_encrypted.
    previous_ssh_private_key_encrypted: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    previous_ssh_key_rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # True между dispatch'ем ротации/provision'а и callback'ом worker'а: в БД
    # уже свежий ciphertext, на боксе ещё старый материал. Retry до callback'а
    # форсит `force_replace=True` — иначе race-сценарий «dispatch ok, callback
    # потерялся» оставил бы drift между server-БД и реальным сервером.
    credentials_pending_apply: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    has_sudo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    unix_groups: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    # Soft-FK на auth_service.users.id (`usr_<hex>`) — кто из людей живёт под
    # этой OS-учёткой. Боты сюда не привязываются. CHECK на БД —
    # ck_server_accounts_linked_user_id_format (допускает и usr_, и bot_ для
    # forward-совместимости, но в практике пишутся только usr_).
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
    # Soft-FK на auth_service identity (`usr_<hex>` / `bot_<hex>`).
    # CHECK на БД — ck_server_accounts_created_by_format.
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    server_links: Mapped[list["ServerAccountServer"]] = relationship(
        "ServerAccountServer",
        back_populates="account",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
