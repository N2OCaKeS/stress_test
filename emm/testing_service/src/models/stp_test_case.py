"""Каталог СТП — зеркало тест-кейсов Zephyr Scale (§2.5 плана миграции).

`code` — тот же человекочитаемый код, что и у `test_definitions.code` (легаси
топик, например `"postgresql"`) — join-ключ между двумя каталогами. Не FK:
`test_definitions` и `stp_test_cases` — независимые сущности (per §7 плана
миграции, "stp_test_case не то же самое, что test_definition"), совпадение
кода — это соглашение, не ссылка.

`zephyr_id` nullable — тест-кейс заводится здесь раньше, чем у него появляется
ключ в самом Zephyr (`BT-Txxxx`), заполняется оператором вручную после того,
как кейс создан в Zephyr UI (testing_service Zephyr test-case не создаёт).
"""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class StpTestCase(Base):
    """Один тест-кейс СТП — карточка, привязанная к Zephyr Scale test-case."""

    __tablename__ = "stp_test_cases"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    zephyr_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Soft-ref на auth_service departments — тот же приём, что у test_definitions.
    department_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Soft-FK на auth_service identity (`usr_<hex>`/`bot_<hex>`).
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
