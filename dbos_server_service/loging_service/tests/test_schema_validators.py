"""Unit-тесты схем `src/schemas/*` (Pydantic-валидация).

* `EventCreate` — required/литералы/details size validator, actor_type
  значения вне whitelist.
* `RuleCreate` — model_validator (`OVERRIDE_SEVERITY` ↔ `effect_severity`),
  диапазон `priority [1, 1000]`.
* `RegisterEventsRequest` — `events` `min_length=1`.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.schemas.events import EventCreate
from src.schemas.retention import RetentionPolicyCreate, RetentionPolicyUpdate
from src.schemas.rules import RuleCreate, RuleUpdate
from src.schemas.services import EventDefinition, RegisterEventsRequest


_BASE_EVENT = {
    "timestamp": datetime.now(timezone.utc),
    "service": "auth_service",
    "action": "user.login",
    "status": "success",
    "allowed": True,
}


# ── EventCreate ──────────────────────────────────────────────────────────────

class TestEventCreateRequired:
    @pytest.mark.parametrize("missing", ["timestamp", "service", "action", "status", "allowed"])
    def test_required_fields(self, missing: str):
        payload = {k: v for k, v in _BASE_EVENT.items() if k != missing}
        with pytest.raises(ValidationError):
            EventCreate(**payload)


class TestEventCreateActorType:
    @pytest.mark.parametrize("actor_type", ["user", "bot", "service", "anonymous"])
    def test_whitelisted_actor_types(self, actor_type: str):
        m = EventCreate(**_BASE_EVENT, actor_type=actor_type)
        assert m.actor_type == actor_type

    @pytest.mark.parametrize("bad", ["admin", "system", "operator", "", "User"])
    def test_outside_whitelist_rejected(self, bad: str):
        with pytest.raises(ValidationError):
            EventCreate(**_BASE_EVENT, actor_type=bad)


class TestEventCreateStatus:
    @pytest.mark.parametrize("status", ["success", "failure", "denied"])
    def test_whitelist(self, status: str):
        m = EventCreate(**{**_BASE_EVENT, "status": status})
        assert m.status == status

    @pytest.mark.parametrize("bad", ["ok", "fail", "blocked", "", "Success"])
    def test_invalid_status(self, bad: str):
        with pytest.raises(ValidationError):
            EventCreate(**{**_BASE_EVENT, "status": bad})


class TestEventCreateSeverity:
    @pytest.mark.parametrize("sev", ["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    def test_six_levels(self, sev: str):
        m = EventCreate(**_BASE_EVENT, severity=sev)
        assert m.severity == sev

    def test_none_severity_allowed(self):
        m = EventCreate(**_BASE_EVENT, severity=None)
        assert m.severity is None

    @pytest.mark.parametrize("bad", ["info", "FATAL", "OFF", ""])
    def test_invalid_severity(self, bad: str):
        with pytest.raises(ValidationError):
            EventCreate(**_BASE_EVENT, severity=bad)


class TestEventCreateLengths:
    def test_action_max_length(self):
        EventCreate(**{**_BASE_EVENT, "action": "a" * 128})
        with pytest.raises(ValidationError):
            EventCreate(**{**_BASE_EVENT, "action": "a" * 129})

    def test_service_max_length(self):
        EventCreate(**{**_BASE_EVENT, "service": "s" * 64})
        with pytest.raises(ValidationError):
            EventCreate(**{**_BASE_EVENT, "service": "s" * 65})

    def test_actor_id_max_length(self):
        EventCreate(**_BASE_EVENT, actor_id="x" * 48)
        with pytest.raises(ValidationError):
            EventCreate(**_BASE_EVENT, actor_id="x" * 49)


class TestEventCreateDetails:
    def test_default_empty_dict(self):
        m = EventCreate(**_BASE_EVENT)
        assert m.details == {}

    def test_small_details_accepted(self):
        EventCreate(**_BASE_EVENT, details={"key": "value", "n": 42})

    def test_oversized_details_rejected(self):
        oversized = {"k": "x" * 70_000}
        with pytest.raises(ValidationError) as exc:
            EventCreate(**_BASE_EVENT, details=oversized)
        assert "64 KB" in str(exc.value) or "details" in str(exc.value)

    def test_datetime_in_details_handled_by_default_str(self):
        """`json.dumps(..., default=str)` — datetime внутри details не падает."""
        EventCreate(**_BASE_EVENT, details={"when": datetime.now(timezone.utc)})


# ── EventCreate.details: shadow-key guard ────────────────────────────────────


class TestEventCreateDetailsShadowKeys:
    """Actor-identity top-level keys cannot appear inside ``details``.

    Scope narrowed: only ``actor_id`` and ``actor_type`` are
    impersonation-surface. Other top-level fields (``service``, ``action``,
    ``status``, ``department_id``, ``request_id``, ``severity``, ``event_id``,
    ``occurred_at``) are legitimately used as target/scope/context info in
    real audit records across all 4 services — see comment in
    ``schemas/events.py``.
    """

    @pytest.mark.parametrize("key", ["actor_id", "actor_type"])
    def test_top_level_reserved_key_rejected(self, key: str):
        with pytest.raises(ValidationError) as exc:
            EventCreate(**_BASE_EVENT, details={key: "x"})
        assert "reserved" in str(exc.value).lower()

    @pytest.mark.parametrize("variant", ["Actor_Id", "ACTOR_TYPE", "actor_ID"])
    def test_case_insensitive_reservation(self, variant: str):
        """Casing tricks (Actor_Id / ACTOR_TYPE) must not bypass."""
        with pytest.raises(ValidationError):
            EventCreate(**_BASE_EVENT, details={variant: "x"})

    def test_nested_reserved_key_rejected(self):
        """Recursive walk — reserved key at depth 2 is also rejected."""
        with pytest.raises(ValidationError):
            EventCreate(
                **_BASE_EVENT,
                details={"foo": "bar", "nested": {"actor_id": "x"}},
            )

    def test_deeply_nested_reserved_key_rejected(self):
        """Reserved key at depth 4 — still rejected."""
        with pytest.raises(ValidationError):
            EventCreate(
                **_BASE_EVENT,
                details={
                    "a": {"b": {"c": {"actor_type": "spoof"}}},
                },
            )

    def test_reserved_key_inside_list_dict_rejected(self):
        """List values containing dicts with reserved keys also rejected."""
        with pytest.raises(ValidationError):
            EventCreate(
                **_BASE_EVENT,
                details={"items": [{"actor_id": "spoof"}]},
            )

    def test_legitimate_keys_accepted(self):
        EventCreate(
            **_BASE_EVENT,
            details={"foo": "bar", "n": 42, "deep": {"k": [1, 2, 3]}},
        )

    @pytest.mark.parametrize("key", [
        "service", "action", "status", "request_id",
        "severity", "event_id", "occurred_at", "department_id",
    ])
    def test_context_keys_allowed(self, key: str):
        """Target/scope/context keys are accepted — they are not
        impersonation-surface (see scope decision in schemas/events.py)."""
        EventCreate(**_BASE_EVENT, details={key: "x"})


class TestEventCreateDetailsNulByteGuard:
    """Reject NUL bytes anywhere in keys / string values.

    PostgreSQL text columns reject ``\\x00`` (DataError), but JSONB stores
    them silently. Downstream CSV/COPY-TO exports either drop them (data
    corruption) or break — easier to reject at ingest.
    """

    def test_nul_in_string_value_rejected(self):
        with pytest.raises(ValidationError) as exc:
            EventCreate(
                **_BASE_EVENT,
                details={"foo": "bar\x00malicious"},
            )
        assert "NUL" in str(exc.value)

    def test_nul_in_nested_string_value_rejected(self):
        with pytest.raises(ValidationError):
            EventCreate(
                **_BASE_EVENT,
                details={"nested": {"key": "value\x00x"}},
            )

    def test_nul_in_key_rejected(self):
        with pytest.raises(ValidationError):
            EventCreate(
                **_BASE_EVENT,
                details={"foo\x00": "bar"},
            )

    def test_nul_in_list_string_rejected(self):
        with pytest.raises(ValidationError):
            EventCreate(
                **_BASE_EVENT,
                details={"items": ["a", "b\x00c"]},
            )


# ── EventCreate.request_id: charset validator ────────────────────────────────


class TestEventCreateRequestIdCharset:
    """``request_id`` is reflected back into ``X-Request-ID`` response header.

    Without charset restriction, ``request_id="abc\\r\\nSet-Cookie: hijack"``
    enables HTTP response splitting on vulnerable h11/uvicorn versions.
    """

    @pytest.mark.parametrize("rid", [
        "req_123",
        "abc-def-123",
        "ABCxyz_0",
        "a",
        "x" * 64,
        # Точка разрешена — конвенция `req.<id>` / `trace.<span>` встречается
        # у внешних клиентов; middleware пропускает её, схема симметрична.
        "abc.def",
        "req.123",
    ])
    def test_valid_request_id_accepted(self, rid: str):
        m = EventCreate(**_BASE_EVENT, request_id=rid)
        assert m.request_id == rid

    @pytest.mark.parametrize("rid", [
        "abc\r\nSet-Cookie: hijack",
        "abc\nXSS",
        "abc\rXSS",
        "abc def",   # space
        "abc/def",   # slash
        "abc:def",   # colon
        "абв",       # cyrillic
        "abc\x00xyz",  # NUL
        "x" * 65,    # too long
        "",          # empty (must be None or omitted, not empty string)
    ])
    def test_dangerous_request_id_rejected(self, rid: str):
        with pytest.raises(ValidationError):
            EventCreate(**_BASE_EVENT, request_id=rid)

    def test_none_request_id_accepted(self):
        m = EventCreate(**_BASE_EVENT, request_id=None)
        assert m.request_id is None


# ── EventCreate.idempotency_key ──────────────────────────────────────────────


class TestEventCreateIdempotencyKey:
    def test_default_none(self):
        m = EventCreate(**_BASE_EVENT)
        assert m.idempotency_key is None

    def test_explicit_set(self):
        m = EventCreate(**_BASE_EVENT, idempotency_key="batch-2026-001")
        assert m.idempotency_key == "batch-2026-001"

    def test_max_length_128(self):
        EventCreate(**_BASE_EVENT, idempotency_key="x" * 128)
        with pytest.raises(ValidationError):
            EventCreate(**_BASE_EVENT, idempotency_key="x" * 129)


# ── RuleCreate ───────────────────────────────────────────────────────────────

class TestRuleCreateEffectInvariant:
    def test_override_without_severity_rejected(self):
        with pytest.raises(ValidationError) as exc:
            RuleCreate(name="r", effect="OVERRIDE_SEVERITY")
        assert "effect_severity" in str(exc.value)

    def test_override_with_severity_passes(self):
        m = RuleCreate(name="r", effect="OVERRIDE_SEVERITY", effect_severity="CRITICAL")
        assert m.effect_severity == "CRITICAL"

    def test_suppress_with_severity_rejected(self):
        """`effect_severity` имеет смысл только для OVERRIDE — иначе ValidationError."""
        with pytest.raises(ValidationError):
            RuleCreate(name="r", effect="SUPPRESS", effect_severity="INFO")

    def test_allow_without_severity_passes(self):
        m = RuleCreate(name="r", effect="ALLOW")
        assert m.effect_severity is None

    def test_suppress_passes(self):
        m = RuleCreate(name="r", effect="SUPPRESS")
        assert m.effect == "SUPPRESS"


class TestRuleCreatePriorityBounds:
    @pytest.mark.parametrize("p", [1, 100, 1000])
    def test_valid_priority(self, p: int):
        m = RuleCreate(name="r", effect="ALLOW", priority=p)
        assert m.priority == p

    @pytest.mark.parametrize("p", [0, -1, 1001, 5000])
    def test_priority_out_of_range(self, p: int):
        with pytest.raises(ValidationError):
            RuleCreate(name="r", effect="ALLOW", priority=p)

    def test_default_priority_100(self):
        m = RuleCreate(name="r", effect="ALLOW")
        assert m.priority == 100


class TestRuleCreateLengths:
    def test_name_max_length(self):
        RuleCreate(name="n" * 128, effect="ALLOW")
        with pytest.raises(ValidationError):
            RuleCreate(name="n" * 129, effect="ALLOW")

    def test_match_action_max_length(self):
        RuleCreate(name="r", effect="ALLOW", match_action="a" * 128)
        with pytest.raises(ValidationError):
            RuleCreate(name="r", effect="ALLOW", match_action="a" * 129)


class TestRuleCreateLiterals:
    @pytest.mark.parametrize("effect", ["SUPPRESS", "ALLOW", "OVERRIDE_SEVERITY"])
    def test_effect_whitelist(self, effect: str):
        kw = {"effect_severity": "INFO"} if effect == "OVERRIDE_SEVERITY" else {}
        RuleCreate(name="r", effect=effect, **kw)

    @pytest.mark.parametrize("bad", ["suppress", "OVERRIDE", "DENY", ""])
    def test_invalid_effect_rejected(self, bad: str):
        with pytest.raises(ValidationError):
            RuleCreate(name="r", effect=bad)


# ── RuleUpdate ───────────────────────────────────────────────────────────────

class TestRuleUpdate:
    def test_empty_update_valid(self):
        """PATCH без полей допустим (no-op)."""
        m = RuleUpdate()
        assert m.model_dump(exclude_unset=True) == {}

    def test_partial_update(self):
        m = RuleUpdate(is_active=False)
        assert m.model_dump(exclude_unset=True) == {"is_active": False}

    def test_priority_bounds(self):
        with pytest.raises(ValidationError):
            RuleUpdate(priority=0)
        with pytest.raises(ValidationError):
            RuleUpdate(priority=1001)

    def test_update_does_not_enforce_override_invariant(self):
        """`RuleUpdate` НЕ имеет `_validate_effect` model_validator — это известный
        пробел: можно PATCH'ем поставить effect='OVERRIDE_SEVERITY' без
        effect_severity или наоборот."""
        m = RuleUpdate(effect="OVERRIDE_SEVERITY")
        assert m.effect == "OVERRIDE_SEVERITY"
        # effect_severity не задан — но schema-level не падает (валидация в endpoint).


# ── RegisterEventsRequest / EventDefinition ──────────────────────────────────

class TestRegisterEvents:
    def test_empty_list_rejected(self):
        with pytest.raises(ValidationError):
            RegisterEventsRequest(events=[])

    def test_single_event_accepted(self):
        m = RegisterEventsRequest(events=[EventDefinition(action="x.y")])
        assert len(m.events) == 1


class TestEventDefinition:
    def test_action_max_length(self):
        EventDefinition(action="a" * 128)
        with pytest.raises(ValidationError):
            EventDefinition(action="a" * 129)

    def test_description_max_length(self):
        EventDefinition(action="x", description="d" * 256)
        with pytest.raises(ValidationError):
            EventDefinition(action="x", description="d" * 257)


# ── RetentionPolicy ──────────────────────────────────────────────────────────

class TestRetentionPolicyBounds:
    @pytest.mark.parametrize("days", [30, 365, 3650])
    def test_valid_range(self, days: int):
        m = RetentionPolicyCreate(retain_days=days)
        assert m.retain_days == days

    @pytest.mark.parametrize("days", [0, 29, 3651, 10_000, -1])
    def test_out_of_range(self, days: int):
        with pytest.raises(ValidationError):
            RetentionPolicyCreate(retain_days=days)


class TestRetentionPolicyServiceFilter:
    """`service_filter` должен принимать ровно то же множество, что хранит
    `audit_events.service` после `normalize_service_name`. Иначе политика
    с uppercase / digits / `-` тихо не матчит ни одного события.
    """

    def test_uppercase_normalised_to_lowercase(self):
        m = RetentionPolicyCreate(
            retain_days=60, service_filter=["AUTH_SERVICE", "Server_Service"]
        )
        # NFKC + lower → сравнимо с ingest'ом.
        assert m.service_filter == ["auth_service", "server_service"]

    def test_cyrillic_confusable_folded_to_ascii(self):
        # `аuth_service` с кириллической `а` (U+0430) — атакующий мог бы
        # создать политику, никогда не матчащую настоящие события.
        m = RetentionPolicyCreate(
            retain_days=60, service_filter=["аuth_service"]
        )
        assert m.service_filter == ["auth_service"]

    def test_loging_service_in_filter_rejected(self):
        # `loging_service` защищён от ротации в `apply_active`. Политика с
        # ним в фильтре никогда не сработала бы — отбиваем на schema-уровне
        # (422), чтобы оператор увидел сразу, а не разбирался почему
        # «филтр зарегистрировался, но ничего не чистит».
        with pytest.raises(ValidationError) as excinfo:
            RetentionPolicyCreate(
                retain_days=60, service_filter=["loging_service"]
            )
        assert "protected" in str(excinfo.value)

    def test_loging_service_case_variant_also_rejected(self):
        # Нормализация case-fold'ит до `loging_service` ДО защиты-check'а,
        # поэтому uppercase / case-variant / confusable формы тоже банятся.
        with pytest.raises(ValidationError):
            RetentionPolicyCreate(
                retain_days=60, service_filter=["LoGiNg_SeRvIcE"]
            )
        with pytest.raises(ValidationError):
            # кириллическая `о` → ascii `o` через _CONFUSABLES_MAP.
            RetentionPolicyCreate(
                retain_days=60, service_filter=["lоging_service"]
            )

    def test_digits_rejected(self):
        # Ingest принимает только `[a-z_]`. Цифры тихо проскакивали
        # под прежним `[A-Za-z0-9._-]` и оседали в БД мёртвым весом.
        with pytest.raises(ValidationError):
            RetentionPolicyCreate(retain_days=60, service_filter=["auth2"])

    def test_dash_rejected(self):
        with pytest.raises(ValidationError):
            RetentionPolicyCreate(retain_days=60, service_filter=["auth-service"])

    def test_dot_rejected(self):
        with pytest.raises(ValidationError):
            RetentionPolicyCreate(retain_days=60, service_filter=["auth.service"])

    def test_space_rejected(self):
        with pytest.raises(ValidationError):
            RetentionPolicyCreate(retain_days=60, service_filter=["bad name"])

    def test_zero_width_space_stripped_to_valid_name(self):
        # ZWSP внутри имени — NFKC оставит его, но `_INVISIBLE_CHARS_RE`
        # удалит. Результат — каноническое `auth_service`.
        m = RetentionPolicyCreate(
            retain_days=60, service_filter=["auth​_service"]
        )
        assert m.service_filter == ["auth_service"]

    def test_dedup_after_normalisation(self):
        # Confusable + uppercase → одна и та же каноническая форма.
        m = RetentionPolicyCreate(
            retain_days=60,
            service_filter=["auth_service", "Auth_Service", "аuth_service"],
        )
        assert m.service_filter == ["auth_service"]

    def test_too_long_after_normalisation_rejected(self):
        with pytest.raises(ValidationError):
            RetentionPolicyCreate(retain_days=60, service_filter=["a" * 65])
