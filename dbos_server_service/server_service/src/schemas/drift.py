"""Схемы ответа для `GET /servers/{id}/drift`.

Drift-сводка собирается агрегацией событий `server_account.drift_detected`
из loging_service — фактический recompute по БД не делается (модель shared:
истина в server_service, факт-состояние бокса фиксирует только inventory).
Endpoint удобен как «что случилось с этим сервером за последние N часов».
"""

from datetime import datetime

from pydantic import BaseModel, Field


class DriftEventItem(BaseModel):
    """Один drift-сигнал, распакованный из event'а loging."""

    login: str = Field(description="Login, по которому зафиксирован drift.")
    drift_type: str = Field(
        description="unknown_login / attributes / missing_on_box (как в emit'е).",
    )
    fields: list[str] | None = Field(
        default=None,
        description="Для drift_type='attributes' — разошедшиеся поля.",
    )
    detected_at: datetime = Field(
        description="Timestamp event'а из loging (timestamp колонки)."
    )


class ServerDriftResponse(BaseModel):
    """Ответ `GET /servers/{id}/drift?since=<iso8601>`."""

    server_id: str = Field(description="ID сервера.")
    since: datetime = Field(description="Начало окна агрегации (UTC ISO-8601).")
    drifts: list[DriftEventItem] = Field(
        default_factory=list,
        description=(
            "Список drift-сигналов в окне. Отсортирован по `detected_at` "
            "(свежие первыми) — зеркалит сортировку loging events."
        ),
    )
    truncated: bool = Field(
        default=False,
        description=(
            "True, если loging вернул limit'ом ровно cap страницы (1000) — "
            "возможны более старые события за окном, сузь `since`."
        ),
    )
