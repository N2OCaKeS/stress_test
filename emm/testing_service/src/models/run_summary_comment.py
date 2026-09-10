"""End-of-run комментарий в Confluence-блоге (§2.7, §9.2 плана миграции).

Перенос легаси-механизма `SendCommentToConfluence`
(`allta_app/libs/libconfluence.py`, вызывается из `allta_back.py`) —
идемпотентный комментарий на уже существующем blog-посте, найденном по
title-шаблону версии RC, со ссылкой на уже существующую STP-страницу
статистики. Ни blog-пост, ни STP-страница здесь не заводятся — оба остаются
внешними предусловиями, как и в легаси.

В отличие от легаси (добавить один раз и больше не трогать), здесь
`confluence_comment_id` хранится для UPDATE при повторном прогоне того же
RC, а `body_snapshot` — снимок последнего отправленного тела, чтобы не
дёргать Confluence API, если текст не изменился (см. `services/run_summary.py`).

Одна строка на `test_run_id` — кампания получает не больше одного
комментария за весь свой жизненный цикл.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class RunSummaryComment(Base):
    """Состояние end-of-run комментария Confluence для одной кампании."""

    __tablename__ = "run_summary_comments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    test_run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("test_runs.id", ondelete="CASCADE"),
        unique=True, nullable=False, index=True,
    )
    # Id найденного по title-шаблону blog-поста релиза — не создаётся нами.
    confluence_blog_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Id своего комментария на этом посте — нужен для UPDATE, не только для
    # проверки "есть/нет", как в легаси.
    confluence_comment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Id найденной STP-страницы статистики, на которую ссылается комментарий
    # — тоже не создаётся нами.
    stp_page_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    # Последний отправленный текст комментария — basis для diff перед update.
    body_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
