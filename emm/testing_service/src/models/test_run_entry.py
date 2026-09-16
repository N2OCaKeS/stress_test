"""Зафиксированный элемент состава кампании, независимо от числа попыток."""

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class TestRunEntry(Base):
    __tablename__ = "test_run_entries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    test_run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("test_runs.id", ondelete="CASCADE"), index=True
    )
    stand_id: Mapped[str] = mapped_column(String(64))
    test_id: Mapped[str] = mapped_column(String(64))
    test_code: Mapped[str] = mapped_column(String(64))
    test_name: Mapped[str] = mapped_column(String(256))
    kernel: Mapped[str | None] = mapped_column(String(64))
    # Режим безопасности теста (test_definitions.mode) на момент постановки
    # этого элемента кампании в очередь — кампания больше не несёт единого
    # режима на все свои тесты, каждый элемент помнит режим своего теста.
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    enqueue_error_code: Mapped[str | None] = mapped_column(String(64))
    enqueue_error: Mapped[str | None] = mapped_column(String(2048))
