"""Модель VmSnapshot — снимок виртуальной машины на hub-сервере.

Снимки живут отдельной таблицей. Источник истины — гипервизор hub'а;
БД зеркалит через callback `POST /internal/vms/{id}/snapshots` (батч-синк по
имени). Два семейства:

* `<ver>_build` — системные golden-снимки (`is_system=True`): их создаёт
  сборочный флоу vm.create, несут дефолт-креды образа (`u:1`), в UI скрыты,
  руками не удаляются/не откатываются.
* пользовательские / `<ver>` — с mgmt-кредами на момент снятия (для режима
  `per_snapshot`, §6 дизайна).

Цепочка снимков — через self-FK `parent_snapshot_id`. При удалении ВМ снимки
сносятся каскадом (FK ondelete=CASCADE); parent обнуляется (SET NULL), чтобы
удаление промежуточного снимка не рушило потомков.

Креды-по-снимку хранятся зеркалом серверных управляющих кред: `mgmt_user` +
envelope AES-256-GCM `mgmt_password_encrypted` (+ опц. `mgmt_ssh_private_key_
encrypted`) со своим AAD, привязанным к строке снимка (см. secrets_service).
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.constants import VmSnapshotKind, VmSnapshotState, VmSnapshotType
from src.db.base import Base


class VmSnapshot(Base):
    """Снимок ВМ: имя, цепочка, тип/состояние, флаг текущего + креды-по-снимку."""

    __tablename__ = "vm_snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    vm_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("vms.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Плоское имя снимка (`virsh snapshot-create-as --name`); уникально в ВМ.
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Цепочка снимков. SET NULL при удалении родителя — потомки остаются.
    parent_snapshot_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("vm_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Способ снятия (virsh-механика): disk_only / full.
    snapshot_type: Mapped[str] = mapped_column(
        String(16), default=VmSnapshotType.DISK_ONLY, nullable=False
    )
    # Смысловая группа для UI: os_baseline (чистый снимок версии ОС) / user.
    kind: Mapped[str] = mapped_column(
        String(16), default=VmSnapshotKind.USER, nullable=False
    )
    # Версия ОС, зафиксированная снимком (напр. `1.8.1.6`); NULL, если неизвестна.
    os_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Режим Astra на момент снимка: orel / smolensk / NULL.
    mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Системный golden-снимок (`<ver>_build`): скрыт из выдачи, защищён от
    # ручного delete/revert.
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), default=VmSnapshotState.CREATING, nullable=False
    )
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # «Текущий» снимок ВМ — на него указывает активное состояние диска. Ровно
    # один снимок ВМ несёт is_current=True; revert переносит флаг.
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # ── mgmt-креды на момент снимка (режим per_snapshot) ─────────────────────
    # Аккаунт, чьи креды сохранены (обычно гостевой `u`). Приватный ключ и пароль —
    # envelope AES-256-GCM через secrets_service со своим AAD. Все nullable:
    # системные `_build` креды не хранят (дефолт образа `u:1`), да и до первого
    # revert/passwd креды могут быть неизвестны server_service.
    mgmt_user: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mgmt_password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    mgmt_ssh_private_key_encrypted: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Soft-FK на auth_service identity (`usr_<hex>` / `bot_<hex>`).
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("vm_id", "name", name="uq_vm_snapshot_name"),
        # Быстрый lookup текущего снимка ВМ.
        Index("ix_vm_snapshots_vm_current", "vm_id", "is_current"),
    )

    vm: Mapped["Vm"] = relationship("Vm")  # noqa: F821
