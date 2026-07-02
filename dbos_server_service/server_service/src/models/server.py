"""Модель Server — основная сущность сервиса."""

from datetime import datetime
from ipaddress import IPv4Address, IPv6Address

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.constants import BusyState, PowerState, ServerStatus
from src.db.base import Base


class Server(Base):
    """Карточка сервера + кэш состояний (power/busy) + железная мета."""

    __tablename__ = "servers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # hostname / ip_address / serial_number — globally unique across
    # departments, не per-dept. Документировано в `services/server.py`
    # (`_DUPLICATE_HINT`); ниже зеркалится в ORM как ориентир для читателя.
    hostname: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # `unique=True` уже создаёт UNIQUE-индекс — `index=True` здесь даёт
    # дополнительный обычный B-tree, но alembic-autogenerate комбинирует их
    # в один `ix_servers_ip_address (UNIQUE)`. Оставлено для совместимости
    # с initial-миграцией; на физической схеме лишних индексов нет.
    ip_address: Mapped[IPv4Address | IPv6Address] = mapped_column(
        INET, unique=True, nullable=False, index=True
    )
    mgmt_ip_address: Mapped[IPv4Address | IPv6Address | None] = mapped_column(INET, nullable=True)
    ssh_port: Mapped[int] = mapped_column(Integer, default=22, nullable=False)
    os_version_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("os_versions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    # Обновляется write-callback'ами worker'а: `services/server.py:873`
    # (admin-PATCH) и `services/internal_service.py:831` (inventory submit).
    os_last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Режим безопасности Astra с бокса (латиницей: Smolensk/Orel/Voronezh).
    # Per-server факт из inventory-callback'а — версия ОС в каталоге общая, а
    # режим у каждого сервера свой. UI склеивает "<os_version.name> <mode>".
    os_security_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    department_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(32), default=ServerStatus.UNKNOWN, nullable=False
    )
    power_state: Mapped[str] = mapped_column(
        String(32), default=PowerState.UNKNOWN, nullable=False
    )
    # Откуда и когда пришло последнее значение power_state. Заполняется
    # internal-callback'ом воркера по результату живой пробы (`power.status`):
    # source — bmc/ping/ssh, checked_at — момент приёма (UTC). До первой пробы
    # обе колонки NULL, power_state остаётся дефолтным unknown.
    power_state_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    power_state_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    busy_state: Mapped[str] = mapped_column(
        String(32), default=BusyState.FREE, nullable=False
    )
    # Soft-FK на auth_service.users.id / bot_accounts.id; формат
    # `usr_<hex>` либо `bot_<hex>` (FK через DB-границу не натянуть).
    # CHECK на БД — ck_servers_busy_user_id_format.
    busy_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    busy_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    busy_note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # UNIQUE автоматически создаёт b-tree, поэтому отдельный `index=True`
    # не пишем — лишний дубль в DDL. `ip_address` оставлен с `index=True`
    # как косметика читаемости (alembic объединил физически в один индекс).
    serial_number: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    asset_tag: Mapped[str | None] = mapped_column(String(128), nullable=True)
    location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    cpu_brand: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cpu_model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    cpu_cores: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cpu_threads: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cpu_frequency_ghz: Mapped[float | None] = mapped_column(Float, nullable=True)
    ram_total_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    network_interface_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decommissioned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Бутстрап управления (#14): после успешного prepare worker заводит
    # управляющего пользователя DBOS и кладёт ему публичный ключ. is_managed
    # фиксирует факт онбординга, management_user — имя заведённого аккаунта,
    # prepared_at — момент подтверждения от worker'а.
    is_managed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    management_user: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Детектнутая воркером при prepare редакция ОС / режим создания управляющей
    # учётки (`astra_orel`/`astra_smolensk`/`astra_voronezh`/`other_os`). Хранит
    # строку, а не FK на enum — значения фиксированы ManagementMode, лишняя
    # таблица-каталог не нужна.
    management_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    prepared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Управляющая SSH-пара и пароль пользователя dbos НА ЭТОМ сервере (#3).
    # Раньше управление шло по единому глобальному ключу из env воркера; теперь
    # каждый сервер несёт свою пару. Public — открытым текстом (кладётся в
    # authorized_keys при prepare и нужен для отпечатка в UI). Private и пароль —
    # envelope AES-256-GCM через secrets_service со своим AAD. Все nullable: до
    # prepare сервер кред не имеет, материал появляется на онбординге.
    mgmt_ssh_public_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    mgmt_ssh_private_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    mgmt_password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    mgmt_creds_rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Переходное окно ротации (зеркало previous_password_encrypted у
    # server_account): прежний материал держим, пока новый не подтверждён
    # callback'ом воркера, чтобы fetch отдавал рабочий на боксе ключ и не было
    # лок-аута. Зануляются после applied-callback'а.
    previous_mgmt_ssh_private_key_encrypted: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    previous_mgmt_password_encrypted: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    # True между записью свежего ciphertext'а (prepare/rotate dispatch) и
    # callback'ом «применено на боксе». Пока True, fetch отдаёт previous-материал
    # (рабочий на боксе), а не свежий — это держит анти-локаут-инвариант.
    mgmt_creds_pending_apply: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
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
    # CHECK на БД — ck_servers_created_by_format.
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        # Композитный индекс под типовой list — «все сервера моего отдела в статусе X».
        Index("ix_servers_department_status", "department_id", "status"),
        # Для быстрого «кто что занял» — busy-dashboard.
        Index("ix_servers_busy_state_user", "busy_state", "busy_user_id"),
    )

    os_version: Mapped["OsVersion | None"] = relationship(  # noqa: F821
        "OsVersion", back_populates="servers"
    )
    account_links: Mapped[list["ServerAccountServer"]] = relationship(  # noqa: F821
        "ServerAccountServer", back_populates="server", cascade="all, delete-orphan"
    )
    ipmi_controller: Mapped["IpmiController | None"] = relationship(  # noqa: F821
        "IpmiController",
        back_populates="server",
        cascade="all, delete-orphan",
        uselist=False,
    )
    disks: Mapped[list["ServerDisk"]] = relationship(  # noqa: F821
        "ServerDisk", back_populates="server", cascade="all, delete-orphan"
    )
