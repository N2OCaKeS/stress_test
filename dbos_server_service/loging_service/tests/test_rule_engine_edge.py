"""Edge-кейсы для rule engine — pure unit, без БД и кеша.

Покрывает `action_matches_pattern`, `_resolve_default_severity`,
`_matches` и `apply_rules` (через `_cache.get` monkeypatched).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.models.audit_rule import AuditRule
from src.schemas.events import EventCreate
from src.services import rule_service


def _event(**kw) -> EventCreate:
    data = {
        "service": "auth_service",
        "action": "user.login",
        "status": "success",
        "allowed": True,
        "actor_id": "usr_x",
        "actor_type": "user",
        "details": {},
        "timestamp": datetime.now(timezone.utc),
    }
    data.update(kw)
    return EventCreate(**data)


def _rule(*, effect="ALLOW", priority=100, match_service=None, match_action=None,
          match_status=None, match_severity=None, match_allowed=None,
          effect_severity=None) -> AuditRule:
    r = AuditRule(
        id="rl_test",
        name="t",
        effect=effect,
        priority=priority,
        match_service=match_service,
        match_action=match_action,
        match_status=match_status,
        match_severity=match_severity,
        match_allowed=match_allowed,
        effect_severity=effect_severity,
        is_active=True,
    )
    return r


# ── action_matches_pattern ───────────────────────────────────────────────────

class TestActionMatchesPattern:
    @pytest.mark.parametrize("action, pattern, expected", [
        ("user.login", "user.login", True),
        ("user.login", "user.*", True),
        ("user.create", "user.*", True),
        ("user.login.extra", "user.*", False),         # `*` — один сегмент
        ("user.login", "*.login", True),
        ("admin.login", "*.login", True),
        ("user.login", "*.*", True),
        ("user.create.audit", "user.*.audit", True),
        ("user.login", "user.*.login", False),         # три сегмента vs два
        ("user.login", "user", False),
        ("foo", "bar", False),
        ("foo", "*", True),
        ("foo.bar", "*", False),                       # `*` не пересекает точку
    ])
    def test_matches(self, action, pattern, expected):
        assert rule_service.action_matches_pattern(action, pattern) is expected

    def test_special_regex_chars_in_pattern_are_escaped(self):
        """Точка в pattern не должна работать как regex-метасимвол."""
        assert rule_service.action_matches_pattern("userxlogin", "user.login") is False
        assert rule_service.action_matches_pattern("user.login", "user.login") is True

    def test_dollar_sign_escaped(self):
        # `$` метасимвол — должен escape-нуться
        assert rule_service.action_matches_pattern("user$login", "user$login") is True

    def test_brackets_escaped(self):
        """Glob-символы `[a]` НЕ должны интерпретироваться как character class."""
        assert rule_service.action_matches_pattern("user.[abc].login", "user.[abc].login") is True
        assert rule_service.action_matches_pattern("user.a.login", "user.[abc].login") is False

    def test_empty_action_with_wildcard(self):
        # `*` требует ≥1 символ — пустой action не матчится
        assert rule_service.action_matches_pattern("", "*") is False


# ── _resolve_default_severity ────────────────────────────────────────────────

class TestResolveDefaultSeverity:
    def test_known_pair_uses_table(self):
        assert rule_service._resolve_default_severity("user.login", "success") == "INFO"

    def test_known_failure_critical(self):
        assert rule_service._resolve_default_severity("user.login", "failure") == "CRITICAL"

    def test_unknown_success_defaults_info(self):
        assert rule_service._resolve_default_severity("custom.action", "success") == "INFO"

    def test_unknown_failure_defaults_warning(self):
        assert rule_service._resolve_default_severity("custom.action", "failure") == "WARNING"

    def test_unknown_denied_defaults_warning(self):
        assert rule_service._resolve_default_severity("custom.action", "denied") == "WARNING"

    def test_unknown_status_falls_back_to_info(self):
        assert rule_service._resolve_default_severity("foo", "weird-status") == "INFO"


# ── _matches ─────────────────────────────────────────────────────────────────

class TestMatches:
    def test_no_match_criteria_matches_anything(self):
        """Правило без критериев должно матчиться на любое событие."""
        assert rule_service._matches(_rule(), _event()) is True

    def test_match_service(self):
        assert rule_service._matches(_rule(match_service="auth_service"), _event()) is True
        assert rule_service._matches(_rule(match_service="other"), _event()) is False

    def test_match_action_pattern(self):
        assert rule_service._matches(_rule(match_action="user.*"), _event()) is True
        assert rule_service._matches(_rule(match_action="bot.*"), _event()) is False

    def test_match_status(self):
        assert rule_service._matches(_rule(match_status="success"), _event()) is True
        assert rule_service._matches(_rule(match_status="failure"), _event()) is False

    def test_match_severity_null_matches_anything(self):
        """`match_severity=None` — это «не фильтровать», матч любого severity."""
        assert rule_service._matches(_rule(match_severity=None), _event(severity="INFO")) is True

    def test_match_severity_specific(self):
        assert rule_service._matches(
            _rule(match_severity="CRITICAL"),
            _event(severity="INFO"),
        ) is False

    def test_match_allowed_null_skipped(self):
        assert rule_service._matches(_rule(match_allowed=None), _event(allowed=False)) is True

    def test_match_allowed_filters(self):
        assert rule_service._matches(_rule(match_allowed=True), _event(allowed=False)) is False


# ── apply_rules с моками кеша ────────────────────────────────────────────────

@pytest.fixture
def cache_stub(monkeypatch):
    rules_storage: list[AuditRule] = []

    def fake_get(_self, _db):
        return rules_storage

    monkeypatch.setattr(rule_service._cache, "get", fake_get.__get__(rule_service._cache))

    class _Ctl:
        def set_rules(self, *rules: AuditRule):
            rules_storage.clear()
            rules_storage.extend(rules)
    return _Ctl()


class TestApplyRules:
    def test_no_rules_event_passes_through_with_default_severity(self, cache_stub):
        cache_stub.set_rules()
        out = rule_service.apply_rules(None, _event())
        assert out is not None
        assert out.severity == "INFO"

    def test_override_severity_applied(self, cache_stub):
        cache_stub.set_rules(_rule(
            effect="OVERRIDE_SEVERITY", effect_severity="CRITICAL",
            match_action="user.login",
        ))
        out = rule_service.apply_rules(None, _event())
        assert out.severity == "CRITICAL"

    def test_suppress_returns_none(self, cache_stub):
        cache_stub.set_rules(_rule(effect="SUPPRESS", match_action="user.login"))
        assert rule_service.apply_rules(None, _event()) is None

    def test_allow_returns_event_immediately(self, cache_stub):
        cache_stub.set_rules(
            _rule(effect="ALLOW", priority=100, match_action="user.login"),
            _rule(effect="SUPPRESS", priority=50, match_action="user.login"),  # не должно сработать
        )
        out = rule_service.apply_rules(None, _event())
        assert out is not None

    def test_override_chain_picks_last_match(self, cache_stub):
        """Цепочка ≥2 OVERRIDE — последний матч даёт финальный severity."""
        cache_stub.set_rules(
            _rule(effect="OVERRIDE_SEVERITY", effect_severity="WARNING", match_action="user.login"),
            _rule(effect="OVERRIDE_SEVERITY", effect_severity="CRITICAL", match_action="user.login"),
        )
        out = rule_service.apply_rules(None, _event())
        assert out.severity == "CRITICAL"

    def test_override_with_null_effect_severity_is_ignored(self, cache_stub):
        cache_stub.set_rules(_rule(
            effect="OVERRIDE_SEVERITY", effect_severity=None, match_action="user.login",
        ))
        out = rule_service.apply_rules(None, _event())
        # default INFO осталось
        assert out.severity == "INFO"

    def test_explicit_severity_kept_when_no_override(self, cache_stub):
        cache_stub.set_rules()
        out = rule_service.apply_rules(None, _event(severity="ERROR"))
        assert out.severity == "ERROR"

    def test_apply_rules_does_not_mutate_input(self, cache_stub):
        """`model_copy` гарантирует, что input event не меняется."""
        cache_stub.set_rules(_rule(
            effect="OVERRIDE_SEVERITY", effect_severity="CRITICAL", match_action="user.login",
        ))
        original = _event()
        before_severity = original.severity
        rule_service.apply_rules(None, original)
        assert original.severity == before_severity

    def test_suppress_after_override_still_suppresses(self, cache_stub):
        cache_stub.set_rules(
            _rule(effect="OVERRIDE_SEVERITY", effect_severity="ERROR", match_action="user.login"),
            _rule(effect="SUPPRESS", match_action="user.login"),
        )
        assert rule_service.apply_rules(None, _event()) is None

    def test_non_matching_rules_dont_affect_event(self, cache_stub):
        cache_stub.set_rules(_rule(
            effect="SUPPRESS", match_action="bot.token_create",
        ))
        out = rule_service.apply_rules(None, _event())
        assert out is not None and out.severity == "INFO"


# ── defence-in-depth: self-audit loging_service всегда обходит правила ───────


class TestApplyRulesLogingServiceEarlyReturn:
    """`apply_rules` для `service='loging_service'` возвращает payload без
    каких-либо изменений — даже при существовании SUPPRESS/OVERRIDE-правил.

    Закрывает «admin создал SUPPRESS match_service='loging_service' и отключил
    весь собственный аудит» (defence-in-depth).
    """

    def test_suppress_rule_does_not_apply_to_loging_service(self, cache_stub):
        """SUPPRESS match_service='loging_service' не должен подавлять
        self-audit, даже если кто-то по ошибке вызовет `record()` вместо
        `record_admin_action()`."""
        cache_stub.set_rules(_rule(effect="SUPPRESS", match_service="loging_service"))
        payload = _event(service="loging_service", action="logging.admin_access")
        out = rule_service.apply_rules(None, payload)
        assert out is not None
        # payload должен вернуться буквально как пришёл (без severity-дефолта)
        assert out is payload

    def test_wildcard_suppress_does_not_apply_to_loging_service(self, cache_stub):
        """SUPPRESS match_action='*' тоже не должен подавлять self-audit."""
        cache_stub.set_rules(_rule(effect="SUPPRESS", match_action="logging.*"))
        payload = _event(service="loging_service", action="logging.events_queried")
        assert rule_service.apply_rules(None, payload) is payload

    def test_override_severity_does_not_apply_to_loging_service(self, cache_stub):
        """OVERRIDE_SEVERITY не должен изменять severity self-audit-события."""
        cache_stub.set_rules(_rule(
            effect="OVERRIDE_SEVERITY", effect_severity="TRACE",
            match_service="loging_service",
        ))
        payload = _event(
            service="loging_service",
            action="logging.admin_access",
            severity="CRITICAL",
        )
        out = rule_service.apply_rules(None, payload)
        # severity не тронут — payload отдан как есть
        assert out is payload
        assert out.severity == "CRITICAL"

    def test_loging_service_with_severity_none_returned_as_is(self, cache_stub):
        """Even severity=None НЕ резолвится через _DEFAULT_SEVERITY — early-return
        отдаёт payload буквально. Severity-резолюция возложена на
        `record_admin_action`, который и есть единственный легальный
        писатель self-audit."""
        cache_stub.set_rules()
        payload = _event(
            service="loging_service",
            action="logging.events_queried",
            severity=None,
        )
        out = rule_service.apply_rules(None, payload)
        assert out is payload
        assert out.severity is None

    def test_other_services_still_processed(self, cache_stub):
        """Sanity: ранний return срабатывает только для loging_service."""
        cache_stub.set_rules(_rule(effect="SUPPRESS", match_service="auth_service"))
        payload = _event(service="auth_service", action="user.login")
        assert rule_service.apply_rules(None, payload) is None
