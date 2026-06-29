"""Модель `retired_key_versions` — durable-пометка выведенных версий ключа.

KeyStore (`FileKeyStore`) при отсутствии файла bootstrap'ится из env'а
(`SECRET_ENCRYPTION_KEY__v<N>`), а в прод-k8s файл лежит на emptyDir и
исчезает при рестарте пода. Без durable-пометки выведенная версия
«воскресла» бы из env при следующем bootstrap'е, и материал ключа,
который мы объявили уничтоженным, снова оказался бы в keystore.

Таблица фиксирует факт вывода версии. На старте сервиса
`key_rotation_service.reconcile_tombstones` сверяет её с keystore и
вычищает версии, которые воскресли из env. На retire (ручной и авто) сюда
кладётся строка.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class RetiredKeyVersion(Base):
    """Версия мастер-ключа, выведенная из обращения (материал удалён)."""

    __tablename__ = "retired_key_versions"

    version: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    retired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
