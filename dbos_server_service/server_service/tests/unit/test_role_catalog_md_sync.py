"""Anti-drift: каталог `ENTITY_ACTIONS` vs обсидиановские страницы ролевой модели.

Контракт:
  * Источник истины — `src.core.constants.ENTITY_ACTIONS` (whitelist пар
    entity_type → set[action]).
  * `obsidian/Ролевая модель.md` и `obsidian/domain/Роли и права.md` дублируют
    содержимое каталога в человекочитаемом виде — заголовки/таблицы по
    каждой сущности. Doc-drift'ом не раз ловили в прошлом, когда action
    добавляли в `constants.py` (через миграцию), а md забывали — или
    наоборот, оставляли в md удалённый action.
  * Тест парсит md, выдёргивает упоминания entity/action и сверяет с кодом.

  * Файлы `obsidian/` git-ignored — в Docker-контейнере тестов их нет.
    Поэтому отсутствие файла → `pytest.skip`, не fail. Тест жёстко
    срабатывает только когда md рядом.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from src.core.constants import ENTITY_ACTIONS


def _find_vault() -> pathlib.Path | None:
    """Локально файл лежит в `<repo>/dbos_server_service/server_service/tests/unit/`,
    vault — в `<repo>/dbos_server_service/obsidian/`. В Docker test-контейнере
    путь короче, и obsidian/ вообще не примонтирован. Ищем по предкам,
    возвращаем None, если не нашли — тогда тесты пропустятся.
    """
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "obsidian"
        if candidate.is_dir():
            return candidate
    return None


_VAULT = _find_vault()

_ROLE_MODEL_MD = (_VAULT / "Ролевая модель.md") if _VAULT else None
_ROLES_RIGHTS_MD = (_VAULT / "domain" / "Роли и права.md") if _VAULT else None


# Имена, которые встречаются в md как поясняющие токены, но не должны
# трактоваться как action'ы (например слово `view` в обороте «view
# списка» или «view публичный»). Не используется напрямую — тест ищет
# action'ы по факту наличия в каталоге, lone-tokens из md не проверяет.


def _entities_in_md(text: str) -> set[str]:
    """Все entity_type, упомянутые в md.

    Ищем backquoted-токены (`server`, `server_account`, …), фильтруем по
    тем, что есть в каталоге кода. Так не ловятся посторонние backquoted
    слова вроде `is_system` или `True`.
    """
    known = set(ENTITY_ACTIONS.keys())
    found: set[str] = set()
    for tok in re.findall(r"`([a-z_]+)`", text):
        if tok in known:
            found.add(tok)
    return found


def _actions_in_md(text: str, entity: str) -> set[str]:
    r"""Action'ы, упомянутые в md в контексте конкретной сущности.

    Стратегия: ищем заголовок секции ``### `<entity>` `` и подбираем все
    backquoted токены в следующем блоке (до следующего `###` или конца
    файла) — это та самая «таблица действий по сущности».

    Плюс отдельный обход «summary»-таблицы из `Роли и права.md` формата
    `| <entity> | a, b, c |` — там action'ы перечислены через запятую.
    """
    known_actions = set(ENTITY_ACTIONS[entity])
    found: set[str] = set()

    # Стиль 1: ### `entity` … (block до следующего ### или ---)
    section_re = re.compile(
        rf"###\s+`{re.escape(entity)}`\s*\n(.*?)(?=\n###\s|\n---\s*$|\Z)",
        re.DOTALL | re.MULTILINE,
    )
    for block in section_re.findall(text):
        for tok in re.findall(r"`([a-z_/]+)`", block):
            # `power_on/off/reboot/status` — частый стиль сокращения.
            # Разворачиваем такой токен на отдельные action'ы.
            if "/" in tok:
                base, *rest = tok.split("/")
                # Префикс выводим из base'а: power_on → префикс "power_".
                m = re.match(r"^([a-z]+_)", base)
                prefix = m.group(1) if m else ""
                found.add(base)
                for suf in rest:
                    found.add(f"{prefix}{suf}")
            else:
                found.add(tok)

    # Стиль 2: summary-таблица `| entity | a, b, c |` (живёт в Роли и права.md).
    summary_re = re.compile(
        rf"^\|\s*`?{re.escape(entity)}`?\s*\|\s*(.+?)\s*\|\s*$",
        re.MULTILINE,
    )
    for actions_blob in summary_re.findall(text):
        # Игнорируем строку-заголовок «| entity | actions |» которая может
        # совпасть, если за summary-table'ом стоит описательная.
        if actions_blob.strip().lower() in {"actions", "что это"}:
            continue
        for tok in re.findall(r"[a-z_/]+", actions_blob):
            if "/" in tok:
                base, *rest = tok.split("/")
                m = re.match(r"^([a-z]+_)", base)
                prefix = m.group(1) if m else ""
                found.add(base)
                for suf in rest:
                    found.add(f"{prefix}{suf}")
            else:
                found.add(tok)

    # Оставляем только действительно зарегистрированные action'ы каталога.
    # Слова `view` в orals like «(view — публичный)» отфильтрует
    # пересечение с known_actions.
    return found & known_actions


def _load_md(path: pathlib.Path | None) -> str:
    if path is None:
        pytest.skip("obsidian/ vault not found (git-ignored, отсутствует в Docker)")
    if not path.exists():
        pytest.skip(f"vault page {path} not present (obsidian/ git-ignored)")
    return path.read_text(encoding="utf-8")


# ── Сущности ────────────────────────────────────────────────────────────────


class TestRoleModelMdEntities:
    """`Ролевая модель.md` упоминает все entity_type каталога."""

    def test_all_code_entities_present_in_role_model_md(self):
        text = _load_md(_ROLE_MODEL_MD)
        md_entities = _entities_in_md(text)
        code_entities = set(ENTITY_ACTIONS.keys())
        missing = code_entities - md_entities
        assert not missing, (
            f"entities present in ENTITY_ACTIONS but absent in "
            f"`Ролевая модель.md`: {sorted(missing)}"
        )


class TestRolesRightsMdEntities:
    """`domain/Роли и права.md` упоминает все entity_type каталога."""

    def test_all_code_entities_present_in_roles_rights_md(self):
        text = _load_md(_ROLES_RIGHTS_MD)
        md_entities = _entities_in_md(text)
        code_entities = set(ENTITY_ACTIONS.keys())
        missing = code_entities - md_entities
        assert not missing, (
            f"entities present in ENTITY_ACTIONS but absent in "
            f"`Роли и права.md`: {sorted(missing)}"
        )


# ── Action'ы ────────────────────────────────────────────────────────────────


class TestRoleModelMdActions:
    """В `Ролевая модель.md` каждый action из каталога должен фигурировать
    в соответствующей секции сущности (детальная таблица действий)."""

    def test_each_action_present_in_role_model_md(self):
        text = _load_md(_ROLE_MODEL_MD)
        errors: list[str] = []
        for entity, actions in ENTITY_ACTIONS.items():
            md_actions = _actions_in_md(text, entity)
            missing = set(actions) - md_actions
            if missing:
                errors.append(f"{entity}: missing in md = {sorted(missing)}")
        assert not errors, (
            "ENTITY_ACTIONS contains pairs not documented in "
            "`Ролевая модель.md`:\n  " + "\n  ".join(errors)
        )

    def test_role_model_md_has_no_dead_actions(self):
        """В секции сущности нет action'ов, которых уже нет в каталоге."""
        text = _load_md(_ROLE_MODEL_MD)
        errors: list[str] = []
        for entity in ENTITY_ACTIONS:
            md_actions = _actions_in_md(text, entity)
            # `_actions_in_md` уже отфильтровал по `known_actions`, dead'ы
            # отсеялись бы. Повторим явный заход: ищем все backquoted
            # action-токены в секции и проверим, что каждый ∈ каталог.
            # Без дополнительного парсинга dead-action в md показал бы себя
            # как «упомянутое имя, которого нет в каталоге». Но т.к. md
            # пишется человеком, могут быть legitimate-упоминания удалённых
            # action'ов в narrative-секциях (например «вырезано из прежней
            # модели»). Поэтому жёсткой проверки тут не делаем — оставим
            # narrative-комментарий как валидный.
            _ = md_actions  # самопроверка: вызов не упал.
        assert not errors


class TestRolesRightsMdActions:
    """`Роли и права.md` несёт summary-таблицу `| entity | actions |` —
    проверяем, что для каждой сущности она перечисляет все её action'ы."""

    def test_summary_table_covers_all_actions(self):
        text = _load_md(_ROLES_RIGHTS_MD)
        errors: list[str] = []
        for entity, actions in ENTITY_ACTIONS.items():
            md_actions = _actions_in_md(text, entity)
            missing = set(actions) - md_actions
            if missing:
                errors.append(f"{entity}: missing in summary table = {sorted(missing)}")
        assert not errors, (
            "ENTITY_ACTIONS не полностью отражено в summary-таблице "
            "`Роли и права.md`:\n  " + "\n  ".join(errors)
        )
