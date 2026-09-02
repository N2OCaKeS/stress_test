"""Модель VmImage — каталог боксов-образов ВМ (зеркало FTP `test-box-config.json`).

Каталог наполняется синком с анонимного FTP (`libvirt_box`-секция): имя бокса
→ URL `.tar.gz`. При `vm.create` сервис резолвит `box` карточки ВМ в `url` этой
таблицы и кладёт `box_url` в payload VM_CREATE — без него воркер не знает,
откуда скачивать образ.

Образ либо глобальный (`hub_server_id IS NULL` — доступен всем hub'ам), либо
привязан к конкретному hub'у (`hub_server_id`). `os_versions` заполняется для
universal-боксов (`vm_station` несёт несколько ОС на одном диске).
"""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from src.core.constants import VmImageKind
from src.db.base import Base


class VmImage(Base):
    """Бокс-образ ВМ: имя, URL артефакта, тип (universal/single), привязка к hub'у."""

    __tablename__ = "vm_images"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(16), default=VmImageKind.SINGLE, nullable=False
    )
    # NULL — глобальный образ (все hub'ы); иначе привязка к конкретному hub'у.
    hub_server_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("servers.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # Список ОС внутри universal-бокса (для single — пусто).
    os_versions: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{}"
    )
    # Минимальный размер системного диска (ГБ) для этого бокса — занятое место
    # разметки образа. UI предупреждает заранее, если запрошенный диск меньше.
    # NULL — данных о минимуме нет (каталог не несёт эту метрику).
    min_disk_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        # Один hub-специфичный образ на пару (name, hub). NULL-hub'ы Postgres
        # считает различными, поэтому глобальную уникальность имени держим
        # отдельным partial-index'ом ниже.
        UniqueConstraint("name", "hub_server_id", name="uq_vm_image_name_hub"),
        Index(
            "uq_vm_image_global_name",
            "name",
            unique=True,
            postgresql_where="hub_server_id IS NULL",
        ),
    )
