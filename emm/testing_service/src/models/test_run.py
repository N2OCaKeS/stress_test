"""Прогон — fleet-wide кампания (§2.4, §6.1 плана миграции).

Один `test_run` — это один РЦ (`os_version_id`) + `mode` + `kernel`, запущенный
сразу на весь выбранный оператором пул стендов (`test_run_stands`) одного
отдела. Не путать с `stp_test_runs` (Zephyr test-run/execution, §2.5, волна 8)
— это отдельная сущность на другом уровне, привязка к СТП появится позже.

`os_version_id`/`kernel`/`mode` — те же три ключа, которые `queue.enqueue()`
требует в `launch_context` под именами `RC`/`KERNEL`/`MODE`. Кампания просто
раскладывает их по каждому найденному тесту при постановке в очередь (см.
`services/test_run.py`), без FK — `os_version_id` межсервисная ссылка на
каталог `server_service`, тем же приёмом, что и у `global_variable`/
`test_stand`.

`test_run_stands` — вход запроса (какие стенды оператор явно выбрал при
создании кампании), не авто-вычисляется и не обновляется постфактум, поэтому
хранится как JSONB-снэпшот, а не отдельной таблицей связей.

`status` материализован и пересчитывается событийно из состояний дочерних
`queue_items` (`queue_items.test_run_id`, `services/test_run_status.py`) —
не вычисляется на лету при каждом чтении, чтобы `GET`-список кампаний не
платил N+1 запросов по дочерним item'ам ради фильтра по статусу.

`debug_mode` из черновика плана намеренно не заведено — debug-режим снимает
привязку теста к стенду (§5.5), а кампания по построению работает только по
`pinned_stand_id` каждого стенда пула; поле, которое всегда `false`, не несёт
информации. Подробное обоснование — в отчёте волны.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class TestRun(Base):
    """Одна кампания — прогон каталога тестов на пуле стендов."""

    __tablename__ = "test_runs"
    __table_args__ = (UniqueConstraint("created_by", "client_request_id", name="uq_test_runs_client_request"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    os_version_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    kernels: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    kernel: Mapped[str] = mapped_column(String(64), nullable=False)
    department_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    test_run_stands: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    composition_source: Mapped[str] = mapped_column(String(32), nullable=False, default="legacy_queue", server_default="legacy_queue")
    final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Soft-FK на auth_service identity (`usr_<hex>`/`bot_<hex>`).
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    client_request_id: Mapped[str | None] = mapped_column(String(128))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
