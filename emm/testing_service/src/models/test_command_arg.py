"""Слоты конструктора команд — упорядоченный список аргументов одного теста.

Команда теста — не строка, а последовательность `test_command_args`,
отсортированная по `position`. Каждый слот — либо литерал
(`kind=literal`, значение в `literal_value`), либо ссылка на глобальную
переменную (`kind=variable`, `variable_id`), с необязательным
`override_value` — per-test переопределением значения переменной (§3.2 плана
миграции). Воркер резолвит слоты в список аргументов процесса, не в строку —
это то, что закрывает shell-инъекцию из легаси (`subprocess.run(cmd,
shell=True)`).

`variable_id` — настоящий FK на `global_variables.id`: обе таблицы живут в
одной БД, это внутридоменная ссылка, в отличие от `pinned_stand_id` у
`TestDefinition`.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class TestCommandArg(Base):
    """Один слот команды: литерал или ссылка на переменную."""

    __tablename__ = "test_command_args"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    test_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("test_definitions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Порядок в списке (drag&drop в будущем UI-конструкторе). Уникальность не
    # форсируется на уровне БД — переупорядочивание идёт отдельными PATCH'ами
    # по одному слоту, жёсткий UNIQUE(test_id, position) заставил бы клиента
    # сдвигать соседей отдельной транзакцией на каждый drag.
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    literal_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    variable_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("global_variables.id", ondelete="RESTRICT"),
        nullable=True,
    )
    override_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
