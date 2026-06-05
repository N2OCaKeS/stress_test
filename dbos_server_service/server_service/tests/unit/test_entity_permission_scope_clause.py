"""Юнит-тест на `_scope_clause` из `repositories/entity_permission.py`.

Функция формирует WHERE-условие для точечного scope-лукапа в матрице
прав. `department_id is None` → `EntityPermission.department_id IS NULL`,
иначе — равенство по столбцу. При рефакторинге репозитория легко
перевести условие в противоположный матч (например, перепутать
`is_(None)` и `is_not(None)`) — без отдельного теста такая ошибка
ловится только косвенно через интеграцию.
"""

from __future__ import annotations

from sqlalchemy.sql import operators

from src.models import EntityPermission
from src.repositories.entity_permission import _scope_clause


def _column_name(expr):
    """`clause.left` отдаёт `Column`-объект (не InstrumentedAttribute);
    сравниваем по имени столбца, чтобы не зависеть от ORM-instrumentation.
    """
    return getattr(expr, "name", None) or str(expr).split(".")[-1]


class TestScopeClause:
    def test_none_yields_is_null(self):
        clause = _scope_clause(None)
        compiled = str(clause.compile(compile_kwargs={"literal_binds": True}))
        assert "entity_permissions.department_id IS NULL" in compiled

    def test_department_yields_equality(self):
        clause = _scope_clause("dep_abc123")
        compiled = str(clause.compile(compile_kwargs={"literal_binds": True}))
        assert "entity_permissions.department_id" in compiled
        assert "'dep_abc123'" in compiled

    def test_none_references_correct_column(self):
        clause = _scope_clause(None)
        assert _column_name(clause.left) == "department_id"
        assert clause.left.table is EntityPermission.__table__

    def test_dept_references_correct_column(self):
        clause = _scope_clause("dep_xyz")
        assert _column_name(clause.left) == "department_id"
        assert clause.left.table is EntityPermission.__table__
        assert clause.operator is operators.eq
