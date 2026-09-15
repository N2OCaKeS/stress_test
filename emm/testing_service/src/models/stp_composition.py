"""Текущий активный состав СТП для пары `(department_id, os_version_id)` (§D4/D5 плана миграции).

Одна строка на пару — `UNIQUE(department_id, os_version_id)`. `scope`
(`StpCompositionScope`) — явный выбор администратора/пользователя, задаётся
кнопками «Полный набор»/«По changelog» (§D5), никогда не выводится из вида
строки версии (см. `services/stp.py`, ранее — `_is_full_scope` угадывал по
`rc.endswith(".1")`, это запрещено §D4).

`revision` растёт только когда `scope` РЕАЛЬНО меняется (changelog↔full) —
повторный вызов с тем же `scope` состав не трогает и ревизию не увеличивает,
даже если попутно донаводит недостающие прогоны/ячейки для новых стендов.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.constants import StpCompositionScope
from src.db.base import Base


class StpComposition(Base):
    """Активный `scope`+`revision` состава СТП одного отдела на одну РЦ."""

    __tablename__ = "stp_compositions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    os_version_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Soft-ref на auth_service identity, как stp_cells.updated_by — не FK.
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
        UniqueConstraint("department_id", "os_version_id", name="uq_stp_compositions_dept_os_version"),
        CheckConstraint(
            f"scope IN ('{StpCompositionScope.CHANGELOG}', '{StpCompositionScope.FULL}')",
            name="ck_stp_compositions_scope",
        ),
    )
