"""Unit-тесты src/utils/ids.py: префиксы ID для каждой сущности."""

import re

from src.utils.ids import (
    _new_id,
    audit_event_id,
    audit_rule_id,
    service_event_id,
)


_HEX32 = re.compile(r"^[0-9a-f]{32}$")


class TestAuditEventId:
    def test_starts_with_log_prefix(self):
        assert audit_event_id().startswith("log_")

    def test_format_is_prefix_plus_hex32(self):
        body = audit_event_id().removeprefix("log_")
        assert _HEX32.fullmatch(body)

    def test_unique(self):
        ids = {audit_event_id() for _ in range(1000)}
        assert len(ids) == 1000


class TestAuditRuleId:
    def test_starts_with_rl_prefix(self):
        assert audit_rule_id().startswith("rl_")

    def test_unique(self):
        assert len({audit_rule_id() for _ in range(1000)}) == 1000


class TestServiceEventId:
    def test_starts_with_se_prefix(self):
        assert service_event_id().startswith("se_")

    def test_unique(self):
        assert len({service_event_id() for _ in range(1000)}) == 1000


class TestRetentionPolicyId:
    def test_uses_rp_prefix(self):
        # _retention_policy_id живёт в models/retention_policy.py
        from src.models.retention_policy import _retention_policy_id
        assert _retention_policy_id().startswith("rp_")
        body = _retention_policy_id().removeprefix("rp_")
        assert _HEX32.fullmatch(body)


class TestNewIdHelper:
    def test_custom_prefix(self):
        assert _new_id("custom_").startswith("custom_")
        assert len(_new_id("x_")) == 2 + 32

    def test_empty_prefix(self):
        result = _new_id("")
        assert _HEX32.fullmatch(result)


class TestIdLengthFitsDbColumn:
    """Все ID хранятся в колонках String(48). Префикс + 32 hex = ?"""

    def test_log_id_fits_48(self):
        assert len(audit_event_id()) <= 48  # 4 + 32 = 36

    def test_rl_id_fits_48(self):
        assert len(audit_rule_id()) <= 48

    def test_se_id_fits_48(self):
        assert len(service_event_id()) <= 48
