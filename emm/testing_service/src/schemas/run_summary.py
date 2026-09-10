"""Pydantic-схемы для `GET /test-runs/{id}/summary-comment` (§2.7, §9.2 плана миграции)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RunSummaryCommentResponse(BaseModel):
    """Статус end-of-run комментария Confluence кампании. Пустые поля — строки ещё нет."""

    model_config = ConfigDict(from_attributes=True)

    id: str | None = Field(default=None, description="None, если попытка публикации ещё не производилась.")
    test_run_id: str
    status: str | None = Field(
        default=None,
        description="posted/skipped_no_blog/skipped_no_stp_page/failed, либо None — ещё не пытались.",
    )
    confluence_blog_id: str | None = Field(default=None, description="Id найденного blog-поста релиза.")
    confluence_comment_id: str | None = Field(default=None, description="Id своего комментария на посте.")
    stp_page_id: str | None = Field(default=None, description="Id найденной STP-страницы статистики.")
    posted_at: datetime | None = Field(default=None)
    updated_at: datetime | None = Field(default=None)
