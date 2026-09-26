"""seed legacy launch formulas as template/test_field/stand variables

Revision ID: d6f2b8c4e1a9
Revises: c3e9a1f5b2d7
Create Date: 2026-09-24 10:05:00.000000

До формулы `CONFLUENCE_NEW_PAGE`, `TEST_CYCLE_NAME`, `TEST_CASE_NAME`,
`PARENT_PAGE`, `STAND` были зашиты в код `services/launch_context.py` (удалён)
(вместе со словарём «ветка → рубрика» и префиксом `DEBUG_`). Теперь
это данные: переменные с источниками `template` / `test_field` / `stand` /
`os_version` / `static`, которые резолвит `services/variable_resolver.py`.
Значения по умолчанию дают ровно легаси-результат allta_app:

* `TEST_TOPIC` = `test_definitions.changelog_component` (D8) — рубрика
  отчёта, ключ легаси `tests_list` (`emm/allta_app_full/allta_image_conf.py:109-119`).
* `TEST_SHORT_NAME` = `test_definitions.short_name` (D6), легаси-ключ
  словаря `tests` (`allta_image_conf.py:237-305`, `allta_back.py:496`
  `test_mapped = tests[tcas]`). Колонку добавит; пока её нет (или она
  пуста) — `fallback: full_name`, то же, что давал прежний код.
* `TEST_CASE_NAME` = `test_definitions.full_name` — имя тест-кейса Zephyr
  (`allta_back.py:494`, `tcas = entry[1]`).
* `STAND` = номер стенда (`allta_back.py:169`:
  `str(stand).replace("stand", "")`).
* `STAND_TOKEN` = легаси-имя стенда `stand3` (`allta_back.py:174-175`,
  `backup_image.py:297` `args.STAND`); у стендов без легаси-имени — id.
* `RC_NAME` — имя версии ОС (`allta_back.py:170` `-rs {release}`,
  `backup_image.py:297` `args.RELEASE`). Источник `os_version`, резолв даёт
  до него — временно `launch_context["RC"]` (TODO в резолвере).
* `TEST_CYCLE_NAME` = `{RC_NAME}_{MODE}_{KERNEL}_{STAND_TOKEN}`
  (`allta_back.py:175`: `f'-tcyc {release}_{mode}_{kernel}_{stand}'`).
* `CONFLUENCE_NEW_PAGE` = `{IS_DEBUG_PREFIX}{TEST_SHORT_NAME}_{RC_NAME}_{MODE}_{KERNEL}_{STAND_TOKEN}`
  (`backup_image.py:297`:
  `f'{args.TEST}_{args.RELEASE}_{args.MODE}_{args.KERNEL}_{args.STAND}'`).
* `PARENT_PAGE` = `{IS_DEBUG_PREFIX}STRESS_report {RC_NAME} ⬝ {TEST_TOPIC}`
  (`allta_image_conf.py:122-127`: `f'STRESS_report {key} ⬝ {topic}'`).
* `DEBUG_PREFIX` = `DEBUG_` (`static`) и `IS_DEBUG_PREFIX` — шаблон
  `{DEBUG_PREFIX}` с условием `when: debug`. В легаси debug-запусков не было;
  префикс — требование emm (owner п.11): результат отладочного прогона не
  должен попасть на боевую страницу Confluence. Раньше `DEBUG_` был
  константой в `launch_context.py`.

Существующие строки (`STAND`, `TEST_CASE_NAME`, `TEST_CYCLE_NAME`,
`CONFLUENCE_NEW_PAGE`, `PARENT_PAGE`, сиды `b1e7c4a9d203`/`f3a8c1e9b204`/
`b6d2f9a1c735`) переводятся на новый источник только если их не трогали
(`source='launch_context' AND source_ref IS NULL`). Новые вставляются с
`ON CONFLICT (code) DO NOTHING` — переменную с тем же кодом, заведённую
руками, миграция не перезаписывает.
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d6f2b8c4e1a9"
down_revision: Union[str, None] = "c3e9a1f5b2d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (code, label, source, source_ref, description)
_NEW_VARIABLES: list[tuple[str, str, str, dict, str]] = [
    (
        "TEST_TOPIC", "Рубрика отчёта теста", "test_field",
        {"field": "changelog_component"},
        "Компонент changelog теста — рубрика страницы отчёта Confluence "
        "(allta_image_conf.py:109-119, tests_list).",
    ),
    (
        "TEST_SHORT_NAME", "Короткое имя теста", "test_field",
        {"field": "short_name", "fallback": "full_name"},
        "Легаси-ключ словаря tests (XFS, postgresql-sm, …) — allta_image_conf.py:237-305. "
        "Пока short_name не задан — полное имя теста.",
    ),
    (
        "STAND_TOKEN", "Имя стенда", "stand",
        {"field": "legacy_token", "fallback": "id"},
        "Легаси-имя стенда (stand3); у стенда без легаси-имени — его id. "
        "allta_back.py:174-175, backup_image.py:297.",
    ),
    (
        "RC_NAME", "Имя версии ОС", "os_version",
        {"field": "name", "segments": None, "uu_segments": None},
        "Человеческое имя версии ОС (1.8.1.6) из карточки версии, а не её id. "
        "allta_back.py:170, backup_image.py:297.",
    ),
    (
        "DEBUG_PREFIX", "Префикс debug-запуска", "static",
        {"value": "DEBUG_"},
        "Префикс заголовков Confluence в debug-запуске, чтобы отладочный "
        "результат не попал на боевую страницу отчёта.",
    ),
    (
        "IS_DEBUG_PREFIX", "Префикс, если запуск debug", "template",
        {"template": "{DEBUG_PREFIX}", "when": "debug"},
        "В debug-запуске равен DEBUG_PREFIX, в обычном — пустой строке.",
    ),
]

# (code, source, source_ref, description) — перевод существующих сидов.
_CONVERTED: list[tuple[str, str, dict, str]] = [
    (
        "STAND", "stand", {"field": "number"},
        "Номер стенда (stand3 → 3) для -sn: целевые скрипты объявляют "
        "choices=['1','3',…]. allta_back.py:169.",
    ),
    (
        "TEST_CASE_NAME", "test_field", {"field": "full_name"},
        "Легаси-флаг -tcas: имя тест-кейса Zephyr = полное имя теста. allta_back.py:494.",
    ),
    (
        "TEST_CYCLE_NAME", "template",
        {"template": "{RC_NAME}_{MODE}_{KERNEL}_{STAND_TOKEN}"},
        "Легаси-флаг -tcyc. allta_back.py:175.",
    ),
    (
        "CONFLUENCE_NEW_PAGE", "template",
        {"template": "{IS_DEBUG_PREFIX}{TEST_SHORT_NAME}_{RC_NAME}_{MODE}_{KERNEL}_{STAND_TOKEN}"},
        "Легаси-флаг --confluence-new-page. backup_image.py:297.",
    ),
    (
        "PARENT_PAGE", "template",
        {"template": "{IS_DEBUG_PREFIX}STRESS_report {RC_NAME} ⬝ {TEST_TOPIC}"},
        "Легаси-флаг -pp/--confluence-parent-page. allta_image_conf.py:122-127.",
    ),
]

# Описания до — для downgrade.
_PREVIOUS_DESCRIPTIONS: dict[str, str] = {
    "STAND": "Стенд, на котором исполняется тест. Резолвер появится вместе с каталогом стендов.",
    "TEST_CASE_NAME": "Легаси-флаг -tcas (test case name). Задаётся вызывающим при постановке в очередь.",
    "TEST_CYCLE_NAME": "Легаси-флаг -tcyc (test cycle name). Задаётся вызывающим при постановке в очередь.",
    "CONFLUENCE_NEW_PAGE": (
        "Легаси-флаг --confluence-new-page. Вычисляется автоматически "
        "queue.py::claim_next (full_name теста + RC + MODE + KERNEL + id "
        "стенда) — вызывающий это значение не задаёт, любое переданное "
        "им значение перезаписывается на claim."
    ),
    "PARENT_PAGE": "Легаси-флаг -pp/--confluence-parent-page. Задаётся вызывающим при постановке в очередь.",
}


def _jsonb(name: str) -> sa.BindParameter:
    return sa.bindparam(name, type_=postgresql.JSONB)


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

    convert = sa.text(
        "UPDATE global_variables SET source = :source, source_ref = :source_ref, "
        "description = :description, updated_at = now() "
        "WHERE code = :code AND source = 'launch_context' AND source_ref IS NULL"
    ).bindparams(_jsonb("source_ref"))
    for code, source, source_ref, description in _CONVERTED:
        bind.execute(convert, {
            "code": code, "source": source, "source_ref": source_ref, "description": description,
        })


def downgrade() -> None:
    bind = op.get_bind()
    revert = sa.text(
        "UPDATE global_variables SET source = 'launch_context', source_ref = NULL, "
        "description = :description, updated_at = now() "
        "WHERE code = :code AND source = :source AND source_ref = CAST(:source_ref AS jsonb)"
    )
    for code, source, source_ref, _description in _CONVERTED:
        bind.execute(revert, {
            "code": code, "source": source, "source_ref": json.dumps(source_ref),
            "description": _PREVIOUS_DESCRIPTIONS[code],
        })
    codes = [code for code, *_ in _NEW_VARIABLES]
    bind.execute(
        sa.text(
            "DELETE FROM global_variables WHERE code IN :codes AND created_by IS NULL "
            "AND id NOT IN (SELECT variable_id FROM test_command_args WHERE variable_id IS NOT NULL)"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": codes},
    )
