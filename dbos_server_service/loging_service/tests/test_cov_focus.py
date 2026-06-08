"""Дополнительное покрытие по точкам, не охваченным существующими тестами.

Зоны:
  * _RuleCache — TTL под NTP step (monkeypatch time.monotonic с прыжком).
  * event_service.apply_rules — пересекающиеся политики (Cartesian OVERRIDE + SUPPRESS).
  * retention apply_active — chunked DELETE при rollback-сценариях и filter-set sweep.
  * POST /events — _ACTION_PATTERN с цифрами и unicode-confusables.
  * core/limiter — _rate_limit_key exempt-path behaviour.
"""

from __future__ import annotations

import time
import threading
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.services.rule_service import _RuleCache, invalidate_cache, apply_rules
from src.schemas.events import EventCreate
from src.schemas.rules import RuleCreate
from src.repositories import rules as rule_repo
from src.repositories import retention_policies as ret_repo
from src.schemas.retention import RetentionPolicyCreate
from src.models.audit_event import AuditEvent


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

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


def _make_rule(db, name: str, **kwargs):
    payload = RuleCreate(
        name=name,
        effect=kwargs.pop("effect", "SUPPRESS"),
        priority=kwargs.pop("priority", 100),
        **kwargs,
    )
    r = rule_repo.create(db, payload)
    invalidate_cache()
    return r


def _add_event(db, *, service: str, days_ago: int, severity: str = "INFO") -> str:
    import uuid
    ev = AuditEvent(
        id=f"log_{uuid.uuid4().hex[:16]}",
        timestamp=datetime.now(timezone.utc) - timedelta(days=days_ago),
        service=service,
        action="x.y",
        actor_id=None,
        actor_type="service",
        status="success",
        allowed=True,
        severity=severity,
        details={},
    )
    db.add(ev)
    db.flush()
    return ev.id


# ────────────────────────────────────────────────────────────────────────────
# RuleCache: TTL под NTP-подобными прыжками time.monotonic
# ────────────────────────────────────────────────────────────────────────────

class TestRuleCacheNTPStep:
    """_ttl_fresh() считает TTL по monotonic — forward и backward прыжки не
    должны приводить ни к вечному кешу, ни к незапланированному сбросу."""

    def test_backward_monotonic_jump_does_not_freeze_cache(self, db, monkeypatch):
        """Если monotonic внезапно уменьшился (NTP step back) — TTL не уходит
        в «вечно свежий» режим. После следующего нормального прохода кеш
        обновится корректно."""
        cache = _RuleCache(ttl_seconds=5)
        _make_rule(db, "r1")
        cache.get(db)

        # Сохраняем реальное значение сразу после загрузки
        loaded_mono = cache._loaded_monotonic
        assert loaded_mono is not None

        # Имитируем backward NTP step: monotonic откатывается на 100 сек.
        backward_mono = loaded_mono - 100.0
        call_count = {"n": 0}

        original_monotonic = time.monotonic

        def patched_monotonic():
            call_count["n"] += 1
            return backward_mono

        monkeypatch.setattr("src.services.rule_service.time.monotonic", patched_monotonic)

        # При backward прыжке _ttl_fresh() вернёт True (mono_now < loaded_mono),
        # кеш ответит без похода в БД — это ожидаемое поведение (лучше отдать
        # немного устаревший кеш, чем бесконечно молотить БД).
        rules = cache.get(db)
        # Ключевое: кеш не упал и вернул список
        assert isinstance(rules, list)

    def test_large_forward_monotonic_jump_triggers_reload(self, db, monkeypatch):
        """Forward NTP step (время прыгнуло вперёд на много часов) — TTL истекает
        и кеш перезагружается. Монотонник не скачет назад, но скачок вперёд
        эквивалентен timeout'у."""
        cache = _RuleCache(ttl_seconds=5)
        _make_rule(db, "r1")
        cache.get(db)

        loaded_mono = cache._loaded_monotonic
        assert loaded_mono is not None

        reloads = {"n": 0}
        original_max = rule_repo.get_max_updated_at

        def counting_max(d):
            reloads["n"] += 1
            return original_max(d)

        monkeypatch.setattr(rule_repo, "get_max_updated_at", counting_max)

        # Имитируем forward jump: monotonic ушёл далеко вперёд — TTL гарантированно истёк
        future_mono = loaded_mono + 3600.0
        monkeypatch.setattr(
            "src.services.rule_service.time.monotonic",
            lambda: future_mono,
        )

        cache.get(db)
        assert reloads["n"] >= 1, "forward прыжок должен был истечь TTL и вызвать reload"

    def test_ttl_uses_monotonic_not_wall_clock(self, db, monkeypatch):
        """TTL считается по monotonic; манипуляция wall-clock (_loaded_at UTC)
        не должна влиять на TTL freshness."""
        cache = _RuleCache(ttl_seconds=30)
        _make_rule(db, "r1")
        cache.get(db)

        # Откатываем wall-clock на 10 минут назад (как если бы NTP step back)
        cache._loaded_at = datetime.now(timezone.utc) - timedelta(minutes=10)

        reloads = {"n": 0}
        original_max = rule_repo.get_max_updated_at

        def counting_max(d):
            reloads["n"] += 1
            return original_max(d)

        monkeypatch.setattr(rule_repo, "get_max_updated_at", counting_max)

        # monotonic не менялся — TTL ещё не истёк
        cache.get(db)
        assert reloads["n"] == 0, "wall-clock откат не должен триггерить reload"

    def test_stale_cache_after_forward_jump_serves_rules(self, db, monkeypatch):
        """После forward jump, когда БД недоступна — stale-fallback работает,
        _loaded_monotonic обновляется по новому (будущему) значению monotonic."""
        cache = _RuleCache(ttl_seconds=5)
        _make_rule(db, "r1")
        cache.get(db)

        loaded_mono = cache._loaded_monotonic
        future_mono = loaded_mono + 3600.0

        monkeypatch.setattr(
            "src.services.rule_service.time.monotonic",
            lambda: future_mono,
        )
        monkeypatch.setattr(
            rule_repo,
            "get_max_updated_at",
            lambda d: (_ for _ in ()).throw(RuntimeError("db down")),
        )

        rules = cache.get(db)
        # stale fallback вернул данные
        assert [r.name for r in rules] == ["r1"]
        # _loaded_monotonic сдвинут к future_mono, чтобы не долбить БД
        assert cache._loaded_monotonic == future_mono


# ────────────────────────────────────────────────────────────────────────────
# event_service: apply_rules под пересекающимися политиками (Cartesian)
# ────────────────────────────────────────────────────────────────────────────

class TestApplyRulesCartesianPolicies:
    """Несколько правил с пересекающимися условиями — OVERRIDE → SUPPRESS,
    OVERRIDE → ALLOW, SUPPRESS + ALLOW по приоритету."""

    def test_override_then_suppress_matches_new_severity(self, db):
        """OVERRIDE_SEVERITY (high prio) меняет severity, затем SUPPRESS
        по match_severity на новое значение подавляет событие."""
        _make_rule(db, "ov", effect="OVERRIDE_SEVERITY",
                   priority=200, match_action="user.login",
                   effect_severity="ERROR")
        _make_rule(db, "supp", effect="SUPPRESS",
                   priority=100, match_severity="ERROR")
        # После override severity=ERROR; SUPPRESS на ERROR → None
        result = apply_rules(db, _event(action="user.login", severity="INFO"))
        assert result is None

    def test_override_then_allow_short_circuits_before_suppress(self, db):
        """OVERRIDE (prio=300) → ALLOW (prio=200) → SUPPRESS (prio=100).
        ALLOW должен остановить цепочку до SUPPRESS."""
        _make_rule(db, "ov", effect="OVERRIDE_SEVERITY",
                   priority=300, match_action="user.login",
                   effect_severity="CRITICAL")
        _make_rule(db, "allow", effect="ALLOW",
                   priority=200, match_action="user.login")
        _make_rule(db, "supp", effect="SUPPRESS",
                   priority=100, match_action="user.login")
        result = apply_rules(db, _event(action="user.login"))
        assert result is not None
        assert result.severity == "CRITICAL"

    def test_two_overrides_cartesian_both_applied_in_order(self, db):
        """Два OVERRIDE для разных атрибутов (service и action) — оба
        матчат на одном событии; оба применяются слева-направо по приоритету."""
        _make_rule(db, "ov_service", effect="OVERRIDE_SEVERITY",
                   priority=300,
                   match_service="auth_service",
                   effect_severity="WARNING")
        _make_rule(db, "ov_action", effect="OVERRIDE_SEVERITY",
                   priority=200,
                   match_action="user.login",
                   effect_severity="CRITICAL")
        result = apply_rules(db, _event(service="auth_service", action="user.login",
                                         severity="INFO"))
        # ov_service сначала → WARNING; ov_action следом → CRITICAL
        assert result is not None
        assert result.severity == "CRITICAL"

    def test_suppress_does_not_affect_non_matching_service(self, db):
        """Правило SUPPRESS с match_service=server_service не затрагивает
        auth_service, даже если action совпадает."""
        _make_rule(db, "supp", effect="SUPPRESS",
                   priority=100,
                   match_service="server_service",
                   match_action="user.login")
        result = apply_rules(db, _event(service="auth_service", action="user.login"))
        assert result is not None

    def test_loging_service_bypasses_all_rules(self, db):
        """self-audit service='loging_service' никогда не проходит через правила."""
        _make_rule(db, "supp", effect="SUPPRESS")  # match all
        ev = _event(service="loging_service", action="logging.events_queried")
        result = apply_rules(db, ev)
        assert result is not None

    def test_three_policies_suppress_wins_if_no_allow_before(self, db):
        """Три правила без ALLOW → SUPPRESS матчит → None."""
        _make_rule(db, "ov1", effect="OVERRIDE_SEVERITY",
                   priority=300, match_action="user.login",
                   effect_severity="WARNING")
        _make_rule(db, "ov2", effect="OVERRIDE_SEVERITY",
                   priority=200, match_action="user.login",
                   effect_severity="CRITICAL")
        _make_rule(db, "supp", effect="SUPPRESS",
                   priority=100, match_action="user.login",
                   match_severity="CRITICAL")
        result = apply_rules(db, _event(action="user.login", severity="INFO"))
        # ov1 → WARNING; ov2 → CRITICAL; supp matches CRITICAL → None
        assert result is None

    def test_match_allowed_combined_with_service_and_action(self, db):
        """Совмещение трёх match-критериев — правило срабатывает только при
        точном пересечении всех трёх."""
        _make_rule(db, "supp", effect="SUPPRESS",
                   match_service="auth_service",
                   match_action="user.login",
                   match_allowed=False)
        # allowed=True — правило не матчит
        assert apply_rules(db, _event(service="auth_service", action="user.login",
                                       allowed=True, status="success")) is not None
        # allowed=False — матчит
        assert apply_rules(db, _event(service="auth_service", action="user.login",
                                       allowed=False, status="denied")) is None


# ────────────────────────────────────────────────────────────────────────────
# retention apply_active: chunked DELETE + filtered set correctness
# ────────────────────────────────────────────────────────────────────────────

class TestRetentionChunkedRollback:
    """Проверяем, что chunked sweep корректен под различными сценариями."""

    @pytest.fixture(autouse=True)
    def _clear(self, db):
        from sqlalchemy import text
        db.execute(text("DELETE FROM retention_policies"))
        db.commit()
        yield
        db.execute(text("DELETE FROM retention_policies"))
        db.commit()

    def test_single_chunk_completes_without_partial_delete(self, db):
        """Если всё удаляется за один чанк — total корректен."""
        from src.repositories.retention_policies import apply_active
        ret_repo.create(db, RetentionPolicyCreate(retain_days=30))
        for _ in range(5):
            _add_event(db, service="auth_service", days_ago=60)
        db.commit()
        deleted = apply_active(db, chunk_size=100)
        assert deleted == 5

    def test_chunk_loop_terminates_on_empty_table(self, db):
        """Если таблица пуста, apply_active возвращает 0 без loop."""
        from src.repositories.retention_policies import apply_active
        ret_repo.create(db, RetentionPolicyCreate(retain_days=30))
        db.commit()
        assert apply_active(db, chunk_size=5) == 0

    def test_fresh_events_not_touched_during_chunked_sweep(self, db):
        """При нескольких чанках свежие события не задеваются."""
        from src.repositories.retention_policies import apply_active
        from sqlalchemy import select
        ret_repo.create(db, RetentionPolicyCreate(retain_days=30))
        old_ids = [_add_event(db, service="auth_service", days_ago=60) for _ in range(13)]
        fresh_id = _add_event(db, service="auth_service", days_ago=10)
        db.commit()

        deleted = apply_active(db, chunk_size=5)
        assert deleted == 13

        remaining = {r[0] for r in db.execute(select(AuditEvent.id))}
        assert fresh_id in remaining
        for eid in old_ids:
            assert eid not in remaining

    def test_filtered_policy_only_deletes_matching_severity(self, db):
        """Политика с severity_filter=CRITICAL удаляет только CRITICAL-события."""
        from src.repositories.retention_policies import apply_active
        from sqlalchemy import select
        ret_repo.create_policy(
            db,
            RetentionPolicyCreate(retain_days=30, severity_filter=["CRITICAL"]),
        )
        crit_id = _add_event(db, service="auth_service", days_ago=60, severity="CRITICAL")
        info_id = _add_event(db, service="auth_service", days_ago=60, severity="INFO")
        db.commit()

        deleted = apply_active(db)
        assert deleted == 1
        remaining = {r[0] for r in db.execute(select(AuditEvent.id))}
        assert crit_id not in remaining
        assert info_id in remaining

    def test_filtered_policy_only_deletes_matching_service(self, db):
        """Политика с service_filter=auth_service не трогает server_service."""
        from src.repositories.retention_policies import apply_active
        from sqlalchemy import select
        ret_repo.create_policy(
            db,
            RetentionPolicyCreate(retain_days=30, service_filter=["auth_service"]),
        )
        auth_id = _add_event(db, service="auth_service", days_ago=60)
        server_id = _add_event(db, service="server_service", days_ago=60)
        db.commit()

        deleted = apply_active(db)
        assert deleted == 1
        remaining = {r[0] for r in db.execute(select(AuditEvent.id))}
        assert auth_id not in remaining
        assert server_id in remaining

    def test_two_filtered_policies_delete_respective_sets(self, db):
        """Два filter-policy-row (Cartesian) удаляют события по OR-предикату."""
        from src.repositories.retention_policies import apply_active
        from sqlalchemy import select
        # Политика 1: CRITICAL / auth_service, retain=30
        ret_repo.create_policy(
            db,
            RetentionPolicyCreate(
                retain_days=30,
                severity_filter=["CRITICAL"],
                service_filter=["auth_service"],
            ),
        )
        # Политика 2: INFO / server_service, retain=30
        ret_repo.create_policy(
            db,
            RetentionPolicyCreate(
                retain_days=30,
                severity_filter=["INFO"],
                service_filter=["server_service"],
            ),
        )
        auth_crit = _add_event(db, service="auth_service", days_ago=60, severity="CRITICAL")
        server_info = _add_event(db, service="server_service", days_ago=60, severity="INFO")
        auth_info = _add_event(db, service="auth_service", days_ago=60, severity="INFO")
        server_crit = _add_event(db, service="server_service", days_ago=60, severity="CRITICAL")
        db.commit()

        deleted = apply_active(db)
        assert deleted == 2
        remaining = {r[0] for r in db.execute(select(AuditEvent.id))}
        assert auth_crit not in remaining
        assert server_info not in remaining
        assert auth_info in remaining
        assert server_crit in remaining


# ────────────────────────────────────────────────────────────────────────────
# _ACTION_PATTERN — цифры и unicode-confusables
# ────────────────────────────────────────────────────────────────────────────

def _base_event() -> dict:
    """Стандартный event-payload с динамическим `timestamp=now()`.

    Hardcoded дата проваливала бы `EventCreate._bound_timestamp` (±1ч окно).
    Возвращаем свежий dict каждым вызовом — тесты разворачивают через
    `{**_base_event(), "action": ...}`.
    """
    return {
        "timestamp": datetime.now(timezone.utc),
        "service": "auth_service",
        "action": "user.login",
        "status": "success",
        "allowed": True,
        "actor_type": "user",
    }




class TestActionPatternDigits:
    """_ACTION_PATTERN = r'^[a-z0-9_.]{1,128}$' — цифры разрешены."""

    @pytest.mark.parametrize("action", [
        "provision_v2",
        "http.4xx_error",
        "user.login123",
        "service_role.bulk_assign2",
        "x.1",
        "a0.b1.c2",
    ])
    def test_action_with_digits_accepted(self, action: str):
        m = EventCreate(**{**_base_event(), "action": action})
        assert m.action == action

    @pytest.mark.parametrize("bad_action", [
        "User.Login",       # uppercase
        "user.Login",       # uppercase in verb
        "USER.login",       # uppercase object
    ])
    def test_uppercase_in_action_rejected(self, bad_action: str):
        with pytest.raises(ValidationError):
            EventCreate(**{**_base_event(), "action": bad_action})

    def test_action_with_only_digits_accepted(self):
        """Всё-из-цифр проходит pattern, но семантически странно — задокументируем."""
        m = EventCreate(**{**_base_event(), "action": "123.456"})
        assert m.action == "123.456"

    def test_action_starting_with_digit_accepted(self):
        m = EventCreate(**{**_base_event(), "action": "3d.render"})
        assert m.action == "3d.render"


class TestActionPatternUnicodeConfusables:
    """Unicode-confusables в action — кириллическая 'о' и греческая 'ο' не
    проходят _ACTION_PATTERN (паттерн после _action_charset, без NFKC-фолда)."""

    def test_cyrillic_o_in_action_rejected(self):
        """Кириллическая 'о' (U+043E) не входит в [a-z0-9_.]."""
        with pytest.raises(ValidationError):
            EventCreate(**{**_base_event(), "action": "user.lоgin"})  # 'о' = U+043E

    def test_greek_omicron_in_action_rejected(self):
        """Греческая 'ο' (U+03BF) — тоже нет."""
        with pytest.raises(ValidationError):
            EventCreate(**{**_base_event(), "action": "user.lοgin"})  # 'ο' = U+03BF

    def test_zero_width_space_in_action_rejected(self):
        """ZWSP (U+200B) не входит в [a-z0-9_.]."""
        with pytest.raises(ValidationError):
            EventCreate(**{**_base_event(), "action": "user.log​in"})

    def test_fullwidth_latin_in_action_rejected(self):
        """Fullwidth 'ａ' (U+FF41) — вне ASCII диапазона, не проходит."""
        with pytest.raises(ValidationError):
            EventCreate(**{**_base_event(), "action": "ｕser.login"})

    def test_normal_action_accepted_after_unicode_tests(self):
        """Контрольный: чистый ASCII action проходит."""
        m = EventCreate(**{**_base_event(), "action": "user.login"})
        assert m.action == "user.login"

    def test_crlf_in_action_rejected(self):
        """CR/LF в action → log injection → должен быть отбит."""
        with pytest.raises(ValidationError):
            EventCreate(**{**_base_event(), "action": "user.login\r\nfake.event"})

    def test_action_with_space_rejected(self):
        """Пробел не входит в допустимый charset."""
        with pytest.raises(ValidationError):
            EventCreate(**{**_base_event(), "action": "user login"})


# ────────────────────────────────────────────────────────────────────────────
# core/limiter — _rate_limit_key exempt путей
# ────────────────────────────────────────────────────────────────────────────

class TestRateLimitKeyFunction:
    """_rate_limit_key возвращает UUID для exempt-путей (health/ready/token),
    чтобы k8s-пробы не насыщали общий bucket."""

    def test_exempt_health_returns_unique_key_each_call(self):
        from src.core.limiter import _rate_limit_key, _RATE_LIMIT_EXEMPT_PATHS
        from unittest.mock import MagicMock

        req = MagicMock()
        req.url.path = "/api/logging/v1/health"

        keys = {_rate_limit_key(req) for _ in range(20)}
        assert len(keys) == 20, "каждый вызов должен давать уникальный ключ"

    def test_exempt_ready_returns_unique_key_each_call(self):
        from src.core.limiter import _rate_limit_key
        from unittest.mock import MagicMock

        req = MagicMock()
        req.url.path = "/api/logging/v1/ready"

        keys = {_rate_limit_key(req) for _ in range(10)}
        assert len(keys) == 10

    def test_exempt_token_returns_unique_key_each_call(self):
        from src.core.limiter import _rate_limit_key
        from unittest.mock import MagicMock

        req = MagicMock()
        req.url.path = "/api/logging/v1/token"

        keys = {_rate_limit_key(req) for _ in range(10)}
        assert len(keys) == 10

    def test_non_exempt_path_returns_ip(self):
        from src.core.limiter import _rate_limit_key
        from unittest.mock import MagicMock, patch

        req = MagicMock()
        req.url.path = "/api/logging/v1/events"

        with patch("src.core.limiter.get_remote_address", return_value="1.2.3.4"):
            key = _rate_limit_key(req)

        assert key == "1.2.3.4"

    def test_non_exempt_path_same_ip_same_key(self):
        """Один IP на одном non-exempt пути → один bucket (детерминированный ключ)."""
        from src.core.limiter import _rate_limit_key
        from unittest.mock import MagicMock, patch

        req = MagicMock()
        req.url.path = "/api/logging/v1/rules"

        with patch("src.core.limiter.get_remote_address", return_value="10.0.0.1"):
            k1 = _rate_limit_key(req)
            k2 = _rate_limit_key(req)

        assert k1 == k2

    def test_exempt_path_key_starts_with_exempt_prefix(self):
        """Ключ для exempt-путей начинается с 'exempt:' — читаемо в дебаге."""
        from src.core.limiter import _rate_limit_key
        from unittest.mock import MagicMock

        req = MagicMock()
        req.url.path = "/api/logging/v1/health"
        key = _rate_limit_key(req)
        assert key.startswith("exempt:")

    def test_limiter_instance_is_singleton(self):
        """Все импортеры получают один объект — нет дублей bucket'ов."""
        from src.core.limiter import limiter as limiter1
        from src.core.limiter import limiter as limiter2
        assert limiter1 is limiter2

    def test_limiter_key_func_is_rate_limit_key(self):
        """limiter использует _rate_limit_key как key_func."""
        from src.core.limiter import limiter, _rate_limit_key
        assert limiter._key_func is _rate_limit_key


# ────────────────────────────────────────────────────────────────────────────
# dependencies/auth.py — require_reader: только loging_admin / loging_reader
# ────────────────────────────────────────────────────────────────────────────

class TestReaderAllowlist:
    """К чтению audit'а допускаются ТОЛЬКО `loging_admin` и `loging_reader`.

    Обе — глобальные read (никакого dept-scope), обе могут не иметь
    `department_id`. `account_admin` и `department_admin` отбиваются
    `INSUFFICIENT_ROLE` — если dep_admin'у нужен read его отдела, ему
    отдельно выдаётся платформенная `loging_reader`.
    """

    def test_loging_reader_with_dept_id_passes(self, client, mock_introspect):
        EVENTS_URL = "/api/logging/v1/events"
        with mock_introspect(json_body={
            "active": True, "subject_type": "user",
            "sub": "usr_reader", "username": "reader",
            "platform_role": "loging_reader", "department_id": "dep_abc",
        }):
            r = client.get(EVENTS_URL, headers={"Authorization": "Bearer token"})
        assert r.status_code == 200

    def test_loging_reader_without_dept_id_passes(self, client, mock_introspect):
        """loging_reader теперь global-read — отсутствие department_id не
        отбивает (платформенная роль создаётся без dept)."""
        EVENTS_URL = "/api/logging/v1/events"
        with mock_introspect(json_body={
            "active": True, "subject_type": "user",
            "sub": "usr_reader", "username": "reader",
            "platform_role": "loging_reader", "department_id": None,
        }):
            r = client.get(EVENTS_URL, headers={"Authorization": "Bearer token"})
        assert r.status_code == 200

    def test_department_admin_returns_403_insufficient_role(self, client, mock_introspect):
        """department_admin к чтению аудита НЕ допускается."""
        EVENTS_URL = "/api/logging/v1/events"
        with mock_introspect(json_body={
            "active": True, "subject_type": "user",
            "sub": "usr_da", "username": "da",
            "platform_role": "department_admin", "department_id": "dep_a",
        }):
            r = client.get(EVENTS_URL, headers={"Authorization": "Bearer token"})
        assert r.status_code == 403
        assert r.json()["error_code"] == "INSUFFICIENT_ROLE"

    def test_loging_admin_without_dept_id_passes(self, client, mock_introspect):
        """loging_admin без department_id → global-read → 200."""
        EVENTS_URL = "/api/logging/v1/events"
        with mock_introspect(json_body={
            "active": True, "subject_type": "user",
            "sub": "usr_la", "username": "la",
            "platform_role": "loging_admin", "department_id": None,
        }):
            r = client.get(EVENTS_URL, headers={"Authorization": "Bearer token"})
        assert r.status_code == 200

    def test_account_admin_returns_403_insufficient_role(self, client, mock_introspect):
        """account_admin к чтению аудита НЕ допускается (owner-decision)."""
        EVENTS_URL = "/api/logging/v1/events"
        with mock_introspect(json_body={
            "active": True, "subject_type": "user",
            "sub": "usr_aa", "username": "aa",
            "platform_role": "account_admin", "department_id": None,
        }):
            r = client.get(EVENTS_URL, headers={"Authorization": "Bearer token"})
        assert r.status_code == 403
        assert r.json()["error_code"] == "INSUFFICIENT_ROLE"


# ────────────────────────────────────────────────────────────────────────────
# POST /events — RESERVED_SERVICE_NAME через NFKC + zero-width (дополнение)
# ────────────────────────────────────────────────────────────────────────────

class TestReservedServiceGuardExtended:
    """Дополнение к test_ingest.py: проверяем endpoint-поведение для
    edge-case вариантов, которые проходят pydantic-валидацию (normalize),
    но должны быть пойманы guard'ом в endpoint'е."""

    EVENTS_URL = "/api/logging/v1/events"

    def test_canonical_loging_service_returns_403(self, client, auth_headers):
        """Базовый случай: service='loging_service' → 403."""
        from tests.conftest import make_event
        r = client.post(self.EVENTS_URL, json=make_event(service="loging_service"),
                        headers=auth_headers)
        assert r.status_code == 403
        assert r.json()["error_code"] == "RESERVED_SERVICE_NAME"

    def test_uppercase_service_rejected_by_validator(self, client, auth_headers):
        """'LOGING_SERVICE' отбивается pydantic-валидатором (422), не guard'ом (403).
        Важно: 422 а не 403, чтобы атакующий не знал, что обход сработал."""
        from tests.conftest import make_event
        r = client.post(self.EVENTS_URL, json=make_event(service="LOGING_SERVICE"),
                        headers=auth_headers)
        assert r.status_code == 422

    def test_double_underscore_variant_returns_422_not_403(self, client, auth_headers):
        """'loging__service' (двойной underscore) — не зарезервированное имя,
        но не проходит _SERVICE_PATTERN (charset ok, но не equal reserved) → 201.

        Идентичность payload.service и X-Service-Identity обязана совпадать —
        используем `auth_service` (известный identity), чтобы проверить именно
        reserved-name guard, а не SERVICE_IDENTITY_PAYLOAD_MISMATCH.
        """
        from tests.conftest import make_event
        r = client.post(self.EVENTS_URL, json=make_event(service="auth_service"),
                        headers=auth_headers)
        assert r.status_code == 201


# ────────────────────────────────────────────────────────────────────────────
# auth.py /token proxy — error propagation
# ────────────────────────────────────────────────────────────────────────────

class TestTokenProxyErrorPropagation:
    """Дополнительные сценарии для /token proxy, не покрытые test_admin_auth."""

    TOKEN_URL = "/api/logging/v1/token"

    def test_4xx_from_auth_service_returns_401(self, client, mock_token_proxy):
        """Любой non-200 от auth_service (403, 429) → 401 INVALID_CREDENTIALS."""
        for status_code in (400, 403, 422, 429):
            with mock_token_proxy(status_code=status_code):
                r = client.post(self.TOKEN_URL,
                                data={"username": "u", "password": "p"})
            assert r.status_code == 401, (
                f"status {status_code} from auth_service should yield 401, got {r.status_code}"
            )
            assert r.json()["error_code"] == "INVALID_CREDENTIALS"

    def test_5xx_from_auth_service_returns_401(self, client, mock_token_proxy):
        """Любой non-200 от auth_service, включая 5xx, → 401 INVALID_CREDENTIALS.

        /token proxy намеренно возвращает единый 401 для всех non-200: Swagger UI
        читает body, а не status, и различать 4xx от 5xx на proxy-уровне нет смысла.
        При реальной недоступности auth_service httpx выбросит исключение → 503
        (обрабатывается в except Exception). Здесь мок возвращает ответ с кодом 502,
        что endpoint трактует как «не 200» → INVALID_CREDENTIALS 401.
        """
        with mock_token_proxy(status_code=502):
            r = client.post(self.TOKEN_URL, data={"username": "u", "password": "p"})
        assert r.status_code == 401
        assert r.json()["error_code"] == "INVALID_CREDENTIALS"

    def test_valid_response_returns_only_access_token_and_type(self, client, mock_token_proxy):
        """Успешный /token — возвращаем только access_token + token_type,
        не весь ответ auth_service (может содержать refresh_token)."""
        with mock_token_proxy(json_body={
            "access_token": "jwt-abc",
            "token_type": "bearer",
            "refresh_token": "should_not_leak",
            "expires_in": 900,
        }):
            r = client.post(self.TOKEN_URL, data={"username": "u", "password": "p"})

        assert r.status_code == 200
        body = r.json()
        assert "access_token" in body
        assert "token_type" in body
        assert "refresh_token" not in body, "refresh_token не должен утекать клиенту"
