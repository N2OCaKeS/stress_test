"""RC_NAME and derived os_version variables, starter.sh args template

Revision ID: 89394402c071
Revises: f8b3d1e6a254
Create Date: 2026-09-24 12:00:00.000000

`launch_context["RC"]` — id карточки версии ОС (`osv_<hex>`): им
параметризуются `prepare-for-test` и ACS, и таким он остаётся. Человеческое
имя версии и его производные — переменные источника `os_version`
(`services/variable_resolver.py::_from_os_version`, CONTRACTS.md C1), которые
резолвятся по карточке версии из server_service:

* `RC_NAME` (заведена в `d6f2b8c4e1a9`) = имя версии целиком (`1.8.1.6`) —
  легаси `args.RELEASE` (`emm/allta_app_full/backup_image.py:55-59`, `-rs`).
* `RC_RELEASE` = первые 3 сегмента, у UU-версии 5 (`1.8.1.6` → `1.8.1`,
  `1.7.9.UU.1.2` → `1.7.9.UU.1`) — `release_version` из
  `emm/allta_app_full/allta_back.py:350-359` (блок `filter_url`) и
  `emm/allta_app_full/libs/zefir.py:243-251`.
* `RC_BRANCH` = первые 2 сегмента (`1.8`) — ветка релиза, по которой легаси
  выбирал бокс ВМ (`startswith('1.7')`).
* `RC_NUMBER` = `rc_number` карточки (`RC3`) — ручная метка на версии в
  server_service (заголовок блога, `services/run_summary.py`).

Слоты каталога `-tcv`/`-vbox`, которые смотрели на `RC` (то есть получали
`osv_<hex>`), переводятся на `RC_NAME`: легаси `tcv = f'-tcv {args.RELEASE}'`
(`backup_image.py:309`), `-vbox {args.TCYCLE.split('_')[0]}`
(`backup_image.py:310`, первый сегмент test cycle = `RC_NAME`). Переводятся
только variable-слоты `RC`, непосредственно за которыми (по `position`) идёт
литерал `-tcv` или `-vbox`.

Аргументы `starter.sh` — шаблон в переменной `STARTER_ARGS_TEMPLATE`
(`static`, временно до, который переносит его в профиль запуска):
`{TEST_BRANCH} {GIT_TOKEN_FILE} {DATES_FILE} {RC_NAME} {STARTER_SUFFIX}` —
легаси `sudo bash /home/u/starter.sh {branch} "{__git_token}" {dates_name}
{args.RELEASE} <suffix>` (`backup_image.py:1034-1040`), `$4` уходит в
`prepare.sh $1 $4 $6` (`emm/allta_app_full/starter.sh:74`). Токен вместо
значения передаётся именем файла (как и раньше в emm). `TEST_BRANCH`,
`GIT_TOKEN_FILE`, `DATES_FILE`, `STARTER_SUFFIX` — значения, которые знает
только claim (`queue.claim_next` кладёт их в контекст резолва поверх
`launch_context`), поэтому источник у них `launch_context`.

Новые строки вставляются с `ON CONFLICT (code) DO NOTHING` — переменную,
заведённую руками с тем же кодом, миграция не перезаписывает.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "89394402c071"
down_revision: Union[str, None] = "f8b3d1e6a254"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (code, label, source, source_ref, description)
_NEW_VARIABLES: list[tuple[str, str, str, dict | None, str]] = [
    (
        "RC_RELEASE", "Релиз версии ОС", "os_version",
        {"field": "name", "segments": 3, "uu_segments": 5},
        "Релизная часть имени версии: 3 сегмента (1.8.1.6 → 1.8.1), у срочного "
        "обновления 5 (1.7.9.UU.1.2 → 1.7.9.UU.1). allta_back.py:350-359.",
    ),
    (
        "RC_BRANCH", "Ветка версии ОС", "os_version",
        {"field": "name", "segments": 2, "uu_segments": None},
        "Первые 2 сегмента имени версии (1.8.1.6 → 1.8).",
    ),
    (
        "RC_NUMBER", "Номер РЦ", "os_version",
        {"field": "rc_number", "segments": None, "uu_segments": None},
        "Номер РЦ (RC3) — ручная метка на карточке версии ОС в server_service.",
    ),
    (
        "STARTER_ARGS_TEMPLATE", "Аргументы starter.sh", "static",
        {"value": "{TEST_BRANCH} {GIT_TOKEN_FILE} {DATES_FILE} {RC_NAME} {STARTER_SUFFIX}"},
        "Позиционные аргументы starter.sh; токены по пробелам, подстановки {CODE} "
        "в каждом. backup_image.py:1034-1040 ($4 = версия ОС). Временно до "
        "профилей запуска.",
    ),
    (
        "TEST_BRANCH", "Git-ветка теста", "launch_context", None,
        "Ветка монорепо с кодом теста (category теста), $1 у starter.sh. "
        "Заполняется на claim, постановщик её не задаёт.",
    ),
    (
        "GIT_TOKEN_FILE", "Файл git-токена", "launch_context", None,
        "Имя файла с заголовком Authorization для git clone, $2 у starter.sh. "
        "Заполняется на claim.",
    ),
    (
        "DATES_FILE", "Файл dates.conf", "launch_context", None,
        "Имя файла с аргументами теста (dates.conf), $3 у starter.sh. Заполняется на claim.",
    ),
    (
        "STARTER_SUFFIX", "Суффикс starter.sh", "launch_context", None,
        "Режим run.py (kernel/balance/oom или пусто) — starter_suffix теста, $5 у "
        "starter.sh. Заполняется на claim.",
    ),
]

_SLOT_FLAGS = ("-tcv", "-vbox")

_RC_NAME_DESCRIPTION = (
    "Человеческое имя версии ОС (1.8.1.6) из карточки версии, а не её id: "
    "-tcv, -vbox, $4 у starter.sh, заголовки. allta_back.py:170, backup_image.py:297,309."
)
# Описание из d6f2b8c4e1a9 — для downgrade.
_RC_NAME_PREVIOUS_DESCRIPTION = (
    "Человеческое имя версии ОС (1.8.1.6) из карточки версии, а не её id. "
    "allta_back.py:170, backup_image.py:297."
)


def _jsonb(name: str) -> sa.BindParameter:
    # `none_as_null`: у переменных без ссылки — SQL NULL, а не JSON `null`.
    return sa.bindparam(name, type_=postgresql.JSONB(none_as_null=True))


def _switch_slots(bind, from_code: str, to_code: str) -> None:
    """variable-слоты `from_code` сразу после литерала `-tcv`/`-vbox` → `to_code`."""
    bind.execute(
        sa.text(
            "UPDATE test_command_args AS a "
            "SET variable_id = (SELECT id FROM global_variables WHERE code = :to_code), "
            "    updated_at = now() "
            "WHERE a.kind = 'variable' "
            "  AND a.variable_id = (SELECT id FROM global_variables WHERE code = :from_code) "
            "  AND EXISTS (SELECT 1 FROM global_variables WHERE code = :to_code) "
            "  AND (SELECT p.literal_value FROM test_command_args AS p "
            "       WHERE p.test_id = a.test_id AND p.position < a.position "
            "       ORDER BY p.position DESC LIMIT 1) IN :flags"
        ).bindparams(sa.bindparam("flags", expanding=True)),
        {"from_code": from_code, "to_code": to_code, "flags": list(_SLOT_FLAGS)},
    )


def _set_rc_name_description(bind, current: str, new: str) -> None:
    """Описание `RC_NAME` меняется, только если его не правили руками."""
    bind.execute(sa.text(
        "UPDATE global_variables SET description = :new, updated_at = now() "
        "WHERE code = 'RC_NAME' AND description = :current"
    ), {"current": current, "new": new})


def upgrade() -> None:
    bind = op.get_bind()
    insert = sa.text(
        "INSERT INTO global_variables "
        "(id, code, label, source, value_type, choices_source, is_sensitive, description, source_ref) "
        "VALUES (:id, :code, :label, :source, 'string', NULL, false, :description, :source_ref) "
        "ON CONFLICT (code) DO NOTHING"
    ).bindparams(_jsonb("source_ref"))
    for code, label, source, source_ref, description in _NEW_VARIABLES:
        bind.execute(insert, {
            "id": f"gvar_{code.lower()}", "code": code, "label": label, "source": source,
            "description": description, "source_ref": source_ref,
        })

    _set_rc_name_description(bind, _RC_NAME_PREVIOUS_DESCRIPTION, _RC_NAME_DESCRIPTION)

    _switch_slots(bind, "RC", "RC_NAME")


def downgrade() -> None:
    bind = op.get_bind()
    _switch_slots(bind, "RC_NAME", "RC")
    _set_rc_name_description(bind, _RC_NAME_DESCRIPTION, _RC_NAME_PREVIOUS_DESCRIPTION)
    codes = [code for code, *_ in _NEW_VARIABLES]
    bind.execute(
        sa.text(
            "DELETE FROM global_variables WHERE code IN :codes AND created_by IS NULL "
            "AND id NOT IN (SELECT variable_id FROM test_command_args WHERE variable_id IS NOT NULL)"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": codes},
    )
