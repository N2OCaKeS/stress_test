"""Справочник семейств статистики для пер-категорийного пересчёта (D18).

Раньше жил константой `statistics_client._CATEGORY_SPECS`; теперь это данные:
добавить семейство (например, `Docker`/`Network`, которые внешний сервис умеет,
но кнопки в легаси не имели) можно из настроек статистики без правки кода.

Одна строка — один POST во внешний сервис статистики (`statistics/main_api.py`):
`path` — маршрут (`/base-statistics`, `/parsec-statistics`, ...), остальные поля
— тело запроса (`title_statistics`, `set_of_test_types`, необязательные
`comparison_list`/`comparison_kernel_list`). Сид — восемь семейств легаси
(миграция `tp17_statistics_categories`).

Справочник платформенный (как и `statistics_settings`): сервис статистики один.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class StatisticsCategory(Base):
    """Одно семейство тестов, которое можно пересчитать отдельно."""

    __tablename__ = "statistics_categories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Ключ, который передаётся в `POST /statistics/recalculate` и пишется в
    # `statistics_recalc_status.category` — поэтому неизменяем после создания.
    key: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    # Маршрут внешнего сервиса статистики, относительно `statistics_settings.base_url`.
    path: Mapped[str] = mapped_column(String(128), nullable=False)
    # Внешний сервис выбирает парсер по этому полю (`statistics/main_api.py:46-60`),
    # поэтому три семейства на общем `/base-statistics` различаются только им.
    title_statistics: Mapped[str] = mapped_column(String(128), nullable=False)
    set_of_test_types: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]",
    )
    # NULL — ключ не кладётся в тело запроса вовсе (у внешнего сервиса он
    # `Optional[List] = None`), как легаси не клало то, чего нет в `statistics_conf`.
    comparison_list: Mapped[list | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    comparison_kernel_list: Mapped[list | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True,
    )
    # Выключенное семейство не показывается в модалке и не принимается ручным
    # триггером, но остаётся в справочнике (не нужно удалять, чтобы спрятать).
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true",
    )
    # Порядок в модалке и порядок пересчёта при выборе нескольких семейств.
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )
    # NULL — строка из сида миграции.
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
