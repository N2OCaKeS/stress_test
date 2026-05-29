"""AuditOutbox — transactional outbox для audit-событий.

Назначение:

`_runner.run_task` раньше эмитил audit_client.emit в отдельной сессии после
коммита mark_succeeded/mark_failed. Это ломало инвариант «succeeded в DB ⇒
audit-событие отправлено»: SIGKILL/сетевой обрыв между commit и emit
оставлял task'у в финальном состоянии без audit-trail.

Outbox-pattern решает проблему *at-least-once* доставкой:

  1. в *той же* транзакции, что обновляет status task'и, INSERT-им строку
     с audit-payload'ом в `audit_outbox`;
  2. фоновый publisher (`src.services.audit_outbox_publisher`) выбирает все
     unpublished-строки (`published_at IS NULL`) и шлёт их в loging_service;
  3. на успех — UPDATE `published_at = now()`. На сбой — increment
     `attempts`, строка остаётся в очереди и будет переотправлена.

Гарантия: после успешного commit'а task-сессии outbox-row существует.
Publisher может перезапускаться неограниченное число раз, дубликаты
обрабатываются на стороне loging_service (идемпотентность по
`request_id`/`action`/`timestamp`).

Failure-mode `attempts ≥ MAX_PUBLISH_ATTEMPTS` (env, default 50) publisher
помечает row отравленным: выставляет `published_at = now()` и логирует ERROR
`audit_outbox: poisoned row dropped …`. Событие потеряно, но row перестаёт
блокировать SKIP LOCKED выборку и attempts не уходит в overflow.

Per-row backoff: между неуспешными попытками publisher ставит
`next_retry_at = now() + 2^attempts` (capped). SELECT отфильтровывает
row'ы, у которых backoff ещё не дотик'ал, — иначе при долгой недоступности
loging_service row крутилась бы в каждом тике loop'а и attempts уходил бы
в cap за минуты, не за часы.

Ручной re-attempt из DLQ: taskiq-таска `internal.outbox_re_attempt`
(см. `src/main.py`) сбрасывает `published_at`, `attempts`,
`next_retry_at` для указанной row → она снова видна publisher'у.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class AuditOutbox(Base):
    """ORM-row для одного отложенного audit-события.

    Жизненный цикл: INSERT в task-lifecycle сессии → SELECT publisher'ом
    → emit в loging_service → UPDATE published_at. На fail emit'а —
    attempts++ и last_error, row остаётся unpublished для следующего
    прохода.
    """

    __tablename__ = "audit_outbox"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # task_id — для трассировки и debug; nullable=True потому что у
    # task_not_found-события нет своей task-row, но audit всё равно нужно
    # довезти.
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # payload — *уже подготовленный* dict, который audit_client.emit() умеет
    # принимать как kwargs (action + остальные поля). См.
    # `audit_outbox_publisher._publish_one`.
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Backoff per row: после очередного fail'а publisher ставит сюда
    # `now() + 2^attempts seconds`. SELECT отфильтровывает строки, у
    # которых это время ещё не наступило, — между retry'ями row отдыхает
    # вместо того, чтобы крутиться в каждом тике loop'а и жечь HTTP-
    # лимиты loging_service. NULL означает «ретраить можно сразу»
    # (первый прогон или после ручного re-attempt).
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # Partial-index по «ещё не опубликованным» — publisher сканирует
        # только их, цена INSERT для уже отправленных копеечная. Ключ
        # `(next_retry_at, created_at)` — фильтр по «можно ретраить»
        # сразу попадает в b-tree.
        Index(
            "ix_audit_outbox_unpublished_retry",
            "next_retry_at",
            "created_at",
            postgresql_where=text("published_at IS NULL"),
        ),
    )
