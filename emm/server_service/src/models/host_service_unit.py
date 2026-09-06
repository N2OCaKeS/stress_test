"""ORM-модель `HostServiceUnit` — per-department список systemd-юнитов.

Нет платформенного дефолтного списка (раньше был hardcoded `ALLTA_HOST_UNITS`
на 12 юнитов) — каждый отдел сам решает, что у него крутится на хосте, и
добавляет юниты сюда через `/settings/host-services/units`. `unit_name` —
базовое имя юнита без `.service` (нормализация/валидация формата — в
service-слое, `services/host_services_settings.py`); должно совпадать с тем,
что администратор отдела прописал в sudoers на хосте через `gen_sudoers.sh`
(см. `scripts/host-control/README.md`) — иначе control для этого юнита будет
отклонён guard-скриптом/sudo на хосте, даже если он есть в этой таблице.
"""

from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class HostServiceUnit(Base):
    """Один systemd-юнит в списке отдела для host-service control."""

    __tablename__ = "host_service_units"
    __table_args__ = (
        UniqueConstraint("department_id", "unit_name", name="uq_host_service_units_department_unit"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    unit_name: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Soft-FK на auth_service identity (`usr_<hex>` / `bot_<hex>`), как в
    # `AcsDepartmentAccess.created_by`.
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
