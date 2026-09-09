"""Лог одного прогона теста — метаданные (§2.6, §8 плана миграции).

Сам текст лога живёт отдельно (`test_log_blobs`, 1:1) — не колонкой здесь,
чтобы обычный SELECT списка/карточки не таскал потенциально длинный TEXT.
`test_log_segments` — навигация поверх этого текста (офсеты + чекпоинты/
команды), тоже отдельная таблица (§8.2).

`queue_item_id` — ON DELETE SET NULL: лог обязан пережить возможное будущее
удаление `queue_item`а (сейчас такой операции в сервисе нет), поэтому связь
не блокирует (`RESTRICT`, как у ссылок из `queue_items` внутрь себя) и не
каскадирует удаление лога вместе с queue_item'ом. `stand_id`/`test_id`/
`os_version_major`/`rc`/`kernel` снэпшотятся на этой же строке отдельно и
остаются осмысленными сами по себе, даже когда `queue_item_id` станет NULL.

`stand_id`/`test_id` — без FK, те же снэпшот-метки контекста, что и
`os_version_major`/`rc`/`kernel` (взяты из `launch_context` соответствующего
`queue_item`а в момент создания лога, не резолвятся откуда-то ещё) — стенд
или тест не обязаны существовать вечно, чтобы лог оставался читаемым.

`internal_path` — логический идентификатор в духе будущего объектного
хранилища (`logs/<department_id>/<os_version_major>/<rc>/<stand_id>/
<queue_item_id>.log`), не реальный путь на диске пода: у `testing_service`
нет персистентного тома (2 реплики за одним Service, только `emptyDir`),
поэтому сам текст лежит в Postgres (`test_log_blobs`), общем на все реплики.

`protected` — вычисляется политикой ротации (`services/log_rotation.py`):
`true` у логов, чей `rc` входит в 2 самых свежих значения внутри
`os_version_major`. `expires_at` зарезервировано под будущий явный TTL, не
используется текущей ротацией (там считается `created_at` +
`settings.log_retention_days`).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class TestLog(Base):
    """Один лог прогона теста — метаданные, без самого текста."""

    __tablename__ = "test_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    queue_item_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("queue_items.id", ondelete="SET NULL"),
        nullable=True, unique=True, index=True,
    )
    stand_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    test_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    os_version_major: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rc: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kernel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    internal_path: Mapped[str] = mapped_column(String(512), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    protected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
