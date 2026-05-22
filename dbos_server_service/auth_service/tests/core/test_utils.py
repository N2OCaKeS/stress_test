"""Unit-тесты для `src/utils/{ids,pagination,time}.py` — без БД, без HTTP."""

from datetime import datetime, timedelta, timezone

import pytest

from src.utils import ids, pagination, time as time_utils


# ── ids.py ────────────────────────────────────────────────────────────────────

class TestIdGenerators:
    @pytest.mark.parametrize("factory, prefix", [
        (ids.user_id, "usr_"),
        (ids.department_id, "dep_"),
        (ids.session_id, "ses_"),
        (ids.pat_id, "pat_"),
        (ids.bot_id, "bot_"),
        (ids.bot_token_id, "btk_"),
        (ids.ban_id, "ban_"),
        (ids.oauth_client_id, "cli_"),
        (ids.oauth_code_id, "oac_"),
        (ids.service_role_def_id, "srd_"),
        (ids.group_id, "grp_"),
        (ids.group_membership_id, "gms_"),
        (ids.group_service_access_id, "gsa_"),
        (ids.group_service_role_id, "gsr_"),
        (ids.bot_service_role_id, "bsr_"),
    ])
    def test_prefix_matches_factory(self, factory, prefix: str):
        assert factory().startswith(prefix)

    @pytest.mark.parametrize("factory", [
        ids.user_id, ids.department_id, ids.bot_id, ids.bot_service_role_id,
    ])
    def test_each_call_unique(self, factory):
        ids_set = {factory() for _ in range(1000)}
        assert len(ids_set) == 1000

    def test_new_id_format(self):
        val = ids._new_id("test_")
        assert val.startswith("test_")
        # uuid4.hex → 32 hex chars
        assert len(val) == len("test_") + 32
        suffix = val[len("test_"):]
        # Только [0-9a-f] (hex без дефисов)
        assert all(c in "0123456789abcdef" for c in suffix)

    def test_new_id_accepts_empty_prefix(self):
        val = ids._new_id("")
        assert len(val) == 32


# ── pagination.py ────────────────────────────────────────────────────────────

class TestPaginationParams:
    def test_defaults(self):
        p = pagination.PaginationParams()
        assert p.offset == 0
        assert p.limit == 50

    @pytest.mark.parametrize("offset", [-1, -10, -10_000])
    def test_negative_offset_clamped_to_zero(self, offset: int):
        p = pagination.PaginationParams(offset=offset)
        assert p.offset == 0

    @pytest.mark.parametrize("limit", [0, -1, -50])
    def test_zero_or_negative_limit_clamped_to_one(self, limit: int):
        p = pagination.PaginationParams(limit=limit)
        assert p.limit == 1

    @pytest.mark.parametrize("limit, expected", [(200, 200), (201, 200), (300, 200), (1_000_000, 200)])
    def test_oversized_limit_capped(self, limit: int, expected: int):
        p = pagination.PaginationParams(limit=limit)
        assert p.limit == expected

    def test_in_range_values_passthrough(self):
        p = pagination.PaginationParams(offset=100, limit=25)
        assert p.offset == 100
        assert p.limit == 25


# ── time.py ──────────────────────────────────────────────────────────────────

class TestTimeHelpers:
    def test_utcnow_is_timezone_aware(self):
        now = time_utils.utcnow()
        assert now.tzinfo is not None
        assert now.tzinfo.utcoffset(now) == timedelta(0)

    def test_expires_at_minutes(self):
        delta = time_utils.expires_at(minutes=5) - time_utils.utcnow()
        # Тонкая флапа на медленных машинах: ±2 секунды.
        assert timedelta(minutes=4, seconds=58) <= delta <= timedelta(minutes=5, seconds=2)

    def test_expires_at_days(self):
        delta = time_utils.expires_at(days=14) - time_utils.utcnow()
        assert timedelta(days=13, hours=23) <= delta <= timedelta(days=14, seconds=2)

    def test_expires_at_combined(self):
        delta = time_utils.expires_at(days=1, minutes=10) - time_utils.utcnow()
        assert timedelta(days=1, minutes=9, seconds=58) <= delta <= timedelta(days=1, minutes=10, seconds=2)

    def test_expires_at_zero_returns_now(self):
        delta = time_utils.expires_at() - time_utils.utcnow()
        assert -timedelta(seconds=2) <= delta <= timedelta(seconds=2)

    def test_is_expired_past_returns_true(self):
        past = datetime.now(timezone.utc) - timedelta(seconds=10)
        assert time_utils.is_expired(past) is True

    def test_is_expired_future_returns_false(self):
        future = datetime.now(timezone.utc) + timedelta(minutes=5)
        assert time_utils.is_expired(future) is False

    def test_is_expired_naive_assumed_utc(self):
        """Безтаймзонный datetime трактуется как UTC — фиксируем поведение."""
        naive_past = datetime.utcnow() - timedelta(seconds=30)
        assert time_utils.is_expired(naive_past) is True
        naive_future = datetime.utcnow() + timedelta(minutes=5)
        assert time_utils.is_expired(naive_future) is False

    def test_is_expired_unix_epoch_far_past(self):
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        assert time_utils.is_expired(epoch) is True

    def test_is_expired_far_future(self):
        far = datetime(9999, 1, 1, tzinfo=timezone.utc)
        assert time_utils.is_expired(far) is False
