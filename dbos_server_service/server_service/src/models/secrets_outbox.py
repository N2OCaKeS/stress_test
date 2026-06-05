"""ORM-модель для outbox-таблицы постепенной ре-шифрации секретов.

Контекст. Прежний пайплайн `secrets.reencrypt_lazy` ходил sync-вызовом
`POST /internal/secrets/reencrypt_batch` и держал `AsyncSessionLocal()`
открытым на всё время decrypt-N → encrypt-N → UPDATE-N. При активной
ротации мастер-ключа это могло вычистить весь pool сервиса.

Outbox-модель разбивает работу:

* server-service одноразово сидит outbox-row'ы при смене активной версии
  ключа (см. :func:`secrets_migration_service.seed_outbox`);
* worker периодик клиентим строки маленькими батчами через
  `FOR UPDATE SKIP LOCKED` (короткая транзакция);
* финализация каждой строки — отдельная короткая транзакция.

В результате pool не блокируется длинной транзакцией.
"""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class ReencryptOutboxEntry(Base):
    """Одна строка outbox'а — задача «перешифровать секрет owner-row'а».

    Жизненный цикл:

    * ``pending`` — посеяно, ожидает claim'а;
    * ``processing`` — claim'нуто worker'ом, finalize в полёте;
    * ``done`` — новый ciphertext записан в исходную таблицу,
      строка ждёт cleanup'а;
    * ``failed`` — последняя попытка завершилась ошибкой; оператор
      разбирается по `last_error` и при желании перезаписывает `status`
      обратно в `pending` для повторной попытки.

    Idempotency: уникальный индекс `(entity_type, entity_id)` где
    `status IN ('pending','processing')` — повторный seed не плодит
    дубликаты, пока предыдущая запись не закрыта.
    """

    __tablename__ = "secrets_reencrypt_outbox"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # Wire-формат `v<N>$...` — то, что лежало в owner-row на момент
    # seed'а. Источник истины для decrypt'а: даже если кто-то параллельно
    # ротирует пароль на этой строке, outbox получит фиктивную ошибку при
    # write-back'е (текущее значение в БД уже не совпадёт с legacy_ciphertext).
    #
    # Контракт: outbox-row всегда содержит ciphertext (NOT NULL). Seed-логика
    # `_legacy_ciphertext_filter` пропускает owner-row'ы с NULL ciphertext'ом
    # (discovered-аккаунты без пароля) — они не попадают в outbox.
    legacy_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    # CHECK на БД ограничивает множество значений —
    # ck_secrets_reencrypt_outbox_status.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # claim-запрос идёт по `status='pending'` и сортирует по `created_at`
        # для FIFO.
        Index(
            "ix_secrets_reencrypt_outbox_status_created",
            "status",
            "created_at",
        ),
        # Узкий unique гарантирует «одна активная задача на owner-row'у».
        Index(
            "uq_secrets_reencrypt_outbox_entity_active",
            "entity_type",
            "entity_id",
            unique=True,
            postgresql_where="status IN ('pending', 'processing')",
        ),
    )
