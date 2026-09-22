"""Вычисляемая часть `launch_context` — значения, которые считает сам сервис.

Вызывающий (кампания, одиночная постановка, публичная очередь) присылает
только то, что реально выбирает человек: `RC`, `KERNEL`, `MODE`. Остальные
переменные команды теста в allta_app не были входными параметрами — они
собирались из уже известного прямо перед запуском, и здесь делается ровно то
же самое, в `queue.claim_next` (момент, когда стенд и все три входных
значения окончательно известны).

Соответствие легаси:

* `PARENT_PAGE` — `allta_image_conf.py::parent_page_list()`:
  `f'STRESS_report {release} ⬝ {topic}'`, где topic — рубрика теста из словаря
  `tests_list`. Рубрика однозначно выводится из `test_definitions.category`
  (ветка монорепо), см. `_TOPIC_BY_CATEGORY`.
* `CONFLUENCE_NEW_PAGE` — `backup_image.py:297`:
  `f'{TEST}_{RELEASE}_{MODE}_{KERNEL}_{STAND}'`.
* `TEST_CYCLE_NAME` — `allta_back.py:175`: `f'{release}_{mode}_{kernel}_{stand}'`.
  Формат важен не только для Zephyr: `virt/test_run.py` и `parsec/ps_run.py`
  берут из него первый сегмент как версию РЦ (`args.TCYC.split('_')[0]`).
* `TEST_CASE_NAME` — `allta_back.py:494`: имя тест-кейса Zephyr. В каталоге это
  `test_definitions.full_name` — при импорте туда легли ровно ключи легаси-
  словаря `tests` (`'file system benchmark. XFS'` и т.п.).
* `STAND` — `allta_back.py:169`: `str(stand).replace('stand', '')`, то есть
  голый номер. Целевые скрипты объявляют `-sn` через `choices=['1','3','4',…]`,
  поэтому ничего, кроме номера, туда класть нельзя.

Чего здесь нет: `FOLDER_TREE_ID`. Легаси брал его из `cycle_tree_index()` —
словаря «версия → id папки Zephyr», который наполнял отдельный бот. В emm
такого словаря нет и заводить его незачем: конечный скрипт всё равно ходит в
Jira с `--username none --token none`, своя отчётность в Zephyr живёт в
`services/stp*.py`. Слот `-fti` поэтому импортируется со статическим
`override_value: "none"` — как уже сделано для `--username`/`--token`/`-ba`.

Значение, посчитанное здесь, кладётся в `launch_context` и дальше резолвится
общим кодом `test_command_arg._resolve_variable_slot`. Явный `override_value`
на слоте по-прежнему сильнее — ручная настройка конкретного теста не
перебивается.
"""

from __future__ import annotations

from src.models import TestDefinition, TestStand

# Рубрика Confluence по ветке монорепо. Источник — `allta_image_conf.py`
# `tests_list` (рубрика → список тестов), свёрнутый до category, потому что
# внутри одной ветки рубрика у всех тестов одна и та же.
_TOPIC_BY_CATEGORY: dict[str, str] = {
    "postgresql": "PostgreSQL",
    "file_systems": "Файловые системы",
    "cluster_file_systems": "Файловые системы",
    "auditd": "Системные службы",
    "syslog_ng": "Системные службы",
    "overflow": "Системные службы",
    "kernel": "Системные службы",
    "astraevents": "Системные службы",
    "astra_openvpn": "Системные службы",
    "exim": "Системные службы",
    "linux_system": "UnixBench",
    "freeipa": "FreeIPA",
    "parsec": "Parsec",
    "apache2": "Apache",
    "docker": "Docker/Podman/LXC",
    "virt": "Qemu/KVM/Libvirt",
    "network": "Network",
}


def stand_token(stand: TestStand) -> str:
    """Имя стенда для всего, что видит человек или легаси: `stand3`, иначе `id`.

    Fallback на `id` осознанный — стенд, заведённый уже в emm, легаси-имени не
    имеет, и лучше показать хоть что-то уникальное, чем пустую ячейку.
    """
    return stand.legacy_token or stand.id


def stand_number(stand: TestStand) -> str | None:
    """Голый номер стенда для `-sn` — `stand3` → `"3"`. `None`, если имени нет."""
    if not stand.legacy_token:
        return None
    digits = "".join(ch for ch in stand.legacy_token if ch.isdigit())
    return digits or None


_DEBUG_CONFLUENCE_PREFIX = "DEBUG_"


def computed_values(
    test: TestDefinition, stand: TestStand, ctx: dict[str, str], *, debug_mode: bool = False,
) -> dict[str, str]:
    """Значения переменных, которые сервис считает сам, а не получает снаружи.

    `ctx` — то, что пришло от вызывающего (`RC`/`KERNEL`/`MODE`). Результат
    предназначен для наложения поверх `ctx`: вычисляемое поле всегда сильнее
    одноимённого входного, иначе постановщик задания мог бы подделать,
    например, заголовок страницы Confluence.

    `debug_mode=True` — результат отладочного запуска не должен попасть на ту
    же страницу Confluence, что боевой прогон (иначе он либо перезапишет
    боевой отчёт, либо сломает его добавлением незапланированных данных).
    Отсюда префикс `DEBUG_` у `CONFLUENCE_NEW_PAGE` и `PARENT_PAGE` — отдельное
    отладочное пространство страниц вместо реального рубрикатора отчётов.
    """
    rc = ctx.get("RC", "")
    mode = ctx.get("MODE", "")
    kernel = ctx.get("KERNEL", "")
    token = stand_token(stand)
    prefix = _DEBUG_CONFLUENCE_PREFIX if debug_mode else ""

    values = {
        "CONFLUENCE_NEW_PAGE": f"{prefix}{test.full_name}_{rc}_{mode}_{kernel}_{token}",
        "TEST_CYCLE_NAME": f"{rc}_{mode}_{kernel}_{token}",
        "TEST_CASE_NAME": test.full_name,
    }

    topic = _TOPIC_BY_CATEGORY.get(test.category or "")
    if topic:
        values["PARENT_PAGE"] = f"{prefix}STRESS_report {rc} ⬝ {topic}"

    number = stand_number(stand)
    if number:
        values["STAND"] = number

    return values
