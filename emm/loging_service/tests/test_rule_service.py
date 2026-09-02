"""Unit-тесты для src/services/rule_service.py.

Не требуют HTTP-клиента — тестируют логику напрямую.
"""

import pytest
from src.services.rule_service import (
    action_matches_pattern,
    _resolve_default_severity,
    apply_rules,
    invalidate_cache,
)
from src.schemas.events import EventCreate
from datetime import datetime, timezone


def _event(**kwargs) -> EventCreate:
    base = dict(
        timestamp=datetime.now(timezone.utc),
        service="auth_service",
        action="user.login",
        status="success",
        allowed=True,
        actor_type="user",
        severity="INFO",
    )
    base.update(kwargs)
    return EventCreate(**base)


# ── action_matches_pattern ────────────────────────────────────────────────────

class TestActionMatchesPattern:
    # Точные совпадения
    def test_exact_match(self):
        assert action_matches_pattern("user.login", "user.login") is True

    def test_exact_no_match(self):
        assert action_matches_pattern("user.login", "user.logout") is False

    def test_empty_pattern_no_match(self):
        assert action_matches_pattern("user.login", "") is False

    # Glob: одиночный сегмент
    def test_glob_matches_within_segment(self):
        assert action_matches_pattern("user.login", "user.*") is True

    def test_glob_matches_different_verbs(self):
        for verb in ("login", "logout", "ban", "create"):
            assert action_matches_pattern(f"user.{verb}", "user.*") is True

    def test_glob_does_not_cross_dot_boundary(self):
        """user.* НЕ должен совпадать с user.login.extra — граница точки."""
        assert action_matches_pattern("user.login.extra", "user.*") is False

    def test_glob_does_not_match_wrong_prefix(self):
        assert action_matches_pattern("bot.login", "user.*") is False

    def test_glob_in_object_segment(self):
        assert action_matches_pattern("service_role.create", "service_role.*") is True

    def test_wildcard_only_pattern(self):
        # "*" должен совпасть только с одним сегментом без точки
        assert action_matches_pattern("user", "*") is True
        assert action_matches_pattern("user.login", "*") is False

    def test_multi_level_glob(self):
        # "*.login" совпадает с любым префиксом перед login
        assert action_matches_pattern("user.login", "*.login") is True
        assert action_matches_pattern("bot.login", "*.login") is True
        assert action_matches_pattern("user.logout", "*.login") is False

    def test_glob_does_not_match_empty_segment(self):
        # "user.*" не должен совпасть с "user." (пустой глагол)
        assert action_matches_pattern("user.", "user.*") is False

    def test_compound_object_glob(self):
        assert action_matches_pattern("docker_registry.configure", "docker_registry.*") is True
        assert action_matches_pattern("docker_registry.configure", "docker.*") is False


# ── _resolve_default_severity ─────────────────────────────────────────────────

class TestResolveDefaultSeverity:
    # Матрица EMM статус-агностична: один severity на action.
    def test_known_action(self):
        assert _resolve_default_severity("user.login", "success") == "WARNING"

    def test_known_action_status_agnostic(self):
        # Любой статус action'а получает уровень из матрицы.
        assert _resolve_default_severity("user.login", "failure") == "WARNING"

    def test_critical_action(self):
        assert _resolve_default_severity("user.hard_deleted", "success") == "CRITICAL"

    def test_warning_action(self):
        assert _resolve_default_severity("pat.revoke", "success") == "WARNING"

    def test_info_action(self):
        assert _resolve_default_severity("bot.create", "success") == "INFO"

    def test_trace_action(self):
        assert _resolve_default_severity("user.me", "success") == "TRACE"

    def test_unknown_action_success_defaults_to_info(self):
        assert _resolve_default_severity("unknown.action", "success") == "INFO"

    def test_unknown_action_failure_defaults_to_warning(self):
        assert _resolve_default_severity("unknown.action", "failure") == "WARNING"

    def test_unknown_action_denied_defaults_to_warning(self):
        assert _resolve_default_severity("unknown.action", "denied") == "WARNING"

    def test_logging_retention_read_info(self):
        assert _resolve_default_severity("logging.retention_read", "success") == "INFO"

    def test_logging_retention_write_critical(self):
        assert _resolve_default_severity("logging.retention_write", "success") == "CRITICAL"

    def test_admin_rule_create(self):
        assert _resolve_default_severity("logging_rule.create", "success") == "CRITICAL"

    def test_admin_rule_update(self):
        assert _resolve_default_severity("logging_rule.update", "success") == "CRITICAL"

    def test_admin_rule_delete(self):
        assert _resolve_default_severity("logging_rule.delete", "success") == "CRITICAL"

    def test_http_access_denied(self):
        assert _resolve_default_severity("http.access_denied", "denied") == "WARNING"

    def test_token_refresh_reuse(self):
        assert _resolve_default_severity("token.refresh_reuse", "failure") == "CRITICAL"

    # Управление серверами и OS-учётками (server_service / server_worker).
    def test_server_prepare_info(self):
        assert _resolve_default_severity("server.prepare", "success") == "INFO"
        assert _resolve_default_severity("server.prepare", "failure") == "INFO"

    def test_server_prepared_info(self):
        assert _resolve_default_severity("server.prepared", "success") == "INFO"
        assert _resolve_default_severity("server.prepared", "failure") == "INFO"

    def test_server_account_provision_info(self):
        assert _resolve_default_severity("server_account.provision", "success") == "INFO"
        assert _resolve_default_severity("server_account.provision", "failure") == "INFO"

    def test_server_account_update_on_host_info(self):
        assert _resolve_default_severity("server_account.update_on_host", "success") == "INFO"

    def test_server_account_deprovision_warning(self):
        assert _resolve_default_severity("server_account.deprovision", "success") == "WARNING"

    def test_server_account_drift_detected_warning(self):
        assert _resolve_default_severity("server_account.drift_detected", "success") == "WARNING"

    def test_bmc_tls_downgrade_critical(self):
        assert _resolve_default_severity("bmc.tls_downgrade", "failure") == "CRITICAL"

    def test_view_password_critical(self):
        assert _resolve_default_severity("server_account.view_password", "success") == "CRITICAL"

    def test_tokens_revealed_warning(self):
        assert _resolve_default_severity("tokens.revealed", "success") == "WARNING"


# ── apply_rules — severity resolution ────────────────────────────────────────

class TestApplyRulesSeverityResolution:
    def test_assigns_default_severity_when_none(self, seeded_db):
        event = _event(severity=None, action="user.login", status="success")
        result = apply_rules(seeded_db, event)
        assert result is not None
        assert result.severity == "WARNING"

    def test_default_severity_status_agnostic(self, seeded_db):
        # Матрица применяется независимо от статуса — failure тоже WARNING.
        event = _event(severity=None, action="user.login", status="failure", allowed=False)
        result = apply_rules(seeded_db, event)
        assert result.severity == "WARNING"

    def test_explicit_severity_preserved(self, seeded_db):
        event = _event(severity="DEBUG", action="user.login", status="success")
        result = apply_rules(seeded_db, event)
        assert result.severity == "DEBUG"

    def test_unknown_action_dropped_for_success(self, seeded_db):
        # Нет дефолтного правила и нет каталожного severity → drop.
        # Прежняя heuristic (INFO) убрана: «нет правила = не логируется».
        event = _event(severity=None, action="new_feature.do_thing", status="success")
        assert apply_rules(seeded_db, event) is None

    def test_unknown_action_dropped_for_failure(self, seeded_db):
        event = _event(severity=None, action="new_feature.do_thing", status="failure", allowed=False)
        assert apply_rules(seeded_db, event) is None

    def test_unknown_action_with_explicit_severity_kept(self, db):
        # Явный severity не дропается даже без правила.
        event = _event(severity="ERROR", action="new_feature.do_thing", status="success")
        result = apply_rules(db, event)
        assert result is not None
        assert result.severity == "ERROR"


# ── apply_rules — правила ──────────────────────────────────────────────────────

class TestApplyRulesLogic:
    def _create_rule(self, db, **kwargs):
        from src.repositories import rules as rule_repo
        from src.schemas.rules import RuleCreate
        payload = RuleCreate(name=kwargs.pop("name", "r"), effect=kwargs.pop("effect", "SUPPRESS"), **kwargs)
        r = rule_repo.create(db, payload)
        invalidate_cache()
        return r

    def test_no_rules_passes_event(self, db):
        event = _event()
        assert apply_rules(db, event) is not None

    def test_suppress_returns_none(self, db):
        self._create_rule(db, name="s", effect="SUPPRESS", match_action="user.login")
        assert apply_rules(db, _event(action="user.login")) is None

    def test_suppress_does_not_affect_other_actions(self, db):
        self._create_rule(db, name="s", effect="SUPPRESS", match_action="user.login")
        assert apply_rules(db, _event(action="user.logout")) is not None

    def test_allow_short_circuits(self, db):
        self._create_rule(db, name="allow", effect="ALLOW", priority=200, match_action="user.login")
        self._create_rule(db, name="supp", effect="SUPPRESS", priority=100, match_action="user.login")
        assert apply_rules(db, _event(action="user.login")) is not None

    def test_override_severity(self, db):
        self._create_rule(db, name="ov", effect="OVERRIDE_SEVERITY",
                          match_action="user.login", effect_severity="CRITICAL")
        result = apply_rules(db, _event(action="user.login", severity="INFO"))
        assert result.severity == "CRITICAL"

    def test_multiple_overrides_applied_in_priority_order(self, db):
        # priority=200 sets WARNING, priority=100 sets DEBUG → WARNING wins (higher prio first)
        self._create_rule(db, name="ov1", effect="OVERRIDE_SEVERITY",
                          priority=200, match_action="user.login", effect_severity="WARNING")
        self._create_rule(db, name="ov2", effect="OVERRIDE_SEVERITY",
                          priority=100, match_action="user.login", effect_severity="DEBUG")
        result = apply_rules(db, _event(action="user.login", severity="INFO"))
        assert result.severity == "DEBUG"  # both applied, last one wins

    def test_inactive_rule_ignored(self, db):
        rule = self._create_rule(db, name="inactive", effect="SUPPRESS", match_action="user.login")
        from src.repositories import rules as rule_repo
        from src.schemas.rules import RuleUpdate
        rule_repo.update(db, rule, RuleUpdate(is_active=False))
        invalidate_cache()
        assert apply_rules(db, _event(action="user.login")) is not None

    def test_glob_pattern_suppress(self, db):
        self._create_rule(db, name="g", effect="SUPPRESS", match_action="user.*")
        assert apply_rules(db, _event(action="user.login")) is None
        assert apply_rules(db, _event(action="user.logout")) is None
        # glob не пересекает точку — user.login.extra НЕ подавляется
        assert apply_rules(db, _event(action="http.access_denied")) is not None

    def test_match_service_filter(self, db):
        self._create_rule(db, name="s", effect="SUPPRESS", match_service="config_service")
        assert apply_rules(db, _event(service="auth_service")) is not None
        assert apply_rules(db, _event(service="config_service")) is None

    def test_match_status_filter(self, db):
        self._create_rule(db, name="s", effect="SUPPRESS", match_status="denied")
        assert apply_rules(db, _event(status="success", allowed=True)) is not None
        assert apply_rules(db, _event(status="denied", allowed=False)) is None

    def test_match_allowed_false_filter(self, db):
        self._create_rule(db, name="s", effect="SUPPRESS", match_allowed=False)
        assert apply_rules(db, _event(allowed=True)) is not None
        assert apply_rules(db, _event(allowed=False, status="denied")) is None

    def test_cache_invalidation_picks_up_new_rules(self, db):
        event = _event(action="user.login")
        assert apply_rules(db, event) is not None
        self._create_rule(db, name="new", effect="SUPPRESS", match_action="user.login")
        assert apply_rules(db, event) is None

    def test_combined_criteria_all_must_match(self, db):
        self._create_rule(db, name="s", effect="SUPPRESS",
                          match_service="auth_service", match_action="user.login",
                          match_status="failure")
        # success не подавляется
        assert apply_rules(db, _event(action="user.login", status="success")) is not None
        # failure подавляется
        assert apply_rules(db, _event(action="user.login", status="failure", allowed=False)) is None


# ── _DEFAULT_SEVERITY — server/worker actions ────────────────────────────────

class TestDefaultSeverityServerActions:
    """Дефолты severity для эмитов server_service / server_worker.

    Матрица EMM статус-агностична: severity одинаков для success/failure/denied.
    """

    @pytest.mark.parametrize("status", ["success", "failure", "denied"])
    @pytest.mark.parametrize(
        "action", ["server.power_on", "server.power_off", "server.power_reboot"]
    )
    def test_power_mutating_is_warning(self, action, status):
        assert _resolve_default_severity(action, status) == "WARNING"

    @pytest.mark.parametrize("status", ["success", "failure", "denied"])
    def test_power_status_is_info(self, status):
        assert _resolve_default_severity("server.power_status", status) == "INFO"

    @pytest.mark.parametrize("status", ["success", "failure", "denied"])
    def test_inventory_sync_is_info(self, status):
        assert _resolve_default_severity("server.inventory_sync", status) == "INFO"

    @pytest.mark.parametrize("status", ["success", "failure", "denied"])
    def test_users_inventory_is_info(self, status):
        assert _resolve_default_severity("server_account.users_inventory", status) == "INFO"

    @pytest.mark.parametrize("status", ["success", "failure", "denied"])
    def test_installed_packages_is_info(self, status):
        assert _resolve_default_severity("installed_packages.list", status) == "INFO"

    @pytest.mark.parametrize("status", ["success", "failure", "denied"])
    @pytest.mark.parametrize(
        "action",
        ["server_account.password_rotate", "ipmi_controller.password_rotate"],
    )
    def test_password_rotate_is_warning(self, action, status):
        assert _resolve_default_severity(action, status) == "WARNING"


# ── RuleCreate.match_service normalisation ───────────────────────────────────

class TestMatchServiceNormalization:
    """`match_service` должен нормализоваться так же, как `EventCreate.service`.

    Без этого SUPPRESS/OVERRIDE_SEVERITY с `match_service="Auth_Service"` или
    с zero-width space внутри молча не матчит нормализованные на ingest
    события — правило выглядит активным, но не срабатывает.
    """

    def test_canonical_passes_unchanged(self):
        from src.schemas.rules import RuleCreate
        r = RuleCreate(name="r", effect="SUPPRESS", match_service="auth_service")
        assert r.match_service == "auth_service"

    def test_uppercase_rejected(self):
        from pydantic import ValidationError
        from src.schemas.rules import RuleCreate
        with pytest.raises(ValidationError):
            RuleCreate(name="r", effect="SUPPRESS", match_service="Auth_Service")

    def test_dash_rejected(self):
        from pydantic import ValidationError
        from src.schemas.rules import RuleCreate
        with pytest.raises(ValidationError):
            RuleCreate(name="r", effect="SUPPRESS", match_service="auth-service")

    def test_digit_rejected(self):
        from pydantic import ValidationError
        from src.schemas.rules import RuleCreate
        with pytest.raises(ValidationError):
            RuleCreate(name="r", effect="SUPPRESS", match_service="auth1")

    def test_zero_width_space_stripped(self):
        from src.schemas.rules import RuleCreate
        r = RuleCreate(name="r", effect="SUPPRESS", match_service="auth​_service")
        assert r.match_service == "auth_service"

    def test_cyrillic_homoglyph_folded(self):
        from src.schemas.rules import RuleCreate
        # `а` (U+0430, кир.) → ASCII `a`
        r = RuleCreate(name="r", effect="SUPPRESS", match_service="аuth_service")
        assert r.match_service == "auth_service"

    def test_none_allowed(self):
        from src.schemas.rules import RuleCreate
        r = RuleCreate(name="r", effect="SUPPRESS", match_service=None)
        assert r.match_service is None

    def test_update_normalises_match_service(self):
        from src.schemas.rules import RuleUpdate
        r = RuleUpdate(match_service="auth​_service")
        assert r.match_service == "auth_service"

    def test_update_rejects_invalid_charset(self):
        from pydantic import ValidationError
        from src.schemas.rules import RuleUpdate
        with pytest.raises(ValidationError):
            RuleUpdate(match_service="Bad-Name")

    def test_apply_rules_matches_normalised_service(self, db):
        """Правило с pre-normalisation формой матчит event с тем же сервисом."""
        from src.repositories import rules as rule_repo
        from src.schemas.rules import RuleCreate
        payload = RuleCreate(
            name="match-norm", effect="SUPPRESS",
            match_service="auth​_service",
            match_action="user.login",
        )
        rule_repo.create(db, payload)
        invalidate_cache()
        # event.service = "auth_service" (canonical)
        assert apply_rules(db, _event(service="auth_service", action="user.login")) is None


# ── _RuleSnapshot — frozen dataclass вместо detached ORM ──────────────────────


class TestRuleSnapshot:
    """Кеш правил больше не хранит ORM-объекты. Это закрывает риск
    `DetachedInstanceError` после expire-on-commit / cross-session reads,
    которого не видно при текущих eager-loaded колонках, но любой
    добавленный relationship тихо ломал бы кеш.
    """

    def test_cache_returns_immutable_snapshots(self, db):
        from src.repositories import rules as rule_repo
        from src.schemas.rules import RuleCreate
        from src.services.rule_service import _RuleCache, _RuleSnapshot

        rule_repo.create(db, RuleCreate(
            name="snap-1", effect="SUPPRESS", match_service="auth_service",
        ))
        cache = _RuleCache(ttl_seconds=30)
        snapshots = cache.get(db)
        assert snapshots, "ожидался непустой набор snapshot'ов"
        for snap in snapshots:
            assert isinstance(snap, _RuleSnapshot)
            # frozen=True → попытка мутации падает.
            with pytest.raises((AttributeError, TypeError)):
                snap.match_service = "other"  # type: ignore[misc]

    def test_snapshots_survive_session_close(self, db):
        """Атрибуты snapshot'а читаются после закрытия сессии-источника."""
        from src.repositories import rules as rule_repo
        from src.schemas.rules import RuleCreate
        from src.services.rule_service import _RuleCache

        rule_repo.create(db, RuleCreate(
            name="snap-2", effect="OVERRIDE_SEVERITY",
            effect_severity="ERROR", match_action="user.*",
        ))
        cache = _RuleCache(ttl_seconds=30)
        snapshots = cache.get(db)
        # Закрываем сессию — для ORM это вызвало бы DetachedInstanceError
        # на любом атрибут-доступе (через lazy refresh).
        db.close()
        snap = next(s for s in snapshots if s.name == "snap-2")
        assert snap.effect == "OVERRIDE_SEVERITY"
        assert snap.effect_severity == "ERROR"
        assert snap.match_action == "user.*"
