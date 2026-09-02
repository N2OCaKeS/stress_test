"""Человеческие описания сущностей и действий матрицы прав.

Состав (какие сущности и какие действия существуют) задаёт
:data:`src.core.constants.ENTITY_ACTIONS` — это источник истины. Здесь только
текстовые описания плюс пометка «чувствительное», которые отдаются
read-эндпоинтом каталога для UI и ИБ-обзора.

Описания обязаны покрывать каждую пару `(entity_type, action)` из
`ENTITY_ACTIONS`; недостающее описание ловит smoke-тест каталога.
"""

from __future__ import annotations

from src.core.constants import EntityType, SecretAction

ENTITY_DESCRIPTIONS: dict[str, str] = {
    EntityType.SECRET: (
        "Учётные данные для внешней системы (Jira, Confluence, Git и т.п.): "
        "логин и секрет с областью действия personal / department / "
        "cross_department. Матрица управляет только неличными (department) "
        "секретами отдела-владельца; личные секреты остаются доступны лишь "
        "владельцу."
    ),
}

ACTION_DESCRIPTIONS: dict[str, str] = {
    SecretAction.READ: "Видеть карточку и листинг секрета без раскрытия значения.",
    SecretAction.REVEAL: "Раскрыть значение секрета (reveal).",
    SecretAction.WRITE: "Создать или изменить секрет (имя, логин, значение).",
    SecretAction.DELETE: "Удалить секрет.",
    SecretAction.GRANT_ACL: "Выдать или снять per-credential доступ роли (RoleACL).",
    SecretAction.GRANT_DEPT: (
        "Раздать секрет другому отделу (DeptGrant) в рамках cross_department."
    ),
    SecretAction.MANAGE_STATUS: "Блокировать или восстанавливать секрет (управление статусом).",
}

# Чувствительные действия — раскрытие значения и привилегированное управление
# кред'ой. Аудит уровня CRITICAL; в системных дефолтных грантах их несёт только
# admin, кастомным ролям — прицельно. Метаданные (`read`) и `write` в этот
# набор не входят: `read` не раскрывает значение, `write` — обычная правка.
SENSITIVE_ACTIONS: frozenset[str] = frozenset({
    SecretAction.REVEAL,
    SecretAction.DELETE,
    SecretAction.GRANT_ACL,
    SecretAction.GRANT_DEPT,
    SecretAction.MANAGE_STATUS,
})
