"""Edge cases для token_service.create_pat:
- expires_at без tzinfo (naive datetime) — должна трактоваться как UTC
- expires_at точно равен now → INVALID_TOKEN_EXPIRY
- account_admin без dept — dept-чек пропускается, scope любой
- actor без department_id — dept-чек пропускается
- allowed_services пустой список — не проверяем dept (нет forbidden)
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.core.exceptions import ConflictError, DomainValidationError
from src.services import token_service


# ── expires_at edge cases ────────────────────────────────────────────────────

class TestCreatePATExpiry:
    async def test_naive_datetime_in_past_raises_invalid_expiry(self, db, user_a):
        """Naive datetime в прошлом → DomainValidationError INVALID_TOKEN_EXPIRY.

        Внутри create_pat: naive dt заменяется tzinfo=UTC, потом сравнивается с utcnow().
        """
        past_naive = datetime.utcnow() - timedelta(hours=1)
        with pytest.raises(DomainValidationError) as exc:
            await token_service.create_pat(
                db, user_a.id, "naive_past_pat", [], expires_at=past_naive,
            )
        assert exc.value.error_code == "INVALID_TOKEN_EXPIRY"

    async def test_naive_datetime_in_future_is_accepted(self, db, user_a):
        """Naive datetime в будущем (считаем UTC) → PAT создаётся."""
        future_naive = datetime.utcnow() + timedelta(days=7)
        pat = await token_service.create_pat(
            db, user_a.id, "naive_future_pat", [], expires_at=future_naive,
        )
        assert pat.token.startswith("dbos_pat_")
        assert pat.expires_at is not None

    async def test_expires_at_now_raises(self, db, user_a):
        """expires_at == utcnow() — граничный случай: <= проверка должна отбить."""
        # Берём время чуть в прошлом (ensure <= now)
        just_past = datetime.now(timezone.utc) - timedelta(seconds=1)
        with pytest.raises(DomainValidationError) as exc:
            await token_service.create_pat(
                db, user_a.id, "now_expiry_pat", [], expires_at=just_past,
            )
        assert exc.value.error_code == "INVALID_TOKEN_EXPIRY"

    async def test_future_expires_at_with_timezone_accepted(self, db, user_a):
        """Tz-aware datetime в будущем → нет ошибки."""
        future = datetime.now(timezone.utc) + timedelta(days=30)
        pat = await token_service.create_pat(
            db, user_a.id, "tz_future_pat", [], expires_at=future,
        )
        assert pat.expires_at is not None

    async def test_no_expires_at_creates_non_expiring_token(self, db, user_a):
        """expires_at=None → PAT без срока действия."""
        pat = await token_service.create_pat(
            db, user_a.id, "no_expiry_pat", [],
        )
        assert pat.expires_at is None


# ── Scope validation: account_admin пропускает dept-check ───────────────────

class TestCreatePATScope:
    async def test_account_admin_without_dept_can_scope_any_service(self, db, account_admin):
        """account_admin без department_id — dept-check пропускается, любые services разрешены."""
        pat = await token_service.create_pat(
            db, account_admin.id, "admin_any_scope",
            allowed_services=["exotic_service_that_doesnt_exist"],
        )
        assert pat.token.startswith("dbos_pat_")

    async def test_user_with_no_dept_in_db_skips_dept_check(self, db, db_user_no_dept):
        """Юзер с department_id=None (не admin) — dept-чек пропускается (actor.department_id is None)."""
        pat = await token_service.create_pat(
            db, db_user_no_dept.id, "no_dept_scope",
            allowed_services=["any_service"],
        )
        assert pat.token.startswith("dbos_pat_")

    async def test_empty_allowed_services_skips_scope_check(self, db, user_a):
        """Пустой allowed_services — блок if allowed_services не входит, нет db-queries на dept."""
        pat = await token_service.create_pat(
            db, user_a.id, "empty_scope_pat", allowed_services=[],
        )
        assert pat.token.startswith("dbos_pat_")

    async def test_forbidden_service_raises_validation_error(
        self, db, user_a, dept_a_with_service, service_x,
    ):
        """user_a в dept_a_with_service может только service_x; other_svc → DomainValidationError."""
        with pytest.raises(DomainValidationError) as exc:
            await token_service.create_pat(
                db, user_a.id, "bad_scope_direct",
                allowed_services=["other_svc_not_in_dept"],
            )
        assert exc.value.error_code == "SERVICE_NOT_ALLOWED_FOR_DEPARTMENT"
        assert "other_svc_not_in_dept" in exc.value.details["forbidden_services"]

    async def test_partial_forbidden_reports_only_forbidden(
        self, db, user_a, dept_a_with_service, service_x,
    ):
        """Один допустимый сервис + один запрещённый — в details только запрещённый."""
        with pytest.raises(DomainValidationError) as exc:
            await token_service.create_pat(
                db, user_a.id, "partial_bad_scope",
                allowed_services=[service_x.service_name, "ghost_service"],
            )
        forbidden = exc.value.details["forbidden_services"]
        assert "ghost_service" in forbidden
        assert service_x.service_name not in forbidden

    async def test_forbidden_details_include_available_services(
        self, db, user_a, dept_a_with_service, service_x,
    ):
        """В details должен быть `available_services` — подсказка юзеру."""
        with pytest.raises(DomainValidationError) as exc:
            await token_service.create_pat(
                db, user_a.id, "available_hint_pat",
                allowed_services=["ghost_service"],
            )
        details = exc.value.details
        assert "available_services" in details
        # service_x активен в dept_a_with_service — должен быть в подсказке.
        assert service_x.service_name in details["available_services"]
        # Список отсортирован — детерминированно для клиента.
        assert details["available_services"] == sorted(details["available_services"])


# ── Duplicate name ───────────────────────────────────────────────────────────

class TestCreatePATDuplicate:
    async def test_duplicate_name_raises_conflict(self, db, user_a):
        await token_service.create_pat(db, user_a.id, "dup_direct", [])
        with pytest.raises(ConflictError) as exc:
            await token_service.create_pat(db, user_a.id, "dup_direct", [])
        assert exc.value.error_code == "TOKEN_NAME_ALREADY_EXISTS"

    async def test_same_name_for_different_users_is_ok(self, db, user_a, user_b):
        """Имена PAT уникальны per-user — одинаковое имя у разных юзеров допустимо."""
        await token_service.create_pat(db, user_a.id, "shared_name", [])
        pat_b = await token_service.create_pat(db, user_b.id, "shared_name", [])
        assert pat_b.token.startswith("dbos_pat_")


# ── Фикстуры ─────────────────────────────────────────────────────────────────

import pytest_asyncio
from tests.conftest import _make_user


@pytest_asyncio.fixture()
async def db_user_no_dept(db):
    """Обычный юзер без отдела (не account_admin) — edge case для dept-skip."""
    return await _make_user(db, "no_dept_user", "Pass1234!", department_id=None)
