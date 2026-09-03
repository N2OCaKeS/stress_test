"""Bootstrap-пароль конкретной OS-версии — нужен для авто-`server.prepare`
после восстановления снимка ACS.

Восстановление снимка (`clonezilla-snap/restore-backup`) полностью
переписывает диск сервера: управляющий SSH-ключ DBOS не переживает reimage,
поэтому автоматический prepare, который идёт сразу после успешного restore,
не может резолвить bootstrap-креды ни из привязанного `server_account` (диск
переписан, аккаунта больше нет), ни из ручного ввода (никто не вызывает
`server.prepare` руками — restore идёт по расписанию/кнопкой без интерактивного
шага). Единственный источник — заранее заведённый владельцем пароль
дефолтного пользователя того образа, которым сервер восстанавливается,
привязанный к конкретной версии каталога ОС.

1:1 с `os_versions`: одна OS-версия — одна пара `{ssh_username, password}`.
Пароль хранится тем же AES-256-GCM конвертом, что и остальные секреты
server_service (`services/secrets_service.py`), под собственным AAD
(`aad_for_os_version_bootstrap_password`), привязанным к `os_version_id`.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class OsVersionBootstrapPassword(Base):
    """Bootstrap-пароль версии ОС для авто-prepare после restore снимка ACS."""

    __tablename__ = "os_version_bootstrap_passwords"
    __table_args__ = (
        UniqueConstraint(
            "os_version_id", name="uq_os_version_bootstrap_password_os_version"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    os_version_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("os_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ssh_username: Mapped[str] = mapped_column(String(128), nullable=False)
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
