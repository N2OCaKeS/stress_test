"""GET /rules SELECT/COUNT ограничены statement_timeout.

Симметричный гард с `events.query`: SELECT и COUNT в `rules.get_all` /
`get_by_id` бегут под `SET LOCAL statement_timeout = :ms` в SAVEPOINT'е,
на превышении (`57014 query_canceled`) repo возвращает пустую страницу /
total=0 / None — без 500 наружу.
"""

from __future__ import annotations

from sqlalchemy import text

from src.core.config import get_settings
from src.models.audit_rule import AuditRule
from src.repositories import rules as rules_repo

from tests._helpers import insert_rule as _insert_rule_base


def _insert_rule(db, name: str = "rule-timeout") -> AuditRule:
    return _insert_rule_base(db, name=name)


def test_get_all_applies_statement_timeout(db, monkeypatch):
    """`SET LOCAL statement_timeout` отрабатывает с значением из настроек."""
    monkeypatch.setenv("AUDIT_QUERY_STATEMENT_TIMEOUT_MS", "9123")
    get_settings.cache_clear()
    try:
        _insert_rule(db)

        captured: list[str] = []
        original_execute = db.execute

        def spy(clause, *args, **kwargs):
            result = original_execute(clause, *args, **kwargs)
            sql_text = str(getattr(clause, "text", clause))
            if "set local statement_timeout" in sql_text.lower():
                captured.append(
                    original_execute(text("SHOW statement_timeout")).scalar_one()
                )
            return result

        monkeypatch.setattr(db, "execute", spy)

        rules, total = rules_repo.get_all(db)
        assert total == 1
        assert len(rules) == 1
        # Должно быть как минимум 2 SET LOCAL: один на COUNT, один на SELECT.
        assert captured.count("9123ms") >= 2
    finally:
        get_settings.cache_clear()


def test_get_by_id_applies_statement_timeout(db, monkeypatch):
    monkeypatch.setenv("AUDIT_QUERY_STATEMENT_TIMEOUT_MS", "1234")
    get_settings.cache_clear()
    try:
        rule = _insert_rule(db, name="rule-by-id-timeout")

        captured: list[str] = []
        original_execute = db.execute

        def spy(clause, *args, **kwargs):
            result = original_execute(clause, *args, **kwargs)
            sql_text = str(getattr(clause, "text", clause))
            if "set local statement_timeout" in sql_text.lower():
                captured.append(
                    original_execute(text("SHOW statement_timeout")).scalar_one()
                )
            return result

        monkeypatch.setattr(db, "execute", spy)

        found = rules_repo.get_by_id(db, rule.id)
        assert found is not None
        assert found.id == rule.id
        assert "1234ms" in captured
    finally:
        get_settings.cache_clear()


def test_get_all_timeout_disabled_when_zero(db, monkeypatch):
    """`audit_query_statement_timeout_ms=0` отключает обёртку — нет SET LOCAL."""
    monkeypatch.setenv("AUDIT_QUERY_STATEMENT_TIMEOUT_MS", "0")
    get_settings.cache_clear()
    try:
        _insert_rule(db, name="rule-no-timeout")

        captured: list[str] = []
        original_execute = db.execute

        def spy(clause, *args, **kwargs):
            sql_text = str(getattr(clause, "text", clause))
            if "set local statement_timeout" in sql_text.lower():
                captured.append(sql_text)
            return original_execute(clause, *args, **kwargs)

        monkeypatch.setattr(db, "execute", spy)

        rules, total = rules_repo.get_all(db)
        assert total == 1
        assert len(rules) == 1
        assert captured == []
    finally:
        get_settings.cache_clear()
