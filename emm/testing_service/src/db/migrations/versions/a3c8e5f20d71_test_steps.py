"""Multi-step tests (test_steps), rerun script, queue step progress

Revision ID: a3c8e5f20d71
Revises: e4c7b2a91d35
Create Date: 2026-09-24 00:00:00.000000

Решение D14 плана паритета. Многоступенчатый тест — упорядоченные
шаги `test_steps` на одной брони стенда: у шага свои слоты команды
(`test_command_args.step_id`), свой `starter_suffix`, свой шаг настройки
стенда (D9: `test_definitions.stand_setup` переезжает сюда) и
способ запуска `run_mode`:

* `full` — команда запуска профиля (`starter.sh` с клонированием ветки);
* `rerun` — повторный запуск уже склонированного кода: вместо `starter.sh`
  по тому же пути кладётся `launch_profile_versions.rerun_script`, команда
  запуска и остановки — те же (T1 работает без изменений).

Данные:

* каждому существующему тесту — один шаг (позиция 0) с его `stand_setup` и
  `starter_suffix`; все слоты теста переезжают в этот шаг. Колонки
  `test_definitions.stand_setup`/`starter_suffix` удаляются (API теста
  по-прежнему принимает и отдаёт их как значения первого шага);
* `rerun_script` всех версий профилей запуска — легаси-команда повторной
  фазы `db_kernel_changer`: `cd /home/u/git/stress_test/{branch}/ && sudo
  {VENV_PATH} run.py -n {dates_name} -kn kernel`
  (`emm/allta_app_full/backup_image.py:935-939`, `VENV_PATH` —
  `allta_image_conf.py:16`); флаг `run.py` выбирается по `$5`, как в
  `starter.sh:80-88`;
* `postgresql.kernels` и `postgresql.tantor_kernels` (легаси
  `db_kernel_changer`, `backup_image.py:895-941`, вызов 1028-1032) — 4 шага
  на одной брони: `maxcpus=8/16/24/32` через `kernel_cmdline_extra`
  (легаси `sed … maxcpus={cpu_count}` + `update-grub` + `reboot`,
  `backup_image.py:896-897,922-925`), `-q N` и `-sf begin` / без `-sf` /
  `-sf end` в слотах (`backup_image.py:898-918`), первый шаг `full`
  (`starter.sh … kernel`, :931-933), остальные `rerun` (:934-939); у tantor
  перед повторными фазами — `systemctl restart tantor-se-server-15.service`
  (:935-936) скриптом настройки шага без перезагрузки после. `readiness` —
  `ready`. Если слоты теста уже поменяли руками (нет `-q 8 -sf begin`) или у
  теста уже несколько шагов — тест не трогается.

`queue_items`: `current_step_index`, `step_count` (сколько
шагов было на момент последнего перехода — для прогресса в UI) и
`stand_setup_correlation_id` (`correlation_id` операции «настройка без
restore» server_service между шагами: пишется до вызова, по
нему сшивается callback; у каждой попытки свой — server_service
идемпотентен по нему).
"""
from __future__ import annotations

import json
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a3c8e5f20d71"
down_revision: Union[str, None] = "e4c7b2a91d35"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Легаси `backup_image.py:935-939` (+ `VENV_PATH`, `allta_image_conf.py:16`).
# Аргументы — те же, что у `starter.sh`: `$1` ветка, `$3` файл dates, `$5` суффикс.
RERUN_SCRIPT = (
    "#!/bin/bash\n"
    "\n"
    "# Повторный запуск уже склонированной ветки (шаг run_mode=rerun):\n"
    "# без клонирования и prepare.sh. Аргументы — как у starter.sh.\n"
    'cd /home/u/git/stress_test/"$1"/ || exit 1\n'
    "\n"
    'if [ "$5" == "kernel" ]; then\n'
    '    /home/u/python/Python-3.12.1/venv/bin/python3.12 run.py -n "$3" -kn "$5"\n'
    'elif [ "$5" == "balance" ]; then\n'
    '    /home/u/python/Python-3.12.1/venv/bin/python3.12 run.py -n "$3" -bl "$5"\n'
    'elif [ "$5" == "oom" ]; then\n'
    '    /home/u/python/Python-3.12.1/venv/bin/python3.12 run.py -n "$3" -oom "$5"\n'
    "else\n"
    '    /home/u/python/Python-3.12.1/venv/bin/python3.12 run.py -n "$3"\n'
    "fi\n"
)

# `backup_image.py:1028-1032`: (cpu_count, position).
_KERNEL_PHASES = ((8, "begin"), (16, None), (24, None), (32, "end"))
_KERNEL_TESTS = {"postgresql.kernels": False, "postgresql.tantor_kernels": True}  # code → tantor
# `backup_image.py:936`.
_TANTOR_RESTART = "systemctl restart tantor-se-server-15.service\n"


def _step_id() -> str:
    return f"tstep_{uuid4().hex}"


def _stand_setup(cpu_count: int, *, tantor_restart: bool) -> dict:
    return {
        "kernel_cmdline_extra": [f"maxcpus={cpu_count}"],
        "script": _TANTOR_RESTART if tantor_restart else "",
        "run_as": "root",
        "phase": "after_boot",
        # Легаси после рестарта tantor сразу запускал фазу, без перезагрузки.
        "reboot_after": False if tantor_restart else None,
        "timeout_seconds": 1800,
    }


def _phase_name(cpu_count: int, position: str | None) -> str:
    return f"maxcpus={cpu_count}" + (f" · {position}" if position else "")


def _convert_kernels(bind) -> None:
    for code, tantor in _KERNEL_TESTS.items():
        test = bind.execute(sa.text("SELECT id FROM test_definitions WHERE code = :code"), {"code": code}).first()
        if test is None:
            continue
        steps = bind.execute(
            sa.text("SELECT id, stand_setup, starter_suffix FROM test_steps WHERE test_id = :t"), {"t": test.id},
        ).all()
        if len(steps) != 1 or steps[0].stand_setup is not None:
            continue
        first = steps[0]
        slots = bind.execute(
            sa.text(
                "SELECT id, kind, literal_value, variable_id, override_value FROM test_command_args "
                "WHERE step_id = :s ORDER BY position, id"
            ),
            {"s": first.id},
        ).all()
        values = [s.literal_value if s.kind == "literal" else None for s in slots]
        try:
            q_at = next(i for i in range(len(values) - 1) if values[i] == "-q" and values[i + 1] == "8")
            sf_at = next(i for i in range(len(values) - 1) if values[i] == "-sf" and values[i + 1] == "begin")
        except StopIteration:
            continue  # слоты уже меняли руками — не трогаем

        for index, (cpu_count, position) in enumerate(_KERNEL_PHASES):
            setup = _stand_setup(cpu_count, tantor_restart=tantor and index > 0)
            if index == 0:
                bind.execute(
                    sa.text(
                        "UPDATE test_steps SET name = :name, stand_setup = CAST(:setup AS jsonb), "
                        "updated_at = now() WHERE id = :id"
                    ),
                    {"id": first.id, "name": _phase_name(cpu_count, position), "setup": json.dumps(setup)},
                )
                continue
            step_id = _step_id()
            bind.execute(
                sa.text(
                    "INSERT INTO test_steps (id, test_id, position, name, starter_suffix, run_mode, stand_setup) "
                    "VALUES (:id, :test_id, :position, :name, :suffix, 'rerun', CAST(:setup AS jsonb))"
                ),
                {
                    "id": step_id, "test_id": test.id, "position": index,
                    "name": _phase_name(cpu_count, position), "suffix": first.starter_suffix,
                    "setup": json.dumps(setup),
                },
            )
            new_position = 0
            for i, slot in enumerate(slots):
                literal = slot.literal_value
                if position is None and i in (sf_at, sf_at + 1):
                    continue  # средние фазы — без `-sf`
                if i == q_at + 1:
                    literal = str(cpu_count)
                elif i == sf_at + 1 and position:
                    literal = position
                bind.execute(
                    sa.text(
                        "INSERT INTO test_command_args "
                        "(id, test_id, step_id, position, kind, literal_value, variable_id, override_value) "
                        "VALUES (:id, :test_id, :step_id, :position, :kind, :literal, :variable_id, :override)"
                    ),
                    {
                        "id": f"targ_{uuid4().hex}", "test_id": test.id, "step_id": step_id,
                        "position": new_position, "kind": slot.kind,
                        "literal": literal if slot.kind == "literal" else None,
                        "variable_id": slot.variable_id, "override": slot.override_value,
                    },
                )
                new_position += 1
        bind.execute(
            sa.text("UPDATE test_definitions SET readiness = 'ready', updated_at = now() WHERE id = :id"),
            {"id": test.id},
        )


def upgrade() -> None:
    op.create_table(
        "test_steps",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "test_id", sa.String(length=64),
            sa.ForeignKey("test_definitions.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("starter_suffix", sa.String(length=16), nullable=True),
        sa.Column("run_mode", sa.String(length=16), nullable=False, server_default="full"),
        sa.Column("stand_setup", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("run_mode IN ('full', 'rerun')", name="ck_test_steps_run_mode"),
    )
    op.create_index("ix_test_steps_test_id", "test_steps", ["test_id"])

    op.add_column("test_command_args", sa.Column("step_id", sa.String(length=64), nullable=True))
    bind = op.get_bind()
    tests = bind.execute(sa.text("SELECT id, stand_setup, starter_suffix FROM test_definitions")).all()
    for test in tests:
        step_id = _step_id()
        bind.execute(
            sa.text(
                "INSERT INTO test_steps (id, test_id, position, name, starter_suffix, run_mode, stand_setup) "
                "VALUES (:id, :test_id, 0, '', :suffix, 'full', CAST(:setup AS jsonb))"
            ),
            {
                "id": step_id, "test_id": test.id, "suffix": test.starter_suffix,
                "setup": None if test.stand_setup is None else json.dumps(test.stand_setup),
            },
        )
        bind.execute(
            sa.text("UPDATE test_command_args SET step_id = :step_id WHERE test_id = :test_id"),
            {"step_id": step_id, "test_id": test.id},
        )
    op.alter_column("test_command_args", "step_id", nullable=False)
    op.create_foreign_key(
        "fk_test_command_args_step", "test_command_args", "test_steps", ["step_id"], ["id"], ondelete="CASCADE",
    )
    op.create_index("ix_test_command_args_step_id", "test_command_args", ["step_id"])
    op.drop_column("test_definitions", "stand_setup")
    op.drop_column("test_definitions", "starter_suffix")

    op.add_column("launch_profile_versions", sa.Column("rerun_script", sa.Text(), nullable=True))
    bind.execute(
        sa.text("UPDATE launch_profile_versions SET rerun_script = :script WHERE rerun_script IS NULL"),
        {"script": RERUN_SCRIPT},
    )

    op.add_column("queue_items", sa.Column(
        "current_step_index", sa.Integer(), nullable=False, server_default=sa.text("0"),
    ))
    op.add_column("queue_items", sa.Column("step_count", sa.Integer(), nullable=True))
    op.add_column("queue_items", sa.Column("stand_setup_correlation_id", sa.String(length=128), nullable=True))
    op.create_index("ix_queue_items_stand_setup_correlation_id", "queue_items", ["stand_setup_correlation_id"])

    _convert_kernels(bind)


def downgrade() -> None:
    # Обратно — только первый шаг: слоты остальных шагов теряются.
    op.drop_index("ix_queue_items_stand_setup_correlation_id", table_name="queue_items")
    op.drop_column("queue_items", "stand_setup_correlation_id")
    op.drop_column("queue_items", "step_count")
    op.drop_column("queue_items", "current_step_index")
    op.drop_column("launch_profile_versions", "rerun_script")

    op.add_column("test_definitions", sa.Column("stand_setup", postgresql.JSONB(), nullable=True))
    op.add_column("test_definitions", sa.Column("starter_suffix", sa.String(length=16), nullable=True))
    first_steps = (
        "SELECT DISTINCT ON (test_id) id, test_id, stand_setup, starter_suffix FROM test_steps "
        "ORDER BY test_id, position, id"
    )
    op.execute(sa.text(
        f"UPDATE test_definitions t SET stand_setup = f.stand_setup, starter_suffix = f.starter_suffix "
        f"FROM ({first_steps}) f WHERE f.test_id = t.id"
    ))
    op.execute(sa.text(f"DELETE FROM test_command_args WHERE step_id NOT IN (SELECT id FROM ({first_steps}) f)"))
    op.drop_index("ix_test_command_args_step_id", table_name="test_command_args")
    op.drop_constraint("fk_test_command_args_step", "test_command_args", type_="foreignkey")
    op.drop_column("test_command_args", "step_id")
    op.drop_index("ix_test_steps_test_id", table_name="test_steps")
    op.drop_table("test_steps")
