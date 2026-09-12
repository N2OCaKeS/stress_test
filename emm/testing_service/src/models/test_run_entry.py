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
    enqueue_error_code: Mapped[str | None] = mapped_column(String(64))
    enqueue_error: Mapped[str | None] = mapped_column(String(2048))
