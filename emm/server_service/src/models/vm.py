"""Модель Vm — виртуальная машина на hub-сервере.

Основа VM-домена. ВМ живёт на подготовленном как VMS-hub сервере
(`hub_server_id`), исполнение — по SSH через worker'а. Два независимых поля
занятости:

* `status` — бронь под тест: `free` / `run test` / `debug test` / `<login>`
  забронировавшего. Гейтит управляющие операции (§2 дизайна).
* `busy_state` — lifecycle-lock на время долгой операции (creating/deleting/
  updating/powering); NULL, когда операция не идёт. Снимает callback воркера.

Диски/снимки/пулы IP — отдельные таблицы; здесь только карточка
ВМ с денормализованным «текущим» состоянием (power/ip/ресурсы).
"""

from datetime import datetime
from ipaddress import IPv4Address, IPv6Address

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.constants import (
    VM_GRAPHICS_DEFAULT,
    VmCredStrategy,
    VmNetworkMode,
    VmPowerState,
    VM_STATUS_FREE,
)
from src.db.base import Base


class Vm(Base):
    """Карточка ВМ + кэш состояния (power/ip) + бронь/lifecycle-lock."""

    __tablename__ = "vms"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Голое имя ВМ (без префикса stand<N>_). Уникальность имени — в пределах
    # hub'а; на БД держим partial-unique (hub_server_id, name).
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Hostname гостя (`hostnamectl set-hostname`). Опционален: пусто → берётся
    # имя ВМ (`name`). Отдельно от `name`, т.к. имя ВМ уникально в пределах
    # hub'а, а hostname внутри гостя может отличаться/повторяться.
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Опциональный номер стенда — глобально уникален в паре servers+vm.
    number: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    hub_server_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("servers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    department_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Версия ОС ВМ — свободная строка (`1.7.5.9`/`1.8.1.6`), не FK на каталог:
    # universal-бокс несёт обе ОС на одном диске, «текущая» переключается revert'ом
    # снимка. Каталожный FK тут смысла не имеет.
    os_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Имя бокса-образа (`vm_station`, `1.8.1.o`, `xfs.*` …) из FTP-каталога.
    box: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Гостевые факты инвентаризации (vm.inventory_sync). Версия ядра гостя
    # (`uname -r`) и момент последнего успешного приёма фактов от воркера.
    # Оба nullable: до первой инвентаризации пусто. os_version выше —
    # box-authoritative, обновляется тем же callback'ом.
    kernel: Mapped[str | None] = mapped_column(String(256), nullable=True)
    os_last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    network_mode: Mapped[str] = mapped_column(
        String(16), default=VmNetworkMode.BRIDGE, nullable=False
    )
    ip_address: Mapped[IPv4Address | IPv6Address | None] = mapped_column(
        INET, nullable=True
    )
    # Бронь под тест (см. module docstring). Дефолт — free.
    status: Mapped[str] = mapped_column(
        String(64), default=VM_STATUS_FREE, nullable=False
    )
    power_state: Mapped[str] = mapped_column(
        String(16), default=VmPowerState.UNKNOWN, nullable=False
    )
    power_state_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cpu: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ram_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    autostart: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Тип графической консоли ВМ (vnc/spice) — выбирается при создании и уезжает
    # воркеру (`--graphics`). Дефолт vnc. graphics_port — фактический порт дисплея
    # на hub'е, который воркер сообщает в state-callback'е (NULL до первой пробы;
    # прокси иначе резолвит его через virsh).
    graphics: Mapped[str] = mapped_column(
        String(8), default=VM_GRAPHICS_DEFAULT, nullable=False
    )
    graphics_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cred_strategy: Mapped[str] = mapped_column(
        String(16), default=VmCredStrategy.PER_SNAPSHOT, nullable=False
    )
    # Lifecycle-lock: NULL — свободна, иначе creating/deleting/updating/powering.
    # busy_since — момент постановки лока (для TTL-recovery застрявших ВМ).
    busy_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    busy_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Сигналы доступности гостя (как у серверов, опционально). Пишет callback
    # воркера по результату пробы; NULL до первой пробы.
    ping_reachable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ping_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ssh_reachable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ssh_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Последняя ошибка от воркера (create/power/delete) — для UI-диагностики.
    last_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # ── per-VM управляющие креды (зеркало серверных, §9 дизайна) ──────────────
    # После vm.prepare гость несёт свою управляющую SSH-пару + пароль (базовая
    # учётка образа `u:1` снесена). is_managed фиксирует факт онбординга,
    # mgmt_user — имя управляющего пользователя. Public-ключ — открытым текстом
    # (кладётся в authorized_keys, показывается отпечатком в UI); private и
    # пароль — envelope AES-256-GCM через secrets_service со своим AAD. Все
    # nullable: до prepare ВМ управляющих кред не имеет.
    is_managed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mgmt_user: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mgmt_ssh_public_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    mgmt_ssh_private_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    mgmt_password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # True между записью свежего ciphertext'а (prepare/rotate dispatch) и
    # callback'ом «применено в госте». В отличие от серверных кред, previous-
    # зеркал у ВМ нет: тестовая ВМ при сбое ротации перекатывается заново или
    # откатывается снимком, поэтому анти-локаут-окно не держим.
    mgmt_creds_pending_apply: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    mgmt_creds_rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        # Имя ВМ уникально в пределах одного hub'а.
        Index("uq_vms_hub_name", "hub_server_id", "name", unique=True),
        # Типовой list — «все ВМ отдела в статусе X».
        Index("ix_vms_department_status", "department_id", "status"),
    )

    hub_server: Mapped["Server"] = relationship("Server")  # noqa: F821
