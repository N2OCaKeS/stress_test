"""Кэш ответа внешнего `changelog.service` по build_version (§2.7, §7 плана миграции).

Один build_version (RC) не меняет свой changelog задним числом — сборка уже
выпущена, набор изменившихся компонентов для неё зафиксирован навсегда.
Поэтому кэш не имеет обычного короткого TTL: `changelog_service.py` считает
запись валидной практически бессрочно (см. `changelog_cache_ttl_seconds` в
`core/config.py` — дефолт исчисляется месяцами, а не минутами) и обращается к
внешнему сервису заново только если строки для этого `build_version` ещё нет
или она устарела дольше настроенного TTL.

`response_json` — сырой JSON-ответ `changelog.service` как есть (не
преобразованный список компонентов), чтобы формат извлечения компонентов
(`changelog_service._extract_component`) можно было менять без миграции кэша.
"""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class ChangelogCache(Base):
    """Одна строка на `build_version` (RC)."""

    __tablename__ = "changelog_cache"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    build_version: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    response_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
