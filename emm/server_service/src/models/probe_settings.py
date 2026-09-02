"""Настройки интервалов проб статуса — платформенный singleton.

Одна строка на всю платформу (PK зафиксирован `SINGLETON_ID`). Хранит частоту
и вкл/выкл двух групп проб, которые снимает server_worker:

* reachability (ping + ssh) — как часто щупать доступность;
* power (ipmi / virsh domstate) — как часто щупать питание.

Настройки лежат в БД, а не в env воркера, чтобы одинаково работать и в docker,
и в k8s: воркер читает их через internal-эндпоинт, UI редактирует через
account_admin. Singleton-строки достаточно — конфиг глобальный, не привязан к
департаменту.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base

# Единственная допустимая строка таблицы. Все чтения/записи идут по этому PK.
SINGLETON_ID = "default"

# Дефолты и нижние границы интервалов (секунды). reachability чаще (ping/ssh
# дёшевы), power реже (ipmi/domstate дороже). Границы держат воркер от
# самозадушивания слишком частым опросом.
DEFAULT_REACHABILITY_INTERVAL_SECONDS = 60
DEFAULT_POWER_INTERVAL_SECONDS = 300
MIN_REACHABILITY_INTERVAL_SECONDS = 15
MIN_POWER_INTERVAL_SECONDS = 60


class ProbeSettings(Base):
    """Платформенный singleton-конфиг частоты проб статуса."""

    __tablename__ = "probe_settings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=SINGLETON_ID)
    reachability_probe_interval_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_REACHABILITY_INTERVAL_SECONDS,
        server_default=str(DEFAULT_REACHABILITY_INTERVAL_SECONDS),
    )
    power_probe_interval_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_POWER_INTERVAL_SECONDS,
        server_default=str(DEFAULT_POWER_INTERVAL_SECONDS),
    )
    reachability_probe_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    power_probe_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
