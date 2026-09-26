"""Прогон — fleet-wide кампания (§2.4, §6.1 плана миграции).

Один `test_run` — это один РЦ (`os_version_id`) + `kernel`, запущенный сразу
на весь выбранный оператором пул стендов (`test_run_stands`) одного отдела.
Не путать с `stp_test_runs` (Zephyr test-run/execution, §2.5) — это
отдельная сущность на другом уровне, привязка к СТП появится позже.

`mode` — легаси-поле, оставленное nullable для обратной совместимости и
БОЛЬШЕ НЕ ЗАПОЛНЯЕТСЯ новыми кампаниями: режим безопасности — теперь фиксированное
свойство каждого теста (`test_definitions.mode`), а не кампании целиком,
поэтому одна кампания легитимно содержит и orel-, и smolensk-тесты
одновременно (см. `test_run_entries.mode`). Старые кампании, созданные до
этого изменения, сохраняют исторически записанное значение.

`os_version_id`/`kernel` — те же два ключа, которые `queue.enqueue()`
требует в `launch_context` под именами `RC`/`KERNEL` (третий, `MODE`, берётся
из `test_run_entries.mode`). Кампания раскладывает их по каждому найденному
тесту при постановке в очередь (см. `services/test_run.py`), без FK —
`os_version_id` межсервисная ссылка на каталог `server_service`, тем же
приёмом, что и у `global_variable`/`test_stand`.

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
информации.

`stp_composition_id`/`stp_revision` — снэпшот того, из какого состава СТП
выведена кампания, когда `test_run_stands` не задан явно (`composition_source
== "stp_composition"`, см. `services/test_run.py`). Соft-ref на
`stp_compositions.id`, тем же приёмом, что `os_version_id` — межсервисной FK
здесь тоже нет, а внутрисервисной не заводили специально: более позднее
переключение состава СТП не должно незаметно переинтерпретировать уже
начатую кампанию, поэтому связь фиксируется числом/id на момент создания, а
не живой ссылкой. Оба поля `NULL` для явного `test_run_stands` (СТП не
участвует) и для кампаний, созданных до появления `stp_compositions` для
этой РЦ.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class TestRun(Base):
    """Одна кампания — прогон каталога тестов на пуле стендов."""

    __tablename__ = "test_runs"
    __table_args__ = (UniqueConstraint("created_by", "client_request_id", name="uq_test_runs_client_request"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    os_version_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Легаси, nullable — см. docstring модуля. Новые кампании его не пишут.
    mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    kernels: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    kernel: Mapped[str] = mapped_column(String(64), nullable=False)
    department_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    test_run_stands: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    composition_source: Mapped[str] = mapped_column(String(32), nullable=False, default="legacy_queue", server_default="legacy_queue")
    # Soft-ref на stp_compositions.id, см. docstring модуля. NULL — явный test_run_stands или СТП ещё не генерировалась.
    stp_composition_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stp_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
