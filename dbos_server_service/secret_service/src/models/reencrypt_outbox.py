"""Модель `reencrypt_outbox_entries` — задачи на proactive re-encrypt credentials.

Lazy re-encrypt в `credential_service._lazy_reencrypt_if_needed` перешифровывает
ciphertext под активный мастер-ключ только при чтении кред'ы. На «холодных»
credential'ах (никто не reveal'ит) row остаётся под старой версией, и
`migration_status.remaining_legacy` никогда не упадёт до нуля → ротационный
скрипт не может дропнуть `SECRET_ENCRYPTION_KEY__v<old>`.

Outbox-pattern закрывает этот пробел: после ротации оператор зовёт
`POST /internal/reencrypt_outbox/seed`, который сканирует credentials с
legacy-префиксом и публикует pending row'ы. Воркер / ручной call дальше
тянет batch'ами `process` — каждая транзакция короткая (decrypt → encrypt →
UPDATE credential + UPDATE outbox-row → commit), pool не блокируется.

Поля:

* `id` — `rox_<32 hex>`, формат симметричен `cred_<hex>`.
* `credential_id` — soft-FK на `credentials.id`. Делаем CASCADE'ом FK, чтобы
  hard-delete'нутая cred'а не оставляла висящий outbox-row.
* `source_version` — wire-версия `v<N>$`, под которой row была на момент seed'а.
* `target_version` — активная версия на момент seed'а; финализация процессит
  под текущим active (берётся из Settings в момент process'а), а это поле —
  справочное (audit / диагностика).
* `seeded_at` — когда задача попала в очередь.
* `completed_at` — успешный processed или error-finalize timestamp.
* `error_message` — последняя ошибка decrypt/encrypt (NULL для pending / done).
* `attempts` — сколько раз process пытался эту row'у обработать.
* `status` — pending | done | error.

Идемпотентность: partial UNIQUE по `credential_id` среди pending row'ов — не
даёт повторному seed'у плодить дубли для credential'ы, которая всё ещё
не обработана. Если предыдущая попытка `done` / `error` — новый seed после
очередной ротации добавит новую row нормально.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


OUTBOX_STATUS_VALUES = ("pending", "done", "error")

STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_ERROR = "error"


class ReencryptOutboxEntry(Base):
    """Задача на perevod credential.secret_encrypted под текущий ключ."""

    __tablename__ = "reencrypt_outbox_entries"

    # `rox_` — reencrypt outbox; симметрично `cred_`, `usr_`, и т.п.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    credential_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("credentials.id", ondelete="CASCADE"),
        nullable=False,
    )

    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    target_version: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'pending'"),
    )

    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    seeded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'done', 'error')",
            name="ck_reencrypt_outbox_status",
        ),
        # Bound `error_message` чтобы кто-нибудь не залил гигабайты в audit.
        CheckConstraint(
            "error_message IS NULL OR length(error_message) <= 4096",
            name="ck_reencrypt_outbox_error_len",
        ),
        Index("ix_reencrypt_outbox_status", "status"),
        Index("ix_reencrypt_outbox_credential_id", "credential_id"),
        # Partial UNIQUE по pending: повторный seed на одну и ту же cred'у
        # не плодит дубли активных задач, пока row не закроется done/error.
        Index(
            "uq_reencrypt_outbox_pending_per_cred",
            "credential_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )
