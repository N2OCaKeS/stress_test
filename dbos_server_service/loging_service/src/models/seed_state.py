"""`SeedState` — маркер «дефолтные правила засеяны».

Одна row на каждый именованный seed-набор (`key`). Дефолтные severity-правила
сидируются один раз: при первом старте сервиса с пустым маркером
`rule_service.seed_default_rules` создаёт все дефолтные правила и пишет сюда
row с `key="default_severity_rules"`. Повторный старт видит маркер и ничего
не делает — удалённые админом дефолты НЕ воскресают.

Маркер живёт отдельной таблицей, а не флагом на `audit_rules`, потому что
«дефолты уже сеялись» — факт уровня всего набора, а не отдельной row'и:
после удаления всех дефолтов в `audit_rules` не осталось бы ни одной строки,
по которой можно отличить «никогда не сеяли» от «посеяли и всё удалили».
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class SeedState(Base):
    __tablename__ = "seed_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    seeded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
