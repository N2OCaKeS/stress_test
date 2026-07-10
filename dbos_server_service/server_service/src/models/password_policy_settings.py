"""Настраиваемая парольная политика server-аккаунтов — платформенный singleton.

Одна строка на всю платформу (PK зафиксирован `SINGLETON_ID`). Хранит базовую
политику для ручного ввода пароля на create/rotate server-аккаунтов и
IPMI-credentials: минимальную длину и обязательность буквы/цифры. Редактирует
её платформенный `account_admin` через `/admin/password-policy`; сервис читает
строку в процессный кэш `core/password_policy` на старте.

Настройка лежит в БД, а не в коде, чтобы менять требования без передеплоя.
Singleton-строки достаточно — политика глобальная, не привязана к департаменту.
Усиленная политика (bootstrap на prepare) настраиваемой не является и живёт в
коде.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.password_policy import MIN_PASSWORD_LENGTH
from src.db.base import Base

# Единственная допустимая строка таблицы. Все чтения/записи идут по этому PK.
SINGLETON_ID = "default"


class PasswordPolicySettings(Base):
    """Платформенный singleton-конфиг базовой парольной политики."""

    __tablename__ = "password_policy_settings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=SINGLETON_ID)
    min_length: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=MIN_PASSWORD_LENGTH,
        server_default=str(MIN_PASSWORD_LENGTH),
    )
    require_letter: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    require_digit: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    # Actor-id платформенного админа, применившего последнее изменение. NULL до
    # первой правки (строка засеяна миграцией дефолтами).
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
