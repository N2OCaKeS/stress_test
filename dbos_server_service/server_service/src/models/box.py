"""Модель Box — заготовка-образ для создания ВМ (пер-департамент каталог).

Бокс описывает готовый диск-образ, из которого разворачивается ВМ: формат
артефакта, URL для скачивания, предустановленный в образе пользователь и то,
сколько ОС и снимков лежит на диске изначально. Скачивание/импорт по URL —
отдельный поток; здесь только каталожная запись.

Пароль предустановленного пользователя хранится зашифрованным в формате
secrets_service token (AES-256-GCM с версией ключа в wire-префиксе), как у
`server_account.password_encrypted`. В ответах наружу отдаётся только держателю
`view_password`.
"""

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class Box(Base):
    """Каталожная запись бокса-заготовки отдела."""

    __tablename__ = "boxes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Отдел-владелец. Имя бокса уникально в пределах отдела (uq_boxes_dept_name).
    department_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Формат артефакта: tar / qcow / qcow2 / raw / … — свободная строка, набор
    # открытый (валидацию на непустоту делает схема).
    format: Mapped[str] = mapped_column(String(32), nullable=False)
    # Источник скачивания (https/ftp/smb/http/…). Только метаданные — сам
    # download/import живёт в отдельной задаче. Опционален.
    download_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Предустановленный в образе пользователь. Логин — открытым текстом, пароль —
    # envelope AES-256-GCM (secrets_service) со своим AAD, как у server_account.
    # Оба nullable: у бокса без встроенной учётки их нет.
    base_user_login: Mapped[str | None] = mapped_column(String(128), nullable=True)
    base_user_password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Что лежит на диске образа изначально: список версий ОС и имён снимков.
    os_versions: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    initial_snapshots: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
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
        Index("ix_boxes_department_id", "department_id"),
        Index("uq_boxes_dept_name", "department_id", "name", unique=True),
    )
